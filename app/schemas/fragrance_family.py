from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from .user import PyObjectId
from bson import ObjectId

class FragranceFamilyBase(BaseModel):
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    image_url: Optional[str] = None
    is_featured: bool = False
    sort_order: int = 0

class FragranceFamilyCreate(FragranceFamilyBase):
    pass

class FragranceFamilyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    image_url: Optional[str] = None
    is_featured: Optional[bool] = None
    sort_order: Optional[int] = None

class FragranceFamilyOut(FragranceFamilyBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str}
    }
