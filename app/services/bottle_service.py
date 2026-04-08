from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.bottle import BottleCreate, BottleUpdate
from bson import ObjectId
from datetime import datetime


class BottleService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["bottles"]

    async def get_all(self, include_inactive: bool = False):
        query: dict = {}
        if not include_inactive:
            query["is_active"] = {"$ne": False}
        cursor = self.collection.find(query).sort(
            [("sort_order", 1), ("created_at", -1)]
        )
        return await cursor.to_list(length=200)

    async def get_by_id(self, bottle_id: str):
        return await self.collection.find_one({"_id": ObjectId(bottle_id)})

    async def get_by_slug(self, slug: str):
        return await self.collection.find_one({"slug": slug})

    async def create(self, data: BottleCreate):
        doc = data.dict()
        if doc.get("is_default"):
            await self.collection.update_many({}, {"$set": {"is_default": False}})
        doc["created_at"] = datetime.utcnow()
        result = await self.collection.insert_one(doc)
        return await self.collection.find_one({"_id": result.inserted_id})

    async def update(self, bottle_id: str, data: BottleUpdate):
        update_data = {k: v for k, v in data.dict(exclude_unset=True).items()}
        if update_data.get("is_default"):
            await self.collection.update_many(
                {"_id": {"$ne": ObjectId(bottle_id)}},
                {"$set": {"is_default": False}},
            )
        await self.collection.update_one(
            {"_id": ObjectId(bottle_id)}, {"$set": update_data}
        )
        return await self.get_by_id(bottle_id)

    async def delete(self, bottle_id: str):
        return await self.collection.delete_one({"_id": ObjectId(bottle_id)})
