from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.services.mail_service import MailService
from app.services.offer_service import OfferService


def normalize_instagram_handle(handle: str) -> str:
    return (handle or "").strip().lstrip("@").lower()


def is_instagram_url(url: str) -> bool:
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
        return host in ("instagram.com", "www.instagram.com", "instagr.am", "www.instagr.am")
    except Exception:
        return False


class PromoSubmissionService:
    COLLECTION = "instagram_promo_submissions"

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.collection = db[self.COLLECTION]
        self.orders = db["orders"]
        self.offer_service = OfferService(db)
        self.mail_service = MailService()

    def _campaign_config(self, campaign: Optional[dict]) -> dict:
        if not campaign:
            return {}
        return campaign.get("config") or {}

    def _deadline_from_config(self, config: dict) -> datetime:
        days = int(config.get("submission_deadline_days") or 14)
        return datetime.now(timezone.utc) + timedelta(days=days)

    async def get_by_id(self, submission_id: str) -> Optional[dict]:
        if not ObjectId.is_valid(submission_id):
            return None
        return await self.collection.find_one({"_id": ObjectId(submission_id)})

    async def get_by_order_id(self, order_id: str) -> Optional[dict]:
        return await self.collection.find_one({"order_id": order_id})

    async def bulk_by_order_ids(self, order_ids: list[str]) -> dict[str, dict]:
        if not order_ids:
            return {}
        cursor = self.collection.find({"order_id": {"$in": order_ids}})
        out: dict[str, dict] = {}
        async for doc in cursor:
            out[str(doc.get("order_id"))] = doc
        return out

    def to_summary(self, doc: Optional[dict]) -> Optional[dict]:
        if not doc:
            return None
        prize = doc.get("prize_snapshot") or {}
        raw_status = doc.get("status")
        if raw_status == "fulfilled":
            raw_status = "approved"
        elif raw_status == "under_review":
            raw_status = "submitted"
        return {
            "status": raw_status,
            "poster_instagram_username": doc.get("poster_instagram_username"),
            "post_url": doc.get("post_url"),
            "prize_label": prize.get("label"),
            "deadline_at": doc.get("deadline_at"),
            "rejection_reason": doc.get("rejection_reason"),
        }

    async def create_for_order(self, order: dict, campaign: dict) -> Optional[dict]:
        order_id = str(order.get("_id"))
        existing = await self.get_by_order_id(order_id)
        if existing:
            return existing

        config = self._campaign_config(campaign)
        now = datetime.now(timezone.utc)
        doc = {
            "order_id": order_id,
            "user_id": str(order.get("user_id") or "guest"),
            "customer_email": order.get("customer_email") or "",
            "customer_name": order.get("customer_name") or "",
            "campaign_id": str(campaign.get("_id")),
            "status": "awaiting_post",
            "poster_instagram_username": None,
            "post_url": None,
            "posted_by_note": None,
            "submitted_at": None,
            "deadline_at": self._deadline_from_config(config),
            "prize_template_id": None,
            "prize_snapshot": None,
            "rejection_reason": None,
            "admin_notes": None,
            "reviewed_by": None,
            "reviewed_at": None,
            "fulfilled_at": None,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = await self.collection.insert_one(doc)
            return await self.collection.find_one({"_id": result.inserted_id})
        except DuplicateKeyError:
            return await self.get_by_order_id(order_id)

    def _is_promo_eligible(
        self,
        order: dict,
        submission: Optional[dict] = None,
    ) -> bool:
        """Eligible only if the order was placed during an active promo (flagged at create) or already has a submission."""
        if submission:
            return True
        return bool(order.get("instagram_promo_opt_in"))

    async def _campaign_for_order(self, order: dict) -> Optional[dict]:
        """Active campaign, or the campaign that was live when this order was placed."""
        campaign = await self.offer_service.get_active_instagram_promo()
        if campaign:
            return campaign
        cid = order.get("instagram_promo_campaign_id")
        if cid:
            return await self.offer_service.get_by_id(str(cid))
        return None

    async def _verify_order_access(
        self,
        order: dict,
        email: Optional[str],
        current_user: Optional[dict],
    ) -> None:
        if current_user:
            uid = str(current_user.get("_id"))
            if str(order.get("user_id")) == uid:
                return
            user_email = (current_user.get("email") or "").strip().lower()
            order_email = (order.get("customer_email") or "").strip().lower()
            if user_email and order_email and user_email == order_email:
                return
        if email:
            supplied = email.strip().lower()
            order_email = (order.get("customer_email") or "").strip().lower()
            if supplied and order_email and supplied == order_email:
                return
        raise ValueError("Email verification required to access this promo submission.")

    async def get_public_by_order(
        self,
        order_id: str,
        email: Optional[str] = None,
        current_user: Optional[dict] = None,
    ) -> dict:
        if not ObjectId.is_valid(order_id):
            raise ValueError("Invalid order ID.")
        order = await self.orders.find_one({"_id": ObjectId(order_id)})
        if not order:
            raise ValueError("Order not found.")

        await self._verify_order_access(order, email, current_user)

        campaign = await self._campaign_for_order(order)
        config = self._campaign_config(campaign)
        display = (campaign or {}).get("display") or {}

        submission = await self.get_by_order_id(order_id)
        if (
            not submission
            and order.get("status") == "delivered"
            and order.get("instagram_promo_opt_in")
            and campaign
        ):
            submission = await self.create_for_order(order, campaign)

        eligible = self._is_promo_eligible(order, submission)

        status = submission.get("status") if submission else None
        if submission and submission.get("deadline_at"):
            deadline = submission["deadline_at"]
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if (
                status == "awaiting_post"
                and datetime.now(timezone.utc) > deadline
            ):
                await self.collection.update_one(
                    {"_id": submission["_id"]},
                    {"$set": {"status": "expired", "updated_at": datetime.now(timezone.utc)}},
                )
                submission = await self.get_by_order_id(order_id)
                status = "expired"

        prize_label = None
        if submission and submission.get("prize_snapshot"):
            prize_label = submission["prize_snapshot"].get("label")

        can_submit = bool(
            eligible
            and order.get("status") == "delivered"
            and submission
            and submission.get("status") == "awaiting_post"
        )

        display_status = status
        if status == "fulfilled":
            display_status = "approved"
        elif status == "under_review":
            display_status = "submitted"

        return {
            "order_id": order_id,
            "status": display_status or ("not_enrolled" if not eligible else "pending_delivery"),
            "poster_instagram_username": submission.get("poster_instagram_username") if submission else None,
            "post_url": submission.get("post_url") if submission else None,
            "posted_by_note": submission.get("posted_by_note") if submission else None,
            "submitted_at": submission.get("submitted_at") if submission else None,
            "deadline_at": submission.get("deadline_at") if submission else None,
            "prize_label": prize_label,
            "rejection_reason": submission.get("rejection_reason") if submission else None,
            "can_submit": can_submit,
            "order_delivered": order.get("status") == "delivered",
            "instagram_promo_opt_in": eligible,
            "campaign_rules": {
                "min_followers": config.get("min_followers", 100),
                "require_public_account": config.get("require_public_account", True),
                "required_mention": config.get("required_mention", ""),
                "required_hashtags": config.get("required_hashtags") or [],
                "submission_deadline_days": config.get("submission_deadline_days", 14),
                "max_posts_per_poster_account": config.get("max_posts_per_poster_account", 3),
                "rules_copy": display.get("rules_copy") or "",
                "checkout_label": display.get("checkout_label") or "",
            },
        }

    async def _count_poster_submissions(self, poster_handle: str, window_days: int) -> int:
        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        return await self.collection.count_documents({
            "poster_instagram_username": poster_handle,
            "submitted_at": {"$gte": since},
            "status": {"$nin": ["rejected"]},
        })

    async def submit(
        self,
        order_id: str,
        post_url: str,
        poster_instagram_username: str,
        posted_by_note: Optional[str],
        email: Optional[str],
        current_user: Optional[dict],
    ) -> dict:
        if not ObjectId.is_valid(order_id):
            raise ValueError("Invalid order ID.")
        order = await self.orders.find_one({"_id": ObjectId(order_id)})
        if not order:
            raise ValueError("Order not found.")
        await self._verify_order_access(order, email, current_user)

        if order.get("status") != "delivered":
            raise ValueError("Promo submission is only available after delivery.")

        campaign = await self._campaign_for_order(order)
        submission = await self.get_by_order_id(order_id)
        if not self._is_promo_eligible(order, submission):
            raise ValueError("Instagram promo is not available for this order.")
        if not campaign and not submission:
            raise ValueError("Instagram promo campaign is not active.")

        config = self._campaign_config(campaign)
        if not config and submission and submission.get("campaign_id"):
            stored_campaign = await self.offer_service.get_by_id(submission["campaign_id"])
            config = self._campaign_config(stored_campaign)
        if not submission:
            if not campaign:
                raise ValueError("Instagram promo campaign is not active.")
            submission = await self.create_for_order(order, campaign)
        if not submission:
            raise ValueError("Could not create promo submission.")

        status = submission.get("status")
        if status not in ("awaiting_post",):
            raise ValueError("This order already has a promo submission.")

        deadline = submission.get("deadline_at")
        if deadline:
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > deadline:
                await self.collection.update_one(
                    {"_id": submission["_id"]},
                    {"$set": {"status": "expired", "updated_at": datetime.now(timezone.utc)}},
                )
                raise ValueError("Submission deadline has passed.")

        url = (post_url or "").strip()
        if not is_instagram_url(url):
            raise ValueError("Please provide a valid Instagram post or reel URL.")

        poster = normalize_instagram_handle(poster_instagram_username)
        if not poster or not re.match(r"^[a-z0-9._]{1,30}$", poster):
            raise ValueError("Please provide a valid Instagram username.")

        max_posts = int(config.get("max_posts_per_poster_account") or 3)
        window_days = int(config.get("poster_limit_window_days") or 30)
        count = await self._count_poster_submissions(poster, window_days)
        if count >= max_posts:
            raise ValueError(
                f"This Instagram account has reached the monthly limit "
                f"({max_posts} entries per {window_days} days)."
            )

        now = datetime.now(timezone.utc)
        update = {
            "poster_instagram_username": poster,
            "post_url": url,
            "posted_by_note": (posted_by_note or "").strip() or None,
            "submitted_at": now,
            "status": "submitted",
            "updated_at": now,
        }
        try:
            result = await self.collection.update_one(
                {"_id": submission["_id"], "status": "awaiting_post"},
                {"$set": update},
            )
            if result.matched_count == 0:
                raise ValueError("This order already has a promo submission.")
        except DuplicateKeyError:
            raise ValueError("This Instagram post has already been submitted.")

        updated = await self.get_by_order_id(order_id)
        if not updated or updated.get("status") != "submitted":
            existing_url = await self.collection.find_one({"post_url": url})
            if existing_url:
                raise ValueError("This Instagram post has already been submitted.")
            raise ValueError("Submission failed. Please try again.")

        return updated

    async def list_admin_queue(self, status: Optional[str] = None, limit: int = 200) -> list[dict]:
        query: dict[str, Any] = {}
        if status:
            query["status"] = status
        else:
            query["status"] = {"$in": ["submitted", "under_review", "approved", "rejected"]}
        cursor = self.collection.find(query).sort("submitted_at", -1)
        return await cursor.to_list(length=limit)

    def _find_prize_template(self, campaign: dict, template_id: str) -> Optional[dict]:
        templates = (campaign.get("config") or {}).get("prize_templates") or []
        for t in templates:
            if isinstance(t, dict) and str(t.get("id")) == str(template_id):
                return t
        return None

    async def approve(
        self,
        submission_id: str,
        prize_template_id: str,
        admin_user: dict,
        admin_notes: Optional[str] = None,
    ) -> dict:
        submission = await self.get_by_id(submission_id)
        if not submission:
            raise ValueError("Submission not found.")
        if submission.get("status") not in ("submitted", "under_review"):
            raise ValueError("Only submitted entries can be approved.")

        campaign = await self.offer_service.get_by_id(submission.get("campaign_id", ""))
        if not campaign:
            campaign = await self.offer_service.get_active_instagram_promo()
        if not campaign:
            raise ValueError("Campaign not found.")

        template = self._find_prize_template(campaign, prize_template_id)
        if not template:
            raise ValueError("Invalid prize template.")

        now = datetime.now(timezone.utc)
        prize_snapshot = {
            "id": template.get("id"),
            "label": template.get("label"),
            "items": template.get("items") or [],
        }
        await self.collection.update_one(
            {"_id": submission["_id"]},
            {
                "$set": {
                    "status": "approved",
                    "prize_template_id": prize_template_id,
                    "prize_snapshot": prize_snapshot,
                    "admin_notes": admin_notes,
                    "reviewed_by": str(admin_user.get("_id")),
                    "reviewed_at": now,
                    "updated_at": now,
                }
            },
        )
        updated = await self.get_by_id(submission_id)
        try:
            await self.mail_service.send_instagram_promo_status_update(
                updated.get("customer_email"),
                updated.get("customer_name"),
                updated,
                "approved",
            )
        except Exception as e:
            print(f"[MAIL] Promo approve email failed: {e}")
        return updated

    async def reject(
        self,
        submission_id: str,
        reason: str,
        admin_user: dict,
    ) -> dict:
        submission = await self.get_by_id(submission_id)
        if not submission:
            raise ValueError("Submission not found.")
        if submission.get("status") not in ("submitted", "under_review"):
            raise ValueError("Only submitted entries can be rejected.")

        now = datetime.now(timezone.utc)
        await self.collection.update_one(
            {"_id": submission["_id"]},
            {
                "$set": {
                    "status": "rejected",
                    "rejection_reason": reason.strip(),
                    "reviewed_by": str(admin_user.get("_id")),
                    "reviewed_at": now,
                    "updated_at": now,
                }
            },
        )
        updated = await self.get_by_id(submission_id)
        try:
            await self.mail_service.send_instagram_promo_status_update(
                updated.get("customer_email"),
                updated.get("customer_name"),
                updated,
                "rejected",
            )
        except Exception as e:
            print(f"[MAIL] Promo reject email failed: {e}")
        return updated

    async def fulfill(self, submission_id: str, admin_user: dict) -> dict:
        submission = await self.get_by_id(submission_id)
        if not submission:
            raise ValueError("Submission not found.")
        if submission.get("status") != "approved":
            raise ValueError("Only approved entries can be marked fulfilled.")

        now = datetime.now(timezone.utc)
        await self.collection.update_one(
            {"_id": submission["_id"]},
            {"$set": {"status": "fulfilled", "fulfilled_at": now, "updated_at": now}},
        )
        updated = await self.get_by_id(submission_id)
        try:
            await self.mail_service.send_instagram_promo_status_update(
                updated.get("customer_email"),
                updated.get("customer_name"),
                updated,
                "fulfilled",
            )
        except Exception as e:
            print(f"[MAIL] Promo fulfill email failed: {e}")
        return updated

    async def send_promo_invite_if_needed(self, order: dict, order_id: str) -> None:
        if not order.get("instagram_promo_opt_in"):
            return
        campaign = await self._campaign_for_order(order)
        if not campaign:
            return
        submission = await self.create_for_order(order, campaign)
        if not submission:
            return
        config = self._campaign_config(campaign)
        display = campaign.get("display") or {}
        try:
            await self.mail_service.send_instagram_promo_invite(
                order.get("customer_email"),
                order.get("customer_name"),
                order_id,
                submission,
                config,
                display,
            )
        except Exception as e:
            print(f"[MAIL] Promo invite email failed: {e}")
