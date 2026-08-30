from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from app.schemas.order import OrderCreate, OrderUpdate
from bson import ObjectId
from typing import List, Optional, Any
from datetime import datetime, timezone
import re
import razorpay
from app.config.config import settings
from app.services.mail_service import MailService
from app.services.offer_service import OfferService
from app.services.promo_submission_service import PromoSubmissionService

def convert_objectid_to_str(obj: Any) -> Any:
    """Recursively convert ObjectId instances to strings in a document."""
    if isinstance(obj, ObjectId):
        return str(obj)
    elif isinstance(obj, dict):
        return {key: convert_objectid_to_str(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_objectid_to_str(item) for item in obj]
    else:
        return obj

class OrderService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["orders"]
        self.products = db["products"]
        self.gift_boxes = db["gift_boxes"]
        self.client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        self.mail_service = MailService()

    async def create_razorpay_order(self, amount: float, order_id: str):
        """
        Create a Razorpay order. Amount should be in INR.
        Razorpay expects amount in paise (1 INR = 100 paise).
        """
        data = {
            "amount": int(amount * 100),
            "currency": "INR",
            "receipt": order_id,
            "payment_capture": 1
        }
        try:
            razorpay_order = self.client.order.create(data=data)
            return razorpay_order
        except Exception as e:
            print(f"Razorpay Order Error: {str(e)}")
            raise ValueError(f"Could not create Razorpay order: {str(e)}")

    async def ensure_stock_for_checkout(self, items: List[dict]) -> None:
        """Raises ValueError with message if any line cannot be fulfilled."""
        await self._ensure_stock(items)

    def refund_payment_full(self, payment_id: str, amount_inr: float) -> None:
        """Full refund for a captured payment. amount_inr is in INR (converted to paise)."""
        amount_paise = int(round(amount_inr * 100))
        print(f"[RAZORPAY-REFUND] Attempting refund: payment_id={payment_id}, amount_inr={amount_inr}, amount_paise={amount_paise}")
        try:
            payment = self.client.payment.fetch(payment_id)
            print(f"[RAZORPAY-REFUND] Fetched payment: status={payment.get('status')}, "
                  f"amount={payment.get('amount')}, currency={payment.get('currency')}")
        except Exception as fetch_err:
            print(f"[RAZORPAY-REFUND] payment.fetch() failed: {fetch_err}")
            raise
        if payment.get("status") != "captured":
            raise ValueError(
                f"Payment not in captured state (current: {payment.get('status')})."
            )
        try:
            result = self.client.refund.create({
                "payment_id": payment_id,
                "amount": amount_paise,
            })
            print(f"[RAZORPAY-REFUND] Refund success: {result}")
        except Exception as refund_err:
            print(f"[RAZORPAY-REFUND] refund.create() failed: {refund_err}")
            raise

    async def _resolve_mystery_gift(self, items: List[dict]) -> dict | None:
        """Derive the unlocked mystery-gift tier from the cart subtotal.

        Server-authoritative so the client can't claim a richer tier than the
        cart earns. The subtotal mirrors the storefront cart total exactly
        (sum of price * quantity over all non-cancelled lines). Returns a
        self-contained snapshot so order history stays correct even if the
        offer is later edited or deactivated. Fulfillment is offline, so no
        stock or product mapping is involved.
        """
        offer_service = OfferService(self.products.database)
        offer = await offer_service.get_active_mystery_gift()
        if not offer:
            return None
        subtotal = sum(
            float(it.get("price", 0)) * int(it.get("quantity", 0))
            for it in (items or [])
            if it.get("status") != "cancelled"
        )
        tier = OfferService.resolve_mystery_tier(offer.get("config") or {}, subtotal)
        if not tier:
            return None
        return {
            "offer_id": str(offer.get("_id")),
            "tier_id": tier.get("id"),
            "name": tier.get("name"),
            "min_subtotal": tier.get("min_subtotal"),
            "accent_color": tier.get("accent_color"),
            "icon": tier.get("icon"),
            "tagline": tier.get("tagline"),
            "subtotal_at_unlock": round(subtotal, 2),
        }

    async def create(self, order_in: OrderCreate):
        order_dict = order_in.dict()
        order_dict["created_at"] = order_dict.get("created_at") or datetime.now(timezone.utc)
        await self._ensure_stock(order_dict.get("items", []))
        order_dict["mystery_gift"] = await self._resolve_mystery_gift(order_dict.get("items", []))
        offer_service = OfferService(self.products.database)
        promo = await offer_service.get_active_instagram_promo()
        if promo:
            order_dict["instagram_promo_opt_in"] = True
            order_dict["instagram_promo_campaign_id"] = str(promo.get("_id"))
        free_decants = order_dict.get("free_decants") or []
        if free_decants:
            try:
                await self._validate_free_decants(order_dict.get("items", []), free_decants)
                await self._ensure_free_decant_stock(free_decants)
            except ValueError as e:
                # Offer ended (or eligibility otherwise lost) between checkout
                # and payment capture — drop free decants gracefully so the
                # paid order still goes through. Customer is notified via the
                # `free_decants_dropped_reason` field in the order.
                order_dict["free_decants"] = []
                order_dict["free_decants_dropped_reason"] = str(e)
                free_decants = []

        rzp_oid = (order_dict.get("payment_details") or {}).get("razorpay_order_id")
        idem_key = order_dict.get("idempotency_key")
        try:
            result = await self.collection.insert_one(order_dict)
        except DuplicateKeyError:
            # Another concurrent writer (e.g. retried webhook, COD double-submit,
            # or the verify-and-create / webhook race) already inserted this
            # order. Return the existing row and skip stock decrement — the
            # winning writer already handled it. Mark the result so the caller
            # can also skip re-running side-effects (commission, emails, etc.).
            if rzp_oid:
                existing = await self.collection.find_one(
                    {"payment_details.razorpay_order_id": rzp_oid}
                )
                if existing:
                    existing["_was_duplicate"] = True
                    return existing
            if idem_key:
                existing = await self.collection.find_one(
                    {"idempotency_key": idem_key}
                )
                if existing:
                    existing["_was_duplicate"] = True
                    return existing
            raise

        await self._decrement_stock(order_dict.get("items", []))
        if free_decants:
            await self._decrement_free_decant_stock(free_decants)
        return await self.collection.find_one({"_id": result.inserted_id})

    async def _attach_promo_to_orders(self, orders: list) -> list:
        if not orders:
            return orders
        order_ids = [str(o["_id"]) for o in orders]
        promo_service = PromoSubmissionService(self.collection.database)
        promo_map = await promo_service.bulk_by_order_ids(order_ids)
        for order in orders:
            summary = promo_service.to_summary(promo_map.get(str(order["_id"])))
            if summary:
                order["promo_submission"] = summary
        return orders

    async def get_all(self, user_id: str = None, q: str = None, skip: int = 0, limit: int = 100):
        query: dict = {}
        if user_id:
            query["user_id"] = user_id

        search = (q or "").strip()
        if search:
            query.update(self._build_order_search_filter(search))

        # Get total count for pagination
        total = await self.collection.count_documents(query)
        
        cursor = self.collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
        orders = await cursor.to_list(length=limit)
        orders = await self._attach_promo_to_orders(orders)
        
        # Convert all ObjectIds to strings for JSON serialization
        serializable_orders = [convert_objectid_to_str(order) for order in orders]
        
        return {
            "items": serializable_orders,
            "total": total,
            "skip": skip,
            "limit": limit,
            "has_more": skip + limit < total
        }

    def _build_order_search_filter(self, q: str) -> dict:
        """Match orders by customer details, tracking, or any line-item product."""
        escaped = re.escape(q.strip())
        regex = {"$regex": escaped, "$options": "i"}
        or_clauses: list[dict] = [
            {"customer_name": regex},
            {"customer_email": regex},
            {"customer_phone": regex},
            {"shipping_address": regex},
            {"tracking_id": regex},
            {"courier_name": regex},
            {"coupon_code": regex},
            {"referral_code": regex},
            {"items.name": regex},
            {"items.product_id": regex},
            {"items.bottle_name": regex},
            {"items.set_items.name": regex},
            {"items.set_items.brand": regex},
            {"items.selected_products.name": regex},
            {"free_decants.name": regex},
        ]
        if ObjectId.is_valid(q):
            or_clauses.append({"_id": ObjectId(q)})
        return {"$or": or_clauses}

    def _orders_query_for_user(self, user_id: str, email: Optional[str] = None) -> dict:
        or_clauses: list[dict] = [{"user_id": user_id}]
        if email and email.strip():
            or_clauses.append({
                "customer_email": {
                    "$regex": f"^{re.escape(email.strip())}$",
                    "$options": "i",
                }
            })
        return {"$or": or_clauses} if len(or_clauses) > 1 else or_clauses[0]

    async def stats_for_user(self, user_id: str, email: Optional[str] = None) -> dict:
        """Order count/total using the same rules as get_for_user."""
        query = self._orders_query_for_user(user_id, email)
        order_count = await self.collection.count_documents(query)
        rows = await self.collection.aggregate([
            {"$match": query},
            {
                "$group": {
                    "_id": None,
                    "order_total": {
                        "$sum": {
                            "$cond": [
                                {"$ne": ["$status", "cancelled"]},
                                "$total_amount",
                                0,
                            ]
                        }
                    },
                }
            },
        ]).to_list(length=1)
        order_total = round(float(rows[0]["order_total"]), 2) if rows else 0.0
        return {"order_count": order_count, "order_total": order_total}

    async def bulk_stats_for_users(self, users: list[dict]) -> dict[str, dict]:
        """Compute order stats for many users in one filtered orders scan."""
        if not users:
            return {}

        stats: dict[str, dict] = {}
        uid_set: set[str] = set()
        email_to_uid: dict[str, str] = {}

        for user in users:
            uid = str(user["_id"])
            uid_set.add(uid)
            stats[uid] = {"order_count": 0, "order_total": 0.0}
            email = (user.get("email") or "").strip().lower()
            if email:
                email_to_uid[email] = uid

        or_clauses: list[dict] = [{"user_id": {"$in": list(uid_set)}}]
        if email_to_uid:
            or_clauses.append({
                "$expr": {
                    "$in": [
                        {"$toLower": {"$ifNull": ["$customer_email", ""]}},
                        list(email_to_uid.keys()),
                    ]
                }
            })
        query = {"$or": or_clauses} if len(or_clauses) > 1 else or_clauses[0]

        cursor = self.collection.find(
            query,
            projection={"user_id": 1, "customer_email": 1, "status": 1, "total_amount": 1},
        )
        async for order in cursor:
            user_id = str(order.get("user_id") or "")
            email_key = (order.get("customer_email") or "").strip().lower()
            targets: set[str] = set()
            if user_id in uid_set:
                targets.add(user_id)
            matched_uid = email_to_uid.get(email_key)
            if matched_uid:
                targets.add(matched_uid)
            for uid in targets:
                stats[uid]["order_count"] += 1
                if order.get("status") != "cancelled":
                    stats[uid]["order_total"] += float(order.get("total_amount") or 0)

        for uid in stats:
            stats[uid]["order_total"] = round(stats[uid]["order_total"], 2)
        return stats

    async def get_for_user(self, user_id: str, email: Optional[str] = None, limit: int = 500):
        """Orders linked by account id and matching checkout email."""
        query = self._orders_query_for_user(user_id, email)
        cursor = self.collection.find(query).sort("created_at", -1)
        orders = await cursor.to_list(length=limit)
        return await self._attach_promo_to_orders(orders)

    async def get_by_id(self, order_id: str):
        return await self.collection.find_one({"_id": ObjectId(order_id)})

    async def update(self, order_id: str, order_in: OrderUpdate):
        old_order = await self.get_by_id(order_id)
        if not old_order:
            return None

        update_data = {k: v for k, v in order_in.dict(exclude_unset=True).items()}

        tracking_touched = "tracking_id" in update_data or "tracking_url" in update_data
        if tracking_touched:
            next_id = update_data.get("tracking_id", old_order.get("tracking_id"))
            next_url = update_data.get("tracking_url", old_order.get("tracking_url"))
            if (next_id or next_url) and not old_order.get("shipped_at"):
                update_data["shipped_at"] = datetime.now(timezone.utc)

        await self.collection.update_one(
            {"_id": ObjectId(order_id)}, {"$set": update_data}
        )

        updated_order = await self.get_by_id(order_id)

        if updated_order:
            old_status = old_order.get("status", "")
            new_status = updated_order.get("status", "")
            old_has_tracking = bool(
                (old_order.get("tracking_id") or "").strip()
                or (old_order.get("tracking_url") or "").strip()
            )
            new_has_tracking = bool(
                (updated_order.get("tracking_id") or "").strip()
                or (updated_order.get("tracking_url") or "").strip()
            )
            tracking_newly_added = new_has_tracking and not old_has_tracking
            status_to_shipped = new_status == "shipped" and old_status != "shipped"

            if new_has_tracking and (status_to_shipped or tracking_newly_added):
                email = updated_order.get("customer_email")
                if email:
                    await self.mail_service.send_shipped_notification(
                        email,
                        updated_order.get("customer_name") or "there",
                        order_id,
                        updated_order,
                    )

            if new_status == "delivered" and old_status != "delivered":
                if not updated_order.get("delivered_at"):
                    # Reorder-reminder depletion math anchors on this — start
                    # the "days of supply" clock from when the fragrance
                    # actually arrived, not from when it was ordered/paid.
                    delivered_at = datetime.now(timezone.utc)
                    await self.collection.update_one(
                        {"_id": ObjectId(order_id)},
                        {"$set": {"delivered_at": delivered_at}},
                    )
                    updated_order["delivered_at"] = delivered_at
                await self.mail_service.send_delivery_notification(
                    updated_order.get("customer_email"),
                    updated_order.get("customer_name"),
                    order_id
                )
                promo_service = PromoSubmissionService(self.collection.database)
                await promo_service.send_promo_invite_if_needed(updated_order, order_id)

        return updated_order

    async def restore_stock(self, items: List[dict]):
        """Reverse of _decrement_stock: add back stock for each line item."""
        for item in items:
            quantity = int(item.get("quantity", 0))
            if quantity <= 0:
                continue

            if item.get("gift_box_id"):
                try:
                    await self.gift_boxes.update_one(
                        {"_id": ObjectId(item["gift_box_id"])},
                        {"$inc": {"stock": quantity}},
                    )
                    for sp in item.get("selected_products") or []:
                        sp_ml = int(sp.get("size_ml", 0))
                        if sp.get("product_id") and sp_ml > 0:
                            await self.products.update_one(
                                {"_id": ObjectId(sp["product_id"])},
                                {"$inc": {"stock_ml": sp_ml * quantity}},
                            )
                except Exception:
                    continue
                continue

            product_id = item.get("product_id")
            if not product_id:
                continue
            try:
                product = await self.products.find_one({"_id": ObjectId(product_id)})
            except Exception:
                product = None

            if product and product.get("product_type") == "set":
                set_size = int(item.get("size_ml") or 0)
                if set_size <= 0:
                    continue
                components = item.get("set_items") or product.get("set_items") or []
                for si in components:
                    comp_id = si.get("product_id")
                    if not comp_id:
                        continue
                    await self.products.update_one(
                        {"_id": ObjectId(comp_id)},
                        {"$inc": {"stock_ml": set_size * quantity}},
                    )
                continue

            size_ml = item.get("size_ml")
            if size_ml is None:
                continue
            try:
                if item.get("is_pack"):
                    await self.products.update_one(
                        {"_id": ObjectId(product_id),
                         "variants.size_ml": int(size_ml),
                         "variants.is_pack": True},
                        {"$inc": {"variants.$.stock": quantity}},
                    )
                else:
                    total_ml = int(size_ml) * quantity
                    await self.products.update_one(
                        {"_id": ObjectId(product_id)},
                        {"$inc": {"stock_ml": total_ml}},
                    )
            except Exception:
                continue

    async def _decrement_stock(self, items: List[dict]):
        for item in items:
            quantity = int(item.get("quantity", 0))
            if quantity <= 0:
                continue

            if item.get("gift_box_id"):
                try:
                    await self.gift_boxes.update_one(
                        {"_id": ObjectId(item["gift_box_id"]), "stock": {"$gte": quantity}},
                        {"$inc": {"stock": -quantity}},
                    )
                    for sp in item.get("selected_products") or []:
                        sp_ml = int(sp.get("size_ml", 0))
                        if sp.get("product_id") and sp_ml > 0:
                            total_ml = sp_ml * quantity
                            await self.products.update_one(
                                {"_id": ObjectId(sp["product_id"]), "stock_ml": {"$gte": total_ml}},
                                {"$inc": {"stock_ml": -total_ml}},
                            )
                except Exception:
                    continue
                continue

            product_id = item.get("product_id")
            if not product_id:
                continue
            try:
                product = await self.products.find_one({"_id": ObjectId(product_id)})
            except Exception:
                product = None

            if product and product.get("product_type") == "set":
                set_size = int(item.get("size_ml") or 0)
                if set_size <= 0:
                    continue
                components = item.get("set_items") or product.get("set_items") or []
                for si in components:
                    comp_id = si.get("product_id")
                    if not comp_id:
                        continue
                    total_ml = set_size * quantity
                    await self.products.update_one(
                        {
                            "_id": ObjectId(comp_id),
                            "stock_ml": {"$gte": total_ml},
                        },
                        {"$inc": {"stock_ml": -total_ml}},
                    )
                continue

            size_ml = item.get("size_ml")
            if size_ml is None:
                continue
            try:
                if item.get("is_pack"):
                    await self.products.update_one(
                        {"_id": ObjectId(product_id),
                         "variants.size_ml": int(size_ml),
                         "variants.is_pack": True,
                         "variants.stock": {"$gte": quantity}},
                        {"$inc": {"variants.$.stock": -quantity}},
                    )
                else:
                    total_ml = int(size_ml) * quantity
                    await self.products.update_one(
                        {"_id": ObjectId(product_id), "stock_ml": {"$gte": total_ml}},
                        {"$inc": {"stock_ml": -total_ml}},
                    )
            except Exception:
                continue

    async def _ensure_stock(self, items: List[dict]):
        for item in items:
            quantity = int(item.get("quantity", 0))
            if quantity <= 0:
                continue

            if item.get("gift_box_id"):
                box = await self.gift_boxes.find_one({"_id": ObjectId(item["gift_box_id"])})
                if not box or int(box.get("stock", 0)) < quantity:
                    raise ValueError("Gift box out of stock.")
                box_ml = int(box.get("size_ml", 0))
                for sp in item.get("selected_products") or []:
                    sp_id = sp.get("product_id")
                    if not sp_id:
                        continue
                    product = await self.products.find_one({"_id": ObjectId(sp_id)})
                    if not product:
                        raise ValueError("Insufficient stock for one or more items.")
                    sp_ml = int(sp.get("size_ml", 0)) or box_ml
                    total_ml = sp_ml * quantity
                    available = int(product.get("stock_ml", 0))
                    if available < total_ml:
                        raise ValueError("Insufficient stock for one or more items.")
                continue

            product_id = item.get("product_id")
            if not product_id:
                continue
            product = await self.products.find_one({"_id": ObjectId(product_id)})
            if not product:
                raise ValueError("Insufficient stock for one or more items.")

            if product.get("product_type") == "set":
                set_size = int(item.get("size_ml") or 0)
                if set_size <= 0:
                    raise ValueError("This set is not configured correctly.")
                components = item.get("set_items") or product.get("set_items") or []
                if not components:
                    raise ValueError("This set is not configured correctly.")
                for si in components:
                    comp_id = si.get("product_id")
                    if not comp_id:
                        raise ValueError("This set is not configured correctly.")
                    comp = await self.products.find_one({"_id": ObjectId(comp_id)})
                    if not comp:
                        raise ValueError("Insufficient stock for one or more items.")
                    total_ml = set_size * quantity
                    available = int(comp.get("stock_ml", 0))
                    if available < total_ml:
                        raise ValueError("Insufficient stock for one or more items in this set.")
                continue

            size_ml = item.get("size_ml")
            if size_ml is None:
                continue

            if item.get("is_pack"):
                variant = next(
                    (v for v in product.get("variants", [])
                     if int(v.get("size_ml", 0)) == int(size_ml) and v.get("is_pack")),
                    None,
                )
                available = int(variant.get("stock", 0)) if variant else 0
                if available < quantity:
                    raise ValueError("Insufficient stock for one or more items.")
            else:
                variant = next(
                    (v for v in product.get("variants", [])
                     if int(v.get("size_ml", 0)) == int(size_ml) and not v.get("is_pack")),
                    None,
                )
                if not variant:
                    raise ValueError("Insufficient stock for one or more items.")
                total_ml = int(size_ml) * quantity
                if product.get("stock_ml") is None:
                    computed = 0
                    for v in product.get("variants", []):
                        try:
                            computed += int(v.get("size_ml", 0)) * int(v.get("stock", 0))
                        except Exception:
                            continue
                    await self.products.update_one(
                        {"_id": ObjectId(product_id)},
                        {"$set": {"stock_ml": computed}},
                    )
                    product["stock_ml"] = computed
                available = int(product.get("stock_ml", 0))
                if available < total_ml:
                    raise ValueError("Insufficient stock for one or more items.")

    async def _validate_free_decants(self, items: List[dict], free_decants: List[dict]):
        offer_service = OfferService(self.products.database)
        offer = await offer_service.get_active_by_type("free_decant")
        if not offer:
            raise ValueError("No active free decant offer.")

        config = offer.get("config", {})
        min_ml = int(config.get("min_qualifying_ml", 10))
        max_free = config.get("max_free_per_order")
        eligible_ids = set(config.get("eligible_product_ids", []))
        qualifying_type = config.get("qualifying_type", "decant")

        qualifying_count = 0
        for it in items:
            if it.get("gift_box_id"):
                continue
            if it.get("product_type") == "set":
                continue
            item_is_pack = bool(it.get("is_pack"))
            if qualifying_type == "decant" and item_is_pack:
                continue
            if qualifying_type == "sealed" and not item_is_pack:
                continue
            if int(it.get("size_ml", 0)) >= min_ml:
                qualifying_count += int(it.get("quantity", 0))

        entitled = qualifying_count
        if max_free is not None:
            entitled = min(entitled, int(max_free))

        if len(free_decants) > entitled:
            raise ValueError(
                f"You are entitled to {entitled} free decant(s), but submitted {len(free_decants)}."
            )

        for fd in free_decants:
            if fd.get("product_id") not in eligible_ids:
                raise ValueError(
                    f"Product {fd.get('product_id')} is not eligible for the free decant offer."
                )

    async def _ensure_free_decant_stock(self, free_decants: List[dict]):
        for fd in free_decants:
            product_id = fd.get("product_id")
            size_ml = int(fd.get("size_ml", 2))
            product = await self.products.find_one({"_id": ObjectId(product_id)})
            if not product:
                raise ValueError("Free decant product not found.")
            available = int(product.get("stock_ml", 0))
            if available < size_ml:
                raise ValueError(f"Insufficient stock for free decant: {fd.get('name', product_id)}")

    async def _decrement_free_decant_stock(self, free_decants: List[dict]):
        for fd in free_decants:
            product_id = fd.get("product_id")
            size_ml = int(fd.get("size_ml", 2))
            try:
                await self.products.update_one(
                    {"_id": ObjectId(product_id), "stock_ml": {"$gte": size_ml}},
                    {"$inc": {"stock_ml": -size_ml}},
                )
            except Exception:
                continue

    async def _restore_free_decant_stock(self, free_decants: List[dict]):
        for fd in free_decants:
            product_id = fd.get("product_id")
            size_ml = int(fd.get("size_ml", 2))
            try:
                await self.products.update_one(
                    {"_id": ObjectId(product_id)},
                    {"$inc": {"stock_ml": size_ml}},
                )
            except Exception:
                continue
