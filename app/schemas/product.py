from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from .user import PyObjectId
from bson import ObjectId

class DecantVariant(BaseModel):
    size_ml: int
    price: float
    stock: int

class ProductBase(BaseModel):
    name: str
    brand: str
    description: str
    category: str
    image_url: Optional[str] = None
    images: List[str] = []
    variants: List[DecantVariant]
    is_featured: bool = False
    is_new_arrival: bool = False
    notes_top: List[str] = []
    notes_middle: List[str] = []
    notes_base: List[str] = []
    notes_top_desc: Optional[str] = None
    notes_middle_desc: Optional[str] = None
    notes_base_desc: Optional[str] = None

class ProductCreate(ProductBase):
    pass

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    image_url: Optional[str] = None
    images: Optional[List[str]] = None
    variants: Optional[List[DecantVariant]] = None
    is_featured: Optional[bool] = None
    is_new_arrival: Optional[bool] = None
    notes_top: Optional[List[str]] = None
    notes_middle: Optional[List[str]] = None
    notes_base: Optional[List[str]] = None
    notes_top_desc: Optional[str] = None
    notes_middle_desc: Optional[str] = None
    notes_base_desc: Optional[str] = None

class ProductOut(ProductBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
