from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.mongodb import get_database
from app.schemas.blog import (
    BlogAdminBulkDelete,
    BlogAdminUpsert,
    BlogListResponse,
    BlogMyCreate,
    BlogMyUpdate,
    BlogPostOut,
    BlogRejectBody,
)
from app.services.blog_service import BlogService
from app.utils.deps import get_current_user, require_admin

router = APIRouter(prefix="/blog", tags=["blog"])


# --- Authenticated author (declare before `/{slug}`) ---


@router.get("/my", response_model=list[BlogPostOut])
async def list_my_posts(
    db=Depends(get_database),
    user=Depends(get_current_user),
):
    svc = BlogService(db)
    return await svc.list_my(user)


@router.post("/my", response_model=BlogPostOut)
async def create_my_post(
    body: BlogMyCreate,
    db=Depends(get_database),
    user=Depends(get_current_user),
):
    svc = BlogService(db)
    return await svc.create_my(user, body)


@router.get("/my/{post_id}", response_model=BlogPostOut)
async def get_my_post(
    post_id: str,
    db=Depends(get_database),
    user=Depends(get_current_user),
):
    svc = BlogService(db)
    return await svc.get_my(user, post_id)


@router.put("/my/{post_id}", response_model=BlogPostOut)
async def update_my_post(
    post_id: str,
    body: BlogMyUpdate,
    db=Depends(get_database),
    user=Depends(get_current_user),
):
    svc = BlogService(db)
    return await svc.update_my(user, post_id, body)


@router.post("/my/{post_id}/submit", response_model=BlogPostOut)
async def submit_my_post(
    post_id: str,
    db=Depends(get_database),
    user=Depends(get_current_user),
):
    svc = BlogService(db)
    return await svc.submit_my(user, post_id)


@router.delete("/my/{post_id}", status_code=204)
async def delete_my_post(
    post_id: str,
    db=Depends(get_database),
    user=Depends(get_current_user),
):
    svc = BlogService(db)
    await svc.delete_my(user, post_id)


# --- Admin ---


@router.get("/admin/queue", response_model=list[BlogPostOut])
async def admin_pending_queue(
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    svc = BlogService(db)
    return await svc.list_admin_pending()


@router.get("/admin/all", response_model=list[BlogPostOut])
async def admin_list_all_posts(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    svc = BlogService(db)
    return await svc.list_admin_all(skip=skip, limit=limit)


@router.post("/admin/bulk-delete", status_code=204)
async def admin_bulk_delete_posts(
    body: BlogAdminBulkDelete,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    svc = BlogService(db)
    await svc.admin_bulk_delete(body.post_ids)


@router.get("/admin/{post_id}", response_model=BlogPostOut)
async def admin_get_post(
    post_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    svc = BlogService(db)
    return await svc.admin_get(post_id)


@router.post("/admin", response_model=BlogPostOut)
async def admin_create_post(
    body: BlogAdminUpsert,
    db=Depends(get_database),
    admin=Depends(require_admin),
):
    svc = BlogService(db)
    return await svc.admin_create(admin, body)


@router.put("/admin/{post_id}", response_model=BlogPostOut)
async def admin_update_post(
    post_id: str,
    body: BlogAdminUpsert,
    db=Depends(get_database),
    admin=Depends(require_admin),
):
    svc = BlogService(db)
    return await svc.admin_update(admin, post_id, body)


@router.post("/admin/{post_id}/approve", response_model=BlogPostOut)
async def admin_approve_post(
    post_id: str,
    db=Depends(get_database),
    admin=Depends(require_admin),
):
    svc = BlogService(db)
    return await svc.admin_approve(admin, post_id)


@router.post("/admin/{post_id}/reject", response_model=BlogPostOut)
async def admin_reject_post(
    post_id: str,
    body: BlogRejectBody,
    db=Depends(get_database),
    admin=Depends(require_admin),
):
    svc = BlogService(db)
    return await svc.admin_reject(admin, post_id, body)


@router.delete("/admin/{post_id}", status_code=204)
async def admin_delete_post(
    post_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    svc = BlogService(db)
    await svc.admin_delete(post_id)


# --- Public ---


@router.get("", response_model=BlogListResponse)
async def list_published_posts(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db=Depends(get_database),
):
    svc = BlogService(db)
    items, total = await svc.list_public(skip=skip, limit=limit)
    return BlogListResponse(items=items, total=total)


@router.get("/{slug}", response_model=BlogPostOut)
async def get_published_post(slug: str, db=Depends(get_database)):
    svc = BlogService(db)
    post = await svc.get_public_by_slug(slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post
