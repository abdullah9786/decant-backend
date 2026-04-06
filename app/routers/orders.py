from fastapi import APIRouter, Depends, Request, status, HTTPException
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import hashlib
import hmac
import json
from bson import ObjectId
from pydantic import BaseModel
from app.schemas.order import (
    OrderCreate,
    OrderUpdate,
    OrderOut,
    OrderTrackOut,
    InitiatePaymentRequest,
)
from app.services.order_service import OrderService
from app.db.mongodb import get_database
from app.utils.deps import get_current_user, get_current_user_optional, require_admin
from app.services.auth_service import AuthService
from app.services.mail_service import MailService
from app.services.commission_service import CommissionService
from app.services.coupon_service import CouponService
from app.config.config import settings

router = APIRouter(prefix="/orders", tags=["orders"])

class RazorpayOrderResponse(BaseModel):
    id: str
    entity: str
    amount: int
    currency: str
    receipt: str
    status: str

class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

class VerifyAndCreateRequest(BaseModel):
    payment_details: PaymentVerifyRequest
    order_data: OrderCreate

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

@router.get("/", response_model=List[OrderOut])
async def get_orders(user_id: Optional[str] = None, db=Depends(get_database), current_user=Depends(get_current_user)):
    order_service = OrderService(db)
    if current_user.get("is_admin", False):
        return await order_service.get_all(user_id)
    return await order_service.get_all(str(current_user["_id"]))

@router.get("/track/{id}", response_model=OrderTrackOut)
async def track_order(id: str, db=Depends(get_database)):
    order_service = OrderService(db)
    order = await order_service.get_by_id(id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@router.get("/abandoned-checkouts")
async def get_abandoned_checkouts(db=Depends(get_database), _admin=Depends(require_admin)):
    cutoff = datetime.utcnow() - timedelta(minutes=30)
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

    if old_order and updated and old_order.get("influencer_id"):
        try:
            csvc = CommissionService(db)
            order_id_str = str(old_order["_id"])
            old_status = old_order.get("status", "")
            new_status = updated.get("status", "")

            if new_status == "delivered" and old_status != "delivered":
                comm = await db["commissions"].find_one({
                    "order_id": order_id_str, "status": "pending"
                })
                if comm:
                    await csvc.approve_commission(str(comm["_id"]))

            if new_status in ("cancelled", "refunded") and old_status not in ("cancelled", "refunded"):
                comm = await db["commissions"].find_one({
                    "order_id": order_id_str,
                    "status": {"$in": ["pending", "approved"]},
                })
                if comm:
                    await csvc.cancel_commission(str(comm["_id"]))

            # Recalculate commission when items change (partial cancellation)
            if order_in.items is not None:
                fulfilled_total = sum(
                    i.get("price", 0) * i.get("quantity", 0)
                    for i in updated.get("items", [])
                    if i.get("status") != "cancelled"
                )
                comm = await db["commissions"].find_one({
                    "order_id": order_id_str,
                    "status": {"$in": ["pending", "approved"]},
                })
                if comm and fulfilled_total != comm.get("order_total", 0):
                    original = comm.get("original_order_total") or comm.get("order_total", 0)
                    rate = comm.get("commission_rate", 0.10)
                    await db["commissions"].update_one(
                        {"_id": comm["_id"]},
                        {"$set": {
                            "original_order_total": round(original, 2),
                            "order_total": round(fulfilled_total, 2),
                            "commission_amount": round(fulfilled_total * rate, 2),
                        }},
                    )
        except Exception as e:
            print(f"[COMMISSION] Auto-update error (non-blocking): {e}")

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
    receipt = f"pre_{int(datetime.utcnow().timestamp())}"
    try:
        rzp_order = await order_service.create_razorpay_order(body.amount, receipt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if body.order_data:
        await db["pending_checkouts"].update_one(
            {"razorpay_order_id": rzp_order["id"]},
            {"$set": {
                "razorpay_order_id": rzp_order["id"],
                "order_data": body.order_data,
                "status": "pending",
                "created_at": datetime.utcnow(),
                "converted_at": None,
                "order_id": None,
            }},
            upsert=True,
        )

    return rzp_order

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
        return existing

    # 2. Prepare Order Data
    order_in = data.order_data
    if current_user and not current_user.get("is_admin", False):
        order_in.user_id = str(current_user["_id"])
        order_in.customer_name = order_in.customer_name or current_user.get("full_name")
        order_in.customer_email = order_in.customer_email or current_user.get("email")
    
    # Update payment details
    order_in.payment_status = "paid"
    order_in.status = "processing"
    order_in.payment_details = {
        "razorpay_order_id": data.payment_details.razorpay_order_id,
        "razorpay_payment_id": data.payment_details.razorpay_payment_id,
        "razorpay_signature": data.payment_details.razorpay_signature,
        "paid_at": datetime.utcnow().isoformat()
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
    
    # 3. Create Order (Stock will be checked and decremented here)
    try:
        new_order = await order_service.create(order_in)

        # 4. Side-effects (commission, coupon, email)
        await _post_order_side_effects(new_order, db)

        await db["pending_checkouts"].update_one(
            {"razorpay_order_id": data.payment_details.razorpay_order_id},
            {"$set": {
                "status": "converted",
                "converted_at": datetime.utcnow(),
                "order_id": str(new_order["_id"]),
            }},
        )

        return new_order
    except ValueError as e:
        detail = str(e)
        if "Insufficient stock" in detail:
            try:
                order_service.refund_payment_full(
                    data.payment_details.razorpay_payment_id
                )
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
        raise HTTPException(status_code=400, detail=detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Order creation failed: {str(e)}")

async def _post_order_side_effects(new_order: dict, db) -> None:
    """Commission, coupon usage, emails — shared by verify-and-create + webhook."""
    if new_order.get("influencer_id"):
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

    pending = await db["pending_checkouts"].find_one({"razorpay_order_id": rzp_order_id})
    if not pending or not pending.get("order_data"):
        print(f"[WEBHOOK] No pending checkout for rzp order {rzp_order_id}")
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
        "paid_at": datetime.utcnow().isoformat(),
        "source": "webhook",
    }

    order_service = OrderService(db)
    try:
        order_in = OrderCreate(**od)
        new_order = await order_service.create(order_in)
    except Exception as e:
        print(f"[WEBHOOK] Order creation failed for rzp order {rzp_order_id}: {e}")
        return {"ok": False, "error": str(e)}

    await _post_order_side_effects(new_order, db)

    await db["pending_checkouts"].update_one(
        {"razorpay_order_id": rzp_order_id},
        {"$set": {
            "status": "converted",
            "converted_at": datetime.utcnow(),
            "order_id": str(new_order["_id"]),
        }},
    )

    print(f"[WEBHOOK] Order {new_order['_id']} created for rzp order {rzp_order_id}")
    return {"ok": True, "order_id": str(new_order["_id"])}


@router.post("/verify-payment")
async def verify_payment(data: PaymentVerifyRequest, db=Depends(get_database)):
    order_service = OrderService(db)
    # Legacy endpoint — kept for backward compat
