from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.order import OrderCreate, OrderUpdate
from bson import ObjectId
from typing import List
from datetime import datetime
import razorpay
from app.config.config import settings
from app.services.mail_service import MailService

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

    async def create(self, order_in: OrderCreate):
        order_dict = order_in.dict()
        order_dict["created_at"] = order_dict.get("created_at") or datetime.utcnow()
        await self._ensure_stock(order_dict.get("items", []))
        result = await self.collection.insert_one(order_dict)
        await self._decrement_stock(order_dict.get("items", []))
        return await self.collection.find_one({"_id": result.inserted_id})

    async def get_all(self, user_id: str = None):
        query = {}
        if user_id:
            query["user_id"] = user_id
        cursor = self.collection.find(query)
        return await cursor.to_list(length=100)

    async def get_by_id(self, order_id: str):
        return await self.collection.find_one({"_id": ObjectId(order_id)})

    async def update(self, order_id: str, order_in: OrderUpdate):
        # Detect status change for email
        old_order = await self.get_by_id(order_id)
        
        update_data = {k: v for k, v in order_in.dict(exclude_unset=True).items()}
        await self.collection.update_one(
            {"_id": ObjectId(order_id)}, {"$set": update_data}
        )
        
        updated_order = await self.get_by_id(order_id)
        
        # If status changed to delivered, send email
        if updated_order and updated_order.get("status") == "delivered" and old_order.get("status") != "delivered":
            await self.mail_service.send_delivery_notification(
                updated_order.get("customer_email"),
                updated_order.get("customer_name"),
                order_id
            )
            
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
            else:
                product_id = item.get("product_id")
                size_ml = item.get("size_ml")
                if not product_id or size_ml is None:
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
            else:
                product_id = item.get("product_id")
                size_ml = item.get("size_ml")
                if not product_id or size_ml is None:
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
            size_ml = item.get("size_ml")
            if not product_id or size_ml is None:
                continue
            product = await self.products.find_one({"_id": ObjectId(product_id)})
            if not product:
                raise ValueError("Insufficient stock for one or more items.")

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
