from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from app.config.config import settings
from app.integrations.shipping.base import ShippingProviderAdapter, ShippingProviderError

logger = logging.getLogger(__name__)

NIMBUSPOST_BASE_URL = "https://api-v2.nimbuspost.com"

# Mumbai pincode range (400001–400104) → Maharashtra
_MUMBAI_PINCODE_RE = re.compile(r"^400\d{3}$")
# Indian mobile embedded in free text (+91 / 91 prefix optional)
_INDIA_MOBILE_IN_TEXT_RE = re.compile(r"(?:\+?91[\s-]?)?([6-9]\d{9})\b")


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _normalize_india_phone(value: Any) -> int:
    """
    NimbusPost accepts a 10-digit mobile or 12-digit 91-prefixed number.
    Handles str/int/float from MongoDB (floats like 9768188453.0 must not
    become 11 digits via str() → "9768188453.0").
    """
    if value is None or value == "":
        raise ShippingProviderError("Customer phone is required for NimbusPost orders.")

    if isinstance(value, bool):
        raise ShippingProviderError("Invalid customer phone number.")

    if isinstance(value, float):
        if not value.is_integer():
            raise ShippingProviderError("Invalid customer phone number.")
        value = int(value)

    if isinstance(value, int):
        phone_raw = str(value)
    else:
        phone_raw = _digits_only(str(value).strip())

    if phone_raw.startswith("91") and len(phone_raw) == 12:
        phone_raw = phone_raw[2:]
    elif phone_raw.startswith("91") and len(phone_raw) > 12:
        phone_raw = phone_raw[-10:]
    elif len(phone_raw) == 11 and phone_raw.startswith("0"):
        phone_raw = phone_raw[1:]
    elif phone_raw.startswith("91") and len(phone_raw) > 10:
        phone_raw = phone_raw[-10:]

    if not re.fullmatch(r"[6-9]\d{9}", phone_raw):
        raise ShippingProviderError(
            "Customer phone must be a valid 10-digit Indian mobile number "
            f"(got {len(phone_raw)} digits after normalization)."
        )

    return int(phone_raw)


def _try_normalize_india_phone(value: Any) -> Optional[int]:
    try:
        return _normalize_india_phone(value)
    except ShippingProviderError:
        return None


def _extract_mobile_from_text(text: str) -> Optional[int]:
    for match in _INDIA_MOBILE_IN_TEXT_RE.finditer(text or ""):
        phone = _try_normalize_india_phone(match.group(1))
        if phone is not None:
            return phone
    return None


def _phone_candidates_from_order(order: Dict[str, Any]) -> List[Any]:
    candidates: List[Any] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if value is None or value == "":
            return
        key = str(value).strip()
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append(value)

    add(order.get("customer_phone"))
    payment_details = order.get("payment_details") or {}
    for key in ("contact", "phone", "customer_phone"):
        add(payment_details.get(key))
    for field in ("shipping_address", "customer_name"):
        text = order.get(field)
        if isinstance(text, str):
            found = _extract_mobile_from_text(text)
            if found is not None:
                add(found)
    return candidates


def _resolve_customer_phone_from_order(order: Dict[str, Any]) -> Optional[int]:
    for candidate in _phone_candidates_from_order(order):
        phone = _try_normalize_india_phone(candidate)
        if phone is not None:
            return phone
    return None


async def _fetch_razorpay_payment_contact(payment_id: str) -> Optional[int]:
    if not payment_id or not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        return None
    try:
        import razorpay

        client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )
        payment = await asyncio.to_thread(client.payment.fetch, payment_id)
        contact = payment.get("contact")
        return _try_normalize_india_phone(contact)
    except Exception as exc:
        logger.warning("Could not fetch Razorpay payment contact for %s: %s", payment_id, exc)
        return None


