from typing import Optional, List
import os
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.utils.security import decode_token
from app.models.user import get_user_by_id

security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> dict:
    """
    Dependency that extracts and validates the JWT token.
    v11.0: Supports tokens from Header (API) or Query Parameter (Media/Video).
    """
    token = None
    
    # 1. Try standard Header first
    if credentials:
        token = credentials.credentials
    
    # 2. Fallback to Query Parameter (Crucial for <video src="..."> tags)
    if not token:
        token = request.query_params.get("token")

    if not token:
        raise HTTPException(status_code=401, detail="Token không tìm thấy")

    payload = decode_token(token)

    if payload is None:
        raise HTTPException(status_code=401, detail="Token không hợp lệ hoặc đã hết hạn")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Loại token không hợp lệ")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token không chứa user ID")

    from app.database import get_db
    if get_db() is None:
        raise HTTPException(status_code=503, detail="Hệ thống đang khởi động kết nối cơ sở dữ liệu. Vui lòng thử lại sau vài giây.")

    user = await get_user_by_id(user_id)
    if not user:
        print(f"   [AUTH ERROR] User {user_id} not found in DB")
        raise HTTPException(status_code=401, detail="Người dùng không tồn tại")

    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account is disabled")

    user_info = {k: v for k, v in user.items() if k != "password_hash"}
    print(f"   [AUTH] Resolved User: {user_info.get('email')} (ID: {user_info.get('id')})")
    return user_info


async def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Dependency that requires admin role."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403, detail="Chỉ Admin mới có quyền truy cập"
        )
    return current_user
