from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import List, Optional

from app.schemas.user import UserOut, UserCreate
from app.schemas.review import (
    ReviewCreate,
    ReviewOut,
    ReviewAdminOut,
    ReviewSummary,
    ReviewEligibility,
    AdminReviewBulkCreate,
    ReviewUpdate,
)
from app.services.user_service import UserService
from app.services.review_service import ReviewService
from app.db.mongodb import get_database
from app.utils.deps import get_current_user, require_admin

user_router = APIRouter(prefix="/users", tags=["users"])
review_router = APIRouter(prefix="/reviews", tags=["reviews"])


@user_router.get("", response_model=List[UserOut])
async def get_users(db=Depends(get_database), _admin=Depends(require_admin)):
    user_service = UserService(db)
    return await user_service.get_all()


@user_router.post("/create-admin", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_admin(user_in: UserCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    user_service = UserService(db)
    return await user_service.create_admin(user_in.dict())


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
