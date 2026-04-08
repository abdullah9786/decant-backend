from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.schemas.bottle import BottleCreate, BottleUpdate, BottleOut
from app.services.bottle_service import BottleService
from app.db.mongodb import get_database
from app.utils.deps import require_admin

router = APIRouter(prefix="/bottles", tags=["bottles"])


@router.get("", response_model=List[BottleOut])
async def get_bottles(
    include_inactive: bool = False,
    size_ml: Optional[int] = None,
    db=Depends(get_database),
):
    service = BottleService(db)
    bottles = await service.get_all(include_inactive)
    if size_ml is not None:
        bottles = [b for b in bottles if size_ml in (b.get("compatible_sizes") or [])]
    return bottles


@router.get("/{id}", response_model=BottleOut)
async def get_bottle(id: str, db=Depends(get_database)):
    service = BottleService(db)
    bottle = await service.get_by_id(id)
    if not bottle:
        raise HTTPException(status_code=404, detail="Bottle not found")
    return bottle


@router.post("", response_model=BottleOut, status_code=status.HTTP_201_CREATED)
async def create_bottle(
    data: BottleCreate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    service = BottleService(db)
    return await service.create(data)


@router.put("/{id}", response_model=BottleOut)
async def update_bottle(
    id: str,
    data: BottleUpdate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    service = BottleService(db)
    updated = await service.update(id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Bottle not found")
    return updated


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bottle(
    id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    service = BottleService(db)
    await service.delete(id)
    return None
