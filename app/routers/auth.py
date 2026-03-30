from fastapi import APIRouter, Depends, status
from app.schemas.auth import (
    Login,
    Token,
    ResendVerification,
    ForgotPasswordRequest,
    ResetPassword,
    RefreshBody,
    LogoutBody,
)
from app.schemas.user import UserCreate, UserOut
from app.services.auth_service import AuthService
from app.db.mongodb import get_database
from app.utils.security import create_access_token
from app.utils.deps import get_current_user_optional

router = APIRouter(prefix="/auth", tags=["auth"])

_SENSITIVE_USER_KEYS = frozenset({
    "password",
    "verification_token",
    "verification_expires_at",
    "reset_token",
    "reset_expires_at",
})


def user_doc_for_response(user: dict) -> UserOut:
    clean = {k: v for k, v in user.items() if k not in _SENSITIVE_USER_KEYS}
    return UserOut.model_validate(clean)

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(user_in: UserCreate, db=Depends(get_database)):
    auth_service = AuthService(db)
    return await auth_service.register(user_in)

@router.post("/login", response_model=Token)
async def login(login_in: Login, db=Depends(get_database)):
    auth_service = AuthService(db)
    user = await auth_service.login(login_in.email, login_in.password)
    access_token = create_access_token(subject=user["email"])
    refresh_token = await auth_service.issue_refresh_token(user["_id"])
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": refresh_token,
        "user": user_doc_for_response(user),
    }


@router.post("/refresh", response_model=Token)
async def refresh_tokens(body: RefreshBody, db=Depends(get_database)):
    auth_service = AuthService(db)
    result = await auth_service.rotate_refresh_token(body.refresh_token)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    user = result["user"]
    access_token = create_access_token(subject=user["email"])
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": result["refresh_token"],
        "user": user_doc_for_response(user),
    }


@router.post("/logout")
async def logout(body: LogoutBody, db=Depends(get_database)):
    auth_service = AuthService(db)
    await auth_service.revoke_refresh_token(body.refresh_token)
    return {"ok": True}

@router.get("/verify")
async def verify_email(token: str, db=Depends(get_database)):
    auth_service = AuthService(db)
    success = await auth_service.verify_email(token)
    if not success:
        return {"verified": False, "message": "Invalid or expired token"}
    return {"verified": True}

@router.post("/resend-verification")
async def resend_verification(payload: ResendVerification, db=Depends(get_database)):
    auth_service = AuthService(db)
    await auth_service.resend_verification(payload.email)
    return {"sent": True}

@router.post("/test-email")
async def test_email(payload: ResendVerification, db=Depends(get_database)):
    auth_service = AuthService(db)
    # Reuse verification sender for testing
    await auth_service.resend_verification(payload.email)
    return {"sent": True}

@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest, db=Depends(get_database)):
    auth_service = AuthService(db)
    await auth_service.request_password_reset(payload.email)
    # Always return success to avoid account enumeration
    return {"sent": True}

@router.post("/reset-password")
async def reset_password(payload: ResetPassword, db=Depends(get_database)):
    auth_service = AuthService(db)
    success = await auth_service.reset_password(payload.token, payload.new_password)
    if not success:
        return {"reset": False, "message": "Invalid or expired token"}
    return {"reset": True}

@router.get("/seed-admin")
async def seed_admin(db=Depends(get_database), current_user=Depends(get_current_user_optional)):
    from app.utils.security import get_password_hash
    existing_admin = await db["users"].find_one({"is_admin": True})
    if existing_admin and not (current_user and current_user.get("is_admin", False)):
        return {"message": "Admin already exists. Use an admin account to manage users."}
    existing = await db["users"].find_one({"email": "admin@decant.com"})
    if existing:
        await db["users"].update_one(
            {"email": "admin@decant.com"},
            {"$set": {"is_admin": True, "is_active": True}}
        )
        return {"message": "Admin user updated"}
    
    await db["users"].insert_one({
        "full_name": "Admin User",
        "email": "admin@decant.com",
        "password": get_password_hash("admin123"),
        "is_admin": True,
        "is_active": True
    })
    return {"message": "Admin user created: admin@decant.com / admin123"}
