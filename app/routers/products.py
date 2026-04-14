from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.schemas.product import ProductCreate, ProductUpdate, ProductOut
from app.services.product_service import ProductService
from app.db.mongodb import get_database
from app.utils.deps import require_admin

router = APIRouter(prefix="/products", tags=["products"])

@router.get("", response_model=List[ProductOut])
async def get_products(
    fragrance_family: Optional[str] = None, 
    brand: Optional[str] = None,
    is_featured: Optional[bool] = None,
    is_new_arrival: Optional[bool] = None,
    q: Optional[str] = None,
    sort_by: Optional[str] = None,
    include_inactive: bool = False,
    db=Depends(get_database)
):
    product_service = ProductService(db)
    return await product_service.get_all(fragrance_family, brand, is_featured, is_new_arrival, q, sort_by, include_inactive)

@router.get("/{id}", response_model=ProductOut)
async def get_product(id: str, db=Depends(get_database)):
    product_service = ProductService(db)
    product = await product_service.get_by_id(id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(product_in: ProductCreate, db=Depends(get_database), _admin=Depends(require_admin)):
    product_service = ProductService(db)
    return await product_service.create(product_in)

@router.put("/{id}", response_model=ProductOut)
async def update_product(id: str, product_in: ProductUpdate, db=Depends(get_database), _admin=Depends(require_admin)):
    product_service = ProductService(db)
    return await product_service.update(id, product_in)

@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(id: str, db=Depends(get_database), _admin=Depends(require_admin)):
    product_service = ProductService(db)
    await product_service.delete(id)
    return None
