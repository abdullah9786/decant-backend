from fastapi import APIRouter, Depends, status, HTTPException
from typing import List
from app.schemas.brand import BrandOut, BrandCreate, BrandUpdate
from app.services.brand_service import BrandService
from app.db.mongodb import get_database
from app.utils.deps import require_admin

router = APIRouter(prefix="/brands", tags=["brands"])


@router.get("", response_model=List[BrandOut])
async def get_brands(db=Depends(get_database)):
    brand_service = BrandService(db)
    return await brand_service.get_all()


@router.post("", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
async def create_brand(brand_in: BrandCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    brand_service = BrandService(db)
    return await brand_service.create(brand_in)


@router.put("/{id}", response_model=BrandOut)
async def update_brand(id: str, brand_in: BrandUpdate, db=Depends(get_database), _admin=Depends(require_admin)):
    brand_service = BrandService(db)
    updated = await brand_service.update(id, brand_in)
    if not updated:
        raise HTTPException(status_code=404, detail="Brand not found")
    return updated


@router.delete("/{id}")
async def delete_brand(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    brand_service = BrandService(db)
    result = await brand_service.delete(id)
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Brand not found")
    return {"message": "Brand deleted successfully"}
