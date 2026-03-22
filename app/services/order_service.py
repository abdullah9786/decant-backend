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

    async def _decrement_stock(self, items: List[dict]):
        for item in items:
            product_id = item.get("product_id")
            size_ml = item.get("size_ml")
            quantity = int(item.get("quantity", 0))
            if not product_id or size_ml is None or quantity <= 0:
                continue
            total_ml = int(size_ml) * quantity
            try:
                await self.products.update_one(
                    {"_id": ObjectId(product_id), "stock_ml": {"$gte": total_ml}},
                    {"$inc": {"stock_ml": -total_ml}},
                )
            except Exception:
                continue

    async def _ensure_stock(self, items: List[dict]):
        for item in items:
            product_id = item.get("product_id")
            size_ml = item.get("size_ml")
            quantity = int(item.get("quantity", 0))
            if not product_id or size_ml is None or quantity <= 0:
                continue
            total_ml = int(size_ml) * quantity
            product = await self.products.find_one({"_id": ObjectId(product_id)})
            if product and product.get("stock_ml") is None:
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
            available = int(product.get("stock_ml", 0)) if product else 0
            if available < total_ml:
                raise ValueError("Insufficient stock for one or more items.")
