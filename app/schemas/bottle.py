from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from datetime import datetime
from .user import PyObjectId
from bson import ObjectId


class BottleBase(BaseModel):
    name: str
    slug: str
    description: str = ""
    image_url: Optional[str] = None
    compatible_sizes: List[int] = []
    size_prices: Dict[str, float] = {}
    is_default: bool = False
    is_active: bool = True
    sort_order: int = 0


class BottleCreate(BottleBase):
    pass


class BottleUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    compatible_sizes: Optional[List[int]] = None
    size_prices: Optional[Dict[str, float]] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class BottleOut(BottleBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
