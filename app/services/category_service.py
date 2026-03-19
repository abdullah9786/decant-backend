from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from app.schemas.category import CategoryCreate, CategoryUpdate

class CategoryService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["categories"]

    async def get_all(self):
        cursor = self.collection.find({}).sort([("sort_order", 1), ("name", 1)])
        return await cursor.to_list(length=100)

    async def get_by_id(self, category_id: str):
        return await self.collection.find_one({"_id": ObjectId(category_id)})

    async def create(self, category_in: CategoryCreate):
        category_dict = category_in.model_dump()
        category_dict["created_at"] = datetime.utcnow()
        # Ensure name is unique/clean if needed
        result = await self.collection.insert_one(category_dict)
        return await self.get_by_id(str(result.inserted_id))

    async def update(self, category_id: str, category_in: CategoryUpdate):
        update_data = {k: v for k, v in category_in.model_dump().items() if v is not None}
        await self.collection.update_one(
            {"_id": ObjectId(category_id)},
            {"$set": update_data}
        )
        return await self.get_by_id(category_id)

    async def delete(self, category_id: str):
        return await self.collection.delete_one({"_id": ObjectId(category_id)})
