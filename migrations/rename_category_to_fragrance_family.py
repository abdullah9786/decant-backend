"""
Migration: Rename 'categories' → 'fragrance_families' collection
         + Rename 'category' → 'fragrance_family' field in products

Run this script ONCE before deploying the code changes.

Usage:
    python -m migrations.rename_category_to_fragrance_family

Or directly:
    MONGODB_URL=<your-url> DATABASE_NAME=<your-db> python migrations/rename_category_to_fragrance_family.py
"""
import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "perfume_decant")


async def migrate():
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]

    # 1. Rename the 'categories' collection → 'fragrance_families'
    existing = await db.list_collection_names()
    if "categories" in existing:
        if "fragrance_families" in existing:
            print("[SKIP] 'fragrance_families' collection already exists. Skipping collection rename.")
        else:
            await db["categories"].rename("fragrance_families")
            print("[OK] Renamed collection: categories → fragrance_families")
    else:
        print("[SKIP] 'categories' collection not found (already renamed or never existed).")

    # 2. Rename the 'category' field → 'fragrance_family' in the products collection
    result = await db["products"].update_many(
        {"category": {"$exists": True}},
        {"$rename": {"category": "fragrance_family"}}
    )
    print(f"[OK] Renamed field 'category' → 'fragrance_family' in {result.modified_count} product(s).")

    client.close()
    print("\n[DONE] Migration complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
