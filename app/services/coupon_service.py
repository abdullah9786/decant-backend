from typing import Optional, Sequence
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from datetime import datetime, timezone


class CouponService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.coupons = db["coupons"]
        self.profiles = db["influencer_profiles"]

    async def create_coupon(self, data: dict) -> dict:
        code = data["code"].upper().strip()
        existing = await self.coupons.find_one({"code": code})
        if existing:
            raise ValueError(f"Coupon code '{code}' already exists")

        profile = await self.profiles.find_one({"_id": ObjectId(data["influencer_id"])})
        if not profile:
            raise ValueError("Influencer not found")

        doc = {
            "code": code,
            "influencer_id": data["influencer_id"],
            "discount_percent": data.get("discount_percent", 5.0),
            "is_active": True,
            "max_uses": data.get("max_uses"),
            "times_used": 0,
            "created_at": datetime.now(timezone.utc),
        }
        result = await self.coupons.insert_one(doc)
        return await self.coupons.find_one({"_id": result.inserted_id})

    async def validate_coupon(
        self,
        code: str,
        cart_items: Optional[Sequence[dict]] = None,
    ) -> dict:
        """Validate a coupon and return discount info + influencer attribution.

        When `cart_items` includes a product that's part of the active daily
        deal, the coupon is rejected. Stacking the deal discount with a
        coupon would double-dip on margin and create unclear commission
        attribution.
        """
        coupon = await self.coupons.find_one({"code": code.upper().strip()})

        if not coupon:
            return {"valid": False, "discount_percent": 0, "influencer_id": None, "message": "Invalid coupon code"}

        if not coupon.get("is_active", True):
            return {"valid": False, "discount_percent": 0, "influencer_id": None, "message": "Coupon is no longer active"}

        if coupon.get("max_uses") and coupon["times_used"] >= coupon["max_uses"]:
            return {"valid": False, "discount_percent": 0, "influencer_id": None, "message": "Coupon has reached its usage limit"}

        profile = await self.profiles.find_one({"_id": ObjectId(coupon["influencer_id"])})
        if not profile or not profile.get("is_active", True):
            return {"valid": False, "discount_percent": 0, "influencer_id": None, "message": "Influencer is inactive"}

        if cart_items:
            # Local import to avoid a circular dependency: pricing_service and
            # offer_service don't reach back into the coupon layer.
            from app.services.offer_service import OfferService

            deal = await OfferService(self.db).get_active_daily_deal()
            if deal:
                deal_pids = {
                    str(pid) for pid in ((deal.get("config") or {}).get("product_ids") or [])
                }
                if any(str(i.get("product_id") or "") in deal_pids for i in cart_items):
                    return {
                        "valid": False,
                        "discount_percent": 0,
                        "influencer_id": None,
                        "message": "Today's deal is already applied. Coupons can't be combined.",
                    }

        return {
            "valid": True,
            "discount_percent": coupon["discount_percent"],
            "influencer_id": coupon["influencer_id"],
            "message": f"{coupon['discount_percent']}% discount applied",
        }

    async def use_coupon(self, code: str) -> None:
        """Increment usage counter after a successful order."""
        await self.coupons.update_one(
            {"code": code.upper().strip()},
            {"$inc": {"times_used": 1}},
        )

    async def release_coupon(self, code: str) -> None:
        """Decrement usage counter when an order using this coupon is cancelled."""
        await self.coupons.update_one(
            {"code": code.upper().strip(), "times_used": {"$gte": 1}},
            {"$inc": {"times_used": -1}},
        )

    async def get_all_coupons(self) -> list:
        cursor = self.coupons.find({}).sort("created_at", -1)
        return await cursor.to_list(length=200)

    async def get_coupons_by_influencer(self, influencer_id: str) -> list:
        cursor = self.coupons.find({"influencer_id": influencer_id}).sort("created_at", -1)
        return await cursor.to_list(length=50)

    async def update_coupon(self, coupon_id: str, data: dict) -> dict | None:
        update_data = {k: v for k, v in data.items() if v is not None}
        if not update_data:
            return await self.coupons.find_one({"_id": ObjectId(coupon_id)})
        await self.coupons.update_one(
            {"_id": ObjectId(coupon_id)},
            {"$set": update_data},
        )
        return await self.coupons.find_one({"_id": ObjectId(coupon_id)})

    async def delete_coupon(self, coupon_id: str) -> bool:
        result = await self.coupons.delete_one({"_id": ObjectId(coupon_id)})
        return result.deleted_count > 0
