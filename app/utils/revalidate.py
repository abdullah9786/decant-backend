from __future__ import annotations

import logging

import httpx

from app.config.config import settings

logger = logging.getLogger(__name__)


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


async def revalidate_product_reviews(product_id: str, slug: str | None = None) -> None:
    """Invalidate cached review data and the product detail page."""
    tag = f"product-reviews:{product_id}"
    paths = [f"/products/{product_id}"]
    if slug and slug != product_id:
        paths.append(f"/products/{slug}")
    await _post_revalidation(paths=paths, tags=[tag])
