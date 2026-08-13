import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config.config import settings
from app.services.mail_service import MailService
from app.services.product_service import ProductService


def _ensure_aware(dt: datetime) -> datetime:
    """Motor/PyMongo return naive UTC datetimes on read (no `tz_aware=True`
    on the client), even though every write in this codebase uses
    `datetime.now(timezone.utc)`. Comparing that naive value against a fresh
    aware `datetime.now(timezone.utc)` raises `TypeError` — normalize here,
    same pattern already used in auth_service/review_service/orders.py.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class ReorderReminderService:
    """Daily sweep: nudge customers to reorder once their decants are

    estimated to be running low. Depletion date is a rough heuristic
    (total decant ml in the order / a configurable daily ml rate), not a
    per-fragrance model — good enough for a "hey, running low?" nudge.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.orders = db["orders"]
        self.reminders = db["reorder_reminders"]
        self.mail_service = MailService()
        self.product_service = ProductService(db)

    async def find_candidate_orders(self) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.REORDER_LOOKBACK_DAYS)
        cursor = self.orders.find(
            {
                "status": "delivered",
                "delivered_at": {"$gte": cutoff, "$ne": None},
            }
        ).sort("delivered_at", 1)
        return await cursor.to_list(length=None)

    def compute_depletion_date(self, order: dict) -> Optional[datetime]:
        delivered_at = order.get("delivered_at")
        if not delivered_at:
            return None
        delivered_at = _ensure_aware(delivered_at)

        total_ml = 0
        for item in order.get("items", []) or []:
            if item.get("status") == "cancelled":
                continue
            # Sealed/pack bottles last months, not weeks — out of scope for
            # this v1 cadence, and gift boxes / sets don't carry a reliable
            # per-decant ml signal on the order line itself.
            if item.get("is_pack") or item.get("gift_box_id"):
                continue
            if item.get("product_type") == "set":
                continue
            size_ml = int(item.get("size_ml") or 0)
            quantity = int(item.get("quantity") or 0)
            if size_ml > 0 and quantity > 0:
                total_ml += size_ml * quantity

        if total_ml <= 0:
            return None

        rate = settings.REORDER_DAILY_ML_RATE or 3
        days_of_supply = total_ml / rate
        return delivered_at + timedelta(days=days_of_supply)

    def _first_decant_item(self, order: dict) -> Optional[dict]:
        for item in order.get("items", []) or []:
            if item.get("status") == "cancelled":
                continue
            if item.get("is_pack") or item.get("gift_box_id"):
                continue
            if item.get("product_type") == "set":
                continue
            if item.get("product_id"):
                return item
        return None

    async def _has_reordered_since(self, order: dict) -> bool:
        """True if the customer placed any (non-cancelled) order after this one delivered."""
        delivered_at = order.get("delivered_at")
        user_id = order.get("user_id")
        email = (order.get("customer_email") or "").strip()

        or_clauses: list[dict] = []
        if user_id:
            or_clauses.append({"user_id": user_id})
        if email:
            or_clauses.append(
                {
                    "customer_email": {
                        "$regex": f"^{re.escape(email)}$",
                        "$options": "i",
                    }
                }
            )
        if not or_clauses:
            return False

        query = {
            "$or": or_clauses,
            "_id": {"$ne": order["_id"]},
            "status": {"$ne": "cancelled"},
            "created_at": {"$gt": delivered_at},
        }
        existing = await self.orders.find_one(query, {"_id": 1})
        return existing is not None

    async def _cooldown_active(self, email: str) -> bool:
        record = await self.reminders.find_one({"customer_email": email})
        if not record:
            return False
        last_sent_at = record.get("last_sent_at")
        if not last_sent_at:
            return False
        last_sent_at = _ensure_aware(last_sent_at)
        cooldown_ends = last_sent_at + timedelta(days=settings.REORDER_COOLDOWN_DAYS)
        return datetime.now(timezone.utc) < cooldown_ends

    async def is_eligible(self, order: dict, depletion_date: datetime) -> tuple[bool, str]:
        email = (order.get("customer_email") or "").strip().lower()
        if not email:
            return False, "no_email"

        now = datetime.now(timezone.utc)
        catchup_deadline = depletion_date + timedelta(days=settings.REORDER_CATCHUP_WINDOW_DAYS)
        if now < depletion_date:
            return False, "not_due_yet"
        if now > catchup_deadline:
            return False, "missed_catchup_window"

        if await self._has_reordered_since(order):
            return False, "already_reordered"

        if await self._cooldown_active(email):
            return False, "cooldown_active"

        return True, ""

    async def _send_reminder(self, order: dict, depletion_date: datetime) -> bool:
        email = (order.get("customer_email") or "").strip()
        if not email:
            return False

        first_item = self._first_decant_item(order)
        related_products: list[dict] = []
        if first_item and first_item.get("product_id"):
            try:
                related = await self.product_service.get_related(
                    first_item["product_id"], limit=3
                )
                related_products = related or []
            except Exception as e:
                print(f"[REORDER] get_related failed for order {order['_id']}: {e!r}")

        sent = await self.mail_service.send_reorder_reminder(
            email,
            order.get("customer_name") or "there",
            order,
            related_products,
        )
        if not sent:
            return False

        email_key = email.lower()
        await self.reminders.update_one(
            {"customer_email": email_key},
            {
                "$set": {
                    "customer_email": email_key,
                    "last_sent_at": datetime.now(timezone.utc),
                    "last_order_id": str(order["_id"]),
                },
                "$inc": {"send_count": 1},
            },
            upsert=True,
        )
        return True

    async def send_due_reminders(self, dry_run: bool = False) -> dict:
        candidates = await self.find_candidate_orders()
        skipped: dict[str, int] = {}
        eligible_count = 0
        sent_count = 0
        details: list[dict[str, Any]] = []

        for order in candidates:
            try:
                depletion_date = self.compute_depletion_date(order)
                if depletion_date is None:
                    skipped["no_decant_items"] = skipped.get("no_decant_items", 0) + 1
                    continue

                eligible, reason = await self.is_eligible(order, depletion_date)
                if not eligible:
                    skipped[reason] = skipped.get(reason, 0) + 1
                    continue

                eligible_count += 1
                entry = {
                    "order_id": str(order["_id"]),
                    "customer_email": order.get("customer_email"),
                    "depletion_date": depletion_date.isoformat(),
                }

                if dry_run:
                    details.append(entry)
                    continue

                sent = await self._send_reminder(order, depletion_date)
                if sent:
                    sent_count += 1
                    details.append(entry)
                else:
                    skipped["send_failed"] = skipped.get("send_failed", 0) + 1
            except Exception as e:
                # One malformed/unexpected order shouldn't take down the whole
                # sweep — log it, skip it, keep processing the rest.
                print(f"[REORDER] order {order.get('_id')} raised during processing: {e!r}")
                skipped["error"] = skipped.get("error", 0) + 1

        return {
            "checked": len(candidates),
            "eligible": eligible_count,
            "sent": sent_count if not dry_run else 0,
            "dry_run": dry_run,
            "skipped": skipped,
            "details": details,
        }
