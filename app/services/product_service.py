from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.product import ProductCreate, ProductUpdate
from bson import ObjectId
from typing import List, Optional
from datetime import datetime
import re
import unicodedata


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


class ProductService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["products"]

    async def _unique_slug(self, base_slug: str, exclude_id: Optional[str] = None) -> str:
        slug = base_slug
        counter = 1
        while True:
            query: dict = {"slug": slug}
            if exclude_id:
                query["_id"] = {"$ne": ObjectId(exclude_id)}
            existing = await self.collection.find_one(query, {"_id": 1})
            if not existing:
                return slug
            slug = f"{base_slug}-{counter}"
            counter += 1

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

    async def get_by_slug(self, slug: str):
        product = await self.collection.find_one({"slug": slug})
        return await self._ensure_stock_ml(product)

    async def get_by_id_or_slug(self, identifier: str):
        if ObjectId.is_valid(identifier):
            product = await self.get_by_id(identifier)
            if product:
                return product
        return await self.get_by_slug(identifier)

    async def create(self, product_in: ProductCreate):
        product_dict = product_in.dict()
        product_dict["created_at"] = product_dict.get("created_at") or datetime.utcnow()
        base_slug = _slugify(f"{product_dict['name']} {product_dict['brand']}")
        product_dict["slug"] = await self._unique_slug(base_slug)
        product_result = await self.collection.insert_one(product_dict)
        return await self.get_by_id(str(product_result.inserted_id))

    async def update(self, product_id: str, product_in: ProductUpdate):
        update_data = {k: v for k, v in product_in.dict(exclude_unset=True).items()}
        if "name" in update_data or "brand" in update_data:
            current = await self.collection.find_one({"_id": ObjectId(product_id)}, {"name": 1, "brand": 1})
            name = update_data.get("name", current.get("name", ""))
            brand = update_data.get("brand", current.get("brand", ""))
            base_slug = _slugify(f"{name} {brand}")
            update_data["slug"] = await self._unique_slug(base_slug, exclude_id=product_id)
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
