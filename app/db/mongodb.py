from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError, OperationFailure
from app.config.config import settings
import logging

class Database:
    client: AsyncIOMotorClient = None
    db = None

db = Database()

async def connect_to_mongo():
    db.client = AsyncIOMotorClient(settings.MONGODB_URL)
    db.db = db.client[settings.DATABASE_NAME]
    await db.db["refresh_sessions"].create_index("token_hash", unique=True)
    await db.db["pending_checkouts"].create_index("razorpay_order_id", unique=True)
    try:
        await db.db["pending_checkouts"].drop_index("created_at_1")
    except Exception:
        pass
    await db.db["pending_checkouts"].create_index("created_at", expireAfterSeconds=7776000)

    # Guards against duplicate orders when Razorpay redelivers a webhook or
    # when the client `verify-and-create` path races with the webhook path.
    # Partial filter so legacy / unpaid orders without a razorpay_order_id
    # aren't constrained.
    try:
        await db.db["orders"].create_index(
            "payment_details.razorpay_order_id",
            unique=True,
            name="uniq_payment_details_razorpay_order_id",
            partialFilterExpression={
                "payment_details.razorpay_order_id": {"$exists": True, "$type": "string"}
            },
        )
    except (DuplicateKeyError, OperationFailure) as e:
        # Most likely cause: pre-existing duplicate orders in the collection.
        # We log loudly so it can be cleaned up — but we do NOT block startup.
        print(
            "\n\033[93m[WARN]\033[0m Could not create unique index on "
            f"orders.payment_details.razorpay_order_id: {e}\n"
            "       Run the dedupe script before the index can be enforced."
        )
        logging.warning(
            "Failed to create unique index on orders.payment_details.razorpay_order_id: %s",
            e,
        )

    # Idempotency key for COD (and any future non-Razorpay) order paths so
    # rapid double-submits return the same order instead of creating a
    # duplicate. Partial filter so legacy / prepaid orders without the
    # field are not constrained.
    try:
        await db.db["orders"].create_index(
            "idempotency_key",
            unique=True,
            name="uniq_orders_idempotency_key",
            partialFilterExpression={
                "idempotency_key": {"$exists": True, "$type": "string"}
            },
        )
    except (DuplicateKeyError, OperationFailure) as e:
        print(
            "\n\033[93m[WARN]\033[0m Could not create unique index on "
            f"orders.idempotency_key: {e}"
        )
        logging.warning(
            "Failed to create unique index on orders.idempotency_key: %s",
            e,
        )

    # Blog: moderation queue + author listing + unique published slugs
    try:
        await db.db["blog_posts"].create_index(
            [("status", 1), ("moderation.submitted_at", -1)],
            name="blog_status_submitted",
        )
        await db.db["blog_posts"].create_index(
            [("author_id", 1), ("status", 1), ("updated_at", -1)],
            name="blog_author_status_updated",
        )
        await db.db["blog_posts"].create_index(
            "slug",
            unique=True,
            name="blog_slug_unique_published",
            partialFilterExpression={"status": "published"},
        )
    except (DuplicateKeyError, OperationFailure) as e:
        logging.warning("Blog indexes: %s", e)

    # Products: speed up the "You may also like" rail. The related query is an
    # $or over these fields filtered by is_active and sorted by sort_order /
    # created_at, so single-field indexes (used via index-union for $or) plus a
    # sort index keep it off collection scans.
    try:
        await db.db["products"].create_index("fragrance_family", name="product_fragrance_family")
        await db.db["products"].create_index("brand", name="product_brand")
        await db.db["products"].create_index("category_ids", name="product_category_ids")
        await db.db["products"].create_index("chip_ids", name="product_chip_ids")
        await db.db["products"].create_index(
            [("sort_order", 1), ("created_at", -1)],
            name="product_sort_order_created",
        )
    except (DuplicateKeyError, OperationFailure) as e:
        logging.warning("Product related-query indexes: %s", e)

    print(f"\n\033[92m[SUCCESS]\033[0m Database connected: {settings.DATABASE_NAME}")
    logging.info(f"Connected to MongoDB: {settings.DATABASE_NAME}")

async def close_mongo_connection():
    db.client.close()
    logging.info("Closed MongoDB connection")

def get_database():
    return db.db
