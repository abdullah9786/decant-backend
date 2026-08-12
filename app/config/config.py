from pydantic_settings import BaseSettings
from typing import List, Optional
import os

class Settings(BaseSettings):
    PROJECT_NAME: str = "Perfume Decant API"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-for-jwt-development")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    
    MONGODB_URL: str = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "perfume_decant")

    # CORS settings
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
        "https://decant-user.vercel.app",
        "https://decant-admin-stage.vercel.app",
        "https://decant-influencer-stage.vercel.app/",
        "https://admin.decume.in",
        "https://decume.in",
        "https://www.decume.in",
        "https://my.decume.in"
    ]

    # Email verification (ZeptoMail)
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:3000")
    ZEPTO_API_URL: str = os.getenv("ZEPTO_API_URL", "https://api.zeptomail.in/v1.1/email")
    ZEPTO_API_KEY: str = os.getenv("ZEPTO_API_KEY", "").strip()
    ZEPTO_FROM_EMAIL: str = os.getenv("ZEPTO_FROM_EMAIL", "")
    ZEPTO_FROM_NAME: str = os.getenv("ZEPTO_FROM_NAME", "Decume.in")
    
    # Razorpay Payment Gateway
    RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

    # Admin Notifications
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")

    # Next.js on-demand ISR revalidation (storefront)
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
    REVALIDATE_SECRET: str = os.getenv("REVALIDATE_SECRET", "")

    # Cash on Delivery
    COD_ENABLED: bool = os.getenv("COD_ENABLED", "true").lower() == "true"
    COD_MAX_AMOUNT: float = float(os.getenv("COD_MAX_AMOUNT", "3000"))
    COD_FEE: float = float(os.getenv("COD_FEE", "49"))

    # NimbusPost shipping (order creation only — courier booked in NimbusPost dashboard)
    NIMBUSPOST_API_KEY: str = os.getenv("NIMBUSPOST_API_KEY", "").strip()
    NIMBUSPOST_API_SECRET: str = os.getenv("NIMBUSPOST_API_SECRET", "").strip()
    NIMBUSPOST_WEBHOOK_SECRET: str = os.getenv("NIMBUSPOST_WEBHOOK_SECRET", "").strip()
    NIMBUSPOST_WAREHOUSE_ID: str = os.getenv("NIMBUSPOST_WAREHOUSE_ID", "").strip()
    NIMBUSPOST_DEFAULT_STATE: str = os.getenv("NIMBUSPOST_DEFAULT_STATE", "Maharashtra").strip()
    NIMBUSPOST_DEFAULT_WEIGHT_G: int = int(os.getenv("NIMBUSPOST_DEFAULT_WEIGHT_G", "250"))
    NIMBUSPOST_DEFAULT_LENGTH_CM: int = int(os.getenv("NIMBUSPOST_DEFAULT_LENGTH_CM", "12"))
    NIMBUSPOST_DEFAULT_WIDTH_CM: int = int(os.getenv("NIMBUSPOST_DEFAULT_WIDTH_CM", "10"))
    NIMBUSPOST_DEFAULT_HEIGHT_CM: int = int(os.getenv("NIMBUSPOST_DEFAULT_HEIGHT_CM", "8"))

    # Reorder reminder — "you're likely running low" lifecycle email.
    # Depletion date = delivered_at + (total decant ml in the order / rate).
    REORDER_DAILY_ML_RATE: float = float(os.getenv("REORDER_DAILY_ML_RATE", "3"))
    REORDER_COOLDOWN_DAYS: int = int(os.getenv("REORDER_COOLDOWN_DAYS", "25"))
    REORDER_CATCHUP_WINDOW_DAYS: int = int(os.getenv("REORDER_CATCHUP_WINDOW_DAYS", "3"))
    REORDER_LOOKBACK_DAYS: int = int(os.getenv("REORDER_LOOKBACK_DAYS", "60"))
    REORDER_REMINDER_CRON_SECRET: str = os.getenv("REORDER_REMINDER_CRON_SECRET", "").strip()

    class Config:
        case_sensitive = True
        env_file = ".env"

settings = Settings()
