from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from app.schemas.category import CategoryCreate, CategoryUpdate

class CategoryService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["categories"]

    async def get_all(self, include_inactive: bool = False):
        query = {} if include_inactive else {"is_active": {"$ne": False}}
        cursor = self.collection.find(query).sort([("sort_order", 1), ("name", 1)])
        return await cursor.to_list(length=200)

    async def get_by_id(self, category_id: str):
        return await self.collection.find_one({"_id": ObjectId(category_id)})

    async def get_by_slug(self, slug: str):
        return await self.collection.find_one({"slug": slug})

    async def create(self, cat_in: CategoryCreate):
        cat_dict = cat_in.model_dump()
        cat_dict["created_at"] = datetime.utcnow()
        result = await self.collection.insert_one(cat_dict)
        return await self.get_by_id(str(result.inserted_id))

    async def update(self, category_id: str, cat_in: CategoryUpdate):
        update_data = {k: v for k, v in cat_in.model_dump().items() if v is not None}
        await self.collection.update_one(
            {"_id": ObjectId(category_id)},
            {"$set": update_data}
        )
        return await self.get_by_id(category_id)

    async def delete(self, category_id: str):
        return await self.collection.delete_one({"_id": ObjectId(category_id)})
