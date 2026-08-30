from fastapi import APIRouter, Depends, Request, status, HTTPException
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
import hashlib
import hmac
import json
from bson import ObjectId
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError
from app.schemas.order import (
    OrderCreate,
    OrderUpdate,
    OrderOut,
    OrderTrackOut,
    InitiatePaymentRequest,
)
from app.services.order_service import OrderService
from app.services.shipping_service import ShippingService
from app.integrations.shipping import ShippingProviderError
from app.integrations.shipping.nimbuspost import (
    extract_webhook_order_number,
    extract_webhook_shipment_info,
    map_webhook_status,
)
from app.db.mongodb import get_database
from app.utils.deps import get_current_user, get_current_user_optional, require_admin
from app.services.auth_service import AuthService
from app.services.mail_service import MailService
from app.services.commission_service import CommissionService
from app.services.coupon_service import CouponService
from app.services.offer_service import OfferService
from app.services.pricing_service import compute_line_unit_price
from app.config.config import settings

router = APIRouter(prefix="/orders", tags=["orders"])

class RazorpayOrderResponse(BaseModel):
    id: str
    entity: str
    amount: int
    currency: str
    receipt: str
    status: str
    free_decants_removed: bool = False
    free_decants_removed_reason: Optional[str] = None

class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

class VerifyAndCreateRequest(BaseModel):
    payment_details: PaymentVerifyRequest
    order_data: OrderCreate

class CustomerCancelRequest(BaseModel):
    customer_email: Optional[str] = None


class PlaceCodRequest(BaseModel):
    order_data: OrderCreate
    idempotency_key: str

