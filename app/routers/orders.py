from fastapi import APIRouter, Depends, status, HTTPException
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from app.schemas.order import OrderCreate, OrderUpdate, OrderOut, OrderTrackOut
from app.services.order_service import OrderService
from app.db.mongodb import get_database
from app.utils.deps import get_current_user, get_current_user_optional, require_admin
from app.services.auth_service import AuthService

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
    return await order_service.update(id, order_in)

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
    
    # 3. Create Order (Stock will be checked and decremented here)
    try:
        return await order_service.create(order_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Order creation failed: {str(e)}")

@router.post("/verify-payment")
async def verify_payment(data: PaymentVerifyRequest, db=Depends(get_database)):
    # Keep legacy verify_payment for existing orders if any
    order_service = OrderService(db)
    # ... rest of legacy logic if needed, but we'll focus on the new one
