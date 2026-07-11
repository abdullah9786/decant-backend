from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Optional, Dict, Any
from datetime import datetime, timezone
from .user import PyObjectId
from bson import ObjectId

class GiftBoxSelectedProduct(BaseModel):
    product_id: str
    name: str = ""
    size_ml: int = 0
    price: float = 0


class SetItemSnapshot(BaseModel):
    product_id: str
    name: str = ""
    brand: str = ""
    size_ml: int = 0


class OrderItem(BaseModel):
    product_id: str
    name: str
    size_ml: int
    price: float
    quantity: int
    status: str = "pending"
    is_pack: bool = False
    product_type: str = "single"
    set_items: Optional[List[SetItemSnapshot]] = None
    gift_box_id: Optional[str] = None
    selected_products: Optional[List[GiftBoxSelectedProduct]] = None
    bottle_id: Optional[str] = None
    bottle_name: Optional[str] = None
    bottle_price: float = 0


class FreeDecantItem(BaseModel):
    product_id: str
    name: str = ""
    size_ml: int = 2
    offer_id: str = ""


class InitiatePaymentItem(BaseModel):
    """Minimal line item for stock validation before Razorpay checkout."""

    product_id: str
    size_ml: int
    quantity: int
    is_pack: bool = False
    product_type: str = "single"
    set_items: Optional[List[SetItemSnapshot]] = None
    gift_box_id: Optional[str] = None
    selected_products: Optional[List[GiftBoxSelectedProduct]] = None
    bottle_id: Optional[str] = None


class InitiatePaymentRequest(BaseModel):
    amount: float
    items: List[InitiatePaymentItem]
    order_data: Optional[Dict[str, Any]] = None

class OrderBase(BaseModel):
    user_id: str
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
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
    free_decants: Optional[List[FreeDecantItem]] = None
    free_decants_dropped_reason: Optional[str] = None
    mystery_gift: Optional[Dict[str, Any]] = None
    payment_method: Literal["prepaid", "cod"] = "prepaid"
    cod_fee: Optional[float] = None
    idempotency_key: Optional[str] = None
    instagram_packing_opt_in: bool = False
    instagram_username: Optional[str] = None
    instagram_promo_opt_in: bool = False
    instagram_promo_campaign_id: Optional[str] = None
    tracking_id: Optional[str] = None
    tracking_url: Optional[str] = None
    courier_name: Optional[str] = None
    shipped_at: Optional[datetime] = None

class OrderCreate(OrderBase):
    pass

class OrderUpdate(BaseModel):
    status: Optional[str] = None
    payment_status: Optional[str] = None
    items: Optional[List[OrderItem]] = None
    payment_details: Optional[Dict[str, Any]] = None
    customer_phone: Optional[str] = None
    tracking_id: Optional[str] = None
    tracking_url: Optional[str] = None
    courier_name: Optional[str] = None

    @field_validator("tracking_id", "tracking_url", "courier_name", mode="before")
    @classmethod
    def strip_optional_text(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            return stripped or None
        return v

    @field_validator("tracking_url")
    @classmethod
    def validate_tracking_url(cls, v: Optional[str]):
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("tracking_url must start with http:// or https://")
        return v

class PromoSubmissionSummary(BaseModel):
    status: str
    poster_instagram_username: Optional[str] = None
    post_url: Optional[str] = None
    prize_label: Optional[str] = None
    deadline_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


class OrderOut(OrderBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    promo_submission: Optional[PromoSubmissionSummary] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

class OrderTrackOut(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    user_id: Optional[str] = None
    status: str = "pending"
    payment_status: Optional[str] = None
    payment_method: Optional[str] = "prepaid"
    cod_fee: Optional[float] = None
    items: List[OrderItem]
    total_amount: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    free_decants: Optional[List[FreeDecantItem]] = None
    free_decants_dropped_reason: Optional[str] = None
    mystery_gift: Optional[Dict[str, Any]] = None
    cancelled_at: Optional[datetime] = None
    cancelled_by: Optional[str] = None
    cancellation_reason: Optional[str] = None
    instagram_promo_opt_in: bool = False
    promo_submission: Optional[PromoSubmissionSummary] = None
    tracking_id: Optional[str] = None
    tracking_url: Optional[str] = None
    courier_name: Optional[str] = None
    shipped_at: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
