from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone
from .user import PyObjectId
from bson import ObjectId


class CartItemRef(BaseModel):
    """Lightweight cart-line shape passed in for daily-deal validation."""

    product_id: str
    size_ml: Optional[int] = None
    quantity: Optional[int] = 1
    is_pack: Optional[bool] = False


class CouponBase(BaseModel):
    code: str
    influencer_id: str
    discount_percent: float = 5.0
    is_active: bool = True
    max_uses: Optional[int] = None


class CouponCreate(CouponBase):
    pass


class CouponUpdate(BaseModel):
    discount_percent: Optional[float] = None
    is_active: Optional[bool] = None
    max_uses: Optional[int] = None


class CouponOut(CouponBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    times_used: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class CouponApplyRequest(BaseModel):
    code: str
    cart_items: Optional[List[CartItemRef]] = None


class CouponApplyResponse(BaseModel):
    valid: bool
    discount_percent: float = 0.0
    influencer_id: Optional[str] = None
    message: str = ""
