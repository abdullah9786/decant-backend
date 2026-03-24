from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from app.schemas.influencer import (
    InfluencerProfileCreate, InfluencerProfileUpdate, InfluencerProfileOut,
    SectionCreate, SectionUpdate, SectionReorder, SectionOut,
    CommissionOut, EarningsSummary, PayoutCreate, PayoutOut,
)
from app.schemas.coupon import CouponCreate, CouponUpdate, CouponOut, CouponApplyRequest, CouponApplyResponse
from app.services.influencer_service import InfluencerService
from app.services.commission_service import CommissionService
from app.services.coupon_service import CouponService
from app.db.mongodb import get_database
from app.utils.deps import get_current_user, require_admin, require_influencer

router = APIRouter(prefix="/influencers", tags=["influencers"])


# ── Helpers ───────────────────────────────────────────────────────

async def _get_influencer_profile(current_user, db):
    svc = InfluencerService(db)
    profile = await svc.get_profile_by_user_id(str(current_user["_id"]))
    if not profile:
        raise HTTPException(status_code=404, detail="Influencer profile not found")
    return profile


# ── Public Storefront ─────────────────────────────────────────────

@router.get("/storefront/{username}")
async def get_storefront(username: str, db=Depends(get_database)):
    svc = InfluencerService(db)
    storefront = await svc.get_storefront(username)
    if not storefront:
        raise HTTPException(status_code=404, detail="Influencer not found")
    return storefront


@router.get("/public/list")
async def list_active_influencers(db=Depends(get_database)):
    svc = InfluencerService(db)
    profiles = await svc.get_all_profiles(active_only=True)
    for p in profiles:
        p["_id"] = str(p["_id"])
    return profiles


# ── Influencer Self-Management ────────────────────────────────────

@router.get("/me", response_model=InfluencerProfileOut)
async def get_my_profile(
    db=Depends(get_database),
    current_user=Depends(require_influencer),
):
    return await _get_influencer_profile(current_user, db)


@router.put("/me", response_model=InfluencerProfileOut)
async def update_my_profile(
    data: InfluencerProfileUpdate,
    db=Depends(get_database),
    current_user=Depends(require_influencer),
):
    profile = await _get_influencer_profile(current_user, db)
    svc = InfluencerService(db)
    updated = await svc.update_profile(str(profile["_id"]), data)
    if not updated:
        raise HTTPException(status_code=500, detail="Update failed")
    return updated


@router.post("/me/sections", response_model=SectionOut, status_code=status.HTTP_201_CREATED)
async def create_section(
    data: SectionCreate,
    db=Depends(get_database),
    current_user=Depends(require_influencer),
):
    profile = await _get_influencer_profile(current_user, db)
    svc = InfluencerService(db)
    return await svc.create_section(str(profile["_id"]), data)


@router.put("/me/sections/{section_id}", response_model=SectionOut)
async def update_section(
    section_id: str,
    data: SectionUpdate,
    db=Depends(get_database),
    current_user=Depends(require_influencer),
):
    profile = await _get_influencer_profile(current_user, db)
    svc = InfluencerService(db)
    section = await svc.update_section(section_id, str(profile["_id"]), data)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    return section


@router.delete("/me/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(
    section_id: str,
    db=Depends(get_database),
    current_user=Depends(require_influencer),
):
    profile = await _get_influencer_profile(current_user, db)
    svc = InfluencerService(db)
    deleted = await svc.delete_section(section_id, str(profile["_id"]))
    if not deleted:
        raise HTTPException(status_code=404, detail="Section not found")
    return None


@router.put("/me/sections/reorder")
async def reorder_sections(
    data: SectionReorder,
    db=Depends(get_database),
    current_user=Depends(require_influencer),
):
    profile = await _get_influencer_profile(current_user, db)
    svc = InfluencerService(db)
    return await svc.reorder_sections(str(profile["_id"]), data.section_ids)


@router.get("/me/sections", response_model=List[SectionOut])
async def get_my_sections(
    db=Depends(get_database),
    current_user=Depends(require_influencer),
):
    profile = await _get_influencer_profile(current_user, db)
    svc = InfluencerService(db)
    return await svc.get_sections(str(profile["_id"]))


@router.get("/me/earnings", response_model=EarningsSummary)
async def get_my_earnings(
    db=Depends(get_database),
    current_user=Depends(require_influencer),
):
    profile = await _get_influencer_profile(current_user, db)
    csvc = CommissionService(db)
    return await csvc.get_earnings_summary(str(profile["_id"]))


