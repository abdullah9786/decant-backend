from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from app.integrations.shipping import ShippingProviderError, get_shipping_provider
from app.integrations.shipping.nimbuspost import enrich_order_customer_phone


class ShippingService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.orders = db["orders"]

    async def create_external_order(
        self, order_id: str, provider: str
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Create an order in an external shipping provider.

        Returns (integration_record, created) where created is False when
        the order was already synced (idempotent).
        """
        order = await self.orders.find_one({"_id": ObjectId(order_id)})
        if not order:
            raise ValueError("Order not found")

        provider_key = provider.lower()
        integrations = dict(order.get("shipping_integrations") or {})
        existing = integrations.get(provider_key)
        if existing and existing.get("external_order_id"):
            return existing, False

        if order.get("status") == "cancelled":
            raise ValueError("Cannot create a shipping order for a cancelled order.")

        adapter = get_shipping_provider(provider_key)

        try:
            order = await enrich_order_customer_phone(order, self.db)
            result = await adapter.create_order(order, order_id)
        except ShippingProviderError as exc:
            error_record = {
                "order_number": str(order_id),
                "status": "error",
                "created_at": datetime.now(timezone.utc),
                "last_error": str(exc),
            }
            await self.orders.update_one(
                {"_id": ObjectId(order_id)},
                {"$set": {f"shipping_integrations.{provider_key}": error_record}},
            )
            raise

        record = {
            "external_order_id": result.get("external_order_id"),
            "order_number": result.get("order_number") or str(order_id),
            "status": result.get("status") or "created",
            "created_at": datetime.now(timezone.utc),
        }
        await self.orders.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {f"shipping_integrations.{provider_key}": record}},
        )
        return record, True
