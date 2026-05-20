"""
One-time cleanup: remove duplicate orders that share the same Razorpay
order id, then delete the orphaned commission rows that were attached to the
deleted orders.

These duplicates were created before the dual-event subscription
(`payment.captured` + `order.paid`) was reduced to a single event AND before
the atomic claim / unique partial index safety nets were added.

Until the duplicates are gone, the unique partial index on
`orders.payment_details.razorpay_order_id` cannot be built, leaving the
collection vulnerable.

Selection rule for which row to KEEP in each duplicate group:
  1. Prefer the row whose `payment_details.source` is NOT "webhook" — that's
     the client-created (`verify-and-create`) one, which has the
     razorpay_signature and JWT-attached `user_id`.
  2. Otherwise prefer the earliest `created_at` — its side-effects
     (confirmation email, commission) ran first.
  3. Tie-breaker: lowest ObjectId.

Usage:
  cd backend
  python -m scripts.dedupe_orders            # dry-run (default, shows what would be deleted)
  python -m scripts.dedupe_orders --apply    # actually delete
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "perfume_decant")


def _sort_key(doc: dict[str, Any]):
    """Lower tuple => preferred-to-keep."""
    source = (doc.get("payment_details") or {}).get("source", "")
    source_rank = 1 if source == "webhook" else 0
    created_at = doc.get("created_at") or datetime.max.replace(tzinfo=timezone.utc)
    if isinstance(created_at, datetime) and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (source_rank, created_at, str(doc["_id"]))


def _fmt_doc(doc: dict[str, Any]) -> str:
    pd = doc.get("payment_details") or {}
    return (
        f"id={doc['_id']} "
        f"created_at={doc.get('created_at')} "
        f"status={doc.get('status')} "
        f"payment_status={doc.get('payment_status')} "
        f"source={pd.get('source', '-')} "
        f"has_signature={bool(pd.get('razorpay_signature'))} "
        f"user_id={doc.get('user_id')}"
    )


async def main():
    parser = argparse.ArgumentParser(description="Dedupe orders sharing the same razorpay_order_id")
    parser.add_argument("--apply", action="store_true", help="Actually delete duplicates (default: dry-run)")
    args = parser.parse_args()

    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    orders = db["orders"]
    commissions = db["commissions"]

    pipeline = [
        {"$match": {"payment_details.razorpay_order_id": {"$exists": True, "$type": "string"}}},
        {"$group": {
            "_id": "$payment_details.razorpay_order_id",
            "docs": {"$push": "$$ROOT"},
            "count": {"$sum": 1},
        }},
        {"$match": {"count": {"$gt": 1}}},
    ]

    groups = []
    async for g in orders.aggregate(pipeline):
        groups.append(g)

    if not groups:
        print("No duplicate orders found. Nothing to do.")
        client.close()
        return

    print(f"Found {len(groups)} duplicate group(s):\n")

    to_delete_ids: list[ObjectId] = []
    to_delete_order_id_strs: list[str] = []

    for g in groups:
        rzp_oid = g["_id"]
        docs = sorted(g["docs"], key=_sort_key)
        keeper = docs[0]
        losers = docs[1:]
        print(f"  rzp_order_id={rzp_oid}  ({len(docs)} rows)")
        print(f"    KEEP   {_fmt_doc(keeper)}")
        for d in losers:
            print(f"    DELETE {_fmt_doc(d)}")
            to_delete_ids.append(d["_id"])
            to_delete_order_id_strs.append(str(d["_id"]))
        print()

    print(
        f"Summary: would delete {len(to_delete_ids)} order row(s) "
        f"and any commissions whose order_id is in that list."
    )

    if not args.apply:
        print("\nDry-run. Re-run with --apply to actually delete.")
        client.close()
        return

    print("\nApplying deletions...")
    # Delete commissions first so we don't leave orphaned payout calculations.
    comm_result = await commissions.delete_many({"order_id": {"$in": to_delete_order_id_strs}})
    print(f"  deleted {comm_result.deleted_count} commission row(s)")

    order_result = await orders.delete_many({"_id": {"$in": to_delete_ids}})
    print(f"  deleted {order_result.deleted_count} order row(s)")

    # Now attempt to build the unique partial index so future duplicates are
    # blocked at the DB level.
    try:
        await orders.create_index(
            "payment_details.razorpay_order_id",
            unique=True,
            name="uniq_payment_details_razorpay_order_id",
            partialFilterExpression={
                "payment_details.razorpay_order_id": {"$exists": True, "$type": "string"}
            },
        )
        print("\n[OK] Unique partial index created.")
    except Exception as e:
        print(f"\n[WARN] Could not create unique index after dedupe: {e}")
        print("       There may still be duplicate rows. Re-run --apply.")
        sys.exit(1)

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
