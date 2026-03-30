from motor.motor_asyncio import AsyncIOMotorClient
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
    print(f"\n\033[92m[SUCCESS]\033[0m Database connected: {settings.DATABASE_NAME}")
    logging.info(f"Connected to MongoDB: {settings.DATABASE_NAME}")

async def close_mongo_connection():
    db.client.close()
    logging.info("Closed MongoDB connection")

def get_database():
    return db.db
