from fastapi import APIRouter, Depends, status, HTTPException
from typing import List
from app.schemas.chip import ChipCreate, ChipUpdate, ChipOut
from app.services.chip_service import ChipService
from app.db.mongodb import get_database
from app.utils.deps import require_admin

router = APIRouter(prefix="/chips", tags=["chips"])


@router.get("/active", response_model=List[ChipOut])
async def get_active_chips(db=Depends(get_database)):
    service = ChipService(db)
    return await service.get_active()


@router.get("", response_model=List[ChipOut])
async def get_all_chips(db=Depends(get_database), _admin=Depends(require_admin)):
    service = ChipService(db)
    return await service.get_all()


@router.get("/{id}", response_model=ChipOut)
async def get_chip(id: str, db=Depends(get_database)):
    service = ChipService(db)
    chip = await service.get_by_id(id)
    if not chip:
        raise HTTPException(status_code=404, detail="Chip not found")
    return chip


@router.post("", response_model=ChipOut, status_code=status.HTTP_201_CREATED)
async def create_chip(chip_in: ChipCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    service = ChipService(db)
    try:
        return await service.create(chip_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{id}", response_model=ChipOut)
async def update_chip(id: str, chip_in: ChipUpdate, db=Depends(get_database), _admin=Depends(require_admin)):
    service = ChipService(db)
    try:
        return await service.update(id, chip_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chip(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    service = ChipService(db)
    await service.delete(id)
    return None