async def enrich_order_customer_phone(
    order: Dict[str, Any], db: Any = None
) -> Dict[str, Any]:
    """
    Resolve a valid 10-digit Indian mobile for shipping integrations.
    Some orders store an invalid customer_phone (e.g. room number); fall back
    to payment_details, pending checkout, and Razorpay payment contact.
    """
    order = dict(order)
    phone = _resolve_customer_phone_from_order(order)
    if phone is not None:
        order["customer_phone"] = str(phone)
        return order

    if db is not None:
        payment_details = order.get("payment_details") or {}
        rzp_order_id = payment_details.get("razorpay_order_id")
        if rzp_order_id:
            pending = await db["pending_checkouts"].find_one(
                {"razorpay_order_id": rzp_order_id}
            )
            if pending:
                pending_phone = (pending.get("order_data") or {}).get("customer_phone")
                phone = _try_normalize_india_phone(pending_phone)
                if phone is not None:
                    order["customer_phone"] = str(phone)
                    return order

        rzp_payment_id = payment_details.get("razorpay_payment_id")
        if rzp_payment_id:
            phone = await _fetch_razorpay_payment_contact(rzp_payment_id)
            if phone is not None:
                order["customer_phone"] = str(phone)
                return order

    stored = order.get("customer_phone")
    raise ShippingProviderError(
        "Customer phone must be a valid 10-digit Indian mobile number. "
        f"Stored value: {stored!r}. Update the phone on the order in admin, "
        "or ensure checkout captures a valid mobile number."
    )


def _parse_pincode(address: str) -> Optional[int]:
    matches = re.findall(r"\b(\d{6})\b", address or "")
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def _infer_state(pincode: Optional[int]) -> str:
    if pincode and _MUMBAI_PINCODE_RE.match(str(pincode)):
        return "Maharashtra"
    return settings.NIMBUSPOST_DEFAULT_STATE or "Maharashtra"


def _parse_shipping_address(order: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort parse of Decume's comma-separated shipping_address string."""
    raw = (order.get("shipping_address") or "").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]

    pincode = _parse_pincode(raw)
    state = _infer_state(pincode)

    city = ""
    street_parts: List[str] = []

    if len(parts) >= 3:
        # Common shape: name/line1, line2..., city, pincode or city, state, pincode
        last = parts[-1]
        if re.fullmatch(r"\d{6}", last):
            if len(parts) >= 2:
                city = parts[-2]
            street_parts = parts[:-2] if len(parts) > 2 else parts[:1]
        else:
            city = parts[-1]
            street_parts = parts[:-1]
    elif len(parts) == 2:
        street_parts = [parts[0]]
        city = parts[1]
    elif len(parts) == 1:
        street_parts = parts
    else:
        street_parts = []

    name = (order.get("customer_name") or "").strip()
    if not name and street_parts:
        name = street_parts[0]
    if not name:
        name = "Customer"

    # If the first street part equals the customer name, drop it from address lines.
    if street_parts and street_parts[0].lower() == name.lower():
        street_parts = street_parts[1:]

    # Send the full, unmodified shipping_address string as the address line —
    # identical to what's shown in Decume admin's "Shipping Address" section,
    # including city/pincode. NimbusPost also gets city/pincode/state as their
    # own structured fields below (required for routing), but the free-text
    # `address` line should not have anything trimmed out of it.
    address_line = raw or "Address not provided"

    phone = _resolve_customer_phone_from_order(order)
    if phone is None:
        phone = _normalize_india_phone(order.get("customer_phone"))

    if not pincode:
        raise ShippingProviderError(
            "Could not parse a 6-digit pincode from the shipping address."
        )

    if not city:
        city = "Unknown"

    shipping: Dict[str, Any] = {
        "name": name[:100],
        "address": address_line[:200],
        "pincode": pincode,
        "city": city[:100],
        "state": state[:100],
        "phone": phone,
    }
    email = (order.get("customer_email") or "").strip()
    if email:
        shipping["email"] = email[:200]
    return shipping


def _items_subtotal(order: Dict[str, Any]) -> float:
    total = 0.0
    for item in order.get("items") or []:
        if item.get("status") == "cancelled":
            continue
        qty = int(item.get("quantity") or 0)
        if qty <= 0:
            continue
        total += float(item.get("price") or 0) * qty
    return round(total, 2)


