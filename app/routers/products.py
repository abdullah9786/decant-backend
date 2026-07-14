from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import List, Optional, Union
from app.schemas.product import (
    ProductCreate,
    ProductUpdate,
    ProductOut,
    BulkChipUpdate,
    ProductSearchResponse,
    ProductListResponse,
)
from app.services.product_service import ProductService
from app.services.offer_service import OfferService
from app.services.pricing_service import apply_daily_deal
from app.db.mongodb import get_database
from app.utils.deps import require_admin
from app.utils.revalidate import (
    revalidate_product,
    revalidate_products_catalog,
)

router = APIRouter(prefix="/products", tags=["products"])


def _product_slug(product: dict) -> str | None:
    pid = str(product.get("_id") or product.get("id") or "")
    slug = product.get("slug")
    return slug if slug and slug != pid else None


async def _annotate_with_deal(db, products):
    """Decorate each product's variants with sale_price / discount_percent.

    Fetches the active daily-deal exactly once per request so list endpoints
    don't fan out to N queries.
    """
    offer_service = OfferService(db)
    deal = await offer_service.get_active_daily_deal()
    if isinstance(products, list):
        return [apply_daily_deal(p, deal) for p in products]
    return apply_daily_deal(products, deal) if products else products


@router.post("/bulk-chips")
async def bulk_update_chips(
    body: BulkChipUpdate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    """Atomically add/remove chip ids across many products in one request."""
    product_service = ProductService(db)
    result = await product_service.bulk_update_chips(body.product_ids, body.add, body.remove)
    await revalidate_products_catalog()
    return result

@router.get("", response_model=Union[List[ProductOut], ProductListResponse])
async def get_products(
    fragrance_family: Optional[str] = None,
    brand: Optional[str] = None,
    is_featured: Optional[bool] = None,
    is_new_arrival: Optional[bool] = None,
    q: Optional[str] = None,
    sort_by: Optional[str] = None,
    include_inactive: bool = False,
    category_id: Optional[str] = None,
    product_type: Optional[str] = None,
    exclude_product_type: Optional[str] = None,
    featured_decant: Optional[bool] = None,
    featured_sealed_bottle: Optional[bool] = None,
    paginated: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_database),
):
    product_service = ProductService(db)
    if paginated:
        result = await product_service.get_all(
            fragrance_family,
            brand,
            is_featured,
            is_new_arrival,
            q,
            sort_by,
            include_inactive,
            category_id,
            product_type,
            exclude_product_type,
            featured_decant=featured_decant,
            featured_sealed_bottle=featured_sealed_bottle,
            skip=skip,
            limit=limit,
            paginate=True,
        )
        result["items"] = await _annotate_with_deal(db, result["items"])
        return ProductListResponse(**result)

    products = await product_service.get_all(
        fragrance_family,
        brand,
        is_featured,
        is_new_arrival,
        q,
        sort_by,
        include_inactive,
        category_id,
        product_type,
        exclude_product_type,
        featured_decant=featured_decant,
        featured_sealed_bottle=featured_sealed_bottle,
    )
    annotated = await _annotate_with_deal(db, products)
    return [ProductOut.model_validate(p) for p in annotated]


@router.get("/search", response_model=ProductSearchResponse)
async def search_products(
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(12, ge=1, le=24),
    skip: int = Query(0, ge=0),
    db=Depends(get_database),
):
    """Paginated product search for the navbar autosuggest and /search page.

    Returns daily-deal-annotated products so price chips, sale strikes, and
    discount pills render correctly in the dropdown as well as on the full
    search page.

    NOTE: This route is registered BEFORE `/{id_or_slug}` on purpose — FastAPI
    matches in declaration order, and a path-param route would otherwise
    capture the literal `/search` segment.
    """
    product_service = ProductService(db)
    result = await product_service.search(q=q, limit=limit, skip=skip)
    result["items"] = await _annotate_with_deal(db, result["items"])
    return result


@router.get("/{id_or_slug}/related", response_model=List[ProductOut])
async def get_related_products(
    id_or_slug: str,
    limit: int = Query(10, ge=1, le=24),
    db=Depends(get_database),
):
    product_service = ProductService(db)
    products = await product_service.get_related(id_or_slug, limit=limit)
    if products is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return await _annotate_with_deal(db, products)


@router.get("/{id_or_slug}", response_model=ProductOut)
async def get_product(id_or_slug: str, db=Depends(get_database)):
    product_service = ProductService(db)
    product = await product_service.get_by_id_or_slug(id_or_slug)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return await _annotate_with_deal(db, product)

@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(product_in: ProductCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    product_service = ProductService(db)
    try:
        created = await product_service.create(product_in)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    pid = str(created.get("_id") or created.get("id") or "")
    await revalidate_product(pid, _product_slug(created))
    return created

@router.put("/{id}", response_model=ProductOut)
async def update_product(id: str, product_in: ProductUpdate, db=Depends(get_database), _admin=Depends(require_admin)):
    product_service = ProductService(db)
    try:
        updated = await product_service.update(id, product_in)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Product not found")
    pid = str(updated.get("_id") or updated.get("id") or id)
    await revalidate_product(pid, _product_slug(updated))
    return updated

@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    product_service = ProductService(db)
    existing = await product_service.get_by_id_or_slug(id)
    await product_service.delete(id)
    if existing:
        pid = str(existing.get("_id") or existing.get("id") or id)
        await revalidate_product(pid, _product_slug(existing))
    else:
        await revalidate_products_catalog()
    return None
