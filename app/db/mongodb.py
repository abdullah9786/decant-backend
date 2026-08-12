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

    try:
        await db.db["orders"].create_index("user_id", name="orders_user_id")
        await db.db["orders"].create_index("customer_email", name="orders_customer_email")
        await db.db["orders"].create_index(
            "instagram_promo_opt_in",
            name="orders_instagram_promo_opt_in",
        )
    except (DuplicateKeyError, OperationFailure) as e:
        logging.warning("Order user lookup indexes: %s", e)

    try:
        await db.db["instagram_promo_submissions"].create_index(
            "order_id",
            unique=True,
            name="uniq_promo_submission_order_id",
        )
        await db.db["instagram_promo_submissions"].create_index(
            "post_url",
            unique=True,
            name="uniq_promo_submission_post_url",
            partialFilterExpression={
                "post_url": {"$exists": True, "$type": "string"},
            },
        )
        await db.db["instagram_promo_submissions"].create_index(
            [("poster_instagram_username", 1), ("submitted_at", -1)],
            name="promo_poster_submitted_at",
        )
        await db.db["instagram_promo_submissions"].create_index(
            "status",
            name="promo_submission_status",
        )
    except (DuplicateKeyError, OperationFailure) as e:
        logging.warning("Instagram promo submission indexes: %s", e)

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

    # Products: category listing/grid filters by `category_ids` and sorts by
    # sort_order / created_at. Without these the category detail page does a
    # collection scan + in-memory sort on every (cold) render.
    try:
        await db.db["products"].create_index("category_ids", name="product_category_ids")
        await db.db["products"].create_index(
            [("sort_order", 1), ("created_at", -1)],
            name="product_sort_order_created",
        )
    except (DuplicateKeyError, OperationFailure) as e:
        logging.warning("Product listing indexes: %s", e)

    try:
        await db.db["search_queries"].create_index(
            "created_at",
            expireAfterSeconds=90 * 24 * 60 * 60,
            name="search_queries_ttl",
        )
        await db.db["search_queries"].create_index(
            [("query_normalized", 1), ("created_at", -1)],
            name="search_queries_normalized_created",
        )
        await db.db["search_queries"].create_index(
            [("client_ip", 1), ("created_at", -1)],
            name="search_queries_ip_created",
        )
    except (DuplicateKeyError, OperationFailure) as e:
        logging.warning("Search query analytics indexes: %s", e)

    # Dedup for at-least-once shipping provider webhooks (NimbusPost, etc.) —
    # (provider, delivery_id) is unique so a redelivered event is a no-op.
    try:
        await db.db["webhook_deliveries"].create_index(
            [("provider", 1), ("delivery_id", 1)],
            unique=True,
            name="uniq_webhook_provider_delivery",
        )
        await db.db["webhook_deliveries"].create_index(
            "received_at",
            expireAfterSeconds=30 * 24 * 60 * 60,
            name="webhook_deliveries_ttl",
        )
    except (DuplicateKeyError, OperationFailure) as e:
        logging.warning("Webhook deliveries indexes: %s", e)

    # Reorder reminder sweep filters candidate orders by delivered_at, and
    # dedupes/cools down per customer via reorder_reminders.customer_email.
    try:
        await db.db["orders"].create_index(
            "delivered_at",
            name="orders_delivered_at",
        )
        await db.db["reorder_reminders"].create_index(
            "customer_email",
            unique=True,
            name="uniq_reorder_reminders_customer_email",
        )
    except (DuplicateKeyError, OperationFailure) as e:
        logging.warning("Reorder reminder indexes: %s", e)

    print(f"\n\033[92m[SUCCESS]\033[0m Database connected: {settings.DATABASE_NAME}")
    logging.info(f"Connected to MongoDB: {settings.DATABASE_NAME}")

async def close_mongo_connection():
    db.client.close()
    logging.info("Closed MongoDB connection")

def get_database():
    return db.db
