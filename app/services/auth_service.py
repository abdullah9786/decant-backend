from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.user import UserCreate
from app.utils.security import get_password_hash, verify_password, create_access_token
from fastapi import HTTPException, status
from bson import ObjectId
from app.config.config import settings
from datetime import datetime, timedelta
import secrets
import httpx

class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["users"]

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
        await self._send_verification_email(user_in.email, user_in.full_name, token)
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
        await self._send_verification_email(email, user.get("full_name"), token)
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
        await self._send_reset_email(email, user.get("full_name"), token)

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

    async def _send_verification_email(self, email: str, full_name: str, token: str) -> None:
        if not settings.ZEPTO_API_KEY or not settings.ZEPTO_FROM_EMAIL:
            return
        verify_link = f"{settings.APP_BASE_URL}/verify-email?token={token}"
        subject = "Verify your email for SCENTS"
        name = full_name or "there"
        html_body = (
            f"<p>Hi {name},</p>"
            f"<p>Thanks for signing up with SCENTS. Please verify your email to activate your account.</p>"
            f"<p><a href=\"{verify_link}\">Verify Email</a></p>"
            f"<p>If you did not create this account, you can ignore this email.</p>"
        )
        payload = {
            "from": {"address": settings.ZEPTO_FROM_EMAIL, "name": settings.ZEPTO_FROM_NAME},
            "to": [{"email_address": {"address": email, "name": full_name or email}}],
            "subject": subject,
            "htmlbody": html_body,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Zoho-enczapikey {settings.ZEPTO_API_KEY}",
        }
        print(f"[ZEPTO] Using API URL: {settings.ZEPTO_API_URL}")
        print(f"[ZEPTO] API key prefix: {settings.ZEPTO_API_KEY[:6]}... len={len(settings.ZEPTO_API_KEY)}")
        if not settings.ZEPTO_API_URL.endswith("/email"):
            print(f"[ZEPTO] Warning: API URL looks wrong: {settings.ZEPTO_API_URL}")
        if len(settings.ZEPTO_API_KEY) < 20:
            print("[ZEPTO] Warning: API key seems too short")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(settings.ZEPTO_API_URL, json=payload, headers=headers)
                if resp.status_code >= 300:
                    req_id = resp.headers.get("x-request-id") or resp.headers.get("x-zoho-request-id") or ""
                    print(f"[ZEPTO] Email send failed: {resp.status_code} {resp.text} {req_id}")
        except httpx.RequestError as exc:
            print(f"[ZEPTO] Email send request error: {exc}")

    async def _send_reset_email(self, email: str, full_name: str, token: str) -> None:
        if not settings.ZEPTO_API_KEY or not settings.ZEPTO_FROM_EMAIL:
            return
        reset_link = f"{settings.APP_BASE_URL}/reset-password?token={token}"
        subject = "Reset your SCENTS password"
        name = full_name or "there"
        html_body = (
            f"<p>Hi {name},</p>"
            f"<p>We received a request to reset your password.</p>"
            f"<p><a href=\"{reset_link}\">Reset Password</a></p>"
            f"<p>If you did not request this, you can ignore this email.</p>"
        )
        payload = {
            "from": {"address": settings.ZEPTO_FROM_EMAIL, "name": settings.ZEPTO_FROM_NAME},
            "to": [{"email_address": {"address": email, "name": full_name or email}}],
            "subject": subject,
            "htmlbody": html_body,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Zoho-enczapikey {settings.ZEPTO_API_KEY}",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(settings.ZEPTO_API_URL, json=payload, headers=headers)
                if resp.status_code >= 300:
                    req_id = resp.headers.get("x-request-id") or resp.headers.get("x-zoho-request-id") or ""
                    print(f"[ZEPTO] Reset email failed: {resp.status_code} {resp.text} {req_id}")
        except httpx.RequestError as exc:
            print(f"[ZEPTO] Reset email request error: {exc}")
