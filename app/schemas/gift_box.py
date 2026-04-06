from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from .user import PyObjectId
from bson import ObjectId


class GiftBoxBase(BaseModel):
    name: str
    slug: str
    description: str = ""
    image_url: Optional[str] = None
    images: List[str] = []
    size_ml: int
    slot_count: int
    box_price: float
    tier: str = "standard"
    is_active: bool = True
    stock: int = 0
    sort_order: int = 0


class GiftBoxCreate(GiftBoxBase):
    pass


class GiftBoxUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    images: Optional[List[str]] = None
    size_ml: Optional[int] = None
    slot_count: Optional[int] = None
    box_price: Optional[float] = None
    tier: Optional[str] = None
    is_active: Optional[bool] = None
    stock: Optional[int] = None
    sort_order: Optional[int] = None


class GiftBoxOut(GiftBoxBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