def _derive_org_charges(order: Dict[str, Any]) -> Dict[str, float]:
    """
    NimbusPost validates: items subtotal + org_charges ≈ order total / collectable.
    Decume checkout adds shipping (₹90) and COD fee (₹49) on top of line items.
    """
    items_subtotal = _items_subtotal(order)
    discount = float(order.get("discount_amount") or 0)
    order_total = float(order.get("total_amount") or 0)
    payment_method = (order.get("payment_method") or "prepaid").lower()

    cod_fee = 0.0
    if payment_method == "cod":
        cod_fee = float(order.get("cod_fee") if order.get("cod_fee") is not None else settings.COD_FEE)

    # total = items - discount + shipping + cod_fee
    shipping_fee = round(order_total - items_subtotal + discount - cod_fee, 2)
    if shipping_fee < 0:
        shipping_fee = 0.0

    org_charges: Dict[str, float] = {}
    if shipping_fee > 0:
        org_charges["shipping_charges"] = shipping_fee
    if cod_fee > 0:
        org_charges["cod_charges"] = cod_fee
    if discount > 0:
        org_charges["discount_amount"] = discount
    return org_charges


def _map_items(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for item in order.get("items") or []:
        if item.get("status") == "cancelled":
            continue
        qty = int(item.get("quantity") or 0)
        if qty <= 0:
            continue
        items.append({
            "name": str(item.get("name") or "Product")[:200],
            "qty": qty,
            "price": float(item.get("price") or 0),
            "sku": str(item.get("product_id") or item.get("name") or "sku")[:100],
        })
    if not items:
        raise ShippingProviderError("Order has no shippable line items.")
    return items


def _build_payload(order: Dict[str, Any], order_id: str) -> Dict[str, Any]:
    if not settings.NIMBUSPOST_WAREHOUSE_ID:
        raise ShippingProviderError("NIMBUSPOST_WAREHOUSE_ID is not configured.")

    payment_method = (order.get("payment_method") or "prepaid").lower()
    payment_mode = "cod" if payment_method == "cod" else "prepaid"

    payload: Dict[str, Any] = {
        "order_number": str(order_id),
        "order_type": "b2c",
        "payment_mode": payment_mode,
        "warehouse_id": settings.NIMBUSPOST_WAREHOUSE_ID,
        "shipping_address": _parse_shipping_address(order),
        "items": _map_items(order),
        "package": {
            # NimbusPost's docs say package.weight is in grams, but the live
            # API actually validates it as kilograms (a 250 g parcel sent as
            # "250" was read as 250 kg, blowing past the 32 kg limit). Convert
            # our gram-based config to kg to match observed behavior.
            "weight": round(settings.NIMBUSPOST_DEFAULT_WEIGHT_G / 1000.0, 3),
            "length": settings.NIMBUSPOST_DEFAULT_LENGTH_CM,
            "width": settings.NIMBUSPOST_DEFAULT_WIDTH_CM,
            "height": settings.NIMBUSPOST_DEFAULT_HEIGHT_CM,
        },
    }

    org_charges = _derive_org_charges(order)
    if org_charges:
        payload["org_charges"] = org_charges

    if payment_mode == "cod":
        payload["order_collectable_amount"] = float(order.get("total_amount") or 0)

    return payload


def _extract_external_order_id(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    for key in ("order_id", "id", "external_order_id", "np_order_id"):
        val = data.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    nested = data.get("data")
    if isinstance(nested, dict):
        return _extract_external_order_id(nested)
    if isinstance(nested, list) and nested and isinstance(nested[0], dict):
        return _extract_external_order_id(nested[0])
    return None


class NimbusPostAdapter(ShippingProviderAdapter):
    provider_id = "nimbuspost"

    async def create_order(self, order: Dict[str, Any], order_id: str) -> Dict[str, Any]:
        if not settings.NIMBUSPOST_API_KEY or not settings.NIMBUSPOST_API_SECRET:
            raise ShippingProviderError("NimbusPost API credentials are not configured.")

        payload = _build_payload(order, order_id)
        headers = {
            "x-api-key": settings.NIMBUSPOST_API_KEY,
            "x-api-secret": settings.NIMBUSPOST_API_SECRET,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{NIMBUSPOST_BASE_URL}/v2/orders",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            logger.warning("NimbusPost request failed: %s", exc)
            raise ShippingProviderError(f"NimbusPost request failed: {exc}") from exc

        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text}

        if response.status_code >= 400:
            message = body.get("message") if isinstance(body, dict) else None
            if not message and isinstance(body, dict):
                message = body.get("error") or body.get("detail")
            raise ShippingProviderError(
                message or f"NimbusPost API error (HTTP {response.status_code})",
                status_code=response.status_code,
                response=body,
            )

        external_id = _extract_external_order_id(body if isinstance(body, dict) else {})
        if not external_id and isinstance(body, dict):
            external_id = str(body.get("order_number") or order_id)

        return {
            "external_order_id": external_id or str(order_id),
            "order_number": str(order_id),
            "status": "created",
            "raw_response": body,
        }


# ── Webhook helpers ──────────────────────────────────────────────────────
#
# Per NimbusPost's Webhooks API reference (WebhookEventPayload schema), every
# delivery — for order.created, order.updated, and tracking.updated alike —
# is a single FLAT JSON object with a fixed 23-key set. Unpopulated keys are
# sent as "" (never omitted, never null). No `data` wrapper, no nested
# `shipment`/`latest` objects.
#
# Relevant keys: `order_number` (echoes what we sent as order_number at
# create — our Decume order `_id`), `awb_number`, `courier_name`, and
# `status` — a space-separated literal: "created" | "shipped" | "in transit"
# | "delivered" | "rto initiated" | "cancelled". (`order_id` is NimbusPost's
# own internal id, not ours — never use it to look up the Decume order.)
#
# `order.updated` is supposed to set `status: "shipped"` exactly on the
# booked→shipped (picked up) transition — but in practice this order-level
# transition can lag behind or never fire cleanly for some couriers: we've
# observed a shipment sitting at NimbusPost's own `orderStatus: "booked"`
# for 13+ hours while its courier tracking already showed "in transit"
# scans. So we also subscribe to `tracking.updated` (fired once per real
# courier scan) as a fallback "shipped" signal — if a scan event exists at
# all, the parcel has physically been picked up, regardless of whether
# NimbusPost's order-level status has caught up yet. `tracking.updated` can
# also report `status: "delivered"`, which we still route to "delivered".
#
# Per NimbusPost's docs, `order.updated`'s `status` is one of: booked,
# shipped, delivered, cancelled, rto initiated (confirmed via a real prod
# payload: booking sends `status: "booked"`, which we deliberately don't
# map — nothing physical has happened yet at that point).
_ORDER_UPDATED_STATUS_MAP = {
    "shipped": "shipped",
    "delivered": "delivered",
}

# `tracking.updated`'s `status` is one of: created, shipped, in transit,
# delivered, rto initiated, cancelled (per NimbusPost's docs). "created" is
# the pre-pickup bucket — it fires the moment the shipment is scheduled/
# booked, before the courier has touched it — so it must NOT be treated as
# a "shipped" signal, or every order flips to "shipped" the instant you
# create the shipment instead of when it's actually picked up.
_TRACKING_IGNORED_STATUSES = {"created", "cancelled", "rto initiated", ""}


def extract_webhook_order_number(payload: Dict[str, Any]) -> Optional[str]:
    """Find the Decume order id in a NimbusPost webhook payload."""
    val = payload.get("order_number")
    if val is not None and str(val).strip():
        return str(val).strip()
    return None


def map_webhook_status(event: str, payload: Dict[str, Any]) -> Optional[str]:
    """Map a webhook payload's `status` to Decume's internal order status
    (`shipped` or `delivered`), or None if it should be ignored, based on
    which event (`order.updated` vs `tracking.updated`) delivered it."""
    status = str(payload.get("status") or "").strip().lower()
    if event == "order.updated":
        return _ORDER_UPDATED_STATUS_MAP.get(status)
    if event == "tracking.updated":
        if status == "delivered":
            return "delivered"
        if status in _TRACKING_IGNORED_STATUSES:
            return None
        # Any other real scan (in transit / out for delivery / picked up /
        # shipped / ...) — the mere existence of a scan means pickup happened.
        return "shipped"
    return None


def extract_webhook_shipment_info(payload: Dict[str, Any]) -> Dict[str, str]:
    """Pull AWB / courier name off a webhook payload for `tracking_id` /
    `courier_name` on the Decume order, if present."""
    info: Dict[str, str] = {}
    awb = payload.get("awb_number")
    courier = payload.get("courier_name")
    if awb:
        info["tracking_id"] = str(awb)
    if courier:
        info["courier_name"] = str(courier)
    return info