@router.post("/", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(order_in: OrderCreate, db=Depends(get_database), current_user=Depends(get_current_user_optional)):
    order_service = OrderService(db)
    if current_user and not current_user.get("is_admin", False):
        order_in.user_id = str(current_user["_id"])
        order_in.customer_name = order_in.customer_name or current_user.get("full_name")
        order_in.customer_email = order_in.customer_email or current_user.get("email")
    try:
        return await order_service.create(order_in)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.get("/")
async def get_orders(
    user_id: Optional[str] = None,
    q: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    order_service = OrderService(db)
    if current_user.get("is_admin", False):
        return await order_service.get_all(user_id, q=q, skip=skip, limit=limit)
    return await order_service.get_all(str(current_user["_id"]), skip=skip, limit=limit)

@router.get("/track/{id}", response_model=OrderTrackOut)
async def track_order(id: str, db=Depends(get_database)):
    order_service = OrderService(db)
    order = await order_service.get_by_id(id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    enriched = await order_service._attach_promo_to_orders([order])
    return enriched[0]

@router.post("/{order_id}/customer-cancel")
async def customer_cancel_order(
    order_id: str,
    body: CustomerCancelRequest,
    db=Depends(get_database),
    current_user=Depends(get_current_user_optional),
):
    order_service = OrderService(db)
    order = await order_service.get_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.get("status") in ("cancelled", "refunded"):
        return {"ok": True, "message": "Order is already cancelled."}

    created_at = order.get("created_at")
    if not created_at or (datetime.now(timezone.utc) - created_at) > timedelta(hours=24):
        raise HTTPException(status_code=400, detail="Cancellation window (24 hours) has expired.")

    if order.get("status") not in ("pending", "processing"):
        raise HTTPException(
            status_code=400,
            detail=f"Order cannot be cancelled in '{order.get('status')}' state.",
        )

    if current_user and str(order.get("user_id")) != "guest":
        if str(order.get("user_id")) != str(current_user["_id"]):
            raise HTTPException(status_code=403, detail="You are not authorized to cancel this order.")
    else:
        if not body.customer_email:
            raise HTTPException(status_code=400, detail="Email is required to cancel a guest order.")
        if (body.customer_email.strip().lower() != (order.get("customer_email") or "").strip().lower()):
            raise HTTPException(status_code=403, detail="Email does not match the order.")

    # Block cancel if commission already paid out
    comm = await db["commissions"].find_one({
        "order_id": str(order["_id"]),
        "status": "paid",
    })
    if comm:
        raise HTTPException(
            status_code=400,
            detail="Commission for this order has already been paid out. Please contact support to cancel.",
        )

    # Refund via Razorpay if payment was captured
    refunded = False
    if (
        order.get("payment_status") == "paid"
        and order.get("payment_details", {}).get("razorpay_payment_id")
    ):
        try:
            order_service.refund_payment_full(
                order["payment_details"]["razorpay_payment_id"],
                order["total_amount"],
            )
            refunded = True
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Refund failed — please contact support. ({e})",
            )

    # Restore stock
    await order_service.restore_stock(order.get("items", []))
    if order.get("free_decants"):
        await order_service._restore_free_decant_stock(order["free_decants"])

    # Update order document
    update_fields: dict = {
        "status": "cancelled",
        "cancelled_at": datetime.now(timezone.utc),
        "cancelled_by": "customer",
        "cancellation_reason": "Customer cancel (24h window)",
    }
    if refunded:
        update_fields["payment_status"] = "refunded"
    await db["orders"].update_one({"_id": order["_id"]}, {"$set": update_fields})

    # Cancel commission
    if order.get("influencer_id"):
        try:
            csvc = CommissionService(db)
            pending_comm = await db["commissions"].find_one({
                "order_id": str(order["_id"]),
                "status": {"$in": ["pending", "approved"]},
            })
            if pending_comm:
                await csvc.cancel_commission(
                    str(pending_comm["_id"]),
                    reason="Order cancelled by customer",
                )
        except Exception as e:
            print(f"[CANCEL] Commission cancel error (non-blocking): {e}")

    # Release coupon usage
    if order.get("coupon_code"):
        try:
            coupon_svc = CouponService(db)
            await coupon_svc.release_coupon(order["coupon_code"])
        except Exception as e:
            print(f"[CANCEL] Coupon release error (non-blocking): {e}")

    # Send cancellation email
    try:
        mail_service = MailService()
        await mail_service.send_order_cancellation(
            order.get("customer_email"),
            order.get("customer_name"),
            order,
        )
    except Exception as e:
        print(f"[CANCEL] Email error (non-blocking): {e}")

    return {
        "ok": True,
        "message": "Order cancelled successfully."
            + (" A refund has been initiated." if refunded else ""),
    }


@router.get("/abandoned-checkouts")
async def get_abandoned_checkouts(db=Depends(get_database), _admin=Depends(require_admin)):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    cursor = db["pending_checkouts"].find({
        "status": "pending",
        "created_at": {"$lt": cutoff},
    }).sort("created_at", -1)
    docs = await cursor.to_list(length=500)
    results = []
    for doc in docs:
        od = doc.get("order_data") or {}
        results.append({
            "id": str(doc["_id"]),
            "razorpay_order_id": doc.get("razorpay_order_id"),
            "customer_name": od.get("customer_name"),
            "customer_email": od.get("customer_email"),
            "customer_phone": od.get("customer_phone"),
            "items": od.get("items", []),
            "total_amount": od.get("total_amount", 0),
            "shipping_address": od.get("shipping_address"),
            "coupon_code": od.get("coupon_code"),
            "influencer_id": od.get("influencer_id"),
            "created_at": doc.get("created_at", "").isoformat() if doc.get("created_at") else None,
        })
    return results


@router.delete("/abandoned-checkouts/{checkout_id}")
async def delete_abandoned_checkout(
    checkout_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    result = await db["pending_checkouts"].delete_one({"_id": ObjectId(checkout_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Checkout not found")
    return {"ok": True}


@router.get("/{id}", response_model=OrderOut)
async def get_order(id: str, db=Depends(get_database), current_user=Depends(get_current_user)):
    order_service = OrderService(db)
    order = await order_service.get_by_id(id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if not current_user.get("is_admin", False) and str(order.get("user_id")) != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Not authorized to view this order")
    return order

@router.put("/{id}", response_model=OrderOut)
async def update_order(id: str, order_in: OrderUpdate, db=Depends(get_database), _admin=Depends(require_admin)):
    order_service = OrderService(db)
    old_order = await order_service.get_by_id(id)
    updated = await order_service.update(id, order_in)

    if old_order and updated:
        csvc = CommissionService(db)
        updated = await csvc.apply_order_status_effects(old_order, updated, order_in)

    return updated

@router.post("/sync", status_code=status.HTTP_200_OK)
async def sync_guest_orders(db=Depends(get_database), current_user=Depends(get_current_user)):
    auth_service = AuthService(db)
    count = await auth_service.attach_guest_orders(current_user.get("email"), str(current_user["_id"]), current_user.get("full_name"))
    return {"synced": count}

@router.post("/initiate-payment-only", response_model=RazorpayOrderResponse)
async def initiate_payment_only(body: InitiatePaymentRequest, db=Depends(get_database)):
    order_service = OrderService(db)
    items_dicts = [i.model_dump() for i in body.items]
    try:
        await order_service.ensure_stock_for_checkout(items_dicts)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    free_decants_removed = False
    free_decants_removed_reason: Optional[str] = None
    order_data = body.order_data
    if order_data and order_data.get("free_decants"):
        try:
            await order_service._validate_free_decants(
                order_data.get("items", []),
                order_data.get("free_decants", []),
            )
            await order_service._ensure_free_decant_stock(order_data.get("free_decants", []))
        except ValueError as e:
            order_data["free_decants"] = []
            order_data["free_decants_dropped_reason"] = str(e)
            free_decants_removed = True
            free_decants_removed_reason = str(e)

    receipt = f"pre_{int(datetime.now(timezone.utc).timestamp())}"
    try:
        rzp_order = await order_service.create_razorpay_order(body.amount, receipt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if order_data:
        await db["pending_checkouts"].update_one(
            {"razorpay_order_id": rzp_order["id"]},
            {"$set": {
                "razorpay_order_id": rzp_order["id"],
                "order_data": order_data,
                "status": "pending",
                "created_at": datetime.now(timezone.utc),
                "converted_at": None,
                "order_id": None,
                "free_decants_removed": free_decants_removed,
                "free_decants_removed_reason": free_decants_removed_reason,
            }},
            upsert=True,
        )

    return {
        **rzp_order,
        "free_decants_removed": free_decants_removed,
        "free_decants_removed_reason": free_decants_removed_reason,
    }

async def _revalidate_daily_deal_pricing(db, order_in: OrderCreate) -> None:
    """Reject orders whose line prices disagree with the server-derived
    price for today's daily deal.

    Tampering vector this closes: a client could pay yesterday's full
    price for an item that should be 50% off today (or vice versa). Without
    a server-side recompute, our discount marketing becomes opt-in for the
    honest user only.

    The check is per-variant: we re-derive what the unit price *should* be
    given the active deal + variant + pack flag. A small tolerance (₹0.5)
    absorbs floating-point rounding.

    Coupon-stacking is also blocked here as a belt-and-suspenders against
    a stale coupon being re-submitted after a deal item was added to cart.
    """
    if not order_in.items:
        return

    offer_service = OfferService(db)
    deal = await offer_service.get_active_daily_deal()

    deal_pids = {
        str(pid)
        for pid in (((deal or {}).get("config") or {}).get("product_ids") or [])
    }

    has_deal_line = False
    distinct_pids = {str(i.product_id) for i in order_in.items if i.product_id}

    products_by_id: dict[str, dict] = {}
    for pid in distinct_pids:
        if not ObjectId.is_valid(pid):
            continue
        prod = await db["products"].find_one({"_id": ObjectId(pid)})
        if prod:
            products_by_id[pid] = prod

    for item in order_in.items:
        if item.gift_box_id:
            # Gift boxes have their own pricing pipeline; skip here.
            continue
        if item.product_type == "set":
            continue
        product = products_by_id.get(str(item.product_id))
        if not product:
            # Stock check will fail later anyway; don't double-report.
            continue
        # Daily-deal price covers the bare variant. Bottle add-ons are
        # priced separately and not subject to the deal.
        expected_unit = compute_line_unit_price(
            product,
            size_ml=int(item.size_ml),
            is_pack=bool(item.is_pack),
            deal=deal,
        )
        if expected_unit is None:
            continue
        expected_with_bottle = expected_unit + float(item.bottle_price or 0)
        if abs(float(item.price) - expected_with_bottle) > 0.5:
            raise HTTPException(
                status_code=409,
                detail="Prices have changed — please refresh your cart.",
            )
        if str(item.product_id) in deal_pids:
            has_deal_line = True

    if has_deal_line and order_in.coupon_code:
        raise HTTPException(
            status_code=409,
            detail="Today's deal is already applied. Coupons can't be combined.",
        )


@router.post("/verify-and-create", response_model=OrderOut)
async def verify_and_create(
    data: VerifyAndCreateRequest, 
    db=Depends(get_database), 
    current_user=Depends(get_current_user_optional)
):
    order_service = OrderService(db)
    
    # 1. Verify Signature
    params_dict = {
        'razorpay_order_id': data.payment_details.razorpay_order_id,
        'razorpay_payment_id': data.payment_details.razorpay_payment_id,
        'razorpay_signature': data.payment_details.razorpay_signature
    }
    
    try:
        order_service.client.utility.verify_payment_signature(params_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Signature verification failed")

    # Idempotency: if an order already exists for this razorpay_order_id (e.g. webhook arrived first), return it
    existing = await db["orders"].find_one({
        "payment_details.razorpay_order_id": data.payment_details.razorpay_order_id
    })
    if existing:
        patch: dict = {}
        incoming_fd = data.order_data.free_decants if data.order_data.free_decants else None
        if incoming_fd and not existing.get("free_decants"):
            patch["free_decants"] = [fd.dict() if hasattr(fd, "dict") else fd.model_dump() for fd in incoming_fd]
        existing_ca = existing.get("created_at")
        if existing_ca and hasattr(existing_ca, "tzinfo") and existing_ca.tzinfo is None:
            patch["created_at"] = existing_ca.replace(tzinfo=timezone.utc)
        if patch:
            await db["orders"].update_one({"_id": existing["_id"]}, {"$set": patch})
            existing.update(patch)
        return existing

    # 2. Prepare Order Data
    order_in = data.order_data
    if current_user and not current_user.get("is_admin", False):
        order_in.user_id = str(current_user["_id"])
        order_in.customer_name = order_in.customer_name or current_user.get("full_name")
        order_in.customer_email = order_in.customer_email or current_user.get("email")

    # 2a. Daily-deal price validation. We do this BEFORE claiming the
    # pending checkout so a tampered request doesn't poison the pending row.
    await _revalidate_daily_deal_pricing(db, order_in)

    # Update payment details
    order_in.payment_status = "paid"
    order_in.status = "processing"
    order_in.payment_details = {
        "razorpay_order_id": data.payment_details.razorpay_order_id,
        "razorpay_payment_id": data.payment_details.razorpay_payment_id,
        "razorpay_signature": data.payment_details.razorpay_signature,
        "paid_at": datetime.now(timezone.utc).isoformat(),
        "contact": order_in.customer_phone,
    }

    # 2b. If a coupon code was provided, validate and attribute to influencer
    if order_in.coupon_code and not order_in.influencer_id:
        try:
            coupon_svc = CouponService(db)
            result = await coupon_svc.validate_coupon(order_in.coupon_code)
            if result["valid"] and result["influencer_id"]:
                order_in.influencer_id = result["influencer_id"]
        except Exception:
            pass

    # 2c. Atomic claim — guarantees only one path (this or the webhook) ever
    # reaches OrderService.create() for a given razorpay_order_id. Eliminates
    # the find_one + insert_one race.
    claimed = await _claim_pending_checkout(db, data.payment_details.razorpay_order_id)
    if not claimed:
        # Webhook (or a retry) already grabbed this checkout. Wait a beat for
        # the winning path to complete, then return whatever it created.
        # Re-check for an existing order; if not found, the other path is
        # mid-flight — surface a 409 so the client can show the spinner.
        existing = await db["orders"].find_one({
            "payment_details.razorpay_order_id": data.payment_details.razorpay_order_id
        })
        if existing:
            return existing
        raise HTTPException(
            status_code=409,
            detail="Order is being finalized via webhook. Please refresh in a moment.",
        )

    # 3. Create Order (Stock will be checked and decremented here)
    try:
        new_order = await order_service.create(order_in)

        # 4. Side-effects (commission, coupon, email) — only on the winning
        # writer. A duplicate (concurrent webhook or retry) skips so we don't
        # double-charge commission or double-email the customer.
        if not new_order.pop("_was_duplicate", False):
            await _post_order_side_effects(new_order, db)

        await db["pending_checkouts"].update_one(
            {"razorpay_order_id": data.payment_details.razorpay_order_id},
            {"$set": {
                "status": "converted",
                "converted_at": datetime.now(timezone.utc),
                "order_id": str(new_order["_id"]),
            }},
        )

        return new_order
    except ValueError as e:
        detail = str(e)
        refunded = False
        if "Insufficient stock" in detail:
            try:
                order_service.refund_payment_full(
                    data.payment_details.razorpay_payment_id,
                    data.order_data.total_amount,
                )
                refunded = True
                detail = (
                    "Some items are no longer in stock. Your payment has been refunded "
                    "automatically. Please update your cart and try again."
                )
            except Exception as ref_err:
                print(f"[RAZORPAY] Refund after stock failure failed: {ref_err}")
                detail = (
                    "Some items are no longer in stock and your order could not be placed. "
                    "Your payment may still be captured — please contact support with your "
                    f"payment id: {data.payment_details.razorpay_payment_id}"
                )
        # Release the claim so a webhook retry doesn't create a phantom order
        # against a refunded payment.
        await db["pending_checkouts"].update_one(
            {"razorpay_order_id": data.payment_details.razorpay_order_id},
            {"$set": {
                "status": "refunded" if refunded else "failed",
                "failed_at": datetime.now(timezone.utc),
                "failure_reason": detail,
            }},
        )
        raise HTTPException(status_code=400, detail=detail)
    except Exception as e:
        # Unknown failure — release the claim back to pending so webhook/retry
        # can attempt to recover, instead of stranding the checkout.
        await db["pending_checkouts"].update_one(
            {
                "razorpay_order_id": data.payment_details.razorpay_order_id,
                "status": "processing",
            },
            {"$set": {"status": "pending"}, "$unset": {"claimed_at": ""}},
        )
        raise HTTPException(status_code=500, detail=f"Order creation failed: {str(e)}")

@router.post("/place-cod", response_model=OrderOut)
async def place_cod(
    data: PlaceCodRequest,
    db=Depends(get_database),
    current_user=Depends(get_current_user_optional),
):
    """Create a Cash on Delivery order. Bypasses Razorpay entirely: no
    `pending_checkouts` row, no signature verification, no webhook. Stock
    is reserved at insert time exactly the same way prepaid orders do it.

    Dedup is enforced by a unique partial index on `orders.idempotency_key`
    plus an explicit short-circuit lookup for a fast path on retries.

    Commission attribution is *not* created here: COD commissions are
    created and approved only when the order is marked delivered (see the
    delivered branch in `update_order`).
    """
    if not settings.COD_ENABLED:
        raise HTTPException(status_code=400, detail="Cash on Delivery is currently unavailable.")

    if not data.idempotency_key or not data.idempotency_key.strip():
        raise HTTPException(status_code=400, detail="idempotency_key is required for COD orders.")

    idem_key = data.idempotency_key.strip()

    # Fast path: if a previous request already created this order, return it.
    existing = await db["orders"].find_one({"idempotency_key": idem_key})
    if existing:
        return existing

    order_in = data.order_data

    # Server-side eligibility — never trust the client. The fee is added
    # back onto the total so that a tampered client can't smuggle a higher
    # ticket size under the cap.
    cod_fee = float(settings.COD_FEE)
    if order_in.cod_fee is not None and float(order_in.cod_fee) != cod_fee:
        raise HTTPException(status_code=400, detail="Invalid COD handling fee.")
    if float(order_in.total_amount) > float(settings.COD_MAX_AMOUNT):
        raise HTTPException(
            status_code=400,
            detail=f"Cash on Delivery is only available for orders up to ₹{int(settings.COD_MAX_AMOUNT):,}.",
        )

    # Auth handling — same shape as verify-and-create so guest + logged-in
    # users behave identically across both checkout paths.
    if current_user and not current_user.get("is_admin", False):
        order_in.user_id = str(current_user["_id"])
        order_in.customer_name = order_in.customer_name or current_user.get("full_name")
        order_in.customer_email = order_in.customer_email or current_user.get("email")

    # Daily-deal price validation. Same tamper-prevention guarantee as
    # verify-and-create — runs before idempotency-key takes effect.
    await _revalidate_daily_deal_pricing(db, order_in)

    # Coupon → influencer attribution (mirrors prepaid path).
    if order_in.coupon_code and not order_in.influencer_id:
        try:
            coupon_svc = CouponService(db)
            result = await coupon_svc.validate_coupon(order_in.coupon_code)
            if result["valid"] and result["influencer_id"]:
                order_in.influencer_id = result["influencer_id"]
        except Exception:
            pass

    # Force the COD shape regardless of what the client sent.
    order_in.payment_method = "cod"
    order_in.payment_status = "cod_pending"
    order_in.status = "pending"
    order_in.cod_fee = cod_fee
    order_in.idempotency_key = idem_key
    order_in.payment_details = {
        "method": "cod",
        "cod_fee": cod_fee,
        "source": "checkout",
    }

    order_service = OrderService(db)
    try:
        new_order = await order_service.create(order_in)
    except ValueError as e:
        # Stock / validation failures from `OrderService._ensure_stock`.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Order creation failed: {str(e)}")

    # Skip side-effects on the duplicate path; the winning writer already
    # ran them. (Belt-and-suspenders for the rare case the index races a
    # concurrent submit past our fast-path lookup.)
    if not new_order.pop("_was_duplicate", False):
        await _post_order_side_effects(new_order, db)

    return new_order


async def _claim_pending_checkout(db, rzp_order_id: str) -> dict | None:
    """
    Atomically transition `pending_checkouts.status` from `pending` → `processing`.

    Returns the (claimed) pending checkout doc on success, or `None` if the
    checkout is already being processed / has been converted by another path.

    This is the race-killer for `verify-and-create` vs `webhook` (and webhook
    retries vs itself). Whoever flips the status first proceeds to insert the
    order; everyone else short-circuits.
    """
    # 30-second stuck-claim recovery: if a previous claim crashed mid-flight
    # without releasing, another writer (e.g. a webhook retry) may legitimately
    # take it over.
    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
    return await db["pending_checkouts"].find_one_and_update(
        {
            "razorpay_order_id": rzp_order_id,
            "$or": [
                {"status": "pending"},
                {"status": "processing", "claimed_at": {"$lt": stale_cutoff}},
            ],
        },
        {"$set": {"status": "processing", "claimed_at": datetime.now(timezone.utc)}},
    )


async def _post_order_side_effects(new_order: dict, db) -> None:
    """Commission, coupon usage, emails — shared by verify-and-create + webhook + place-cod.

    COD orders intentionally skip commission creation here: the commission
    is created (and immediately approved) only when the order is marked
    `delivered`. This keeps RTO'd / undelivered COD orders out of the
    influencer earnings dashboard entirely.
    """
    if new_order.get("influencer_id") and new_order.get("payment_method") != "cod":
        try:
            csvc = CommissionService(db)
            await csvc.create_commission(
                influencer_id=new_order["influencer_id"],
                order_id=str(new_order["_id"]),
                order_total=new_order.get("total_amount", 0),
                buyer_user_id=new_order.get("user_id"),
            )
        except Exception as comm_err:
            print(f"[COMMISSION] Creation error (non-blocking): {comm_err}")

    if new_order.get("coupon_code"):
        try:
            coupon_svc = CouponService(db)
            await coupon_svc.use_coupon(new_order["coupon_code"])
        except Exception:
            pass

    mail_service = MailService()
    try:
        await mail_service.send_order_confirmation(
            new_order.get("customer_email"),
            new_order.get("customer_name"),
            new_order,
        )
        await mail_service.send_admin_new_order_alert(new_order)
    except Exception as mail_err:
        print(f"[MAIL] Async notification error (non-blocking): {mail_err}")


@router.post("/webhook/razorpay")
async def razorpay_webhook(request: Request, db=Depends(get_database)):
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    # Loud log so it's obvious when Razorpay is actually reaching this endpoint
    # (e.g. via a tunnel during local development).
    print(f"\n\033[96m[WEBHOOK]\033[0m razorpay webhook hit (body={len(body)} bytes, has_sig={bool(signature)})")

    if settings.RAZORPAY_WEBHOOK_SECRET:
        expected = hmac.new(
            settings.RAZORPAY_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event")
    if event not in ("payment.captured", "order.paid"):
        return {"ok": True, "skipped": event}

    payment_entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
    )
    rzp_order_id = payment_entity.get("order_id")
    rzp_payment_id = payment_entity.get("id")

    if not rzp_order_id or not rzp_payment_id:
        return {"ok": True, "skipped": "missing ids"}

    existing = await db["orders"].find_one({
        "payment_details.razorpay_order_id": rzp_order_id,
    })
    if existing:
        return {"ok": True, "already_created": str(existing["_id"])}

    # Atomic claim — only one writer ever reaches OrderService.create() for a
    # given razorpay_order_id. If this returns None, either verify-and-create
    # is mid-flight (we let it win) or the checkout is already converted.
    pending = await _claim_pending_checkout(db, rzp_order_id)
    if not pending:
        print(f"[WEBHOOK] Could not claim pending checkout for rzp order {rzp_order_id} (already processing/converted)")
        existing = await db["orders"].find_one({
            "payment_details.razorpay_order_id": rzp_order_id,
        })
        if existing:
            return {"ok": True, "already_created": str(existing["_id"])}
        return {"ok": True, "skipped": "claim_failed"}

    if not pending.get("order_data"):
        print(f"[WEBHOOK] Claimed pending checkout has no order_data for rzp order {rzp_order_id}")
        return {"ok": True, "skipped": "no_pending_checkout"}

    od = pending["order_data"]

    if od.get("coupon_code") and not od.get("influencer_id"):
        try:
            coupon_svc = CouponService(db)
            result = await coupon_svc.validate_coupon(od["coupon_code"])
            if result["valid"] and result["influencer_id"]:
                od["influencer_id"] = result["influencer_id"]
        except Exception:
            pass

    od["payment_status"] = "paid"
    od["status"] = "processing"
    od["payment_details"] = {
        "razorpay_order_id": rzp_order_id,
        "razorpay_payment_id": rzp_payment_id,
        "paid_at": datetime.now(timezone.utc).isoformat(),
        "source": "webhook",
        "contact": od.get("customer_phone"),
    }

    order_service = OrderService(db)
    try:
        order_in = OrderCreate(**od)
        new_order = await order_service.create(order_in)
    except Exception as e:
        print(f"[WEBHOOK] Order creation failed for rzp order {rzp_order_id}: {e}")
        return {"ok": False, "error": str(e)}

    was_duplicate = new_order.pop("_was_duplicate", False)
    if not was_duplicate:
        await _post_order_side_effects(new_order, db)

    await db["pending_checkouts"].update_one(
        {"razorpay_order_id": rzp_order_id},
        {"$set": {
            "status": "converted",
            "converted_at": datetime.now(timezone.utc),
            "order_id": str(new_order["_id"]),
        }},
    )

    if was_duplicate:
        print(f"[WEBHOOK] Duplicate insert blocked by unique index for rzp order {rzp_order_id}; returning existing order {new_order['_id']}")
        return {"ok": True, "already_created": str(new_order["_id"])}

    print(f"[WEBHOOK] Order {new_order['_id']} created for rzp order {rzp_order_id}")
    return {"ok": True, "order_id": str(new_order["_id"])}


@router.post("/webhook/nimbuspost")
async def nimbuspost_webhook(request: Request, db=Depends(get_database)):
    """
    Subscribe to both `order.updated` AND `tracking.updated` in NimbusPost
    (Settings → Webhooks, or `POST /v2/webhooks`). `order.updated` fires
    `status: "shipped"` on the booked→shipped transition and `status:
    "delivered"` on delivery — but this order-level transition can lag
    behind or never fire cleanly for some couriers (observed: a shipment
    stuck at NimbusPost's own `orderStatus: "booked"` for 13+ hours while
    its courier tracking already showed "in transit" scans). `tracking.
    updated` fires once per real courier scan, so we use it as a fallback
    "shipped" signal — any scan existing at all means the parcel was
    physically picked up. The payload is a flat JSON object either way (see
    `map_webhook_status()` / `extract_webhook_*()` in
    `app/integrations/shipping/nimbuspost.py` for the exact shape).

    Reuses `OrderService.update()` (shipped/delivered emails + Instagram
    promo invite) and `CommissionService.apply_order_status_effects()`
    (influencer commission + COD payment flip) so a webhook-driven status
    change behaves identically to an admin manually updating the order.

    `webhook_deliveries` stores the full payload per delivery (30-day TTL),
    not just the dedupe id — if a status transition doesn't happen as
    expected, query that collection by `order_id`/`event` instead of
    guessing at NimbusPost's status vocabulary from docs alone.
    """
    body = await request.body()
    signature = request.headers.get("x-nimbus-signature", "")
    delivery_id = request.headers.get("x-nimbus-delivery", "")
    event = request.headers.get("x-nimbus-event", "")
    print(
        f"\n\033[96m[WEBHOOK]\033[0m nimbuspost webhook hit "
        f"(event={event}, body={len(body)} bytes, delivery={delivery_id}, has_sig={bool(signature)})"
    )

    if settings.NIMBUSPOST_WEBHOOK_SECRET:
        # Header format is `sha256=<lowercase hex hmac of the raw body>`.
        expected = "sha256=" + hmac.new(
            settings.NIMBUSPOST_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Deliveries are at-least-once — dedupe on x-nimbus-delivery so a retry
    # doesn't re-send emails / re-run commission logic. The full payload is
    # kept (not just the id) so a future "why didn't X update" question can
    # be answered from this collection instead of ephemeral stdout logs —
    # TTL-expires after 30 days (see `webhook_deliveries_ttl` index).
    if delivery_id:
        try:
            await db["webhook_deliveries"].insert_one({
                "provider": "nimbuspost",
                "delivery_id": delivery_id,
                "event": event,
                "payload": payload,
                "received_at": datetime.now(timezone.utc),
            })
        except DuplicateKeyError:
            return {"ok": True, "skipped": "duplicate_delivery"}

    try:
        order_number = extract_webhook_order_number(payload)
        if not order_number or not ObjectId.is_valid(order_number):
            print(f"[WEBHOOK] nimbuspost payload missing/unrecognized order_number: {payload}")
            return {"ok": True, "skipped": "missing_order_number", "event": event}

        order = await db["orders"].find_one({"_id": ObjectId(order_number)})
        if not order:
            return {"ok": True, "skipped": "order_not_found"}

        new_status = map_webhook_status(event, payload)
        if not new_status:
            print(f"[WEBHOOK] nimbuspost event with unrecognized status: event={event} payload={payload}")
            return {"ok": True, "skipped": "unrecognized_status", "event": event}

        old_status = order.get("status", "")
        if old_status == new_status:
            return {"ok": True, "skipped": "no_status_change"}
        # Never let a "picked" event downgrade an order that's already further
        # along (delivered / cancelled / refunded) — only ever move forward.
        if new_status == "shipped" and old_status in ("delivered", "cancelled", "refunded"):
            return {"ok": True, "skipped": f"ignoring {new_status} for {old_status} order"}

        update_fields: Dict[str, Any] = {"status": new_status}
        update_fields.update(extract_webhook_shipment_info(payload))
        order_in = OrderUpdate(**update_fields)

        order_service = OrderService(db)
        updated = await order_service.update(order_number, order_in)

        if updated:
            csvc = CommissionService(db)
            updated = await csvc.apply_order_status_effects(order, updated, order_in)
    except Exception as e:
        # Don't let a processing error permanently burn this delivery's
        # dedupe slot — NimbusPost retries on any non-2xx, but our own
        # dedupe check would otherwise treat that retry as a duplicate and
        # silently drop it forever. Delete the just-inserted record so the
        # retry actually gets a chance to reprocess, then 500 so NimbusPost
        # knows to retry.
        if delivery_id:
            await db["webhook_deliveries"].delete_one(
                {"provider": "nimbuspost", "delivery_id": delivery_id}
            )
        print(f"[WEBHOOK] nimbuspost processing failed (event={event}): {e!r}")
        raise HTTPException(status_code=500, detail="Webhook processing failed") from e

    if delivery_id:
        await db["webhook_deliveries"].update_one(
            {"provider": "nimbuspost", "delivery_id": delivery_id},
            {"$set": {"order_id": order_number, "resolved_status": new_status}},
        )

    print(f"[WEBHOOK] nimbuspost order {order_number} status -> {new_status}")
    return {"ok": True, "order_id": order_number, "status": new_status}


class CreateShippingOrderResponse(BaseModel):
    provider: str
    integration: Dict[str, Any]
    created: bool


@router.post(
    "/{order_id}/shipping/{provider}/create",
    response_model=CreateShippingOrderResponse,
    status_code=status.HTTP_200_OK,
)
async def create_shipping_order(
    order_id: str,
    provider: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    """Create an order in an external shipping platform (admin only). Idempotent."""
    shipping_service = ShippingService(db)
    try:
        integration, created = await shipping_service.create_external_order(order_id, provider)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown shipping provider: {provider}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ShippingProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    return {
        "provider": provider.lower(),
        "integration": integration,
        "created": created,
    }
