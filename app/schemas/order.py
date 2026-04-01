from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from .user import PyObjectId
from bson import ObjectId

class OrderItem(BaseModel):
    product_id: str
    name: str
    size_ml: int
    price: float
    quantity: int
    status: str = "pending"


class InitiatePaymentItem(BaseModel):
    """Minimal line item for stock validation before Razorpay checkout."""

    product_id: str
    size_ml: int
    quantity: int


class InitiatePaymentRequest(BaseModel):
    amount: float
    items: List[InitiatePaymentItem]

class OrderBase(BaseModel):
    user_id: str
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    items: List[OrderItem]
    total_amount: float
    status: str = "pending"
    shipping_address: str
    payment_status: str = "pending"
    payment_details: Optional[Dict[str, Any]] = None
    influencer_id: Optional[str] = None
    referral_code: Optional[str] = None
    coupon_code: Optional[str] = None
    discount_amount: Optional[float] = None

class OrderCreate(OrderBase):
    pass

class OrderUpdate(BaseModel):
    status: Optional[str] = None
    payment_status: Optional[str] = None
    items: Optional[List[OrderItem]] = None
    payment_details: Optional[Dict[str, Any]] = None

class OrderOut(OrderBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

class OrderTrackOut(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    customer_name: Optional[str] = None
    status: str = "pending"
    items: List[OrderItem]
    total_amount: float
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
