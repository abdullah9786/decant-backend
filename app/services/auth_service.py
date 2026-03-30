from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.user import UserCreate
from app.utils.security import get_password_hash, verify_password
from fastapi import HTTPException, status
from bson import ObjectId
from app.config.config import settings
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from app.services.mail_service import MailService

class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["users"]
        self.refresh_sessions = db["refresh_sessions"]
        self.mail_service = MailService()

    def _hash_refresh_token(self, plain: str) -> str:
        return hashlib.sha256(plain.encode()).hexdigest()

    async def issue_refresh_token(self, user_id: ObjectId) -> str:
        plain = secrets.token_urlsafe(32)
        token_hash = self._hash_refresh_token(plain)
        expires = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        await self.refresh_sessions.insert_one({
            "user_id": user_id,
            "token_hash": token_hash,
            "expires_at": expires,
            "created_at": datetime.now(timezone.utc),
        })
        return plain

    async def rotate_refresh_token(self, plain_refresh: str) -> dict | None:
        if not plain_refresh or not plain_refresh.strip():
            return None
        token_hash = self._hash_refresh_token(plain_refresh.strip())
        sess = await self.refresh_sessions.find_one({"token_hash": token_hash})
        if not sess:
            return None
        exp = sess["expires_at"]
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            await self.refresh_sessions.delete_one({"_id": sess["_id"]})
            return None
        user = await self.collection.find_one({"_id": sess["user_id"]})
        if not user:
            await self.refresh_sessions.delete_one({"_id": sess["_id"]})
            return None
        if not user.get("is_verified", False):
            await self.refresh_sessions.delete_one({"_id": sess["_id"]})
            return None
        if not user.get("is_active", True):
            await self.refresh_sessions.delete_one({"_id": sess["_id"]})
            return None
        await self.refresh_sessions.delete_one({"_id": sess["_id"]})
        new_plain = await self.issue_refresh_token(user["_id"])
        return {"user": user, "refresh_token": new_plain}

    async def revoke_refresh_token(self, plain_refresh: str) -> bool:
        if not plain_refresh or not plain_refresh.strip():
            return False
        token_hash = self._hash_refresh_token(plain_refresh.strip())
        result = await self.refresh_sessions.delete_one({"token_hash": token_hash})
        return result.deleted_count > 0

    async def attach_guest_orders(self, email: str, user_id: str, full_name: str = None) -> int:
        if not email:
            return 0
        result = await self.collection.database["orders"].update_many(
            {"customer_email": email, "user_id": {"$in": [None, "", "guest"]}},
            {"$set": {"user_id": user_id, "customer_name": full_name}},
        )
        return result.modified_count or 0

    async def register(self, user_in: UserCreate):
        existing_user = await self.collection.find_one({"email": user_in.email})
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )
        
        user_dict = user_in.dict()
        user_dict["password"] = get_password_hash(user_dict["password"])
        user_dict["is_verified"] = False
        token = self._generate_verification_token()
        user_dict["verification_token"] = token
        user_dict["verification_expires_at"] = datetime.utcnow() + timedelta(hours=24)
        user_result = await self.collection.insert_one(user_dict)
        await self.mail_service.send_verification_email(user_in.email, user_in.full_name, token)
        return await self.collection.find_one({"_id": user_result.inserted_id})

    async def login(self, email: str, password: str):
        user = await self.collection.find_one({"email": email})
        if not user or not verify_password(password, user["password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not user.get("is_verified", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email not verified. Please verify your email before logging in."
            )
        # Attach any guest orders placed with this email to the user
        await self.attach_guest_orders(email, str(user["_id"]), user.get("full_name"))
        return user

    async def verify_email(self, token: str) -> bool:
        if not token:
            return False
        user = await self.collection.find_one({"verification_token": token})
        if not user:
            return False
        expires = user.get("verification_expires_at")
        if expires and expires < datetime.utcnow():
            return False
        await self.collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"is_verified": True}, "$unset": {"verification_token": "", "verification_expires_at": ""}},
        )
        return True

    async def resend_verification(self, email: str) -> bool:
        user = await self.collection.find_one({"email": email})
        if not user:
            return False
        if user.get("is_verified", False):
            return True
        token = self._generate_verification_token()
        await self.collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"verification_token": token, "verification_expires_at": datetime.utcnow() + timedelta(hours=24)}},
        )
        await self.mail_service.send_verification_email(email, user.get("full_name"), token)
        return True

    async def request_password_reset(self, email: str) -> None:
        user = await self.collection.find_one({"email": email})
        if not user:
            return
        token = self._generate_verification_token()
        await self.collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"reset_token": token, "reset_expires_at": datetime.utcnow() + timedelta(hours=2)}},
        )
        await self.mail_service.send_reset_email(email, user.get("full_name"), token)

    async def reset_password(self, token: str, new_password: str) -> bool:
        if not token or not new_password:
            return False
        user = await self.collection.find_one({"reset_token": token})
        if not user:
            return False
        expires = user.get("reset_expires_at")
        if expires and expires < datetime.utcnow():
            return False
        hashed = get_password_hash(new_password)
        await self.collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"password": hashed}, "$unset": {"reset_token": "", "reset_expires_at": ""}},
        )
        return True

    def _generate_verification_token(self) -> str:
        return secrets.token_urlsafe(32)
