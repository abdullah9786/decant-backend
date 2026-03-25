from fastapi import APIRouter, Depends, status, HTTPException
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from app.schemas.order import OrderCreate, OrderUpdate, OrderOut, OrderTrackOut
from app.services.order_service import OrderService
from app.db.mongodb import get_database
from app.utils.deps import get_current_user, get_current_user_optional, require_admin
from app.services.auth_service import AuthService
from app.services.mail_service import MailService
from app.services.commission_service import CommissionService
from app.services.coupon_service import CouponService

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
async def initiate_payment_only(amount: float, db=Depends(get_database)):
    order_service = OrderService(db)
    # Using a timestamped guest receipt since we don't have an order ID yet
    receipt = f"pre_{int(datetime.utcnow().timestamp())}"
    try:
        return await order_service.create_razorpay_order(amount, receipt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
        
        # 4. Create commission if order was referred by an influencer
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

        # 5. Trigger Notifications
        mail_service = MailService()
        try:
            await mail_service.send_order_confirmation(
                new_order.get("customer_email"),
                new_order.get("customer_name"),
                new_order
            )
            await mail_service.send_admin_new_order_alert(new_order)
        except Exception as mail_err:
            print(f"[MAIL] Async notification error (non-blocking): {mail_err}")
            
        return new_order
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Order creation failed: {str(e)}")

@router.post("/verify-payment")
async def verify_payment(data: PaymentVerifyRequest, db=Depends(get_database)):
    # Keep legacy verify_payment for existing orders if any
    order_service = OrderService(db)
    # ... rest of legacy logic if needed, but we'll focus on the new one
