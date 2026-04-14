from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from .user import PyObjectId
from bson import ObjectId

class DecantVariant(BaseModel):
    size_ml: int
    price: float
    is_pack: bool = False
    stock: int = 0

class ProductBase(BaseModel):
    name: str
    brand: str
    description: str
    fragrance_family: str
    image_url: Optional[str] = None
    images: List[str] = []
    variants: List[DecantVariant]
    stock_ml: int = 0
    sort_order: int = 0
    is_featured: bool = False
    is_new_arrival: bool = False
    is_active: bool = True
    notes_top: List[str] = []
    notes_middle: List[str] = []
    notes_base: List[str] = []
    notes_top_desc: Optional[str] = None
    notes_middle_desc: Optional[str] = None
    notes_base_desc: Optional[str] = None
    bottle_ids: List[str] = []
    category_ids: List[str] = []

class ProductCreate(ProductBase):
    pass

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    description: Optional[str] = None
    fragrance_family: Optional[str] = None
    image_url: Optional[str] = None
    images: Optional[List[str]] = None
    variants: Optional[List[DecantVariant]] = None
    stock_ml: Optional[int] = None
    sort_order: Optional[int] = None
    is_featured: Optional[bool] = None
    is_new_arrival: Optional[bool] = None
    is_active: Optional[bool] = None
    notes_top: Optional[List[str]] = None
    notes_middle: Optional[List[str]] = None
    notes_base: Optional[List[str]] = None
    notes_top_desc: Optional[str] = None
    notes_middle_desc: Optional[str] = None
    notes_base_desc: Optional[str] = None
    bottle_ids: Optional[List[str]] = None
    category_ids: Optional[List[str]] = None

class ProductOut(ProductBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
