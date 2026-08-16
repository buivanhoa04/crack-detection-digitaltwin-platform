from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
import httpx
import os
from pathlib import Path
from app.config import settings
from app.models.config_store import load_config
from app.middleware.auth import get_current_user, require_admin

router = APIRouter()

def _crack_headers() -> dict:
    """Build headers with the Crack API auth token."""
    conf = load_config()
    token = conf.get("crack_api_token") or settings.CRACK_API_TOKEN
    return {"Authorization": f"Bearer {token}"}

def _get_client(request: Request) -> httpx.AsyncClient:
    # v21.1: Ensure we use the specialized high-timeout client from app state
    if not hasattr(request.app.state, "http_client"):
        request.app.state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0)
        )
    return request.app.state.http_client

@router.get("/debug/list")
async def list_files_debug(current_user: dict = Depends(require_admin)):
    import glob
    try:
        pattern = os.path.join(settings.LOCAL_SOURCES_DIR, "**", "*")
        files = glob.glob(pattern, recursive=True)
        root_files = os.listdir(settings.LOCAL_SOURCES_DIR) if os.path.exists(settings.LOCAL_SOURCES_DIR) else []
        return {
            "local_sources_dir": settings.LOCAL_SOURCES_DIR,
            "exists": os.path.exists(settings.LOCAL_SOURCES_DIR),
            "root_files": root_files,
            "nested_files_count": len(files),
            "sample": [f.replace(os.sep, "/") for f in files[:200]]
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}

@router.get("/{full_path:path}")
async def serve_file_securely(
    request: Request,
    full_path: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Securely serves files by acting as a reverse-proxy to the Crack API server.
    v21.1: Added Auth Headers and High-Performance Streaming.
    """
    client = _get_client(request)
    conf = load_config()
    api_url = conf.get("crack_api_url") or settings.CRACK_API_URL
    
    clean_path = full_path.lstrip("/")
    
    # ── V12.0 Reverse Storage Local Interceptor ──
    # Check if the requested file exists in the LOCAL_SOURCES_DIR
    import re
    from fastapi.responses import FileResponse
    
    # Standardize path
    p_norm = full_path.replace("\\", "/").lstrip("/")
    relative_path = p_norm.replace("files/", "")
    if "\x00" in p_norm or any(part in {".", ".."} for part in p_norm.split("/")):
        raise HTTPException(status_code=400, detail="Invalid media path")
    
    # Attempt 1: Direct hierarchical path match
    source_root = Path(settings.LOCAL_SOURCES_DIR).resolve()
    local_path_candidate = (source_root / relative_path).resolve()
    try:
        local_path_candidate.relative_to(source_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid media path")

    if local_path_candidate.exists() and local_path_candidate.is_file():
        print(f"[FILE_LOCAL_FAST] Serving DIRECT: {local_path_candidate}")
        return FileResponse(str(local_path_candidate))
        
    # Attempt 2: Task-ID based resolution fallback (for flat requests to nested files)
    # If request is just "task_ID/snap.jpg", resolve the hierarchy from DB
    task_id_match = re.search(r'task_([a-zA-Z0-9]+)', p_norm)
    if task_id_match:
        found_task_id = f"task_{task_id_match.group(1)}"
        filename = p_norm.split("/")[-1]
        
        # v71.1: Multi-level resolution (Check results folder first, then parent for video)
        from app.routers.crack import get_task_local_dir
        
        # 1. Try results sub-folder
        local_dir = await get_task_local_dir(found_task_id, find_video=False)
        if local_dir:
            local_resolved = os.path.join(local_dir, filename)
            if os.path.exists(local_resolved) and os.path.isfile(local_resolved):
                return FileResponse(local_resolved)
                
        # 2. Try parent category folder (for video)
        video_resolved = await get_task_local_dir(found_task_id, find_video=True)
        if video_resolved and os.path.basename(video_resolved) == filename:
            return FileResponse(video_resolved)
    
    # Standardize target URL fallback
    if clean_path.startswith("files/"):
        target_url = f"{api_url.rstrip('/')}/{clean_path}"
    else:
        target_url = f"{api_url.rstrip('/')}/files/{clean_path}"
    
    print(f"[FILE PROXY] Requesting: {target_url}")
    
    try:
        # Use streaming to handle images and results efficiently
        response = await client.request(
            "GET", 
            target_url, 
            headers=_crack_headers(), # CRITICAL: Added missing security token
            follow_redirects=True,
            timeout=120.0
        )
        
        if response.status_code != 200:
            print(f"[FILE PROXY ERROR] AI Server returned {response.status_code} for {target_url}")
            raise HTTPException(status_code=response.status_code, detail="Media not found on AI server")
            
        return StreamingResponse(
            response.iter_bytes(chunk_size=1024*1024), 
            media_type=response.headers.get("content-type", "image/jpeg"),
            headers={"Access-Control-Allow-Origin": "*"}
        )
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"[FILE PROXY EXCEPTION] {target_url} -> {str(e)}")
        raise HTTPException(status_code=503, detail=f"AI Server Connection Error: {str(e)}")
