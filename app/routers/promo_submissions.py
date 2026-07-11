from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional

from app.db.mongodb import get_database
from app.schemas.promo_submission import (
    PromoSubmissionApprove,
    PromoSubmissionOut,
    PromoSubmissionPublicOut,
    PromoSubmissionReject,
    PromoSubmissionSubmit,
)
from app.services.promo_submission_service import PromoSubmissionService
from app.utils.deps import get_current_user_optional, require_admin

router = APIRouter(prefix="/promo-submissions", tags=["promo-submissions"])


def _serialize_submission(doc: dict) -> dict:
    if not doc:
        return doc
    out = dict(doc)
    out["_id"] = str(doc["_id"])
    return out


@router.get("/by-order/{order_id}", response_model=PromoSubmissionPublicOut)
async def get_submission_by_order(
    order_id: str,
    email: Optional[str] = Query(None),
    db=Depends(get_database),
    current_user=Depends(get_current_user_optional),
):
    service = PromoSubmissionService(db)
    try:
        return await service.get_public_by_order(order_id, email=email, current_user=current_user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/", response_model=PromoSubmissionPublicOut)
async def submit_promo(
    body: PromoSubmissionSubmit,
    db=Depends(get_database),
    current_user=Depends(get_current_user_optional),
):
    service = PromoSubmissionService(db)
    try:
        await service.submit(
            order_id=body.order_id,
            post_url=body.post_url,
            poster_instagram_username=body.poster_instagram_username,
            posted_by_note=body.posted_by_note,
            email=body.email,
            current_user=current_user,
        )
        return await service.get_public_by_order(
            body.order_id,
            email=body.email,
            current_user=current_user,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/admin/by-order/{order_id}", response_model=PromoSubmissionOut)
async def admin_get_by_order(
    order_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    service = PromoSubmissionService(db)
    doc = await service.get_by_order_id(order_id)
    if not doc:
        raise HTTPException(status_code=404, detail="No promo submission for this order")
    return _serialize_submission(doc)


@router.get("/admin/queue", response_model=List[PromoSubmissionOut])
async def admin_list_queue(
    status: Optional[str] = Query(None),
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    service = PromoSubmissionService(db)
    rows = await service.list_admin_queue(status=status)
    return [_serialize_submission(r) for r in rows]


@router.get("/admin/{submission_id}", response_model=PromoSubmissionOut)
async def admin_get_submission(
    submission_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    service = PromoSubmissionService(db)
    doc = await service.get_by_id(submission_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Submission not found")
    return _serialize_submission(doc)


@router.post("/admin/{submission_id}/approve", response_model=PromoSubmissionOut)
async def admin_approve_submission(
    submission_id: str,
    body: PromoSubmissionApprove,
    db=Depends(get_database),
    admin=Depends(require_admin),
):
    service = PromoSubmissionService(db)
    try:
        doc = await service.approve(
            submission_id,
            body.prize_template_id,
            admin,
            admin_notes=body.admin_notes,
        )
        return _serialize_submission(doc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/{submission_id}/reject", response_model=PromoSubmissionOut)
async def admin_reject_submission(
    submission_id: str,
    body: PromoSubmissionReject,
    db=Depends(get_database),
    admin=Depends(require_admin),
):
    service = PromoSubmissionService(db)
    try:
        doc = await service.reject(submission_id, body.reason, admin)
        return _serialize_submission(doc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
