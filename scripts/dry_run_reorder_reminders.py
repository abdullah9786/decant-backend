"""
Manual verification harness for the reorder-reminder feature
(see .cursor/plans/consumption-based_reorder_reminder_*.plan.md, rollout
step 1).

Since the feature ships with no backfill, no real order has `delivered_at`
set yet, so `POST /reorder-reminders/run?dry_run=true` would report
`checked: 0` against production data today. This script instead pulls a
handful of REAL delivered orders and exercises
`ReorderReminderService.compute_depletion_date` / `.is_eligible` against
in-memory copies with a few synthetic `delivered_at` values, so the ml math
and eligibility branches (not due yet / due / missed catchup window /
already reordered / cooldown) can be sanity-checked against real order
shapes before the feature goes live.

Read-only: never writes to `orders` or `reorder_reminders`, and never calls
MailService (no emails are sent).

Usage:
  cd decant-backend
  python -m scripts.dry_run_reorder_reminders
"""

import asyncio
import copy
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "perfume_decant")


async def main():
    from app.config.config import settings
    from app.services.reorder_reminder_service import ReorderReminderService

    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    service = ReorderReminderService(db)

    real_orders = await db["orders"].find(
        {"status": "delivered"}
    ).sort("created_at", -1).limit(8).to_list(length=8)

    if not real_orders:
        print("No delivered orders found in this database — nothing to verify against.")
        client.close()
        return

    print(f"Loaded {len(real_orders)} real delivered order(s).\n")
    print(
        f"Config: REORDER_DAILY_ML_RATE={settings.REORDER_DAILY_ML_RATE} "
        f"REORDER_COOLDOWN_DAYS={settings.REORDER_COOLDOWN_DAYS} "
        f"REORDER_CATCHUP_WINDOW_DAYS={settings.REORDER_CATCHUP_WINDOW_DAYS} "
        f"REORDER_LOOKBACK_DAYS={settings.REORDER_LOOKBACK_DAYS}\n"
    )

    now = datetime.now(timezone.utc)
    tested = 0

    for order in real_orders:
        base = copy.deepcopy(order)
        base["delivered_at"] = now  # placeholder; overridden per scenario below

        depletion_date = service.compute_depletion_date(base)
        if depletion_date is None:
            print(f"order={order['_id']} -> SKIP (no_decant_items; pack-only/set/gift-box order)")
            continue

        # depletion_date - now == days_of_supply (float) when delivered "now";
        # use that to derive realistic scenario deltas for THIS order's
        # actual ml total. Keep it a float (not `.days`, which floors) so the
        # "at_depletion_date" scenario lands exactly on the boundary.
        days_of_supply = (depletion_date - now).total_seconds() / 86400
        catchup = settings.REORDER_CATCHUP_WINDOW_DAYS

        scenarios = {
            "just_delivered (not due yet)": now,
            "at_depletion_date (due)": now - timedelta(days=days_of_supply, minutes=1),
            "mid_catchup_window (due)": now - timedelta(days=days_of_supply + max(catchup / 2, 0.5)),
            "past_catchup_window (missed)": now - timedelta(days=days_of_supply + catchup + 5),
        }

        tested += 1
        print(f"order={order['_id']} email={order.get('customer_email')} days_of_supply={days_of_supply:.2f}")
        for label, simulated_delivered_at in scenarios.items():
            fake_order = copy.deepcopy(order)
            fake_order["delivered_at"] = simulated_delivered_at
            fake_depletion_date = service.compute_depletion_date(fake_order)
            eligible, reason = await service.is_eligible(fake_order, fake_depletion_date)
            status = "ELIGIBLE" if eligible else f"skip ({reason})"
            print(f"    {label:32s} delivered_at={simulated_delivered_at.date()} -> {status}")
        print()

    print(f"Verified {tested} order(s) with decant items across {len(real_orders)} loaded.")
    print("No writes were made; no emails were sent.")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
