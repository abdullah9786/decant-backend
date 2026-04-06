from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.gift_box import GiftBoxCreate, GiftBoxUpdate
from bson import ObjectId
from typing import Optional
from datetime import datetime


class GiftBoxService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["gift_boxes"]

    async def get_all(self, include_inactive: bool = False):
        query: dict = {}
        if not include_inactive:
            query["is_active"] = {"$ne": False}
        cursor = self.collection.find(query).sort([("sort_order", 1), ("created_at", -1)])
        return await cursor.to_list(length=100)

    async def get_by_id(self, box_id: str):
        return await self.collection.find_one({"_id": ObjectId(box_id)})

    async def get_by_slug(self, slug: str):
        return await self.collection.find_one({"slug": slug})

    async def create(self, box_in: GiftBoxCreate):
        box_dict = box_in.dict()
        box_dict["created_at"] = datetime.utcnow()
        result = await self.collection.insert_one(box_dict)
        return await self.collection.find_one({"_id": result.inserted_id})

    async def update(self, box_id: str, box_in: GiftBoxUpdate):
        update_data = {k: v for k, v in box_in.dict(exclude_unset=True).items()}
        await self.collection.update_one(
            {"_id": ObjectId(box_id)}, {"$set": update_data}
        )
        return await self.get_by_id(box_id)

    async def delete(self, box_id: str):
        return await self.collection.delete_one({"_id": ObjectId(box_id)})
