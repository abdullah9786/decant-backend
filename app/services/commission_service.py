from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from datetime import datetime
from typing import Optional


class CommissionService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.commissions = db["commissions"]
        self.profiles = db["influencer_profiles"]
        self.payouts = db["payouts"]
        self.orders = db["orders"]

    # ── Commission Lifecycle ──────────────────────────────────────

    async def create_commission(
        self, influencer_id: str, order_id: str, order_total: float,
        buyer_user_id: Optional[str] = None,
    ) -> dict | None:
        profile = await self.profiles.find_one({"_id": ObjectId(influencer_id)})
        if not profile or not profile.get("is_active", True):
            return None

        if buyer_user_id and profile.get("user_id") == buyer_user_id:
            return None

        rate = profile.get("commission_rate", 0.10)

        doc = {
            "influencer_id": influencer_id,
            "order_id": order_id,
            "order_total": order_total,
            "commission_rate": rate,
            "commission_amount": round(order_total * rate, 2),
            "status": "pending",
            "payout_id": None,
            "created_at": datetime.utcnow(),
            "approved_at": None,
            "paid_at": None,
        }
        result = await self.commissions.insert_one(doc)
        return await self.commissions.find_one({"_id": result.inserted_id})

    async def approve_commission(self, commission_id: str) -> dict | None:
        c = await self.commissions.find_one({"_id": ObjectId(commission_id)})
        if not c or c["status"] != "pending":
            return None

        order = await self.orders.find_one({"_id": ObjectId(c["order_id"])})
        if not order or order.get("status") != "delivered":
            return None

        await self.commissions.update_one(
            {"_id": ObjectId(commission_id)},
            {"$set": {"status": "approved", "approved_at": datetime.utcnow()}},
        )
        return await self.commissions.find_one({"_id": ObjectId(commission_id)})

    async def cancel_commission(self, commission_id: str, reason: str | None = None) -> dict | None:
        c = await self.commissions.find_one({"_id": ObjectId(commission_id)})
        if not c or c["status"] not in ("pending", "approved"):
            return None
        update: dict = {"status": "cancelled"}
        if reason:
            update["cancellation_reason"] = reason
        await self.commissions.update_one(
            {"_id": ObjectId(commission_id)},
            {"$set": update},
        )
        return await self.commissions.find_one({"_id": ObjectId(commission_id)})

    # ── Queries ───────────────────────────────────────────────────

    async def get_commissions_by_influencer(
        self, influencer_id: str, status_filter: Optional[str] = None
    ) -> list:
        query: dict = {"influencer_id": influencer_id}
        if status_filter:
            query["status"] = status_filter
        cursor = self.commissions.find(query).sort("created_at", -1)
        return await cursor.to_list(length=200)

    async def get_earnings_summary(self, influencer_id: str) -> dict:
        pipeline = [
            {"$match": {"influencer_id": influencer_id}},
            {"$group": {
                "_id": "$status",
                "total": {"$sum": "$commission_amount"},
                "count": {"$sum": 1},
            }},
        ]
        results = {}
        async for doc in self.commissions.aggregate(pipeline):
            results[doc["_id"]] = doc

        total_orders = sum(r["count"] for r in results.values())
        return {
            "total_earnings": round(
                sum(r["total"] for s, r in results.items() if s != "cancelled"), 2
            ),
            "pending_earnings": round(results.get("pending", {}).get("total", 0), 2),
            "approved_earnings": round(results.get("approved", {}).get("total", 0), 2),
            "paid_earnings": round(results.get("paid", {}).get("total", 0), 2),
            "total_orders": total_orders,
        }

    # ── Payouts ───────────────────────────────────────────────────

    async def create_payout(self, influencer_id: str, method: str = "upi") -> dict | None:
        """Create a payout for all approved commissions (admin-triggered)."""
        approved = await self.commissions.find(
            {"influencer_id": influencer_id, "status": "approved"}
        ).to_list(length=500)

        if not approved:
            return None

        amount = round(sum(c["commission_amount"] for c in approved), 2)
        commission_ids = [str(c["_id"]) for c in approved]

        payout_doc = {
            "influencer_id": influencer_id,
            "amount": amount,
            "commission_ids": commission_ids,
            "method": method,
            "status": "pending",
            "scheduled_date": datetime.utcnow(),
            "completed_at": None,
            "created_at": datetime.utcnow(),
        }
        result = await self.payouts.insert_one(payout_doc)
        payout_id = str(result.inserted_id)

        await self.commissions.update_many(
            {"_id": {"$in": [ObjectId(cid) for cid in commission_ids]}},
            {"$set": {"status": "paid", "payout_id": payout_id, "paid_at": datetime.utcnow()}},
        )

        return await self.payouts.find_one({"_id": result.inserted_id})

    async def complete_payout(self, payout_id: str) -> dict | None:
        payout = await self.payouts.find_one({"_id": ObjectId(payout_id)})
        if not payout or payout["status"] != "pending":
            return None
        await self.payouts.update_one(
            {"_id": ObjectId(payout_id)},
            {"$set": {"status": "completed", "completed_at": datetime.utcnow()}},
        )
        return await self.payouts.find_one({"_id": ObjectId(payout_id)})

    async def get_payouts_by_influencer(self, influencer_id: str) -> list:
        cursor = self.payouts.find({"influencer_id": influencer_id}).sort("created_at", -1)
        return await cursor.to_list(length=100)

    async def _get_delivered_order_ids(self, order_ids: list[str]) -> set[str]:
        """Return the subset of order_ids whose orders are delivered."""
        oids = [ObjectId(oid) for oid in order_ids if ObjectId.is_valid(oid)]
        if not oids:
            return set()
        cursor = self.orders.find(
            {"_id": {"$in": oids}, "status": "delivered"},
            {"_id": 1},
        )
        return {str(doc["_id"]) async for doc in cursor}

    async def bulk_approve_all_pending(self) -> int:
        """Approve all pending commissions whose orders are delivered."""
        pending = await self.commissions.find({"status": "pending"}).to_list(length=1000)
        if not pending:
            return 0
        order_ids = [c["order_id"] for c in pending]
        delivered = await self._get_delivered_order_ids(order_ids)
        eligible = [c["_id"] for c in pending if c["order_id"] in delivered]
        if not eligible:
            return 0
        result = await self.commissions.update_many(
            {"_id": {"$in": eligible}},
            {"$set": {"status": "approved", "approved_at": datetime.utcnow()}},
        )
        return result.modified_count

    async def bulk_approve_by_ids(self, commission_ids: list[str]) -> int:
        """Approve specific commissions whose orders are delivered."""
        oids = [ObjectId(cid) for cid in commission_ids]
        pending = await self.commissions.find(
            {"_id": {"$in": oids}, "status": "pending"}
        ).to_list(length=500)
        if not pending:
            return 0
        order_ids = [c["order_id"] for c in pending]
        delivered = await self._get_delivered_order_ids(order_ids)
        eligible = [c["_id"] for c in pending if c["order_id"] in delivered]
        if not eligible:
            return 0
        result = await self.commissions.update_many(
            {"_id": {"$in": eligible}},
            {"$set": {"status": "approved", "approved_at": datetime.utcnow()}},
        )
        return result.modified_count

    async def bulk_cancel_by_ids(self, commission_ids: list[str], reason: str | None = None) -> int:
        """Cancel specific commissions by ID. Returns count cancelled."""
        oids = [ObjectId(cid) for cid in commission_ids]
        update: dict = {"status": "cancelled"}
        if reason:
            update["cancellation_reason"] = reason
        result = await self.commissions.update_many(
            {"_id": {"$in": oids}, "status": {"$in": ["pending", "approved"]}},
            {"$set": update},
        )
        return result.modified_count

    async def bulk_create_payouts(self, method: str = "upi") -> list:
        """Create payouts for every influencer that has approved commissions."""
        pipeline = [
            {"$match": {"status": "approved"}},
            {"$group": {
                "_id": "$influencer_id",
                "total": {"$sum": "$commission_amount"},
                "commission_ids": {"$push": {"$toString": "$_id"}},
            }},
        ]
        groups = []
        async for doc in self.commissions.aggregate(pipeline):
            groups.append(doc)

        created_payouts = []
        for g in groups:
            influencer_id = g["_id"]
            amount = round(g["total"], 2)
            commission_ids = g["commission_ids"]

            payout_doc = {
                "influencer_id": influencer_id,
                "amount": amount,
                "commission_ids": commission_ids,
                "method": method,
                "status": "pending",
                "scheduled_date": datetime.utcnow(),
                "completed_at": None,
                "created_at": datetime.utcnow(),
            }
            result = await self.payouts.insert_one(payout_doc)
            payout_id = str(result.inserted_id)

            await self.commissions.update_many(
                {"_id": {"$in": [ObjectId(cid) for cid in commission_ids]}},
                {"$set": {"status": "paid", "payout_id": payout_id, "paid_at": datetime.utcnow()}},
            )

            payout = await self.payouts.find_one({"_id": result.inserted_id})
            created_payouts.append(payout)

        return created_payouts

    async def bulk_complete_payouts(self, influencer_id: str) -> int:
        """Complete all pending payouts for a given influencer. Returns count completed."""
        result = await self.payouts.update_many(
            {"influencer_id": influencer_id, "status": "pending"},
            {"$set": {"status": "completed", "completed_at": datetime.utcnow()}},
        )
        return result.modified_count

    async def get_all_commissions(self, status_filter: Optional[str] = None) -> list:
        query = {}
        if status_filter:
            query["status"] = status_filter
        cursor = self.commissions.find(query).sort("created_at", -1)
        return await cursor.to_list(length=500)
