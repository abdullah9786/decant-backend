from fastapi import APIRouter, Depends, status, HTTPException
from typing import List
from app.schemas.offer import OfferCreate, OfferUpdate, OfferOut
from app.services.offer_service import OfferService
from app.db.mongodb import get_database
from app.utils.deps import require_admin

router = APIRouter(prefix="/offers", tags=["offers"])


@router.get("/active", response_model=List[OfferOut])
async def get_active_offers(db=Depends(get_database)):
    service = OfferService(db)
    return await service.get_active()


@router.get("", response_model=List[OfferOut])
async def get_all_offers(db=Depends(get_database), _admin=Depends(require_admin)):
    service = OfferService(db)
    return await service.get_all()


@router.get("/{id}", response_model=OfferOut)
async def get_offer(id: str, db=Depends(get_database)):
    service = OfferService(db)
    offer = await service.get_by_id(id)
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")
    return offer


@router.post("", response_model=OfferOut, status_code=status.HTTP_201_CREATED)
async def create_offer(offer_in: OfferCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    service = OfferService(db)
    return await service.create(offer_in)


@router.put("/{id}", response_model=OfferOut)
async def update_offer(id: str, offer_in: OfferUpdate, db=Depends(get_database), _admin=Depends(require_admin)):
    service = OfferService(db)
    return await service.update(id, offer_in)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_offer(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    service = OfferService(db)
    await service.delete(id)
    return None
