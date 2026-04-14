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
        fragrance_family: Optional[str] = None,
        brand: Optional[str] = None,
        is_featured: Optional[bool] = None,
        is_new_arrival: Optional[bool] = None,
        search: Optional[str] = None,
        sort_by: Optional[str] = None,
        include_inactive: bool = False,
        category_id: Optional[str] = None,
    ):
        query: dict = {}
        if not include_inactive:
            query["is_active"] = {"$ne": False}
        if fragrance_family:
            query["fragrance_family"] = fragrance_family
        if brand:
            query["brand"] = brand
        if category_id:
            query["category_ids"] = category_id
        if is_featured is not None:
            query["is_featured"] = is_featured
        if is_new_arrival is not None:
            query["is_new_arrival"] = is_new_arrival
        if search:
            query["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"brand": {"$regex": search, "$options": "i"}},
                {"notes_top": {"$regex": search, "$options": "i"}},
                {"notes_middle": {"$regex": search, "$options": "i"}},
                {"notes_base": {"$regex": search, "$options": "i"}},
            ]
        cursor = self.collection.find(query)
        if sort_by == "newest":
            cursor = cursor.sort("created_at", -1)
        else:
            cursor = cursor.sort([("sort_order", 1), ("created_at", -1)])
        products = await cursor.to_list(length=100)
        normalized = []
        for product in products:
            normalized.append(await self._ensure_stock_ml(product))
        return normalized

    async def get_by_id(self, product_id: str):
        product = await self.collection.find_one({"_id": ObjectId(product_id)})
        return await self._ensure_stock_ml(product)

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

    async def _ensure_stock_ml(self, product: Optional[dict]):
        if not product:
            return product
        if product.get("stock_ml") is None:
            variants = product.get("variants", [])
            computed = 0
            for v in variants:
                try:
                    computed += int(v.get("size_ml", 0)) * int(v.get("stock", 0))
                except Exception:
                    continue
            product["stock_ml"] = computed
            await self.collection.update_one(
                {"_id": product["_id"]},
                {"$set": {"stock_ml": computed}},
            )
        return product
