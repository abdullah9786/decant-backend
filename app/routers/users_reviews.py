from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import List, Optional
from datetime import datetime

from app.schemas.user import UserOut, UserCreate, UserListOut
from app.schemas.order import OrderOut
from app.schemas.review import (
    ReviewCreate,
    ReviewOut,
    ReviewAdminOut,
    ReviewSummary,
    ReviewEligibility,
    AdminReviewBulkCreate,
    ReviewUpdate,
    ReviewBulkIds,
    ReviewBulkPublish,
)
from app.services.user_service import UserService
from app.services.review_service import ReviewService
from app.services.order_service import OrderService
from app.db.mongodb import get_database
from app.utils.deps import get_current_user, require_admin

from pydantic import BaseModel


class UserOrderStats(BaseModel):
    total_orders: int = 0
    total_spent: float = 0
    last_order_at: Optional[datetime] = None


class UserOrdersResponse(BaseModel):
    user: UserOut
    orders: List[OrderOut]
    stats: UserOrderStats


user_router = APIRouter(prefix="/users", tags=["users"])
review_router = APIRouter(prefix="/reviews", tags=["reviews"])


@user_router.get("")
async def get_users(
    skip: int = 0,
    limit: int = 100,
    db=Depends(get_database),
    _admin=Depends(require_admin)
):
    user_service = UserService(db)
    order_service = OrderService(db)
    result = await user_service.get_all(skip=skip, limit=limit)
    users = result["items"]
    bulk_stats = await order_service.bulk_stats_for_users(users)

    enriched = []
    for user in users:
        uid = str(user["_id"])
        stats = bulk_stats.get(uid, {"order_count": 0, "order_total": 0.0})
        enriched.append({
            **user,
            "order_count": stats["order_count"],
            "order_total": stats["order_total"],
        })
    
    return {
        "items": enriched,
        "total": result["total"],
        "skip": result["skip"],
        "limit": result["limit"],
        "has_more": result["has_more"]
    }


@user_router.post("/create-admin", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_admin(user_in: UserCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    user_service = UserService(db)
    return await user_service.create_admin(user_in.dict())


@user_router.get("/{id}/orders", response_model=UserOrdersResponse)
async def get_user_orders(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    user_service = UserService(db)
    user = await user_service.get_by_id(id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    order_service = OrderService(db)
    orders = await order_service.get_for_user(str(user["_id"]), user.get("email"))
    stats = await order_service.stats_for_user(str(user["_id"]), user.get("email"))
    last_order_at = orders[0].get("created_at") if orders else None

    return {
        "user": user,
        "orders": orders,
        "stats": {
            "total_orders": stats["order_count"],
            "total_spent": stats["order_total"],
            "last_order_at": last_order_at,
        },
    }


@user_router.get("/{id}", response_model=UserOut)
async def get_user(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    user_service = UserService(db)
    user = await user_service.get_by_id(id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@user_router.delete("/{id}")
async def delete_user(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    user_service = UserService(db)
    result = await user_service.delete(id)
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted successfully"}


@user_router.post("/{id}/toggle-admin", response_model=UserOut)
async def toggle_admin(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    user_service = UserService(db)
    updated_user = await user_service.toggle_admin(id)
    if not updated_user:
        raise HTTPException(status_code=404, detail="User not found")
    return updated_user


@review_router.post("", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
async def create_review(
    review_in: ReviewCreate,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    review_service = ReviewService(db)
    return await review_service.create_customer_review(current_user, review_in)


@review_router.get("/product/{product_id}", response_model=List[ReviewOut])
async def get_product_reviews(
    product_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db=Depends(get_database),
):
    review_service = ReviewService(db)
    return await review_service.get_by_product(product_id, skip=skip, limit=limit)


@review_router.get("/product/{product_id}/summary", response_model=ReviewSummary)
async def get_product_review_summary(product_id: str, db=Depends(get_database)):
    review_service = ReviewService(db)
    return await review_service.get_summary(product_id)


@review_router.get("/product/{product_id}/eligibility", response_model=ReviewEligibility)
async def get_review_eligibility(
    product_id: str,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    review_service = ReviewService(db)
    return await review_service.get_eligibility(str(current_user["_id"]), product_id)


@review_router.get("/admin", response_model=List[ReviewAdminOut])
async def list_reviews_admin(
    product_id: Optional[str] = None,
    source: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    review_service = ReviewService(db)
    return await review_service.list_admin(
        product_id=product_id,
        source=source,
        skip=skip,
        limit=limit,
    )


@review_router.post("/admin/bulk", status_code=status.HTTP_201_CREATED)
async def bulk_create_reviews_admin(
    payload: AdminReviewBulkCreate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    review_service = ReviewService(db)
    return await review_service.bulk_create_admin(payload)


@review_router.patch("/admin/bulk-publish")
async def bulk_publish_reviews_admin(
    body: ReviewBulkPublish,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    review_service = ReviewService(db)
    return await review_service.bulk_update_published(body.review_ids, body.is_published)


@review_router.post("/admin/bulk-delete")
async def bulk_delete_reviews_admin(
    body: ReviewBulkIds,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    review_service = ReviewService(db)
    return await review_service.bulk_delete(body.review_ids)


@review_router.patch("/{review_id}", response_model=ReviewAdminOut)
async def update_review_admin(
    review_id: str,
    patch: ReviewUpdate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    review_service = ReviewService(db)
    return await review_service.update_admin(review_id, patch)


@review_router.delete("/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review_admin(
    review_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    review_service = ReviewService(db)
    await review_service.delete(review_id)
