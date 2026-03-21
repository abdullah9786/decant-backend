from fastapi import APIRouter, Depends, status, HTTPException
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

@router.post("/{order_id}/initiate-payment", response_model=RazorpayOrderResponse)
async def initiate_payment(order_id: str, db=Depends(get_database)):
    order_service = OrderService(db)
    order = await order_service.get_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Check if already paid
    if order.get("payment_status") == "paid":
         raise HTTPException(status_code=400, detail="Order already paid")

    try:
        rzp_order = await order_service.create_razorpay_order(order.get("total_amount"), order_id)
        # Store rzp_order_id in our order
        await order_service.update(order_id, OrderUpdate(payment_details={"razorpay_order_id": rzp_order['id']}))
        return rzp_order
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/verify-payment")
async def verify_payment(data: PaymentVerifyRequest, db=Depends(get_database)):
    order_service = OrderService(db)
    
    # Verify signature
    params_dict = {
        'razorpay_order_id': data.razorpay_order_id,
        'razorpay_payment_id': data.razorpay_payment_id,
        'razorpay_signature': data.razorpay_signature
    }
    
    try:
        order_service.client.utility.verify_payment_signature(params_dict)
        
        # If verification passes, update order status
        # Find order by razorpay_order_id
        order = await db["orders"].find_one({"payment_details.razorpay_order_id": data.razorpay_order_id})
        if order:
            await order_service.update(str(order["_id"]), OrderUpdate(
                payment_status="paid",
                status="processing",
                payment_details={
                    "razorpay_order_id": data.razorpay_order_id,
                    "razorpay_payment_id": data.razorpay_payment_id,
                    "razorpay_signature": data.razorpay_signature,
                    "paid_at": datetime.utcnow().isoformat()
                }
            ))
            return {"status": "success", "message": "Payment verified successfully"}
        else:
            raise HTTPException(status_code=404, detail="Order not found for this payment")
            
    except Exception as e:
        print(f"Signature Verification Failed: {str(e)}")
        # Log failure
        order = await db["orders"].find_one({"payment_details.razorpay_order_id": data.razorpay_order_id})
        if order:
            await order_service.update(str(order["_id"]), OrderUpdate(payment_status="failed"))
            
        raise HTTPException(status_code=400, detail="Payment verification failed")
