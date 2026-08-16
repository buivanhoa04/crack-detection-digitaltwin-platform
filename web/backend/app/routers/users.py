"""
User management router (Admin CRUD + self-service).
"""
from fastapi import APIRouter, HTTPException, Depends, Request

from app.middleware.auth import get_current_user, require_admin
from app.models.schemas import (
    CreateUserRequest, UpdateUserRequest,
    ChangePasswordRequest, AdminResetPasswordRequest, UserInfo,
)
from app.models.user import (
    get_all_users, get_user_by_email, get_user_by_id,
    create_user, update_user, delete_user, toggle_active, change_password,
)
from app.utils.security import verify_password
from app.models.audit import add_log

router = APIRouter()


# ── List all users (Admin) ───────────────────────────────
@router.get("")
async def list_users(current_user: dict = Depends(require_admin)):
    return {"users": await get_all_users()}


# ── Create user (Admin) ─────────────────────────────────
@router.post("")
async def admin_create_user(
    req: CreateUserRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    if await get_user_by_email(req.email):
        raise HTTPException(status_code=400, detail="Email da duoc su dung")

    user = await create_user(
        email=req.email,
        password=req.password,
        full_name=req.full_name,
        role=req.role,
        created_by=current_user["id"],
    )

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="create_user",
        target=req.email,
        details=f"Role: {req.role}",
        ip_address=request.client.host if request.client else "",
    )

    return {
        "message": "Tao tai khoan thanh cong",
        "user": {k: v for k, v in user.items() if k != "password_hash"},
    }


# ── Get current user profile ────────────────────────────
@router.get("/me")
async def get_my_profile(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}


# ── Update user (Admin) ─────────────────────────────────
@router.put("/{user_id}")
async def admin_update_user(
    user_id: str,
    req: UpdateUserRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    user = await update_user(user_id, req.model_dump(exclude_none=True))
    if not user:
        raise HTTPException(status_code=404, detail="Khong tim thay user")

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="update_user",
        target=user.get("email", user_id),
        ip_address=request.client.host if request.client else "",
    )

    return {
        "message": "Cap nhat thanh cong",
        "user": {k: v for k, v in user.items() if k != "password_hash"},
    }


# ── Toggle user active status (Admin) ───────────────────
@router.put("/{user_id}/toggle")
async def admin_toggle_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Khong the vo hieu hoa chinh minh")

    user = await toggle_active(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Khong tim thay user")

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="toggle_user",
        target=user.get("email", user_id),
        details=f"Active: {user['is_active']}",
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Thanh cong", "is_active": user["is_active"]}


# ── Delete user (Admin) ─────────────────────────────────
@router.delete("/{user_id}")
async def admin_delete_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Khong the xoa chinh minh")

    target_user = await get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Khong tim thay user")

    await delete_user(user_id)

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="delete_user",
        target=target_user.get("email", user_id),
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Da xoa user"}


# ── Admin reset password ────────────────────────────────
@router.put("/{user_id}/reset-password")
async def admin_reset_password(
    user_id: str,
    req: AdminResetPasswordRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    ok = await change_password(user_id, req.new_password)
    if not ok:
        raise HTTPException(status_code=404, detail="Khong tim thay user")

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="reset_password",
        target=user_id,
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Da doi mat khau"}


# ── Self change password ────────────────────────────────
@router.put("/me/password")
async def change_my_password(
    req: ChangePasswordRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    full_user = await get_user_by_id(current_user["id"])
    if not full_user:
        raise HTTPException(status_code=404, detail="User khong ton tai")

    if not verify_password(req.current_password, full_user["password_hash"]):
        raise HTTPException(status_code=400, detail="Mat khau hien tai khong dung")

    await change_password(current_user["id"], req.new_password)

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="change_password",
        target="self",
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Doi mat khau thanh cong"}
