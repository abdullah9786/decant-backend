from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

COLLECTION = "search_queries"
TTL_SECONDS = 90 * 24 * 60 * 60  # 90 days
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_PER_IP = 40


def normalize_query(query: str) -> str:
    cleaned = re.sub(r"\s+", " ", (query or "").strip().lower())
    return cleaned[:80]


class SearchAnalyticsService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db[COLLECTION]

    async def log_search(
        self,
        *,
        query: str,
        result_count: int,
        source: str,
        client_ip: Optional[str] = None,
        user_label: str = "Guest user",
    ) -> bool:
        normalized = normalize_query(query)
        if len(normalized) < 1:
            return False

        if client_ip:
            window_start = datetime.now(timezone.utc) - timedelta(
                seconds=RATE_LIMIT_WINDOW_SECONDS
            )
            recent = await self.collection.count_documents(
                {"client_ip": client_ip, "created_at": {"$gte": window_start}}
            )
            if recent >= RATE_LIMIT_MAX_PER_IP:
                return False

        doc = {
            "query": query.strip()[:80],
            "query_normalized": normalized,
            "result_count": max(0, int(result_count)),
            "source": source if source in ("navbar", "search_page") else "search_page",
            "user_label": (user_label or "Guest user").strip()[:120] or "Guest user",
            "created_at": datetime.now(timezone.utc),
        }
        if client_ip:
            doc["client_ip"] = client_ip

        await self.collection.insert_one(doc)
        return True

    async def get_stats(self, days: int = 30) -> dict:
        days = max(1, min(days, 90))
        since = datetime.now(timezone.utc) - timedelta(days=days)
        match = {"created_at": {"$gte": since}}

        total_searches = await self.collection.count_documents(match)
        zero_result_searches = await self.collection.count_documents(
            {**match, "result_count": 0}
        )
        zero_result_rate = (
            round(zero_result_searches / total_searches, 4) if total_searches else 0.0
        )

        top_cursor = self.collection.aggregate(
            [
                {"$match": match},
                {
                    "$group": {
                        "_id": "$query_normalized",
                        "query": {"$first": "$query"},
                        "count": {"$sum": 1},
                        "avg_results": {"$avg": "$result_count"},
                    }
                },
                {"$sort": {"count": -1}},
                {"$limit": 25},
            ]
        )
        top_queries = [
            {
                "query": row.get("query") or row["_id"],
                "count": row["count"],
                "avg_results": round(float(row.get("avg_results") or 0), 1),
            }
            async for row in top_cursor
        ]

        zero_cursor = self.collection.aggregate(
            [
                {"$match": {**match, "result_count": 0}},
                {
                    "$group": {
                        "_id": "$query_normalized",
                        "query": {"$first": "$query"},
                        "count": {"$sum": 1},
                        "avg_results": {"$avg": "$result_count"},
                    }
                },
                {"$sort": {"count": -1}},
                {"$limit": 25},
            ]
        )
        zero_result_queries = [
            {
                "query": row.get("query") or row["_id"],
                "count": row["count"],
                "avg_results": 0.0,
            }
            async for row in zero_cursor
        ]

        recent_rows = (
            await self.collection.find(match)
            .sort("created_at", -1)
            .limit(40)
            .to_list(length=40)
        )
        recent_searches = [
            {
                "query": row.get("query") or "",
                "user_label": row.get("user_label") or "Guest user",
                "result_count": int(row.get("result_count") or 0),
                "source": row.get("source") or "search_page",
                "created_at": row.get("created_at"),
            }
            for row in recent_rows
            if row.get("query")
        ]

        return {
            "days": days,
            "total_searches": total_searches,
            "zero_result_searches": zero_result_searches,
            "zero_result_rate": zero_result_rate,
            "top_queries": top_queries,
            "zero_result_queries": zero_result_queries,
            "recent_searches": recent_searches,
        }
