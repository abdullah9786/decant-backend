from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime
from .user import PyObjectId
from bson import ObjectId


class InfluencerProfileBase(BaseModel):
    username: str
    display_name: str
    bio: Optional[str] = None
    profile_image_url: Optional[str] = None
    banner_image_url: Optional[str] = None
    is_active: bool = True
    commission_rate: float = 0.10
    payout_upi: Optional[str] = None
    payout_bank_details: Optional[Dict[str, str]] = None


class InfluencerProfileCreate(BaseModel):
    user_id: str
    username: str
    display_name: str
    bio: Optional[str] = None
    profile_image_url: Optional[str] = None
    banner_image_url: Optional[str] = None
    commission_rate: float = 0.10


class InfluencerProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    profile_image_url: Optional[str] = None
    banner_image_url: Optional[str] = None
    payout_upi: Optional[str] = None
    payout_bank_details: Optional[Dict[str, str]] = None


class InfluencerProfileOut(InfluencerProfileBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class SectionCreate(BaseModel):
    title: str
    product_ids: List[str] = []


class SectionUpdate(BaseModel):
    title: Optional[str] = None
    product_ids: Optional[List[str]] = None
    is_active: Optional[bool] = None


class SectionReorder(BaseModel):
    section_ids: List[str]


class SectionOut(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    influencer_id: str
    title: str
    product_ids: List[str] = []
    sort_order: int = 0
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class CommissionOut(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    influencer_id: str
    order_id: str
    order_total: float
    commission_rate: float
    commission_amount: float
    status: str = "pending"
    payout_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    approved_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class EarningsSummary(BaseModel):
    total_earnings: float = 0.0
    pending_earnings: float = 0.0
    approved_earnings: float = 0.0
    paid_earnings: float = 0.0
    total_orders: int = 0


class PayoutCreate(BaseModel):
    influencer_id: str
    method: str = "upi"


class PayoutOut(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    influencer_id: str
    amount: float
    commission_ids: List[str] = []
    method: str
    status: str = "pending"
    scheduled_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
