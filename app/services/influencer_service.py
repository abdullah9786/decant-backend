from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.influencer import (
    InfluencerProfileCreate, InfluencerProfileUpdate,
    SectionCreate, SectionUpdate,
)
from bson import ObjectId
from datetime import datetime
import re

RESERVED_USERNAMES = {
    "products", "cart", "checkout", "brands", "families", "search",
    "new-arrivals", "login", "register", "profile", "orders",
    "track-order", "forgot-password", "reset-password", "verify-email",
    "_not-found", "api", "admin", "influencer", "sitemap", "robots",
    "manifest", "favicon", "health", "creators", "explore",
}

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,29}$")


class InfluencerService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.profiles = db["influencer_profiles"]
        self.sections = db["influencer_sections"]
        self.users = db["users"]
        self.products = db["products"]

    # ── Profile CRUD ──────────────────────────────────────────────

    def validate_username(self, username: str) -> str:
        username = username.lower().strip()
        if username in RESERVED_USERNAMES:
            raise ValueError(f"Username '{username}' is reserved")
        if not USERNAME_PATTERN.match(username):
            raise ValueError(
                "Username must be 3-30 chars, start with a letter or digit, "
                "and contain only lowercase letters, digits, dots, hyphens, or underscores"
            )
        return username

    async def create_profile(self, data: InfluencerProfileCreate) -> dict:
        username = self.validate_username(data.username)

        existing = await self.profiles.find_one({"username": username})
        if existing:
            raise ValueError(f"Username '{username}' is already taken")

        user = await self.users.find_one({"_id": ObjectId(data.user_id)})
        if not user:
            raise ValueError("User not found")

        await self.users.update_one(
            {"_id": ObjectId(data.user_id)},
            {"$set": {"is_influencer": True}},
        )

        now = datetime.utcnow()
        doc = {
            "user_id": data.user_id,
            "username": username,
            "display_name": data.display_name,
            "bio": data.bio or "",
            "profile_image_url": data.profile_image_url or "",
            "banner_image_url": data.banner_image_url or "",
            "is_active": True,
            "commission_rate": data.commission_rate,
            "payout_upi": None,
            "payout_bank_details": None,
            "created_at": now,
            "updated_at": now,
        }
        result = await self.profiles.insert_one(doc)
        return await self.profiles.find_one({"_id": result.inserted_id})

    async def get_profile_by_username(self, username: str) -> dict | None:
        return await self.profiles.find_one({"username": username.lower()})

    async def get_profile_by_user_id(self, user_id: str) -> dict | None:
        return await self.profiles.find_one({"user_id": user_id})

    async def get_profile_by_id(self, profile_id: str) -> dict | None:
        return await self.profiles.find_one({"_id": ObjectId(profile_id)})

    async def update_profile(self, profile_id: str, data: InfluencerProfileUpdate) -> dict | None:
        update_data = {k: v for k, v in data.dict(exclude_unset=True).items()}
        if not update_data:
            return await self.get_profile_by_id(profile_id)
        update_data["updated_at"] = datetime.utcnow()
        await self.profiles.update_one(
            {"_id": ObjectId(profile_id)},
            {"$set": update_data},
        )
        return await self.get_profile_by_id(profile_id)

    async def toggle_active(self, profile_id: str) -> dict | None:
        profile = await self.get_profile_by_id(profile_id)
        if not profile:
            return None
        new_status = not profile.get("is_active", True)
        await self.profiles.update_one(
            {"_id": ObjectId(profile_id)},
            {"$set": {"is_active": new_status, "updated_at": datetime.utcnow()}},
        )
        return await self.get_profile_by_id(profile_id)

    async def get_all_profiles(self, active_only: bool = False) -> list:
        query = {"is_active": True} if active_only else {}
        cursor = self.profiles.find(query).sort("created_at", -1)
        return await cursor.to_list(length=200)

    # ── Section CRUD ──────────────────────────────────────────────

    async def create_section(self, influencer_id: str, data: SectionCreate) -> dict:
        last = await self.sections.find_one(
            {"influencer_id": influencer_id},
            sort=[("sort_order", -1)],
        )
        next_order = (last.get("sort_order", 0) + 1) if last else 0

        doc = {
            "influencer_id": influencer_id,
            "title": data.title,
            "product_ids": data.product_ids,
            "sort_order": next_order,
            "is_active": True,
            "created_at": datetime.utcnow(),
        }
        result = await self.sections.insert_one(doc)
        return await self.sections.find_one({"_id": result.inserted_id})

    async def update_section(self, section_id: str, influencer_id: str, data: SectionUpdate) -> dict | None:
        section = await self.sections.find_one({
            "_id": ObjectId(section_id),
            "influencer_id": influencer_id,
        })
        if not section:
            return None
        update_data = {k: v for k, v in data.dict(exclude_unset=True).items()}
        if not update_data:
            return section
        await self.sections.update_one(
            {"_id": ObjectId(section_id)},
            {"$set": update_data},
        )
        return await self.sections.find_one({"_id": ObjectId(section_id)})

    async def delete_section(self, section_id: str, influencer_id: str) -> bool:
        result = await self.sections.delete_one({
            "_id": ObjectId(section_id),
            "influencer_id": influencer_id,
        })
        return result.deleted_count > 0

    async def reorder_sections(self, influencer_id: str, section_ids: list[str]) -> list:
        for i, sid in enumerate(section_ids):
            await self.sections.update_one(
                {"_id": ObjectId(sid), "influencer_id": influencer_id},
                {"$set": {"sort_order": i}},
            )
        return await self.get_sections(influencer_id)

    async def get_sections(self, influencer_id: str, active_only: bool = False) -> list:
        query = {"influencer_id": influencer_id}
        if active_only:
            query["is_active"] = True
        cursor = self.sections.find(query).sort("sort_order", 1)
        return await cursor.to_list(length=50)

    # ── Public Storefront ─────────────────────────────────────────

    async def get_storefront(self, username: str) -> dict | None:
        """Fetch everything needed for the public storefront page."""
        profile = await self.get_profile_by_username(username)
        if not profile or not profile.get("is_active", True):
            return None

        sections = await self.get_sections(str(profile["_id"]), active_only=True)

        all_product_ids = set()
        for s in sections:
            for pid in s.get("product_ids", []):
                all_product_ids.add(pid)

        products_map = {}
        if all_product_ids:
            oids = [ObjectId(pid) for pid in all_product_ids if ObjectId.is_valid(pid)]
            cursor = self.products.find({"_id": {"$in": oids}})
            async for p in cursor:
                products_map[str(p["_id"])] = p

        enriched_sections = []
        for s in sections:
            products = []
            for pid in s.get("product_ids", []):
                prod = products_map.get(pid)
                if prod and prod.get("stock_ml", 0) > 0:
                    prod["_id"] = str(prod["_id"])
                    products.append(prod)
            enriched_sections.append({
                "_id": str(s["_id"]),
                "title": s["title"],
                "sort_order": s["sort_order"],
                "products": products,
            })

        profile["_id"] = str(profile["_id"])
        return {
            "profile": profile,
            "sections": enriched_sections,
        }
