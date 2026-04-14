from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.schemas.category import CategoryOut, CategoryCreate, CategoryUpdate
from app.services.category_service import CategoryService
from app.db.mongodb import get_database
from app.utils.deps import require_admin

router = APIRouter(prefix="/categories", tags=["categories"])

@router.get("", response_model=List[CategoryOut])
async def get_categories(include_inactive: bool = False, db=Depends(get_database)):
    service = CategoryService(db)
    return await service.get_all(include_inactive)

@router.get("/{id}", response_model=CategoryOut)
async def get_category(id: str, db=Depends(get_database)):
    service = CategoryService(db)
    cat = await service.get_by_id(id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat

@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(cat_in: CategoryCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    service = CategoryService(db)
    return await service.create(cat_in)

@router.put("/{id}", response_model=CategoryOut)
async def update_category(id: str, cat_in: CategoryUpdate, db=Depends(get_database), _admin=Depends(require_admin)):
    service = CategoryService(db)
    updated = await service.update(id, cat_in)
    if not updated:
        raise HTTPException(status_code=404, detail="Category not found")
    return updated

@router.delete("/{id}")
async def delete_category(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    service = CategoryService(db)
    result = await service.delete(id)
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"message": "Category deleted successfully"}