@router.get("/me/commissions", response_model=List[CommissionOut])
async def get_my_commissions(
    status_filter: Optional[str] = None,
    db=Depends(get_database),
    current_user=Depends(require_influencer),
):
    profile = await _get_influencer_profile(current_user, db)
    csvc = CommissionService(db)
    return await csvc.get_commissions_by_influencer(str(profile["_id"]), status_filter)


# ── Admin Endpoints ───────────────────────────────────────────────

@router.post("/create", response_model=InfluencerProfileOut, status_code=status.HTTP_201_CREATED)
async def admin_create_influencer(
    data: InfluencerProfileCreate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    svc = InfluencerService(db)
    try:
        return await svc.create_profile(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{profile_id}/toggle-active", response_model=InfluencerProfileOut)
async def admin_toggle_active(
    profile_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    svc = InfluencerService(db)
    profile = await svc.toggle_active(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Influencer not found")
    return profile


@router.get("/admin/all")
async def admin_list_all_influencers(
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    svc = InfluencerService(db)
    csvc = CommissionService(db)
    profiles = await svc.get_all_profiles()

    result = []
    for p in profiles:
        pid = str(p["_id"])
        earnings = await csvc.get_earnings_summary(pid)
        p["_id"] = pid
        p["earnings"] = earnings
        result.append(p)
    return result


@router.get("/admin/commissions", response_model=List[CommissionOut])
async def admin_list_commissions(
    status_filter: Optional[str] = None,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    csvc = CommissionService(db)
    return await csvc.get_all_commissions(status_filter)


@router.put("/admin/commissions/{commission_id}/approve", response_model=CommissionOut)
async def admin_approve_commission(
    commission_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    csvc = CommissionService(db)
    c = await csvc.approve_commission(commission_id)
    if not c:
        raise HTTPException(status_code=400, detail="Cannot approve this commission")
    return c


@router.put("/admin/commissions/{commission_id}/cancel", response_model=CommissionOut)
async def admin_cancel_commission(
    commission_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    csvc = CommissionService(db)
    c = await csvc.cancel_commission(commission_id)
    if not c:
        raise HTTPException(status_code=400, detail="Cannot cancel this commission")
    return c


@router.post("/admin/payouts", response_model=PayoutOut)
async def admin_create_payout(
    data: PayoutCreate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    csvc = CommissionService(db)
    payout = await csvc.create_payout(data.influencer_id, data.method)
    if not payout:
        raise HTTPException(status_code=400, detail="No approved commissions to pay out")
    return payout


@router.put("/admin/payouts/{payout_id}/complete", response_model=PayoutOut)
async def admin_complete_payout(
    payout_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    csvc = CommissionService(db)
    payout = await csvc.complete_payout(payout_id)
    if not payout:
        raise HTTPException(status_code=400, detail="Cannot complete this payout")
    return payout


@router.get("/admin/payouts/{influencer_id}", response_model=List[PayoutOut])
async def admin_get_payouts(
    influencer_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    csvc = CommissionService(db)
    return await csvc.get_payouts_by_influencer(influencer_id)


# ── Coupon Endpoints ──────────────────────────────────────────────

@router.post("/coupons/validate", response_model=CouponApplyResponse)
async def validate_coupon(
    data: CouponApplyRequest,
    db=Depends(get_database),
):
    csvc = CouponService(db)
    return await csvc.validate_coupon(data.code)


@router.post("/admin/coupons", response_model=CouponOut, status_code=status.HTTP_201_CREATED)
async def admin_create_coupon(
    data: CouponCreate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    csvc = CouponService(db)
    try:
        return await csvc.create_coupon(data.dict())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/admin/coupons", response_model=List[CouponOut])
async def admin_list_coupons(
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    csvc = CouponService(db)
    coupons = await csvc.get_all_coupons()
    for c in coupons:
        c["_id"] = str(c["_id"])
    return coupons


@router.put("/admin/coupons/{coupon_id}", response_model=CouponOut)
async def admin_update_coupon(
    coupon_id: str,
    data: CouponUpdate,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    csvc = CouponService(db)
    coupon = await csvc.update_coupon(coupon_id, data.dict(exclude_unset=True))
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return coupon


@router.delete("/admin/coupons/{coupon_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_coupon(
    coupon_id: str,
    db=Depends(get_database),
    _admin=Depends(require_admin),
):
    csvc = CouponService(db)
    deleted = await csvc.delete_coupon(coupon_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return None
