"""
Authentication router - Login, Register, Token refresh.
"""
from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserInfo,
    RefreshTokenRequest,
)
from app.models.user import authenticate_user, create_user, get_user_by_email
from app.utils.security import create_access_token, create_refresh_token, decode_token

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """Authenticate user and return JWT tokens."""
    from app.database import get_db
    if get_db() is None:
        raise HTTPException(status_code=503, detail="Hệ thống đang khởi động kết nối cơ sở dữ liệu. Vui lòng thử lại sau vài giây.")
        
    user = await authenticate_user(request.email, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng")

    access_token = create_access_token(data={"sub": user["id"]})
    refresh_token = create_refresh_token(data={"sub": user["id"]})

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserInfo(
            id=user["id"],
            email=user["email"],
            full_name=user["full_name"],
            role=user["role"],
            avatar=user.get("avatar"),
        ),
    )


@router.post("/register", response_model=TokenResponse)
async def register(request: RegisterRequest):
    """Register a new user account."""
    from app.database import get_db
    if get_db() is None:
        raise HTTPException(status_code=503, detail="Hệ thống đang khởi động kết nối cơ sở dữ liệu. Vui lòng thử lại sau vài giây.")
        
    existing = await get_user_by_email(request.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email đã được sử dụng")

    user = await create_user(
        email=request.email,
        password=request.password,
        full_name=request.full_name,
        # A public registration endpoint must never grant privileged roles.
        role="user",
    )

    access_token = create_access_token(data={"sub": user["id"]})
    refresh_token = create_refresh_token(data={"sub": user["id"]})

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserInfo(
            id=user["id"],
            email=user["email"],
            full_name=user["full_name"],
            role=user["role"],
            avatar=user.get("avatar"),
        ),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshTokenRequest):
    """Refresh access token using a valid refresh token."""
    from app.database import get_db
    if get_db() is None:
        raise HTTPException(status_code=503, detail="Hệ thống đang khởi động kết nối cơ sở dữ liệu. Vui lòng thử lại sau vài giây.")
        
    payload = decode_token(request.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Refresh token không hợp lệ")

    from app.models.user import get_user_by_id

    user = await get_user_by_id(payload["sub"])
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=401, detail="Người dùng không tồn tại")

    access_token = create_access_token(data={"sub": user["id"]})
    refresh_token = create_refresh_token(data={"sub": user["id"]})

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserInfo(
            id=user["id"],
            email=user["email"],
            full_name=user["full_name"],
            role=user["role"],
            avatar=user.get("avatar"),
        ),
    )
