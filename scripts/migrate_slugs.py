"""
One-time migration: generate slugs for all existing products that don't have one.

Usage:
  cd backend
  python -m scripts.migrate_slugs
"""

import asyncio
import os
import re
import unicodedata

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "perfume_decant")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


async def main():
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    collection = db["products"]

    products = await collection.find(
        {"$or": [{"slug": {"$exists": False}}, {"slug": None}, {"slug": ""}]}
    ).to_list(length=None)

    print(f"Found {len(products)} products without slugs")

    used_slugs: set[str] = set()
    existing = await collection.find({"slug": {"$exists": True, "$ne": None, "$ne": ""}}, {"slug": 1}).to_list(length=None)
    for doc in existing:
        used_slugs.add(doc["slug"])

    updated = 0
    for product in products:
        name = product.get("name", "")
        brand = product.get("brand", "")
        base_slug = slugify(f"{name} {brand}")
        slug = base_slug
        counter = 1
        while slug in used_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1

        used_slugs.add(slug)
        await collection.update_one({"_id": product["_id"]}, {"$set": {"slug": slug}})
        updated += 1
        print(f"  {product['_id']} -> {slug}")

    print(f"\nDone! Updated {updated} products.")

    await collection.create_index("slug", unique=True, sparse=True)
    print("Created unique index on 'slug' field.")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
