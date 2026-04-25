from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from .user import PyObjectId
from bson import ObjectId


CHIP_COLORS = {"amber", "red", "green", "blue", "indigo", "purple", "pink", "slate", "emerald", "orange"}


class ChipBase(BaseModel):
    code: str
    label: str
    color: str = "indigo"
    icon: Optional[str] = None
    priority: int = 0
    is_active: bool = True
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class ChipCreate(ChipBase):
    pass


class ChipUpdate(BaseModel):
    code: Optional[str] = None
    label: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class ChipOut(ChipBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }
