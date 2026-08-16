from fastapi import APIRouter, Depends, HTTPException, Body
import httpx
from app.middleware.auth import require_admin
from app.models.config_store import load_config, save_config
from app.utils.security import verify_password
from app.models.user import get_user_by_id

router = APIRouter()

@router.get("")
async def get_settings(current_user: dict = Depends(require_admin)):
    """Fetch all persistent settings."""
    return load_config()

@router.put("")
async def update_settings(
    settings: dict = Body(...),
    current_user: dict = Depends(require_admin)
):
    """Update settings after verifying admin password."""
    admin_password = settings.pop("admin_password", None)
    if not admin_password:
        raise HTTPException(status_code=400, detail="Vui lòng nhập mật khẩu xác nhận")
    
    # Re-fetch user from MongoDB to get password_hash (current_user doesn't have it)
    from app.models.user import get_user_by_id
    user = await get_user_by_id(current_user["id"])
    
    if not user or not verify_password(admin_password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail="Xác thực thất bại: Mật khẩu không chính xác")
    
    config = load_config()
    # Only update keys that exist in current config or are allowed
    allowed_keys = {
        "crack_api_url", "crack_api_token", 
        "ragflow_api_url", "ragflow_api_token",
        "ragflow_base_url", "dataset_id", "ragflow_api_key",
        "text_chat_id", "vlm_chat_id"
    }
    
    for k, v in settings.items():
        if k in allowed_keys:
            config[k] = v
            
    save_config(config)
    
    # ── AUTO-SYNC: Push RAGFlow config to Middleware ──────────
    # Middleware (port 8088) has its own DB for ragflow settings.
    # We must push the new values there so upload/list/chat all work.
    middleware_sync_result = None
    ragflow_keys = {"ragflow_api_key", "dataset_id", "ragflow_base_url", "text_chat_id", "vlm_chat_id"}
    has_ragflow_changes = any(k in ragflow_keys for k in settings.keys())
    
    if has_ragflow_changes:
        from app.config import settings
        middleware_url = config.get("ragflow_api_url", settings.RAGFLOW_API_URL)
        middleware_token = config.get("ragflow_api_token", settings.RAGFLOW_API_TOKEN)
        
        sync_payload = {
            "ragflow_api_key": config.get("ragflow_api_key", ""),
            "dataset_id": config.get("dataset_id", ""),
            "ragflow_base_url": config.get("ragflow_base_url", ""),
            "text_chat_id": config.get("text_chat_id", ""),
            "vlm_chat_id": config.get("vlm_chat_id", ""),
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    f"{middleware_url}/settings",
                    headers={"Authorization": f"Bearer {middleware_token}"},
                    json=sync_payload
                )
                if res.status_code == 200:
                    middleware_sync_result = "synced"
                else:
                    middleware_sync_result = f"middleware_error: {res.status_code} - {res.text}"
        except Exception as e:
            middleware_sync_result = f"middleware_unreachable: {str(e)}"
    
    return {
        "message": "Cài đặt đã được cập nhật thành công",
        "config": config,
        "middleware_sync": middleware_sync_result
    }
