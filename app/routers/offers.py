from bson import ObjectId
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Any, List
from app.schemas.offer import OfferCreate, OfferUpdate, OfferOut
from app.services.offer_service import OfferService
from app.services.pricing_service import apply_daily_deal
from app.db.mongodb import get_database
from app.utils.deps import require_admin

router = APIRouter(prefix="/offers", tags=["offers"])


def _jsonify(value: Any) -> Any:
    """Recursively convert Mongo-specific types (ObjectId, datetime) into
    JSON-safe primitives. We need this because the daily-deal endpoint
    returns a free-form `{deal, products}` shape that isn't bound to a
    Pydantic response_model (the variants carry runtime-added pricing
    fields that don't sit cleanly on `ProductOut`).
    """
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify(v) for v in value]
    # Pass datetimes / primitives straight through; FastAPI's default
    # encoder handles them fine.
    return value


@router.get("/active", response_model=List[OfferOut])
async def get_active_offers(db=Depends(get_database)):
    service = OfferService(db)
    return await service.get_active()


@router.get("/daily-deal/today")
async def get_today_daily_deal(db=Depends(get_database)):
    """Public, cache-friendly endpoint that drives every consumer surface.

    Returns `{ deal, products }` with each deal product fully annotated with
    sale prices, or `null` when no deal is active. The frontend uses this
    single payload for the hero swap, banner, marquee, promo modal,
    homepage rail, and the dedicated /deals/today page.
    """
    service = OfferService(db)
    deal = await service.get_active_daily_deal()
    if not deal:
        return None
    config = deal.get("config") or {}
    product_ids = [pid for pid in (config.get("product_ids") or []) if ObjectId.is_valid(str(pid))]
    if not product_ids:
        return _jsonify({"deal": deal, "products": []})
    cursor = db["products"].find({
        "_id": {"$in": [ObjectId(pid) for pid in product_ids]},
        "is_active": True,
    })
    docs = await cursor.to_list(length=100)
    annotated = [apply_daily_deal(p, deal) for p in docs]
    return _jsonify({"deal": deal, "products": annotated})


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


async def _reject_overlapping_daily_deal(
    service: OfferService,
    offer_type: str | None,
    starts_at,
    ends_at,
    is_active: bool | None,
    exclude_id: str | None = None,
):
    """Hard-reject overlapping `daily_deal` offers so the active-window
    query always returns at most one. Open-ended deals (missing dates) are
    rejected outright because they would silently shadow every future day.
    """
    if offer_type != "daily_deal":
        return
    if not is_active:
        return
    if starts_at is None or ends_at is None:
        raise HTTPException(
            status_code=400,
            detail="Daily deals must have both a start and end date.",
        )
    overlapping = await service.get_overlapping_daily_deals(
        starts_at, ends_at, exclude_id=exclude_id,
    )
    if overlapping:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Another daily deal '{overlapping[0].get('name', '?')}' is already "
                "scheduled in this window. Edit or deactivate it first."
            ),
        )


@router.post("", response_model=OfferOut, status_code=status.HTTP_201_CREATED)
async def create_offer(offer_in: OfferCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    service = OfferService(db)
    await _reject_overlapping_daily_deal(
        service,
        offer_in.type,
        offer_in.starts_at,
        offer_in.ends_at,
        offer_in.is_active,
    )
    return await service.create(offer_in)


@router.put("/{id}", response_model=OfferOut)
async def update_offer(id: str, offer_in: OfferUpdate, db=Depends(get_database), _admin=Depends(require_admin)):
    service = OfferService(db)
    current = await service.get_by_id(id)
    if not current:
        raise HTTPException(status_code=404, detail="Offer not found")
    merged_type = offer_in.type if offer_in.type is not None else current.get("type")
    merged_starts = offer_in.starts_at if offer_in.starts_at is not None else current.get("starts_at")
    merged_ends = offer_in.ends_at if offer_in.ends_at is not None else current.get("ends_at")
    merged_active = (
        offer_in.is_active if offer_in.is_active is not None else current.get("is_active", True)
    )
    await _reject_overlapping_daily_deal(
        service,
        merged_type,
        merged_starts,
        merged_ends,
        merged_active,
        exclude_id=id,
    )
    return await service.update(id, offer_in)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_offer(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    service = OfferService(db)
    await service.delete(id)
    return None
