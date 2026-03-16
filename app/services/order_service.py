from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.order import OrderCreate, OrderUpdate
from bson import ObjectId
from typing import List
from datetime import datetime

class OrderService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["orders"]
        self.products = db["products"]

    async def create(self, order_in: OrderCreate):
        order_dict = order_in.dict()
        order_dict["created_at"] = order_dict.get("created_at") or datetime.utcnow()
        result = await self.collection.insert_one(order_dict)
        await self._decrement_stock(order_dict.get("items", []))
        return await self.collection.find_one({"_id": result.inserted_id})

    async def get_all(self, user_id: str = None):
        query = {}
        if user_id:
            query["user_id"] = user_id
        cursor = self.collection.find(query)
        return await cursor.to_list(length=100)

    async def get_by_id(self, order_id: str):
        return await self.collection.find_one({"_id": ObjectId(order_id)})

    async def update(self, order_id: str, order_in: OrderUpdate):
        update_data = {k: v for k, v in order_in.dict(exclude_unset=True).items()}
        await self.collection.update_one(
            {"_id": ObjectId(order_id)}, {"$set": update_data}
        )
        return await self.get_by_id(order_id)

    async def _decrement_stock(self, items: List[dict]):
        for item in items:
            product_id = item.get("product_id")
            size_ml = item.get("size_ml")
            quantity = int(item.get("quantity", 0))
            if not product_id or size_ml is None or quantity <= 0:
                continue
            try:
                await self.products.update_one(
                    {"_id": ObjectId(product_id), "variants.size_ml": size_ml},
                    {"$inc": {"variants.$.stock": -quantity}},
                )
            except Exception:
                continue
