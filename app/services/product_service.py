from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.product import ProductCreate, ProductUpdate
from bson import ObjectId
from typing import List, Optional
from datetime import datetime

class ProductService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["products"]

    async def get_all(
        self,
        category: Optional[str] = None,
        brand: Optional[str] = None,
        is_featured: Optional[bool] = None,
        is_new_arrival: Optional[bool] = None,
        search: Optional[str] = None,
        sort_by: Optional[str] = None,
    ):
        query = {}
        if category:
            query["category"] = category
        if brand:
            query["brand"] = brand
        if is_featured is not None:
            query["is_featured"] = is_featured
        if is_new_arrival is not None:
            query["is_new_arrival"] = is_new_arrival
        if search:
            query["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"brand": {"$regex": search, "$options": "i"}},
            ]
        cursor = self.collection.find(query)
        if sort_by == "newest":
            cursor = cursor.sort("created_at", -1)
        return await cursor.to_list(length=100)

    async def get_by_id(self, product_id: str):
        return await self.collection.find_one({"_id": ObjectId(product_id)})

    async def create(self, product_in: ProductCreate):
        product_dict = product_in.dict()
        product_dict["created_at"] = product_dict.get("created_at") or datetime.utcnow()
        product_result = await self.collection.insert_one(product_dict)
        return await self.get_by_id(str(product_result.inserted_id))

    async def update(self, product_id: str, product_in: ProductUpdate):
        update_data = {k: v for k, v in product_in.dict(exclude_unset=True).items()}
        await self.collection.update_one(
            {"_id": ObjectId(product_id)}, {"$set": update_data}
        )
        return await self.get_by_id(product_id)

    async def delete(self, product_id: str):
        return await self.collection.delete_one({"_id": ObjectId(product_id)})
