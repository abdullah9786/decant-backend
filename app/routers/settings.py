from fastapi import APIRouter
from app.config.config import settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/cod")
async def get_cod_settings():
    """Public COD configuration so the checkout UI can gate the COD tile and
    render the handling fee. Read-only and unauthenticated by design.
    """
    return {
        "enabled": settings.COD_ENABLED,
        "max_amount": settings.COD_MAX_AMOUNT,
        "fee": settings.COD_FEE,
    }
