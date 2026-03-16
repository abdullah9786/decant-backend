from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from app.schemas.brand import BrandCreate, BrandUpdate


class BrandService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["brands"]

    async def get_all(self):
        cursor = self.collection.find({}).sort("name", 1)
        return await cursor.to_list(length=200)

    async def get_by_id(self, brand_id: str):
        return await self.collection.find_one({"_id": ObjectId(brand_id)})

    async def create(self, brand_in: BrandCreate):
        brand_dict = brand_in.model_dump()
        brand_dict["created_at"] = datetime.utcnow()
        result = await self.collection.insert_one(brand_dict)
        return await self.get_by_id(str(result.inserted_id))

    async def update(self, brand_id: str, brand_in: BrandUpdate):
        update_data = {k: v for k, v in brand_in.model_dump().items() if v is not None}
        await self.collection.update_one(
            {"_id": ObjectId(brand_id)},
            {"$set": update_data}
        )
        return await self.get_by_id(brand_id)

    async def delete(self, brand_id: str):
        return await self.collection.delete_one({"_id": ObjectId(brand_id)})
