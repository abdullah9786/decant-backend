from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from typing import List

class UserService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["users"]

    async def get_all(self, skip: int = 0, limit: int = 100):
        # Get total count for pagination
        total = await self.collection.count_documents({})
        
        cursor = self.collection.find({}).skip(skip).limit(limit)
        users = await cursor.to_list(length=limit)
        
        # Convert ObjectIds to strings for JSON serialization
        serializable_users = []
        for user in users:
            user_dict = dict(user)
            if "_id" in user_dict:
                user_dict["_id"] = str(user_dict["_id"])
            serializable_users.append(user_dict)
        
        return {
            "items": serializable_users,
            "total": total,
            "skip": skip,
            "limit": limit,
            "has_more": skip + limit < total
        }

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
