"""Blog posts: moderation, slugs, sanitization, and ISR hooks."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import nh3
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from app.schemas.blog import BlogModeration, BlogPostOut, BlogSeo
from app.utils.html_sanitize import sanitize_admin_blog_html
from app.utils.revalidate import revalidate_blog_post, revalidate_paths

RESERVED_SLUGS = frozenset(
    {"write", "me", "admin", "rss.xml", "guidelines", "new", "edit", "pending"}
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def slugify_title(title: str) -> str:
    s = (title or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:120] if s else "post")


def validate_slug(slug: str) -> None:
    s = slug.lower().strip()
    if not s or len(s) > 160:
        raise HTTPException(status_code=400, detail="Invalid slug")
    if s in RESERVED_SLUGS:
        raise HTTPException(status_code=400, detail="Reserved slug")
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", s):
        raise HTTPException(
            status_code=400,
            detail="Slug must use lowercase letters, numbers, and hyphens only",
        )


def ensure_editor_js_root(blocks: dict[str, Any]) -> None:
    if not isinstance(blocks, dict):
        raise HTTPException(status_code=400, detail="blocks must be an object")
    if not isinstance(blocks.get("blocks"), list):
        raise HTTPException(status_code=400, detail="blocks.blocks must be an array")


# Inline formatting allowed inside Editor.js text fields. Kept in sync with the
# admin editor's ParagraphWithLinks sanitizer and the storefront's
# sanitizeBlogInlineHtml renderer so inline links/emphasis survive a save round
# trip instead of being escaped into visible markup.
_INLINE_TAGS: frozenset[str] = frozenset(
    {"a", "b", "strong", "i", "em", "u", "mark", "span", "br"}
)
_INLINE_ATTRS: dict[str, set[str]] = {
    "a": {"href", "target", "rel", "class"},
    "span": {"class"},
}


def clean_inline(text: str) -> str:
    """Allowlist inline formatting (links, emphasis) in a block text field.

    Unlike nh3.clean_text (which escapes ALL markup to visible text), this keeps
    a small set of safe inline tags so author-added links render as links.
    """
    return nh3.clean(
        text,
        tags=set(_INLINE_TAGS),
        attributes={k: set(v) for k, v in _INLINE_ATTRS.items()},
        url_schemes={"http", "https", "mailto", "tel"},
        link_rel="noopener noreferrer",
    )


def _clean_list_item(item: Any) -> Any:
    """Clean a list item: a flat string, or a nested {content, items} node."""
    if isinstance(item, str):
        return clean_inline(item)
    if isinstance(item, dict):
        node = dict(item)
        content = node.get("content")
        if isinstance(content, str):
            node["content"] = clean_inline(content)
        sub = node.get("items")
        if isinstance(sub, list):
            node["items"] = [_clean_list_item(s) for s in sub]
        return node
    return item


def sanitize_blocks_inplace(blocks: dict[str, Any]) -> dict[str, Any]:
    """Allowlist inline markup in Editor.js text fields (community + admin blocks)."""
    out = dict(blocks)
    bl: list[Any] = list(out.get("blocks") or [])
    for block in bl:
        if not isinstance(block, dict):
            continue
        t = block.get("type")
        data = block.get("data")
        if not isinstance(data, dict):
            continue
        if t == "paragraph":
            text = data.get("text")
            if isinstance(text, str):
                data["text"] = clean_inline(text)
        elif t == "header":
            tx = data.get("text")
            if isinstance(tx, str):
                data["text"] = clean_inline(tx)
        elif t == "quote":
            for k in ("text", "caption"):
                v = data.get(k)
                if isinstance(v, str):
                    data[k] = clean_inline(v)
        elif t == "list":
            items = data.get("items")
            if isinstance(items, list):
                data["items"] = [_clean_list_item(i) for i in items]
        elif t == "product":
            pid = data.get("product_id") or data.get("productId")
            if isinstance(pid, str):
                data["product_id"] = pid.strip()[:64]
                data.pop("productId", None)
    out["blocks"] = bl
    return out


class BlogService:
    def __init__(self, db):
        self.col = db["blog_posts"]

    @staticmethod
    def _author_id(user: dict) -> str:
        return str(user["_id"])

    @staticmethod
    def _display_name_from_user(user: dict) -> str:
        name = (user.get("full_name") or "").strip()
        if name:
            return name[:120]
        email = user.get("email")
        if isinstance(email, str) and email.strip():
            local = email.strip().split("@", 1)[0].strip()
            return (local[:120] if local else "Author")
        return "Author"

    @staticmethod
    def _normalize_author_name_input(raw: str | None, fallback_user: dict) -> str:
        if raw is not None and (s := str(raw).strip()):
            return s[:120]
        return BlogService._display_name_from_user(fallback_user)

    def _to_out(self, doc: dict | None, *, resanitize_html: bool = False) -> BlogPostOut | None:
        if not doc:
            return None
        html = doc.get("html_body")
        if resanitize_html and html:
            html = sanitize_admin_blog_html(html)
        return BlogPostOut(
            id=str(doc["_id"]),
            slug=doc["slug"],
            title=doc["title"],
            excerpt=doc.get("excerpt"),
            status=doc["status"],
            content_mode=doc["content_mode"],
            blocks=doc.get("blocks"),
            html_body=html,
            html_source_version=doc.get("html_source_version"),
            author_id=doc["author_id"],
            author_name=doc.get("author_name"),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
            published_at=doc.get("published_at"),
            seo=BlogSeo(**(doc.get("seo") or {})),
            moderation=BlogModeration(**(doc.get("moderation") or {})),
        )

    async def published_slug_taken(self, slug: str, exclude_id: ObjectId | None = None) -> bool:
        q: dict[str, Any] = {"slug": slug.lower(), "status": "published"}
        if exclude_id is not None:
            q["_id"] = {"$ne": exclude_id}
        return await self.col.find_one(q) is not None

    async def get_my(self, user: dict, post_id: str) -> BlogPostOut:
        try:
            oid = ObjectId(post_id)
        except InvalidId as e:
            raise HTTPException(status_code=404, detail="Post not found") from e
        aid = self._author_id(user)
        doc = await self.col.find_one(
            {
                "_id": oid,
                "author_id": aid,
                "status": {"$in": ["draft", "rejected"]},
            }
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Post not found")
        out = self._to_out(doc)
        assert out is not None
        return out

    async def list_public(self, *, skip: int = 0, limit: int = 20) -> tuple[list[BlogPostOut], int]:
        q = {"status": "published"}
        lim = min(max(limit, 1), 200)
        cur = self.col.find(q).sort("published_at", -1).skip(max(skip, 0)).limit(lim)
        items = await cur.to_list(length=lim)
        total = await self.col.count_documents(q)
        return [o for d in items if (o := self._to_out(d, resanitize_html=True))], total

    async def get_public_by_slug(self, slug: str) -> BlogPostOut | None:
        doc = await self.col.find_one({"slug": slug.lower(), "status": "published"})
        return self._to_out(doc, resanitize_html=True)

    async def list_my(self, user: dict) -> list[BlogPostOut]:
        aid = self._author_id(user)
        # Include published/unpublished so authors still see posts after moderation.
        q = {
            "author_id": aid,
            "status": {
                "$in": ["draft", "pending_review", "rejected", "published", "unpublished"]
            },
        }
        cur = self.col.find(q).sort("updated_at", -1)
        items = await cur.to_list(length=200)
        return [o for d in items if (o := self._to_out(d))]

    async def create_my(self, user: dict, body: Any) -> BlogPostOut:
        title = body.title.strip()
        slug = (body.slug or "").strip().lower() or slugify_title(title)
        validate_slug(slug)
        ensure_editor_js_root(body.blocks)
        blocks = sanitize_blocks_inplace(body.blocks)
        nowdt = utcnow()
        doc = {
            "slug": slug,
            "title": title,
            "excerpt": (body.excerpt or "").strip() or None,
            "status": "draft",
            "content_mode": "blocks",
            "blocks": blocks,
            "html_body": None,
            "html_source_version": None,
            "author_id": self._author_id(user),
            "author_name": self._display_name_from_user(user),
            "created_at": nowdt,
            "updated_at": nowdt,
            "published_at": None,
            "seo": body.seo.model_dump(exclude_none=True),
            "moderation": {},
        }
        res = await self.col.insert_one(doc)
        doc["_id"] = res.inserted_id
        out = self._to_out(doc)
        assert out is not None
        return out

    async def update_my(self, user: dict, post_id: str, body: Any) -> BlogPostOut:
        try:
            oid = ObjectId(post_id)
        except InvalidId as e:
            raise HTTPException(status_code=404, detail="Post not found") from e
        aid = self._author_id(user)
        doc = await self.col.find_one({"_id": oid, "author_id": aid})
        if not doc:
            raise HTTPException(status_code=404, detail="Post not found")
        if doc["status"] not in ("draft", "rejected"):
            raise HTTPException(status_code=400, detail="Can only edit draft or rejected posts")
        patch: dict[str, Any] = {"updated_at": utcnow()}
        if body.title is not None:
            patch["title"] = body.title.strip()
        if body.slug is not None:
            s = body.slug.strip().lower()
            validate_slug(s)
            patch["slug"] = s
        if body.excerpt is not None:
            patch["excerpt"] = body.excerpt.strip() or None
        if body.blocks is not None:
            ensure_editor_js_root(body.blocks)
            patch["blocks"] = sanitize_blocks_inplace(body.blocks)
        if body.seo is not None:
            patch["seo"] = body.seo.model_dump(exclude_none=True)
        if doc.get("status") == "rejected":
            m = dict(doc.get("moderation") or {})
            m["rejection_reason"] = None
            m["reviewed_by"] = None
            m["reviewed_at"] = None
            patch["moderation"] = m
        await self.col.update_one({"_id": oid}, {"$set": patch})
        updated = await self.col.find_one({"_id": oid})
        out = self._to_out(updated)
        assert out is not None
        return out

    async def submit_my(self, user: dict, post_id: str) -> BlogPostOut:
        try:
            oid = ObjectId(post_id)
        except InvalidId as e:
            raise HTTPException(status_code=404, detail="Post not found") from e
        aid = self._author_id(user)
        doc = await self.col.find_one({"_id": oid, "author_id": aid})
        if not doc:
            raise HTTPException(status_code=404, detail="Post not found")
        if doc["content_mode"] != "blocks":
            raise HTTPException(status_code=400, detail="Invalid content mode")
        if doc["status"] not in ("draft", "rejected"):
            raise HTTPException(status_code=400, detail="Cannot submit this post")
        slug = doc["slug"]
        if await self.published_slug_taken(slug, exclude_id=oid):
            raise HTTPException(status_code=400, detail="Slug already used by a published post")
        nowdt = utcnow()
        await self.col.update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": "pending_review",
                    "updated_at": nowdt,
                    "moderation.submitted_at": nowdt,
                    "moderation.rejection_reason": None,
                }
            },
        )
        updated = await self.col.find_one({"_id": oid})
        out = self._to_out(updated)
        assert out is not None
        return out

    async def delete_my(self, user: dict, post_id: str) -> None:
        try:
            oid = ObjectId(post_id)
        except InvalidId as e:
            raise HTTPException(status_code=404, detail="Post not found") from e
        aid = self._author_id(user)
        doc = await self.col.find_one({"_id": oid, "author_id": aid})
        if not doc:
            raise HTTPException(status_code=404, detail="Post not found")
        slug = str(doc["slug"])
        await self.col.delete_one({"_id": oid})
        await revalidate_blog_post(slug)

    async def list_admin_pending(self) -> list[BlogPostOut]:
        q = {"status": "pending_review"}
        cur = self.col.find(q).sort([("moderation.submitted_at", -1), ("updated_at", -1)])
        items = await cur.to_list(length=500)
        return [o for d in items if (o := self._to_out(d))]

    async def list_admin_all(self, *, skip: int = 0, limit: int = 100) -> list[BlogPostOut]:
        """All posts for admin dashboard (not the same as the pending-review queue)."""
        cur = self.col.find({}).sort([("updated_at", -1)]).skip(skip).limit(limit)
        items = await cur.to_list(length=limit)
        return [o for d in items if (o := self._to_out(d, resanitize_html=True))]

    async def admin_get(self, post_id: str) -> BlogPostOut:
        try:
            oid = ObjectId(post_id)
        except InvalidId as e:
            raise HTTPException(status_code=404, detail="Post not found") from e
        doc = await self.col.find_one({"_id": oid})
        if not doc:
            raise HTTPException(status_code=404, detail="Post not found")
        out = self._to_out(doc, resanitize_html=True)
        assert out is not None
        return out

    async def admin_approve(self, admin_user: dict, post_id: str) -> BlogPostOut:
        try:
            oid = ObjectId(post_id)
        except InvalidId as e:
            raise HTTPException(status_code=404, detail="Post not found") from e
        doc = await self.col.find_one({"_id": oid, "status": "pending_review"})
        if not doc:
            raise HTTPException(status_code=404, detail="Post not in review queue")
        slug = doc["slug"]
        if await self.published_slug_taken(slug, exclude_id=oid):
            raise HTTPException(status_code=400, detail="Slug conflict with another published post")
        nowdt = utcnow()
        admin_id = str(admin_user["_id"])
        await self.col.update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": "published",
                    "published_at": nowdt,
                    "updated_at": nowdt,
                    "moderation.reviewed_by": admin_id,
                    "moderation.reviewed_at": nowdt,
                    "moderation.rejection_reason": None,
                }
            },
        )
        await revalidate_blog_post(slug)
        out = self._to_out(await self.col.find_one({"_id": oid}), resanitize_html=True)
        assert out is not None
        return out

    async def admin_reject(self, admin_user: dict, post_id: str, body: Any) -> BlogPostOut:
        try:
            oid = ObjectId(post_id)
        except InvalidId as e:
            raise HTTPException(status_code=404, detail="Post not found") from e
        doc = await self.col.find_one({"_id": oid, "status": "pending_review"})
        if not doc:
            raise HTTPException(status_code=404, detail="Post not in review queue")
        nowdt = utcnow()
        admin_id = str(admin_user["_id"])
        await self.col.update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": "rejected",
                    "updated_at": nowdt,
                    "moderation.reviewed_by": admin_id,
                    "moderation.reviewed_at": nowdt,
                    "moderation.rejection_reason": body.reason.strip(),
                }
            },
        )
        out = self._to_out(await self.col.find_one({"_id": oid}))
        assert out is not None
        return out

    async def admin_delete(self, post_id: str) -> None:
        try:
            oid = ObjectId(post_id)
        except InvalidId as e:
            raise HTTPException(status_code=404, detail="Post not found") from e
        doc = await self.col.find_one({"_id": oid})
        if not doc:
            raise HTTPException(status_code=404, detail="Post not found")
        slug = str(doc["slug"])
        await self.col.delete_one({"_id": oid})
        await revalidate_blog_post(slug)

    def _admin_update_author_name(self, doc: dict, body: Any, admin_user: dict) -> str:
        raw = getattr(body, "author_name", None)
        if raw is not None and str(raw).strip():
            return str(raw).strip()[:120]
        existing = doc.get("author_name")
        if existing and str(existing).strip():
            return str(existing).strip()[:120]
        return self._display_name_from_user(admin_user)

    async def admin_bulk_delete(self, post_ids: list[str]) -> None:
        seen: list[ObjectId] = []
        for pid in post_ids:
            try:
                oid = ObjectId(pid)
            except InvalidId as e:
                raise HTTPException(status_code=400, detail=f"Invalid post id: {pid}") from e
            if oid not in seen:
                seen.append(oid)
        if not seen:
            raise HTTPException(status_code=400, detail="No post ids provided")
        cursor = self.col.find({"_id": {"$in": seen}})
        docs = await cursor.to_list(length=len(seen))
        if not docs:
            raise HTTPException(status_code=404, detail="No matching posts found")
        slugs: list[str] = []
        for d in docs:
            s = str(d["slug"])
            if s not in slugs:
                slugs.append(s)
        await self.col.delete_many({"_id": {"$in": [d["_id"] for d in docs]}})
        paths = ["/blog", "/sitemap.xml"] + [f"/blog/{s}" for s in slugs]
        await revalidate_paths(paths)

    async def admin_create(self, admin_user: dict, body: Any) -> BlogPostOut:
        slug = body.slug.strip().lower()
        validate_slug(slug)
        if body.status == "published" and await self.published_slug_taken(slug):
            raise HTTPException(status_code=400, detail="Slug already published")
        nowdt = utcnow()
        aid = str(admin_user["_id"])
        blocks = None
        html: str | None = None
        if body.content_mode == "admin_html":
            if not body.html_body or not str(body.html_body).strip():
                raise HTTPException(status_code=400, detail="html_body required for admin_html")
            html = sanitize_admin_blog_html(str(body.html_body))
        else:
            if not body.blocks:
                raise HTTPException(status_code=400, detail="blocks required for blocks mode")
            ensure_editor_js_root(body.blocks)
            blocks = sanitize_blocks_inplace(body.blocks)
        published_at = nowdt if body.status == "published" else None
        author_label = self._normalize_author_name_input(
            getattr(body, "author_name", None),
            admin_user,
        )
        doc = {
            "slug": slug,
            "title": body.title.strip(),
            "excerpt": (body.excerpt or "").strip() or None,
            "status": body.status,
            "content_mode": body.content_mode,
            "blocks": blocks,
            "html_body": html,
            "html_source_version": (body.html_source_version or "").strip() or None,
            "author_id": aid,
            "author_name": author_label,
            "created_at": nowdt,
            "updated_at": nowdt,
            "published_at": published_at,
            "seo": body.seo.model_dump(exclude_none=True),
            "moderation": {},
        }
        res = await self.col.insert_one(doc)
        doc["_id"] = res.inserted_id
        if body.status == "published":
            await revalidate_blog_post(slug)
        out = self._to_out(doc, resanitize_html=True)
        assert out is not None
        return out

    async def admin_update(self, admin_user: dict, post_id: str, body: Any) -> BlogPostOut:
        try:
            oid = ObjectId(post_id)
        except InvalidId as e:
            raise HTTPException(status_code=404, detail="Post not found") from e
        doc = await self.col.find_one({"_id": oid})
        if not doc:
            raise HTTPException(status_code=404, detail="Post not found")
        old_slug = doc["slug"]
        old_status = doc["status"]
        slug = body.slug.strip().lower()
        validate_slug(slug)
        if body.status == "published" and await self.published_slug_taken(slug, exclude_id=oid):
            raise HTTPException(status_code=400, detail="Slug already published")

        blocks = doc.get("blocks")
        html = doc.get("html_body")
        content_mode = body.content_mode
        if content_mode == "admin_html":
            if body.html_body is not None and str(body.html_body).strip():
                html = sanitize_admin_blog_html(str(body.html_body))
            elif body.html_body is not None and not str(body.html_body).strip():
                html = ""
            blocks = None
        else:
            if body.blocks is not None:
                ensure_editor_js_root(body.blocks)
                blocks = sanitize_blocks_inplace(body.blocks)
            html = None

        nowdt = utcnow()
        published_at = doc.get("published_at")
        if body.status == "published" and old_status != "published":
            published_at = nowdt
        if body.status != "published":
            published_at = None

        hsv = doc.get("html_source_version")
        if body.html_source_version is not None:
            hsv = (body.html_source_version or "").strip() or None

        patch: dict[str, Any] = {
            "slug": slug,
            "title": body.title.strip(),
            "excerpt": (body.excerpt or "").strip() or None,
            "status": body.status,
            "content_mode": content_mode,
            "blocks": blocks,
            "html_body": html,
            "html_source_version": hsv,
            "updated_at": nowdt,
            "published_at": published_at,
            "seo": body.seo.model_dump(exclude_none=True),
            "author_name": self._admin_update_author_name(doc, body, admin_user),
        }
        await self.col.update_one({"_id": oid}, {"$set": patch})
        updated = await self.col.find_one({"_id": oid})
        assert updated is not None
        if body.status == "published" or old_status == "published":
            await revalidate_blog_post(updated["slug"])
            if old_slug != updated["slug"] and old_status == "published":
                await revalidate_blog_post(old_slug)
        out = self._to_out(updated, resanitize_html=True)
        assert out is not None
        return out
