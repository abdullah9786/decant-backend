from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any
from datetime import datetime, timezone
from .user import PyObjectId
from bson import ObjectId


class SetItem(BaseModel):
    product_id: str
    # Display-only fields populated on read; not stored on write.
    name: Optional[str] = None
    brand: Optional[str] = None
    image_url: Optional[str] = None
    slug: Optional[str] = None
    stock_ml: Optional[int] = None


class DecantVariant(BaseModel):
    size_ml: int
    price: float
    is_pack: bool = False
    stock: int = 0
    label: Optional[str] = None
    # Daily-deal annotation. Populated on read by `pricing_service.apply_daily_deal`.
    # `sale_price` equals `original_price` when no deal applies to this variant.
    original_price: Optional[float] = None
    sale_price: Optional[float] = None
    discount_percent: Optional[int] = 0
    deal_id: Optional[str] = None

class ProductBase(BaseModel):
    name: str
    brand: str
    slug: Optional[str] = None
    description: str
    product_type: Literal["single", "set"] = "single"
    set_items: List[SetItem] = []
    theme_label: Optional[str] = None
    fragrance_family: str = ""
    image_url: Optional[str] = None
    images: List[str] = []
    variants: List[DecantVariant]
    stock_ml: int = 0
    sort_order: int = 0
    is_featured: bool = False
    featured_decant: bool = False
    featured_sealed_bottle: bool = False
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
    chip_ids: List[str] = []

class ProductCreate(ProductBase):
    pass

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    product_type: Optional[Literal["single", "set"]] = None
    set_items: Optional[List[SetItem]] = None
    theme_label: Optional[str] = None
    fragrance_family: Optional[str] = None
    image_url: Optional[str] = None
    images: Optional[List[str]] = None
    variants: Optional[List[DecantVariant]] = None
    stock_ml: Optional[int] = None
    sort_order: Optional[int] = None
    is_featured: Optional[bool] = None
    featured_decant: Optional[bool] = None
    featured_sealed_bottle: Optional[bool] = None
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
    chip_ids: Optional[List[str]] = None

class ProductOut(ProductBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    chips: List[Dict[str, Any]] = []

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class BulkChipUpdate(BaseModel):
    product_ids: List[str]
    add: List[str] = []
    remove: List[str] = []


class ProductSearchResponse(BaseModel):
    """Paginated product search payload returned by `GET /products/search`.

    `items` holds the current page; `total` is the overall match count so
    the UI can decide whether to render a "Load more" button via `has_more`.
    """
    items: List[ProductOut]
    total: int
    has_more: bool


class ProductListResponse(BaseModel):
    """Paginated product list returned by `GET /products?paginated=true`.

    Used by the admin products table; the default list endpoint still returns
    a plain array for backward compatibility with existing callers.
    """
    items: List[ProductOut]
    total: int
    skip: int
    limit: int
    has_more: bool
