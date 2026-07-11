from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from .user import PyObjectId
from bson import ObjectId


class OfferBase(BaseModel):
    name: str
    slug: Optional[str] = None
    type: str = "free_decant"
    is_active: bool = True
    sort_order: int = 0
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    config: Dict[str, Any] = {}
    display: Dict[str, Any] = {}


class OfferCreate(OfferBase):
    pass


class OfferUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    type: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    config: Optional[Dict[str, Any]] = None
    display: Optional[Dict[str, Any]] = None


class OfferOut(OfferBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }
