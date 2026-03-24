from pydantic_settings import BaseSettings
from typing import List, Optional
import os

class Settings(BaseSettings):
    PROJECT_NAME: str = "Perfume Decant API"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-for-jwt-development")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7 # 7 days
    
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
        "https://admin.decume.in",
        "https://decume.in",
        "https://influencer.decume.in"
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

    # Admin Notifications
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")

    class Config:
        case_sensitive = True
        env_file = ".env"

settings = Settings()
