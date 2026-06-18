from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

BlogStatus = Literal["draft", "pending_review", "published", "rejected", "unpublished"]
BlogContentMode = Literal["blocks", "admin_html"]


class BlogSeo(BaseModel):
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    og_image: Optional[str] = None
    canonical_path: Optional[str] = None
    noindex: bool = False


class BlogModeration(BaseModel):
    submitted_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


class BlogPostOut(BaseModel):
    id: str
    slug: str
    title: str
    excerpt: Optional[str] = None
    status: BlogStatus
    content_mode: BlogContentMode
    blocks: Optional[dict[str, Any]] = None
    html_body: Optional[str] = None
    html_source_version: Optional[str] = None
    author_id: str
    author_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None
    seo: BlogSeo = Field(default_factory=BlogSeo)
    moderation: BlogModeration = Field(default_factory=BlogModeration)


class BlogListResponse(BaseModel):
    items: list[BlogPostOut]
    total: int


class BlogMyCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    slug: Optional[str] = Field(None, max_length=160)
    excerpt: Optional[str] = Field(None, max_length=2000)
    blocks: dict[str, Any]
    seo: BlogSeo = Field(default_factory=BlogSeo)


class BlogMyUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    slug: Optional[str] = Field(None, max_length=160)
    excerpt: Optional[str] = Field(None, max_length=2000)
    blocks: Optional[dict[str, Any]] = None
    seo: Optional[BlogSeo] = None


class BlogRejectBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


class BlogAdminBulkDelete(BaseModel):
    post_ids: list[str] = Field(..., min_length=1, max_length=100)


class BlogAdminUpsert(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    slug: str = Field(..., min_length=1, max_length=160)
    excerpt: Optional[str] = Field(None, max_length=2000)
    content_mode: BlogContentMode
    blocks: Optional[dict[str, Any]] = None
    html_body: Optional[str] = None  # optional on PUT admin_html to keep existing body
    html_source_version: Optional[str] = Field(None, max_length=500)
    status: BlogStatus = "draft"
    seo: BlogSeo = Field(default_factory=BlogSeo)
    author_name: Optional[str] = Field(None, max_length=120)
