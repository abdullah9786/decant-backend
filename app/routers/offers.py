from bson import ObjectId
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Any, List
from app.schemas.offer import OfferCreate, OfferUpdate, OfferOut
from app.services.offer_service import OfferService
from app.services.pricing_service import apply_daily_deal
from app.db.mongodb import get_database
from app.utils.deps import require_admin
from app.utils.revalidate import revalidate_daily_deal

router = APIRouter(prefix="/offers", tags=["offers"])


def _daily_deal_product_ids(offer: dict | None) -> list[str]:
    if not offer or offer.get("type") != "daily_deal":
        return []
    config = offer.get("config") or {}
    return [str(pid) for pid in (config.get("product_ids") or []) if pid]


async def _daily_deal_pdp_slug_paths(db, product_ids: list[str]) -> list[str]:
    """``/products/{slug}`` paths for ``revalidatePath`` (id-only paths miss real URLs)."""
    out: list[str] = []
    seen: set[str] = set()
    for pid in product_ids:
        if not pid or not ObjectId.is_valid(str(pid)):
            continue
        doc = await db["products"].find_one(
            {"_id": ObjectId(str(pid))},
            {"slug": 1},
        )
        if not doc:
            continue
        slug = doc.get("slug")
        if not slug or str(slug) == str(pid):
            continue
        path = f"/products/{slug}"
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


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


def _validate_mystery_gift_offer(offer_type: str | None, config: dict | None) -> None:
    """Reject malformed mystery-gift tiers so the storefront ladder and
    server-side tier resolution always have clean data to work with.
    """
    if offer_type != "mystery_gift":
        return
    tiers = (config or {}).get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise HTTPException(
            status_code=400,
            detail="A mystery gift offer needs at least one tier.",
        )
    seen_ids: set[str] = set()
    for idx, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            raise HTTPException(status_code=400, detail=f"Tier {idx + 1} is invalid.")
        name = (tier.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail=f"Tier {idx + 1} needs a name.")
        try:
            threshold = float(tier.get("min_subtotal"))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"Tier '{name}' needs a numeric unlock amount.",
            )
        if threshold <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Tier '{name}' unlock amount must be greater than 0.",
            )
        tier_id = (tier.get("id") or "").strip()
        if not tier_id:
            raise HTTPException(status_code=400, detail=f"Tier '{name}' is missing an id.")
        if tier_id in seen_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate tier id '{tier_id}'.",
            )
        seen_ids.add(tier_id)


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
    _validate_mystery_gift_offer(offer_in.type, offer_in.config)
    created = await service.create(offer_in)
    ids = _daily_deal_product_ids(created)
    await revalidate_daily_deal(ids, extra_paths=await _daily_deal_pdp_slug_paths(db, ids))
    return created


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
    if merged_type == "mystery_gift":
        merged_config = offer_in.config if offer_in.config is not None else current.get("config")
        _validate_mystery_gift_offer(merged_type, merged_config)
    updated = await service.update(id, offer_in)
    ids = _daily_deal_product_ids(updated)
    await revalidate_daily_deal(ids, extra_paths=await _daily_deal_pdp_slug_paths(db, ids))
    return updated


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_offer(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    service = OfferService(db)
    existing = await service.get_by_id(id)
    ids = _daily_deal_product_ids(existing)
    await service.delete(id)
    await revalidate_daily_deal(ids, extra_paths=await _daily_deal_pdp_slug_paths(db, ids))
    return None
