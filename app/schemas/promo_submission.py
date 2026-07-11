from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from .user import PyObjectId
from bson import ObjectId


class PromoSubmissionSubmit(BaseModel):
    order_id: str
    email: Optional[str] = None
    post_url: str
    poster_instagram_username: str
    posted_by_note: Optional[str] = None


class PromoSubmissionApprove(BaseModel):
    prize_template_id: str
    admin_notes: Optional[str] = None


class PromoSubmissionReject(BaseModel):
    reason: str


class PromoSubmissionOut(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    order_id: str
    user_id: str
    customer_email: str
    customer_name: str
    campaign_id: str
    status: str
    poster_instagram_username: Optional[str] = None
    post_url: Optional[str] = None
    posted_by_note: Optional[str] = None
    submitted_at: Optional[datetime] = None
    deadline_at: Optional[datetime] = None
    prize_template_id: Optional[str] = None
    prize_snapshot: Optional[Dict[str, Any]] = None
    rejection_reason: Optional[str] = None
    admin_notes: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    fulfilled_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order_status: Optional[str] = None
    instagram_promo_opt_in: Optional[bool] = None

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


class PromoSubmissionPublicOut(BaseModel):
    """Customer-facing view for /instagram-promo and order chips."""

    order_id: str
    status: str
    poster_instagram_username: Optional[str] = None
    post_url: Optional[str] = None
    posted_by_note: Optional[str] = None
    submitted_at: Optional[datetime] = None
    deadline_at: Optional[datetime] = None
    prize_label: Optional[str] = None
    rejection_reason: Optional[str] = None
    can_submit: bool = False
    order_delivered: bool = False
    instagram_promo_opt_in: bool = False
    campaign_rules: Optional[Dict[str, Any]] = None
