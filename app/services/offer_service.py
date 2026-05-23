from datetime import datetime, timezone
from typing import Optional
import re
import unicodedata

from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from app.schemas.offer import OfferCreate, OfferUpdate


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


class OfferService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["offers"]

    def _active_query(self, extra: Optional[dict] = None) -> dict:
        """Build a query that matches offers that are currently within their active window.

        - is_active must be True.
        - starts_at must be null (start now) or in the past.
        - ends_at must be null (no end / manual stop) or in the future.
        """
        now = datetime.now(timezone.utc)
        query: dict = {
            "is_active": True,
            "$and": [
                {"$or": [{"starts_at": None}, {"starts_at": {"$lte": now}}]},
                {"$or": [{"ends_at": None}, {"ends_at": {"$gt": now}}]},
            ],
        }
        if extra:
            query.update(extra)
        return query

    async def get_all(self):
        cursor = self.collection.find().sort("created_at", -1)
        return await cursor.to_list(length=200)

    async def get_active(self):
        cursor = self.collection.find(self._active_query()).sort("created_at", -1)
        return await cursor.to_list(length=200)

    async def get_active_by_type(self, offer_type: str):
        return await self.collection.find_one(self._active_query({"type": offer_type}))

    async def get_active_daily_deal(self):
        """The single active daily-deal offer (or `None`).

        Conflict prevention at create/update time guarantees there's never
        more than one active at a time, so a single document lookup is safe.
        """
        return await self.get_active_by_type("daily_deal")

    async def is_product_in_active_deal(self, product_id: str) -> bool:
        deal = await self.get_active_daily_deal()
        if not deal:
            return False
        config = deal.get("config") or {}
        return str(product_id) in (config.get("product_ids") or [])

    async def get_overlapping_daily_deals(
        self,
        starts_at: datetime,
        ends_at: datetime,
        exclude_id: Optional[str] = None,
    ) -> list:
        """Find daily_deal offers whose window overlaps the given range.

        Used by the admin conflict check so two deals can't both be active on
        the same day. An open-ended deal (starts_at/ends_at = null) is treated
        as overlapping with anything.
        """
        query: dict = {
            "type": "daily_deal",
            "is_active": True,
            "$and": [
                {"$or": [{"ends_at": None}, {"ends_at": {"$gt": starts_at}}]},
                {"$or": [{"starts_at": None}, {"starts_at": {"$lt": ends_at}}]},
            ],
        }
        if exclude_id:
            query["_id"] = {"$ne": ObjectId(exclude_id)}
        cursor = self.collection.find(query)
        return await cursor.to_list(length=100)

    async def get_by_id(self, offer_id: str):
        return await self.collection.find_one({"_id": ObjectId(offer_id)})

    async def _unique_slug(self, base_slug: str, exclude_id: Optional[str] = None) -> str:
        slug = base_slug
        counter = 1
        while True:
            query: dict = {"slug": slug}
            if exclude_id:
                query["_id"] = {"$ne": ObjectId(exclude_id)}
            existing = await self.collection.find_one(query, {"_id": 1})
            if not existing:
                return slug
            slug = f"{base_slug}-{counter}"
            counter += 1

    async def create(self, offer_in: OfferCreate):
        offer_dict = offer_in.model_dump()
        offer_dict["created_at"] = datetime.now(timezone.utc)
        if not offer_dict.get("slug"):
            offer_dict["slug"] = await self._unique_slug(_slugify(offer_dict["name"]))
        result = await self.collection.insert_one(offer_dict)
        return await self.get_by_id(str(result.inserted_id))

    async def update(self, offer_id: str, offer_in: OfferUpdate):
        update_data = {k: v for k, v in offer_in.model_dump(exclude_unset=True).items()}
        if "name" in update_data and "slug" not in update_data:
            update_data["slug"] = await self._unique_slug(
                _slugify(update_data["name"]), exclude_id=offer_id
            )
        await self.collection.update_one(
            {"_id": ObjectId(offer_id)}, {"$set": update_data}
        )
        return await self.get_by_id(offer_id)

    async def delete(self, offer_id: str):
        return await self.collection.delete_one({"_id": ObjectId(offer_id)})
