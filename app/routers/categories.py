from fastapi import APIRouter, Depends, status, HTTPException
from typing import List
from app.schemas.category import CategoryOut, CategoryCreate, CategoryUpdate
from app.services.category_service import CategoryService
from app.db.mongodb import get_database
from app.utils.deps import require_admin

router = APIRouter(prefix="/categories", tags=["categories"])

@router.get("", response_model=List[CategoryOut])
async def get_categories(db=Depends(get_database)):
    category_service = CategoryService(db)
    return await category_service.get_all()

@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(category_in: CategoryCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    category_service = CategoryService(db)
    return await category_service.create(category_in)

@router.put("/{id}", response_model=CategoryOut)
async def update_category(id: str, category_in: CategoryUpdate, db=Depends(get_database), _admin=Depends(require_admin)):
    category_service = CategoryService(db)
    updated = await category_service.update(id, category_in)
    if not updated:
        raise HTTPException(status_code=404, detail="Category not found")
    return updated

@router.delete("/{id}")
async def delete_category(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    category_service = CategoryService(db)
    result = await category_service.delete(id)
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"message": "Category deleted successfully"}
