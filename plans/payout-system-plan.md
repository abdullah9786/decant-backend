# Influencer Payout System — Detailed Plan

## Current State (What's Implemented)

### Commission Lifecycle

1. A customer visits an influencer's storefront (`decume.in/{username}`)
2. A referral is stored in `localStorage` with the influencer's ID and a 7-day expiry
3. When the customer places an order, the `influencer_id` is attached to the order
4. A **commission record** is created in the `commissions` collection with status `"pending"` and amount = 10% of order total
5. When the order status is updated to `"delivered"`, the commission auto-moves to `"approved"`
6. If the order is `"cancelled"` or `"refunded"`, the commission auto-moves to `"cancelled"`

### Payout Flow (Admin-Triggered, Manual)

1. Admin opens the Payouts page in the admin dashboard
2. Each influencer is listed with their **Approved (ready)** and **Total Paid** amounts
3. Admin clicks **"Create Payout"** which:
   - Collects all `"approved"` commissions for that influencer
   - Sums them into a single payout record (status: `"pending"`)
   - Marks all those commissions as `"paid"`
4. Admin manually transfers the money via UPI / bank transfer
5. Admin clicks the checkmark to **"Mark as Completed"** which updates payout status to `"completed"`

### Alternative Attribution (Coupon System)

- If a customer uses an influencer's coupon code (e.g., `SAAD5`) during checkout, the order is attributed to that influencer even without a referral link
- The coupon also provides a discount (e.g., 5%)

### Self-Referral Prevention

- Frontend strips referral data if buyer ID matches influencer ID
- Backend's `create_commission` also checks and returns `None` for self-referrals

---

## Gaps vs. Original Plan

| Feature | Original Plan | Current State |
|---|---|---|
| Fixed payout schedule (10th, 20th, last day) | Planned | Not enforced — admin can create anytime |
| 5-day cutoff rule | Planned | Not implemented — all approved commissions are included |
| Automated payout creation (cron) | Discussed | Not implemented — fully manual |
| Payout method stored per influencer | Planned (UPI / bank) | Profile has `payout_upi` field but method is hardcoded as `"upi"` in the admin call |
| Payout notifications to influencer | Not explicitly planned | Not implemented |
| Payout history in influencer dashboard | Planned | Earnings page exists but doesn't show individual payout records |

---

## Recommended Implementation Plan

### Phase 1: Commission Approval Timestamp

**What:** Ensure `approved_at` is stored on commissions so the cutoff logic works.

**Where:** `backend/app/routers/orders.py` — in the `update_order` endpoint where commissions are approved.

**Change:** When updating commission status to `"approved"`, also set `approved_at: datetime.utcnow()`.

**Why first:** This is a dependency for the cutoff logic in Phase 2.

---

### Phase 2: Cutoff Logic

**What:** When creating a payout, only include commissions that were approved at least 5 days ago.

**Where:** `backend/app/services/commission_service.py` → `create_payout()`

**Change:** Add a date filter to the approved commissions query:

```python
cutoff_date = datetime.utcnow() - timedelta(days=5)
approved = await self.commissions.find({
    "influencer_id": influencer_id,
    "status": "approved",
    "approved_at": {"$lte": cutoff_date}
}).to_list(length=500)
```

---

### Phase 3: Payout Method from Profile

**What:** Use the influencer's saved payout method instead of hardcoding `"upi"`.

**Where:**
- `frontend-admin/src/app/payouts/page.tsx` — read `payout_upi` from profile, pass appropriate method
- `backend/app/services/commission_service.py` — optionally validate the method

**Change:**
- Admin UI shows the influencer's UPI ID next to the payout button
- Method is pulled from the influencer profile rather than hardcoded

---

### Phase 4: Payout History in Influencer Dashboard

**What:** Let influencers see their payout records (date, amount, status, method).

**Where:** `frontend-influencer/src/app/earnings/page.tsx`

**Change:**
- Add a new backend endpoint: `GET /influencers/me/payouts` (requires influencer auth)
- Returns list of payouts for the logged-in influencer
- Display a table/list of payout records below the earnings summary
- Show: date, amount, number of commissions included, method, status (pending/completed)

---

### Phase 5: Scheduled Payout Reminders (Optional)

**What:** Instead of a cron job that auto-creates payouts, send admin a reminder/notification on payout dates (10th, 20th, last day of month) showing which influencers have eligible amounts.

**Why manual over cron:** The original plan requires admin to actually transfer money. Automating payout creation without money transfer creates confusion. A reminder-based approach is more practical.

**Alternative:** If full automation is desired, a lightweight cron job (or a scheduled task in the backend) could auto-create payout records on those dates, and admin just needs to do the money transfer + mark as completed.

---

## Execution Order

1. **Phase 1** — Add `approved_at` field (dependency for cutoff)
2. **Phase 2** — Cutoff logic
3. **Phase 3** — Payout method from profile
4. **Phase 4** — Payout history in influencer dashboard
5. **Phase 5** — Optional: reminders or cron

---

## Files Involved

| File | Change |
|---|---|
| `backend/app/routers/orders.py` | Add `approved_at` when commission moves to approved |
| `backend/app/services/commission_service.py` | Add cutoff filter in `create_payout()` |
| `backend/app/routers/influencers.py` | Add `GET /influencers/me/payouts` endpoint |
| `frontend-admin/src/app/payouts/page.tsx` | Show UPI ID, use profile method |
| `frontend-influencer/src/app/earnings/page.tsx` | Add payout history table |
| `frontend-influencer/src/lib/api/index.ts` | Add `getMyPayouts()` API method |

---

## Summary

The core payout system works end-to-end today. The main gaps are the **5-day cutoff rule**, **payout history visibility for influencers**, and using the **stored payout method** instead of hardcoding. These are relatively small changes. The scheduled payout dates (10th, 20th, last day) are best handled as a manual admin process with optional reminders rather than full automation, since the actual money transfer is always manual.
