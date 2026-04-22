from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from .user import PyObjectId
from bson import ObjectId

class ReviewBase(BaseModel):
    product_id: str
    user_id: str
    user_name: str
    rating: int = Field(..., ge=1, le=5)
    comment: str

class ReviewCreate(ReviewBase):
    pass

class ReviewOut(ReviewBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
