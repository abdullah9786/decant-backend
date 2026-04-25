from datetime import datetime, timezone
from typing import Optional, List

from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from app.schemas.chip import ChipCreate, ChipUpdate


class ChipService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["chips"]

    def _active_query(self, extra: Optional[dict] = None) -> dict:
        """Match chips that are currently within their active window.

        Mirrors OfferService._active_query — the rules are identical:
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
        cursor = self.collection.find().sort([("priority", 1), ("created_at", -1)])
        return await cursor.to_list(length=500)

    async def get_active(self):
        cursor = self.collection.find(self._active_query()).sort([("priority", 1), ("created_at", -1)])
        return await cursor.to_list(length=500)

    async def get_active_by_ids(self, chip_ids: List[str]) -> List[dict]:
        if not chip_ids:
            return []
        object_ids = []
        for cid in chip_ids:
            try:
                object_ids.append(ObjectId(cid))
            except Exception:
                continue
        if not object_ids:
            return []
        cursor = self.collection.find(self._active_query({"_id": {"$in": object_ids}})).sort(
            [("priority", 1), ("created_at", -1)]
        )
        return await cursor.to_list(length=500)

    async def get_by_id(self, chip_id: str):
        return await self.collection.find_one({"_id": ObjectId(chip_id)})

    async def _ensure_unique_code(self, code: str, exclude_id: Optional[str] = None):
        query: dict = {"code": code}
        if exclude_id:
            query["_id"] = {"$ne": ObjectId(exclude_id)}
        existing = await self.collection.find_one(query, {"_id": 1})
        if existing:
            raise ValueError(f"Chip with code '{code}' already exists.")

    async def create(self, chip_in: ChipCreate):
        chip_dict = chip_in.model_dump()
        await self._ensure_unique_code(chip_dict["code"])
        chip_dict["created_at"] = datetime.now(timezone.utc)
        result = await self.collection.insert_one(chip_dict)
        return await self.get_by_id(str(result.inserted_id))

    async def update(self, chip_id: str, chip_in: ChipUpdate):
        update_data = {k: v for k, v in chip_in.model_dump(exclude_unset=True).items()}
        if "code" in update_data:
            await self._ensure_unique_code(update_data["code"], exclude_id=chip_id)
        await self.collection.update_one(
            {"_id": ObjectId(chip_id)}, {"$set": update_data}
        )
        return await self.get_by_id(chip_id)

    async def delete(self, chip_id: str):
        # Also remove the chip reference from any product that has it.
        await self.collection.database["products"].update_many(
            {"chip_ids": chip_id},
            {"$pull": {"chip_ids": chip_id}},
        )
        return await self.collection.delete_one({"_id": ObjectId(chip_id)})
