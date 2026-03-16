from fastapi import APIRouter, Depends, status, HTTPException
from typing import List
from app.schemas.user import UserOut, UserCreate
from app.schemas.review import ReviewCreate, ReviewOut
from app.services.user_service import UserService, ReviewService
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
async def create_review(review_in: ReviewCreate, db=Depends(get_database), _user=Depends(get_current_user)):
    review_service = ReviewService(db)
    return await review_service.create(review_in)

@review_router.get("/{productId}", response_model=List[ReviewOut])
async def get_reviews(productId: str, db=Depends(get_database)):
    review_service = ReviewService(db)
    return await review_service.get_by_product(productId)
