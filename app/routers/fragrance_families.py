from fastapi import APIRouter, Depends, status, HTTPException
from typing import List
from app.schemas.fragrance_family import FragranceFamilyOut, FragranceFamilyCreate, FragranceFamilyUpdate
from app.services.fragrance_family_service import FragranceFamilyService
from app.db.mongodb import get_database
from app.utils.deps import require_admin

router = APIRouter(prefix="/fragrance-families", tags=["fragrance-families"])

@router.get("", response_model=List[FragranceFamilyOut])
async def get_fragrance_families(db=Depends(get_database)):
    service = FragranceFamilyService(db)
    return await service.get_all()

@router.post("", response_model=FragranceFamilyOut, status_code=status.HTTP_201_CREATED)
async def create_fragrance_family(family_in: FragranceFamilyCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    service = FragranceFamilyService(db)
    return await service.create(family_in)

@router.put("/{id}", response_model=FragranceFamilyOut)
async def update_fragrance_family(id: str, family_in: FragranceFamilyUpdate, db=Depends(get_database), _admin=Depends(require_admin)):
    service = FragranceFamilyService(db)
    updated = await service.update(id, family_in)
    if not updated:
        raise HTTPException(status_code=404, detail="Fragrance family not found")
    return updated

@router.delete("/{id}")
async def delete_fragrance_family(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    service = FragranceFamilyService(db)
    result = await service.delete(id)
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Fragrance family not found")
    return {"message": "Fragrance family deleted successfully"}
