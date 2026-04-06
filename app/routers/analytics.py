from fastapi import APIRouter, Depends
from app.db.mongodb import get_database
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timedelta
from typing import List, Dict
from app.utils.deps import require_admin

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("/stats")
async def get_stats(db: AsyncIOMotorDatabase = Depends(get_database), _admin=Depends(require_admin)):
    # 1. Basic Stats
    # Revenue (Delivered only)
    revenue_cursor = db["orders"].aggregate([
        {"$match": {"status": "delivered"}},
        {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
    ])
    revenue_result = await revenue_cursor.to_list(length=1)
    revenue = revenue_result[0]["total"] if revenue_result else 0
    
    # Counts (exclude cancelled / refunded)
    orders_count = await db["orders"].count_documents({"status": {"$nin": ["cancelled", "refunded"]}})
    users_count = await db["users"].count_documents({})
    
    # Low stock
    low_stock_count = await db["products"].count_documents({
        "stock_ml": {"$lt": 50}
    })
    
    # 2. Daily Sales & Orders (Last 7 days)
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    daily_cursor = db["orders"].aggregate([
        {"$match": {"created_at": {"$gte": seven_days_ago}, "status": {"$nin": ["cancelled", "refunded"]}}},
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "sales": {"$sum": "$total_amount"},
                "orders": {"$sum": 1}
            }
        },
        {"$sort": {"_id": 1}}
    ])
    daily_results = await daily_cursor.to_list(length=7)
    
    # Format for frontend (Mon, Tue, etc.)
    days_map = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
    formatted_daily = []
    for res in daily_results:
        dt = datetime.strptime(res["_id"], "%Y-%m-%d")
        formatted_daily.append({
            "name": days_map[dt.weekday()],
            "sales": res["sales"],
            "orders": res["orders"],
            "full_date": res["_id"]
        })

    # 3. Category Mix (Synchronized with Categories Collection)
    # Get official category names
    official_cats_cursor = db["categories"].find({}, {"name": 1})
    official_cats = await official_cats_cursor.to_list(length=100)
    official_names = [cat["name"] for cat in official_cats]

    category_cursor = db["products"].aggregate([
        {"$match": {"category": {"$in": official_names}}},
        {"$group": {"_id": "$category", "value": {"$sum": 1}}},
        {"$sort": {"value": -1}},
        {"$limit": 5}
    ])
    category_results = await category_cursor.to_list(length=5)
    formatted_categories = [{"name": res["_id"], "value": res["value"]} for res in category_results]

    # Calculate AOV
    aov = revenue / orders_count if orders_count > 0 else 0
                
    return {
        "revenue": revenue,
        "orders": orders_count,
        "users": users_count,
        "low_stock": low_stock_count,
        "aov": round(aov, 2),
        "daily_stats": formatted_daily,
        "category_stats": formatted_categories,
        "revenue_change": "+12.5%", # These could be calculated too, but keeping placeholders for now
        "orders_change": "+3.1%",
        "users_change": "+15.2%",
        "low_stock_change": "-2"
    }
