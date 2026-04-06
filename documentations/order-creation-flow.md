# Order Creation Flow — Complete Technical Documentation

## Table of Contents

1. [Overview](#overview)
2. [Architecture Diagram](#architecture-diagram)
3. [MongoDB Collections Involved](#mongodb-collections-involved)
4. [Step-by-Step Flow](#step-by-step-flow)
5. [Dual-Path Order Creation (Idempotency)](#dual-path-order-creation-idempotency)
6. [Webhook Details](#webhook-details)
7. [Post-Order Side Effects](#post-order-side-effects)
8. [Error Handling & Edge Cases](#error-handling--edge-cases)
9. [Frontend UX States](#frontend-ux-states)
10. [Environment Variables](#environment-variables)
11. [API Reference](#api-reference)
12. [Razorpay Dashboard Setup](#razorpay-dashboard-setup)

---

## Overview

The Decume order creation flow uses a **dual-path architecture** to guarantee that every captured Razorpay payment results in a created order — even if the user closes their browser tab mid-checkout.

**Two paths can create an order:**

| Path | Trigger | Speed |
|---|---|---|
| **Client-side** (`verify-and-create`) | Razorpay success callback in browser | Immediate (~1s) |
| **Server-side** (Razorpay webhook) | Razorpay sends `payment.captured` event | Async (~5-30s) |

Both paths are **idempotent** — whichever arrives first creates the order; the second detects it already exists and skips.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          FRONTEND (Next.js)                         │
│                                                                     │
│  ┌───────────┐    ┌──────────────────┐    ┌─────────────────────┐  │
│  │  Step 1:  │───>│  Step 2: Payment  │───>│  Step 4: Confirming │  │
│  │  Address  │    │  (Place Order)    │    │  "Please wait..."   │  │
│  └───────────┘    └────────┬─────────┘    └──────────┬──────────┘  │
│                            │                         │              │
│                            ▼                         ▼              │
│                   ┌────────────────┐        ┌────────────────┐     │
│                   │ initiate-      │        │ verify-and-    │     │
│                   │ payment-only   │        │ create (POST)  │     │
│                   └────────┬───────┘        └───────┬────────┘     │
│                            │                        │               │
└────────────────────────────┼────────────────────────┼───────────────┘
                             │                        │
                             ▼                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         BACKEND (FastAPI)                           │
│                                                                     │
│  ┌──────────────────────────────┐   ┌─────────────────────────┐    │
│  │  POST /initiate-payment-only │   │  POST /verify-and-create│    │
│  │                              │   │                         │    │
│  │  1. Validate stock           │   │  1. Verify signature    │    │
│  │  2. Create Razorpay order    │   │  2. Idempotency check   │    │
│  │  3. Save pending_checkout ◄──┼───┤  3. Prepare order data  │    │
│  │                              │   │  4. Create order in DB  │    │
│  └──────────────┬───────────────┘   │  5. Side-effects        │    │
│                 │                    │  6. Delete pending_     │    │
│                 │                    │     checkout             │    │
│                 │                    └────────────┬────────────┘    │
│                 │                                 │                  │
│                 ▼                                 │                  │
│  ┌──────────────────────────────┐                │                  │
│  │     Razorpay API             │                │                  │
│  │  (creates rzp order,         │                │                  │
│  │   captures payment)          │                │                  │
│  └──────────────┬───────────────┘                │                  │
│                 │                                 │                  │
│                 ▼                                 │                  │
│  ┌──────────────────────────────┐                │                  │
│  │ POST /webhook/razorpay       │                │                  │
│  │  (SAFETY NET)                │                │                  │
│  │                              │                │                  │
│  │  1. Verify HMAC signature    │                │                  │
│  │  2. Idempotency check ───────┼────────────────┘                  │
│  │  3. Load pending_checkout    │                                   │
│  │  4. Create order in DB       │                                   │
│  │  5. Side-effects             │                                   │
│  │  6. Delete pending_checkout  │                                   │
│  └──────────────────────────────┘                                   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

                        ┌──────────────────┐
                        │   MongoDB         │
                        │                  │
                        │  orders          │  ← Final orders
                        │  pending_        │  ← Temp checkout sessions
                        │    checkouts     │     (TTL: 24h)
                        │  products        │  ← Stock management
                        │  commissions     │  ← Influencer earnings
                        │  coupons         │  ← Discount codes
                        └──────────────────┘
```

---

## MongoDB Collections Involved

| Collection | Purpose | Key Fields |
|---|---|---|
| `orders` | Final placed orders | `_id`, `payment_details.razorpay_order_id`, `status`, `payment_status`, `items`, `total_amount` |
| `pending_checkouts` | Temporary checkout session for webhook fallback | `razorpay_order_id` (unique index), `order_data`, `created_at` (TTL 24h) |
| `products` | Product catalog & stock | `_id`, `stock_ml`, `variants` |
| `commissions` | Influencer commission records | `influencer_id`, `order_id`, `commission_amount`, `status` |
| `coupons` | Discount codes linked to influencers | `code`, `influencer_id`, `discount_percent` |

### Indexes on `pending_checkouts`

| Field | Type | Purpose |
|---|---|---|
| `razorpay_order_id` | Unique | Fast lookup + prevent duplicates |
| `created_at` | TTL (86400s) | Auto-delete stale records after 24 hours |

---

## Step-by-Step Flow

### Phase 1: Checkout Initiation (Frontend)

```
User fills shipping address → clicks "Place Order"
```

**Frontend (`checkout/page.tsx`):**

1. Build `orderData` object with:
   - `user_id` (authenticated user ID or `"guest"`)
   - `customer_name`, `customer_email`, `customer_phone`
   - `items[]` (product_id, name, size_ml, price, quantity)
   - `total_amount` (subtotal + shipping - discount)
   - `shipping_address` (formatted string)
   - `influencer_id` (from `decume-ref` localStorage or coupon)
   - `coupon_code`, `discount_amount` (if applied)

2. Build `stockCheckItems[]` (minimal: product_id, size_ml, quantity)

3. Call `POST /api/v1/orders/initiate-payment-only` with:
   ```json
   {
     "amount": 1299,
     "items": [{ "product_id": "...", "size_ml": 10, "quantity": 2 }],
     "order_data": { ...full orderData object... }
   }
   ```

### Phase 2: Stock Validation & Razorpay Order (Backend)

**Endpoint:** `POST /orders/initiate-payment-only`
**Auth:** None required
**File:** `backend/app/routers/orders.py`

1. **Validate stock** for each line item:
   - For each item, compute `total_ml = size_ml × quantity`
   - Look up product's `stock_ml`
   - If `stock_ml` is null, compute from variants and backfill
   - If `available < total_ml` → raise `400: "Insufficient stock for one or more items."`

2. **Create Razorpay order** via Razorpay API:
   - Amount in paise (`amount × 100`)
   - Currency: INR
   - Auto-capture enabled (`payment_capture: 1`)
   - Returns `razorpay_order_id`

3. **Persist pending checkout** in `pending_checkouts` collection:
   ```json
   {
     "razorpay_order_id": "order_NxYz...",
     "order_data": { ...full cart, address, referral, coupon... },
     "created_at": "2026-04-06T10:30:00Z"
   }
   ```

4. **Return** Razorpay order details to frontend:
   ```json
   {
     "id": "order_NxYz...",
     "entity": "order",
     "amount": 129900,
     "currency": "INR",
     "receipt": "pre_1712400600",
     "status": "created"
   }
   ```

### Phase 3: Payment (Razorpay Checkout)

**Frontend:**

1. Fire GA4 `begin_checkout` event
2. Open Razorpay checkout popup with:
   - `order_id` from Phase 2
   - Customer prefill (name, email, phone)
   - Theme color: `#022c22`

3. User completes payment via Card / UPI / Netbanking

4. **Razorpay captures payment** (auto-capture is ON)

5. Razorpay fires the `handler` callback with:
   ```json
   {
     "razorpay_order_id": "order_NxYz...",
     "razorpay_payment_id": "pay_AbCd...",
     "razorpay_signature": "hmac_sha256_signature"
   }
   ```

### Phase 4a: Client-Side Order Creation (Primary Path)

**Frontend immediately:**
- Sets UI to **Step 4** ("Confirming your order... Please do not close this page.")
- Calls `POST /api/v1/orders/verify-and-create`

**Endpoint:** `POST /orders/verify-and-create`
**Auth:** Optional (attaches user_id if logged in)
**File:** `backend/app/routers/orders.py`

1. **Verify Razorpay signature** using `razorpay.utility.verify_payment_signature()`
   - Failure → `400: "Signature verification failed"`

2. **Idempotency check** — query `orders` collection:
   ```python
   db["orders"].find_one({
       "payment_details.razorpay_order_id": razorpay_order_id
   })
   ```
   - If found → return existing order (webhook beat us)

3. **Prepare order data:**
   - Override `user_id` from JWT token if authenticated
   - Set `payment_status = "paid"`, `status = "processing"`
   - Attach payment details with `paid_at` timestamp

4. **Coupon → influencer attribution:**
   - If `coupon_code` present but no `influencer_id`, validate coupon and link influencer

5. **Create order** via `OrderService.create()`:
   - Re-validate stock (final check)
   - Insert into `orders` collection
   - Decrement `stock_ml` for each item

6. **Run side-effects** (see [Post-Order Side Effects](#post-order-side-effects))

7. **Delete pending checkout** record (no longer needed)

8. **Return** created order to frontend

**Frontend on success:**
- Sets UI to **Step 3** (Order Confirmed!)
- Fires GA4 `purchase` event
- Clears cart
- Removes `decume-ref` from localStorage

### Phase 4b: Webhook Order Creation (Safety Net)

**Trigger:** Razorpay sends HTTP POST to your webhook URL after payment capture
**Timing:** Usually 5–30 seconds after payment, but can be delayed

**Endpoint:** `POST /orders/webhook/razorpay`
**Auth:** None (verified via HMAC signature)
**File:** `backend/app/routers/orders.py`

1. **Read raw request body** and `X-Razorpay-Signature` header

2. **Verify HMAC-SHA256 signature** (if `RAZORPAY_WEBHOOK_SECRET` is set):
   ```python
   expected = hmac.new(
       RAZORPAY_WEBHOOK_SECRET.encode(),
       body,
       hashlib.sha256,
   ).hexdigest()
   hmac.compare_digest(expected, signature)
   ```
   - Mismatch → `400: "Invalid webhook signature"`

3. **Parse event** — only process `payment.captured` or `order.paid`

4. **Extract IDs** from payload:
   ```
   payload.payload.payment.entity.id       → razorpay_payment_id
   payload.payload.payment.entity.order_id → razorpay_order_id
   ```

5. **Idempotency check** — same as verify-and-create:
   - If order already exists → return `{"ok": true, "already_created": "..."}`

6. **Load pending checkout** from `pending_checkouts` collection:
   - If not found → return `{"ok": true, "skipped": "no_pending_checkout"}`

7. **Coupon → influencer attribution** (same logic as client path)

8. **Stamp payment details:**
   ```json
   {
     "razorpay_order_id": "order_NxYz...",
     "razorpay_payment_id": "pay_AbCd...",
     "paid_at": "2026-04-06T10:30:15Z",
     "source": "webhook"
   }
   ```
   Note: `"source": "webhook"` distinguishes webhook-created orders from client-created ones.

9. **Create order** via `OrderService.create()` (stock check + decrement)

10. **Run side-effects** (commission, coupon, email)

11. **Delete pending checkout** record

12. **Return** `{"ok": true, "order_id": "..."}`

---

## Dual-Path Order Creation (Idempotency)

```
                    Payment Captured
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
     verify-and-create         Webhook
              │                     │
              ▼                     ▼
     ┌─ Order exists? ─┐   ┌─ Order exists? ─┐
     │  NO      YES     │   │  NO      YES     │
     ▼          ▼       │   ▼          ▼       │
   Create    Return     │ Create    Return     │
   Order     Existing   │ Order    {"already   │
     │                  │   │       created"}  │
     ▼                  │   ▼                  │
   Side-effects         │ Side-effects         │
     │                  │   │                  │
     ▼                  │   ▼                  │
   Delete               │ Delete               │
   pending_checkout     │ pending_checkout     │
     │                  │   │                  │
     ▼                  │   ▼                  │
   DONE ◄──────────────┘ DONE ◄──────────────┘
```

**Key guarantee:** The lookup `orders.find_one({"payment_details.razorpay_order_id": X})` is the single source of truth. Since `razorpay_order_id` is unique per payment session, only one order can ever be created per payment.

### Race condition handling

If both paths run simultaneously, MongoDB's `insert_one` will succeed for the first writer. The second path's earlier `find_one` might return `null`, but the subsequent `insert_one` would create a near-duplicate. In practice, this is extremely unlikely because:
- The client fires immediately (~1s), while the webhook arrives ~5-30s later
- If you want belt-and-suspenders protection, add a unique index on `payment_details.razorpay_order_id`

---

## Webhook Details

### Razorpay Webhook Payload Structure

```json
{
  "event": "payment.captured",
  "payload": {
    "payment": {
      "entity": {
        "id": "pay_AbCd1234567890",
        "entity": "payment",
        "amount": 129900,
        "currency": "INR",
        "status": "captured",
        "order_id": "order_NxYz9876543210",
        "method": "upi",
        "email": "user@example.com",
        "contact": "+919876543210",
        "created_at": 1712400600
      }
    }
  }
}
```

### Retry Behavior

Razorpay automatically retries failed webhook deliveries:
- Retries over ~24 hours with exponential backoff
- Your endpoint must return `2xx` status for Razorpay to consider it delivered
- Non-2xx responses or timeouts trigger retries

### Signature Verification

```
HMAC-SHA256(webhook_secret, raw_request_body) == X-Razorpay-Signature header
```

When `RAZORPAY_WEBHOOK_SECRET` is empty (local dev), signature check is skipped.

---

## Post-Order Side Effects

Both order creation paths call `_post_order_side_effects()`:

```
Order Created
     │
     ├── 1. Commission Creation (if influencer referral)
     │       ├── Check influencer profile exists & is active
     │       ├── Block self-referral (buyer == influencer)
     │       ├── Calculate: order_total × commission_rate (default 10%)
     │       └── Insert into `commissions` collection (status: "pending")
     │
     ├── 2. Coupon Usage (if coupon applied)
     │       └── Increment usage count in `coupons` collection
     │
     └── 3. Email Notifications
             ├── Order confirmation email → customer
             └── New order alert email → admin
```

All side-effects are **non-blocking** — failures are logged but do not prevent order creation.

---

## Error Handling & Edge Cases

### Stock Runs Out Between Initiation and Creation

```
initiate-payment-only     Razorpay Payment     verify-and-create
    ✓ Stock OK ──────────> ✓ Captured ────────> ✗ Stock Gone!
                                                     │
                                                     ▼
                                              Automatic refund
                                              via Razorpay API
                                                     │
                                                     ▼
                                              "Some items are no longer
                                               in stock. Your payment
                                               has been refunded."
```

If the refund API also fails, the user sees a message with their `payment_id` to contact support.

### User Closes Tab After Payment

```
Razorpay Popup: "Success!"
     │
     ▼
User closes browser tab ──────> verify-and-create NEVER fires
     │
     │  (5-30 seconds later)
     ▼
Razorpay webhook fires ──────> Order created from pending_checkout
     │
     ▼
Commission + Email sent
```

### User Stays on Page (Normal Flow)

```
Razorpay Popup: "Success!"
     │
     ▼
UI: "Confirming your order..."  ──────> verify-and-create succeeds
     │
     ▼
UI: "Order Confirmed!"
     │
     │  (5-30 seconds later)
     ▼
Webhook arrives ──────> Sees order exists ──────> Skips (idempotent)
```

### Razorpay Popup Dismissed (No Payment)

```
User clicks X on Razorpay popup
     │
     ▼
ondismiss callback ──────> setLoading(false)
     │
     ▼
No payment captured, no webhook, no order
pending_checkout auto-deleted after 24h (TTL)
```

### verify-and-create Fails (Network Error)

```
verify-and-create ──────> Network error / 500
     │
     ▼
Frontend shows alert: "Payment verification failed..."
UI falls back to Step 2
     │
     │  (but payment IS captured)
     ▼
Webhook fires ──────> Creates order from pending_checkout
```

---

## Frontend UX States

| Step | UI | Trigger |
|---|---|---|
| **1** | Shipping address form + coupon input | Initial state |
| **2** | Payment method selector + "Place Order" button | After address submitted |
| **4** | Spinner + "Confirming your order..." + "Please do not close this page" | Immediately after Razorpay success callback |
| **3** | Green checkmark + "Order Confirmed!" + Track Order link | After verify-and-create returns |

Step 4 is critical — it prevents the user from navigating away during the verify-and-create API call.

---

## Environment Variables

| Variable | File | Required | Description |
|---|---|---|---|
| `RAZORPAY_KEY_ID` | `backend/.env` | Yes | Razorpay API key (test or live) |
| `RAZORPAY_KEY_SECRET` | `backend/.env` | Yes | Razorpay API secret |
| `RAZORPAY_WEBHOOK_SECRET` | `backend/.env` | For production | Webhook signature verification secret |
| `NEXT_PUBLIC_RAZORPAY_KEY_ID` | `frontend-user/.env.local` | Yes | Public Razorpay key for checkout popup |

---

## API Reference

### POST /orders/initiate-payment-only

**Purpose:** Validate stock, create Razorpay order, persist checkout session.

**Request Body:**
```json
{
  "amount": 1299.00,
  "items": [
    { "product_id": "665a...", "size_ml": 10, "quantity": 2 }
  ],
  "order_data": {
    "user_id": "665b...",
    "customer_name": "John Doe",
    "customer_email": "john@example.com",
    "customer_phone": "9876543210",
    "items": [
      { "product_id": "665a...", "name": "Sauvage", "size_ml": 10, "price": 649, "quantity": 2 }
    ],
    "total_amount": 1299,
    "shipping_address": "John Doe, 2nd Floor, ABC Building, MG Road, Mumbai, 400001",
    "status": "pending",
    "influencer_id": "665c...",
    "coupon_code": "SAVE10",
    "discount_amount": 130
  }
}
```

**Success Response (200):**
```json
{
  "id": "order_NxYz9876543210",
  "entity": "order",
  "amount": 129900,
  "currency": "INR",
  "receipt": "pre_1712400600",
  "status": "created"
}
```

**Error Responses:**
- `400` — Insufficient stock
- `500` — Razorpay API error

---

### POST /orders/verify-and-create

**Purpose:** Verify payment signature, create order idempotently, run side-effects.

**Auth:** Optional JWT (attaches user_id if present)

**Request Body:**
```json
{
  "payment_details": {
    "razorpay_order_id": "order_NxYz...",
    "razorpay_payment_id": "pay_AbCd...",
    "razorpay_signature": "hmac..."
  },
  "order_data": {
    "user_id": "665b...",
    "customer_name": "John Doe",
    "items": [ ... ],
    "total_amount": 1299,
    "shipping_address": "...",
    "status": "pending"
  }
}
```

**Success Response (200):** Full `OrderOut` object.

**Error Responses:**
- `400` — Signature verification failed
- `400` — Insufficient stock (auto-refund attempted)
- `500` — Order creation failed

---

### POST /orders/webhook/razorpay

**Purpose:** Razorpay-initiated safety net for order creation.

**Auth:** HMAC-SHA256 signature via `X-Razorpay-Signature` header.

**Request Body:** Raw Razorpay webhook payload (see [Webhook Details](#webhook-details)).

**Responses:**
```json
{ "ok": true, "order_id": "665d..." }           // Order created
{ "ok": true, "already_created": "665d..." }     // Idempotent skip
{ "ok": true, "skipped": "no_pending_checkout" } // No session found
{ "ok": true, "skipped": "payment.authorized" }  // Unhandled event
{ "ok": false, "error": "Insufficient stock..." }// Creation failed
```

---

## Razorpay Dashboard Setup

### Test Mode

1. Go to [Razorpay Dashboard](https://dashboard.razorpay.com) → toggle to **Test Mode**
2. Navigate to **Settings → Webhooks → Add New Webhook**
3. **Webhook URL:** `https://<your-ngrok-or-domain>/api/v1/orders/webhook/razorpay`
4. **Active Events:** Select `payment.captured` and `order.paid`
5. **Secret:** Copy the generated secret → paste into `RAZORPAY_WEBHOOK_SECRET` in `.env`

### Live Mode

Same steps, but in Live Mode. Use your production domain URL.

### Test Card Numbers

| Card Number | Type |
|---|---|
| `4111 1111 1111 1111` | Visa (success) |
| `5267 3181 8797 5449` | Mastercard (success) |

Expiry: any future date. CVV: any 3 digits. OTP: any value in test mode.
