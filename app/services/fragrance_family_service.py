from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from app.schemas.fragrance_family import FragranceFamilyCreate, FragranceFamilyUpdate

class FragranceFamilyService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["fragrance_families"]

    async def get_all(self):
        cursor = self.collection.find({}).sort([("sort_order", 1), ("name", 1)])
        return await cursor.to_list(length=100)

    async def get_by_id(self, family_id: str):
        return await self.collection.find_one({"_id": ObjectId(family_id)})

    async def create(self, family_in: FragranceFamilyCreate):
        family_dict = family_in.model_dump()
        family_dict["created_at"] = datetime.utcnow()
        result = await self.collection.insert_one(family_dict)
        return await self.get_by_id(str(result.inserted_id))

    async def update(self, family_id: str, family_in: FragranceFamilyUpdate):
        update_data = {k: v for k, v in family_in.model_dump().items() if v is not None}
        await self.collection.update_one(
            {"_id": ObjectId(family_id)},
            {"$set": update_data}
        )
        return await self.get_by_id(family_id)

    async def delete(self, family_id: str):
        return await self.collection.delete_one({"_id": ObjectId(family_id)})
