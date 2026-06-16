from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

from bson import ObjectId
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.schemas.review import (
    AdminReviewBulkCreate,
    AdminReviewBulkItem,
    ReviewCreate,
    ReviewEligibility,
    ReviewSummary,
    ReviewUpdate,
)
from app.utils.revalidate import revalidate_product_reviews

REVIEWABLE_ORDER_STATUSES = ["delivered"]


def mask_display_name(full_name: str) -> str:
    parts = [p for p in (full_name or "").strip().split() if p]
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}."
    if parts:
        return parts[0]
    return "Customer"


def _public_review(doc: dict) -> dict:
    out = {
        "_id": doc["_id"],
        "product_id": doc["product_id"],
        "user_name": doc.get("user_name", "Customer"),
        "rating": doc["rating"],
        "comment": doc["comment"],
        "source": doc.get("source", "customer"),
        "is_verified_purchase": bool(doc.get("is_verified_purchase")),
        "created_at": doc.get("created_at") or datetime.now(timezone.utc),
    }
    return out


def _admin_review(doc: dict) -> dict:
    out = _public_review(doc)
    out["user_id"] = doc.get("user_id")
    out["order_id"] = doc.get("order_id")
    out["is_published"] = doc.get("is_published", True)
    return out


class ReviewService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.collection = db["reviews"]

    async def _resolve_product_id(self, item: AdminReviewBulkItem) -> Optional[str]:
        if item.product_id:
            if ObjectId.is_valid(item.product_id):
                prod = await self.db["products"].find_one({"_id": ObjectId(item.product_id)})
                if prod:
                    return str(prod["_id"])
            prod = await self.db["products"].find_one({"slug": item.product_id})
            if prod:
                return str(prod["_id"])
            return None

        if item.product_slug:
            prod = await self.db["products"].find_one({"slug": item.product_slug})
            if prod:
                return str(prod["_id"])
        return None

    async def _canonical_product_id(self, product_ref: str) -> Optional[str]:
        if ObjectId.is_valid(product_ref):
            prod = await self.db["products"].find_one({"_id": ObjectId(product_ref)})
            if prod:
                return str(prod["_id"])
        prod = await self.db["products"].find_one({"slug": product_ref})
        if prod:
            return str(prod["_id"])
        return None

    async def _find_qualifying_order_id(self, user_id: str, product_id: str) -> Optional[str]:
        order = await self.db["orders"].find_one(
            {
                "user_id": user_id,
                "payment_status": "paid",
                "status": {"$in": REVIEWABLE_ORDER_STATUSES},
                "$or": [
                    {"items.product_id": product_id},
                    {"items.set_items.product_id": product_id},
                    {"items.selected_products.product_id": product_id},
                ],
            },
            sort=[("created_at", -1)],
        )
        return str(order["_id"]) if order else None

    async def _user_has_review(self, user_id: str, product_id: str) -> bool:
        existing = await self.collection.find_one(
            {
                "product_id": product_id,
                "user_id": user_id,
                "source": "customer",
            },
        )
        return existing is not None

    async def get_eligibility(self, user_id: str, product_id: str) -> ReviewEligibility:
        canonical_id = await self._canonical_product_id(product_id) or product_id
        if await self._user_has_review(user_id, canonical_id):
            return ReviewEligibility(
                can_review=False,
                has_reviewed=True,
                reason="You have already reviewed this product.",
            )

        order_id = await self._find_qualifying_order_id(user_id, canonical_id)
        if not order_id:
            return ReviewEligibility(
                can_review=False,
                has_reviewed=False,
                reason="Reviews are available after your order is delivered.",
            )

        return ReviewEligibility(can_review=True, has_reviewed=False)

    async def create_customer_review(self, user: dict, review_in: ReviewCreate) -> dict:
        user_id = str(user["_id"])
        product_id = await self._canonical_product_id(review_in.product_id)

        if not product_id:
            raise HTTPException(status_code=404, detail="Product not found")

        product = await self.db["products"].find_one({"_id": ObjectId(product_id)})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        if await self._user_has_review(user_id, product_id):
            raise HTTPException(status_code=409, detail="You have already reviewed this product")

        order_id = await self._find_qualifying_order_id(user_id, product_id)
        if not order_id:
            raise HTTPException(
                status_code=403,
                detail="You can only review products you have purchased and received.",
            )

        display_name = mask_display_name(user.get("full_name") or user.get("email", ""))
        now = datetime.now(timezone.utc)
        doc = {
            "product_id": product_id,
            "user_id": user_id,
            "user_name": display_name,
            "rating": review_in.rating,
            "comment": review_in.comment.strip(),
            "source": "customer",
            "is_verified_purchase": True,
            "order_id": order_id,
            "is_published": True,
            "created_at": now,
        }
        result = await self.collection.insert_one(doc)
        created = await self.collection.find_one({"_id": result.inserted_id})
        await revalidate_product_reviews(
            product_id,
            product.get("slug"),
        )
        return _public_review(created)

    async def get_summary(self, product_id: str) -> ReviewSummary:
        canonical_id = await self._canonical_product_id(product_id) or product_id
        pipeline = [
            {
                "$match": {
                    "product_id": canonical_id,
                    "is_published": {"$ne": False},
                },
            },
            {
                "$group": {
                    "_id": None,
                    "average_rating": {"$avg": "$rating"},
                    "review_count": {"$sum": 1},
                    "r1": {"$sum": {"$cond": [{"$eq": ["$rating", 1]}, 1, 0]}},
                    "r2": {"$sum": {"$cond": [{"$eq": ["$rating", 2]}, 1, 0]}},
                    "r3": {"$sum": {"$cond": [{"$eq": ["$rating", 3]}, 1, 0]}},
                    "r4": {"$sum": {"$cond": [{"$eq": ["$rating", 4]}, 1, 0]}},
                    "r5": {"$sum": {"$cond": [{"$eq": ["$rating", 5]}, 1, 0]}},
                },
            },
        ]
        rows = await self.collection.aggregate(pipeline).to_list(length=1)
        if not rows:
            return ReviewSummary()

        row = rows[0]
        return ReviewSummary(
            average_rating=round(row.get("average_rating") or 0, 1),
            review_count=row.get("review_count") or 0,
            rating_breakdown={
                1: row.get("r1") or 0,
                2: row.get("r2") or 0,
                3: row.get("r3") or 0,
                4: row.get("r4") or 0,
                5: row.get("r5") or 0,
            },
        )

    async def get_by_product(
        self,
        product_id: str,
        *,
        skip: int = 0,
        limit: int = 20,
        include_unpublished: bool = False,
    ) -> List[dict]:
        canonical_id = await self._canonical_product_id(product_id) or product_id
        query: dict[str, Any] = {"product_id": canonical_id}
        if not include_unpublished:
            query["is_published"] = {"$ne": False}

        cursor = (
            self.collection.find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [_public_review(doc) for doc in docs]

    async def list_admin(
        self,
        *,
        product_id: Optional[str] = None,
        source: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[dict]:
        query: dict[str, Any] = {}
        if product_id:
            query["product_id"] = product_id
        if source:
            query["source"] = source

        cursor = (
            self.collection.find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [_admin_review(doc) for doc in docs]

    async def bulk_create_admin(self, payload: AdminReviewBulkCreate) -> dict:
        created: List[str] = []
        failed: List[dict] = []
        affected_product_ids: set[str] = set()

        for index, item in enumerate(payload.reviews):
            if not item.product_id and not item.product_slug:
                failed.append({"index": index, "error": "product_id or product_slug required"})
                continue

            product_id = await self._resolve_product_id(item)
            if not product_id:
                failed.append({"index": index, "error": "Product not found"})
                continue

            created_at = item.created_at
            if created_at and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            doc = {
                "product_id": product_id,
                "user_id": None,
                "user_name": item.user_name.strip(),
                "rating": item.rating,
                "comment": item.comment.strip(),
                "source": "admin",
                "is_verified_purchase": False,
                "order_id": None,
                "is_published": True,
                "created_at": created_at or datetime.now(timezone.utc),
            }
            result = await self.collection.insert_one(doc)
            created.append(str(result.inserted_id))
            affected_product_ids.add(product_id)

        for product_id in affected_product_ids:
            product = await self.db["products"].find_one({"_id": ObjectId(product_id)})
            await revalidate_product_reviews(
                product_id,
                (product or {}).get("slug"),
            )

        return {"created_count": len(created), "created_ids": created, "failed": failed}

    async def _revalidate_for_review_doc(self, doc: dict) -> None:
        product_id = doc.get("product_id")
        if not product_id:
            return
        product = await self.db["products"].find_one({"_id": ObjectId(product_id)})
        await revalidate_product_reviews(product_id, (product or {}).get("slug"))

    async def update_admin(self, review_id: str, patch: ReviewUpdate) -> dict:
        if not ObjectId.is_valid(review_id):
            raise HTTPException(status_code=400, detail="Invalid review id")

        updates = patch.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        result = await self.collection.update_one(
            {"_id": ObjectId(review_id)},
            {"$set": updates},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Review not found")

        doc = await self.collection.find_one({"_id": ObjectId(review_id)})
        await self._revalidate_for_review_doc(doc)
        return _admin_review(doc)

    async def delete(self, review_id: str) -> None:
        if not ObjectId.is_valid(review_id):
            raise HTTPException(status_code=400, detail="Invalid review id")

        doc = await self.collection.find_one({"_id": ObjectId(review_id)})
        result = await self.collection.delete_one({"_id": ObjectId(review_id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Review not found")
        if doc:
            await self._revalidate_for_review_doc(doc)
