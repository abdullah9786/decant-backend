from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class SearchQueryLogCreate(BaseModel):
    query: str = Field(..., min_length=1, max_length=80)
    result_count: int = Field(0, ge=0)
    source: Literal["navbar", "search_page"] = "search_page"


class SearchQueryTopItem(BaseModel):
    query: str
    count: int
    avg_results: float


class SearchQueryRecentItem(BaseModel):
    query: str
    user_label: str
    result_count: int
    source: str
    created_at: datetime


class SearchQueryStatsResponse(BaseModel):
    days: int
    total_searches: int
    zero_result_searches: int
    zero_result_rate: float
    top_queries: List[SearchQueryTopItem]
    zero_result_queries: List[SearchQueryTopItem]
    recent_searches: List[SearchQueryRecentItem]
