from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.product import ProductCreate, ProductUpdate
from app.services.chip_service import ChipService
from bson import ObjectId
from typing import List, Optional
from datetime import datetime, timezone
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
        self.db = db
        self.collection = db["products"]
        self.chip_service = ChipService(db)

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
            # Escape regex special chars so a query like "L'Eau (Issey)" doesn't
            # blow up the Mongo regex engine; substring + case-insensitive only.
            safe = re.escape(search.strip())
            query["$or"] = [
                {"name": {"$regex": safe, "$options": "i"}},
                {"brand": {"$regex": safe, "$options": "i"}},
                {"notes_top": {"$regex": safe, "$options": "i"}},
                {"notes_middle": {"$regex": safe, "$options": "i"}},
                {"notes_base": {"$regex": safe, "$options": "i"}},
                {"description": {"$regex": safe, "$options": "i"}},
            ]
        cursor = self.collection.find(query)
        if sort_by == "newest":
            cursor = cursor.sort("created_at", -1)
        else:
            cursor = cursor.sort([("sort_order", 1), ("created_at", -1)])
        products = await cursor.to_list(length=100)
        active_chips_by_id = await self._fetch_active_chips_by_id()
        normalized = []
        for product in products:
            product = await self._ensure_stock_ml(product)
            normalized.append(self._attach_chips(product, active_chips_by_id))
        return normalized

    async def search(
        self,
        q: str,
        limit: int = 12,
        skip: int = 0,
        include_inactive: bool = False,
    ):
        """Paginated text search over the product catalogue.

        Powers both the navbar autosuggest dropdown (small `limit`) and the
        full /search page (larger `limit` + Load More). Returns the matching
        items plus a `total` count so the frontend can decide whether to show
        the Load More button.

        Searchable fields: name, brand, notes_top/middle/base, description.
        Match is case-insensitive substring with regex special chars escaped
        — i.e. typing "(rose)" won't blow up the Mongo engine.

        For ranking, we boost name/brand matches above note/description
        matches by running two passes and concatenating the results. It's a
        pragmatic relevance hack — sufficient for our catalogue size and
        cheaper than adding a $text index + scoring pipeline right now.
        """
        q_clean = (q or "").strip()
        if not q_clean:
            return {"items": [], "total": 0, "has_more": False}

        safe = re.escape(q_clean)
        active_filter: dict = {}
        if not include_inactive:
            active_filter["is_active"] = {"$ne": False}

        # Pass 1: high-signal fields (product name, brand).
        primary_q = {
            **active_filter,
            "$or": [
                {"name": {"$regex": safe, "$options": "i"}},
                {"brand": {"$regex": safe, "$options": "i"}},
            ],
        }
        # Pass 2: lower-signal fields (notes, description). We'll exclude
        # anything already matched by pass 1 to avoid duplicates.
        secondary_q = {
            **active_filter,
            "$or": [
                {"notes_top": {"$regex": safe, "$options": "i"}},
                {"notes_middle": {"$regex": safe, "$options": "i"}},
                {"notes_base": {"$regex": safe, "$options": "i"}},
                {"description": {"$regex": safe, "$options": "i"}},
            ],
        }

        # Total count = union of both passes (Mongo $or naturally dedupes).
        union_q = {
            **active_filter,
            "$or": primary_q["$or"] + secondary_q["$or"],
        }
        total = await self.collection.count_documents(union_q)

        primary_ids: List[ObjectId] = []
        primary_docs: List[dict] = []
        async for doc in self.collection.find(primary_q).sort(
            [("sort_order", 1), ("created_at", -1)]
        ):
            primary_ids.append(doc["_id"])
            primary_docs.append(doc)

        secondary_docs: List[dict] = []
        if len(primary_docs) < skip + limit:
            secondary_query = {**secondary_q, "_id": {"$nin": primary_ids}}
            async for doc in self.collection.find(secondary_query).sort(
                [("sort_order", 1), ("created_at", -1)]
            ):
                secondary_docs.append(doc)

        combined = primary_docs + secondary_docs
        window = combined[skip : skip + limit]

        active_chips_by_id = await self._fetch_active_chips_by_id()
        normalized = []
        for product in window:
            product = await self._ensure_stock_ml(product)
            normalized.append(self._attach_chips(product, active_chips_by_id))

        return {
            "items": normalized,
            "total": total,
            "has_more": skip + len(window) < total,
        }

    async def get_by_id(self, product_id: str):
        product = await self.collection.find_one({"_id": ObjectId(product_id)})
        product = await self._ensure_stock_ml(product)
        return await self._attach_chips_single(product)

    async def get_by_slug(self, slug: str):
        product = await self.collection.find_one({"slug": slug})
        product = await self._ensure_stock_ml(product)
        return await self._attach_chips_single(product)

    async def get_by_id_or_slug(self, identifier: str):
        if ObjectId.is_valid(identifier):
            product = await self.get_by_id(identifier)
            if product:
                return product
        return await self.get_by_slug(identifier)

    async def create(self, product_in: ProductCreate):
        product_dict = product_in.dict()
        product_dict["created_at"] = product_dict.get("created_at") or datetime.now(timezone.utc)
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

    async def _fetch_active_chips_by_id(self) -> dict:
        """Build a single dict of active chips keyed by their string id.

        Used by get_all() to avoid one chip lookup per product.
        """
        active = await self.chip_service.get_active()
        return {str(chip["_id"]): chip for chip in active}

    def _attach_chips(self, product: Optional[dict], active_chips_by_id: dict) -> Optional[dict]:
        if not product:
            return product
        chip_ids = product.get("chip_ids") or []
        resolved = []
        for cid in chip_ids:
            chip = active_chips_by_id.get(str(cid))
            if not chip:
                continue
            resolved.append({
                "_id": str(chip["_id"]),
                "code": chip.get("code"),
                "label": chip.get("label"),
                "color": chip.get("color", "indigo"),
                "icon": chip.get("icon"),
                "priority": chip.get("priority", 0),
            })
        resolved.sort(key=lambda c: (c.get("priority", 0), c.get("label", "")))
        product["chips"] = resolved
        return product

    async def _attach_chips_single(self, product: Optional[dict]) -> Optional[dict]:
        if not product:
            return product
        chip_ids = product.get("chip_ids") or []
        if not chip_ids:
            product["chips"] = []
            return product
        active = await self.chip_service.get_active_by_ids(chip_ids)
        active_by_id = {str(c["_id"]): c for c in active}
        return self._attach_chips(product, active_by_id)

    async def bulk_update_chips(self, product_ids: List[str], add: List[str], remove: List[str]):
        if not product_ids:
            return {"matched": 0, "modified": 0}
        object_ids = []
        for pid in product_ids:
            try:
                object_ids.append(ObjectId(pid))
            except Exception:
                continue

        modified = 0
        matched = 0
        if remove:
            res = await self.collection.update_many(
                {"_id": {"$in": object_ids}},
                {"$pullAll": {"chip_ids": remove}},
            )
            matched = max(matched, res.matched_count)
            modified += res.modified_count
        if add:
            res = await self.collection.update_many(
                {"_id": {"$in": object_ids}},
                {"$addToSet": {"chip_ids": {"$each": add}}},
            )
            matched = max(matched, res.matched_count)
            modified += res.modified_count
        return {"matched": matched, "modified": modified}
