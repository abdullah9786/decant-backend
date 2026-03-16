from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.review import ReviewCreate
from bson import ObjectId
from typing import List

class ReviewService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["reviews"]

    async def create(self, review_in: ReviewCreate):
        review_dict = review_in.dict()
        result = await self.collection.insert_one(review_dict)
        return await self.collection.find_one({"_id": result.inserted_id})

    async def get_by_product(self, product_id: str):
        cursor = self.collection.find({"product_id": product_id})
        return await cursor.to_list(length=100)

class UserService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["users"]

    async def get_all(self):
        cursor = self.collection.find({})
        return await cursor.to_list(length=100)

    async def get_by_id(self, user_id: str):
        return await self.collection.find_one({"_id": ObjectId(user_id)})

    async def delete(self, user_id: str):
        return await self.collection.delete_one({"_id": ObjectId(user_id)})

    async def toggle_admin(self, user_id: str):
        user = await self.get_by_id(user_id)
        if not user:
            return None
        new_status = not user.get("is_admin", False)
        await self.collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"is_admin": new_status}}
        )
        return await self.get_by_id(user_id)

    async def create_admin(self, user_in: dict):
        from app.utils.security import get_password_hash
        user_dict = user_in.copy()
        user_dict["password"] = get_password_hash(user_dict["password"])
        user_dict["is_admin"] = True
        user_dict["is_active"] = True
        result = await self.collection.insert_one(user_dict)
        return await self.collection.find_one({"_id": result.inserted_id})
