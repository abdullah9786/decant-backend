from __future__ import annotations

import logging

import httpx

from app.config.config import settings

logger = logging.getLogger(__name__)

DAILY_DEAL_TAG = "daily-deal"


async def _post_revalidation(*, paths: list[str], tags: list[str]) -> None:
    if not settings.REVALIDATE_SECRET or not settings.FRONTEND_URL:
        return

    payload: dict = {}
    if paths:
        payload["paths"] = paths
    if tags:
        payload["tags"] = tags
    if not payload:
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{settings.FRONTEND_URL.rstrip('/')}/api/revalidate",
                json=payload,
                headers={"x-revalidate-secret": settings.REVALIDATE_SECRET},
            )
            if response.status_code >= 400:
                logger.warning(
                    "Revalidation failed (%s): %s",
                    response.status_code,
                    response.text,
                )
    except Exception as exc:
        logger.warning("Revalidation request error: %s", exc)


async def revalidate_paths(paths: list[str]) -> None:
    await _post_revalidation(paths=paths, tags=[])


async def revalidate_tags(tags: list[str]) -> None:
    await _post_revalidation(paths=[], tags=tags)


def _product_paths(product_id: str, slug: str | None = None) -> list[str]:
    paths = ["/", "/products", "/sitemap.xml", f"/products/{product_id}"]
    if slug and slug != product_id:
        paths.append(f"/products/{slug}")
    return paths


async def revalidate_product(product_id: str, slug: str | None = None) -> None:
    """Invalidate catalog + product detail pages after admin product changes."""
    await revalidate_paths(_product_paths(product_id, slug))


async def revalidate_products_catalog() -> None:
    """Invalidate list/home pages when many products change (bulk chips, brands)."""
    await revalidate_paths(["/", "/products", "/sitemap.xml"])


async def revalidate_category(slug: str | None = None) -> None:
    paths = ["/", "/categories"]
    if slug:
        paths.append(f"/categories/{slug}")
    await revalidate_paths(paths)


async def revalidate_gift_box(box_id: str) -> None:
    await revalidate_paths(["/gift-boxes", f"/gift-boxes/{box_id}"])


async def revalidate_fragrance_families() -> None:
    await revalidate_paths(["/", "/products", "/families"])


async def revalidate_bottles() -> None:
    await revalidate_paths(["/bottles", "/products"])


async def revalidate_brands() -> None:
    await revalidate_paths(["/brands", "/products"])


async def revalidate_influencer(username: str | None = None) -> None:
    paths = ["/creators", "/sitemap.xml"]
    if username:
        paths.append(f"/{username}")
    await revalidate_paths(paths)


async def revalidate_daily_deal(product_ids: list[str] | None = None) -> None:
    """Invalidate deal surfaces site-wide (root layout + homepage + deal page)."""
    paths = ["/", "/deals/today", "/products"]
    seen = set(paths)
    for pid in product_ids or []:
        if not pid:
            continue
        path = f"/products/{pid}"
        if path not in seen:
            paths.append(path)
            seen.add(path)
    await _post_revalidation(
        paths=paths,
        tags=[DAILY_DEAL_TAG],
    )


async def revalidate_blog_post(slug: str | None = None) -> None:
    """Invalidate blog listing, article, and sitemap after publish/update/unpublish."""
    paths = ["/blog", "/sitemap.xml"]
    if slug:
        paths.append(f"/blog/{slug}")
    await revalidate_paths(paths)


async def revalidate_product_reviews(product_id: str, slug: str | None = None) -> None:
    """Invalidate cached review data and the product detail page (SEO JSON-LD)."""
    tag = f"product-reviews:{product_id}"
    paths = [f"/products/{product_id}"]
    if slug and slug != product_id:
        paths.append(f"/products/{slug}")
    await _post_revalidation(paths=paths, tags=[tag])
