from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from datetime import datetime, timezone
from .user import PyObjectId
from bson import ObjectId

ReviewSource = Literal["customer", "admin"]


class ReviewCreate(BaseModel):
    product_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field(..., min_length=10, max_length=2000)


class AdminReviewBulkItem(BaseModel):
    product_id: Optional[str] = None
    product_slug: Optional[str] = None
    user_name: str = Field(..., min_length=1, max_length=80)
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field(..., min_length=1, max_length=2000)
    created_at: Optional[datetime] = None


class AdminReviewBulkCreate(BaseModel):
    reviews: List[AdminReviewBulkItem] = Field(..., min_length=1, max_length=200)


class ReviewUpdate(BaseModel):
    is_published: Optional[bool] = None
    rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = Field(None, min_length=1, max_length=2000)
    user_name: Optional[str] = Field(None, min_length=1, max_length=80)


class ReviewBulkIds(BaseModel):
    review_ids: List[str] = Field(..., min_length=1, max_length=200)


class ReviewBulkPublish(BaseModel):
    review_ids: List[str] = Field(..., min_length=1, max_length=200)
    is_published: bool


class ReviewOut(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    product_id: str
    user_name: str
    rating: int
    comment: str
    source: ReviewSource = "customer"
    is_verified_purchase: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class ReviewAdminOut(ReviewOut):
    user_id: Optional[str] = None
    order_id: Optional[str] = None
    is_published: bool = True


class ReviewSummary(BaseModel):
    average_rating: float = 0
    review_count: int = 0
    rating_breakdown: dict[int, int] = Field(
        default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
    )


class ReviewEligibility(BaseModel):
    can_review: bool
    has_reviewed: bool
    reason: Optional[str] = None
