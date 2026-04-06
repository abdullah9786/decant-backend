from fastapi import APIRouter, Depends, status, HTTPException
from typing import List
from app.schemas.gift_box import GiftBoxCreate, GiftBoxUpdate, GiftBoxOut
from app.services.gift_box_service import GiftBoxService
from app.db.mongodb import get_database
from app.utils.deps import require_admin

router = APIRouter(prefix="/gift-boxes", tags=["gift-boxes"])


@router.get("", response_model=List[GiftBoxOut])
async def get_gift_boxes(
    include_inactive: bool = False,
    db=Depends(get_database),
):
    service = GiftBoxService(db)
    return await service.get_all(include_inactive)


@router.get("/{id}", response_model=GiftBoxOut)
async def get_gift_box(id: str, db=Depends(get_database)):
    service = GiftBoxService(db)
    box = await service.get_by_id(id)
    if not box:
        raise HTTPException(status_code=404, detail="Gift box not found")
    return box


@router.post("", response_model=GiftBoxOut, status_code=status.HTTP_201_CREATED)
async def create_gift_box(
    box_in: GiftBoxCreate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    service = GiftBoxService(db)
    return await service.create(box_in)


@router.put("/{id}", response_model=GiftBoxOut)
async def update_gift_box(
    id: str,
    box_in: GiftBoxUpdate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    service = GiftBoxService(db)
    updated = await service.update(id, box_in)
    if not updated:
        raise HTTPException(status_code=404, detail="Gift box not found")
    return updated


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gift_box(
    id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    service = GiftBoxService(db)
    await service.delete(id)
    return None
