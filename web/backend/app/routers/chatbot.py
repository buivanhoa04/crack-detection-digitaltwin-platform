from fastapi import APIRouter, Depends, HTTPException, Body, Request
import httpx
import uuid
import traceback
import os
from datetime import datetime
from app.middleware.auth import get_current_user, require_admin
from app.models.config_store import load_config

router = APIRouter()

# --- HELPER: GET CONFIG ---
def get_rag_config():
    from app.config import settings
    config = load_config()
    rag_url = config.get("ragflow_api_url", settings.RAGFLOW_API_URL).rstrip('/')
    rag_token = config.get("ragflow_api_token", settings.RAGFLOW_API_TOKEN)
    return rag_url, rag_token

# --- 1. CHAT ENDPOINT (PROXY TO MIDDLEWARE) ---
@router.post("/chat")
async def chat(
    message: str = Body(..., embed=True),
    session_id: str = Body(..., embed=True),
    current_user: dict = Depends(get_current_user)
):
    rag_url, rag_token = get_rag_config()
    headers = {"X-API-Token": rag_token, "Content-Type": "application/json"}
    user_email = current_user["email"]
    
    payload = {
        "question": message,
        "session_id": session_id,
        "stream": False
    }

    try:
        async with httpx.AsyncClient(timeout=185.0) as client:
            response = await client.post(
                f"{rag_url}/api/v1/chat/text",
                headers=headers,
                params={"user_id": user_email},
                json=payload,
            )
            
            if response.status_code != 200:
                print(f"!!! Gateway Chat Upstream Error: {response.status_code} - {response.text}")
                raise HTTPException(status_code=502, detail=f"Lỗi Gateway: {response.status_code}")
            
            res_json = response.json()
            if isinstance(res_json, dict):
                import re
                if "answer" in res_json and isinstance(res_json["answer"], str):
                    res_json["answer"] = re.sub(r'\[id\d+\]', '', res_json["answer"], flags=re.IGNORECASE)
                elif "data" in res_json and isinstance(res_json["data"], dict) and "answer" in res_json["data"]:
                    res_json["data"]["answer"] = re.sub(r'\[id\d+\]', '', res_json["data"]["answer"], flags=re.IGNORECASE)
            
            return res_json
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"!!! Gateway Chat Exception: {str(e)}")
        raise HTTPException(status_code=502, detail=f"Lỗi Gateway: {str(e)}")

# --- 2. SESSIONS ENDPOINT (PROXY TO MIDDLEWARE) ---
@router.get("/sessions")
async def list_sessions(current_user: dict = Depends(get_current_user)):
    rag_url, rag_token = get_rag_config()
    headers = {"X-API-Token": rag_token}
    user_email = current_user.get("email", "default_user")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{rag_url}/sessions?user_id={user_email}", headers=headers)
        if response.status_code != 200:
            return []
        payload = response.json()
        # Vision analyses use dedicated, non-interactive sessions.  Never
        # expose those transcripts in the text-chat history list.
        def is_vision_session(item: dict) -> bool:
            sid = str(item.get("session_id") or item.get("id") or "").lower()
            title = str(item.get("title") or "").lower()
            return (
                sid.startswith(("analysis_", "vision_"))
                or "bạn là chuyên gia" in title
                or "analysis_" in sid
                or "vision_" in sid
            )
        if isinstance(payload, list):
            return [item for item in payload if not is_vision_session(item)]
        if isinstance(payload, dict) and isinstance(payload.get("sessions"), list):
            payload["sessions"] = [item for item in payload["sessions"] if not is_vision_session(item)]
        return payload
    except Exception as e:
        print(f"!!! Gateway Sessions Exception: {str(e)}")
        return []

# --- 3. MESSAGES ENDPOINT (PROXY TO MIDDLEWARE) ---
@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, current_user: dict = Depends(get_current_user)):
    if session_id.lower().startswith(("analysis_", "vision_")):
        raise HTTPException(status_code=404, detail="Phiên Vision không thuộc lịch sử chat văn bản")
    rag_url, rag_token = get_rag_config()
    headers = {"X-API-Token": rag_token}
    user_email = current_user.get("email")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{rag_url}/sessions/{session_id}/messages",
                headers=headers,
                params={"user_id": user_email},
            )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Phiên chat không tồn tại")
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Middleware Message Error: {response.status_code}")
        
        res_json = response.json()
        if isinstance(res_json, dict) and isinstance(res_json.get("messages"), list):
            import re
            for m in res_json["messages"]:
                if "content" in m and isinstance(m["content"], str):
                    m["content"] = re.sub(r'\[id\d+\]', '', m["content"], flags=re.IGNORECASE)
                    
        return res_json
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail="Không thể tải nội dung phiên chat")

@router.post("/sessions")
async def create_new_session(current_user: dict = Depends(get_current_user)):
    """Request a valid RAGFlow-registered session ID from the Middleware."""
    rag_url, rag_token = get_rag_config()
    headers = {"X-API-Token": rag_token}
    user_email = current_user.get("email", "default_user")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(f"{rag_url}/sessions?user_id={user_email}", headers=headers)
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Middleware Session Error: {response.text}")
        return response.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi gửi lệnh tạo phiên: {str(e)}")

# --- 5. DELETE SESSION ENDPOINT (PROXY TO MIDDLEWARE) ---
@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, current_user: dict = Depends(get_current_user)):
    rag_url, rag_token = get_rag_config()
    headers = {"X-API-Token": rag_token}
    user_email = current_user.get("email")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(
                f"{rag_url}/sessions/{session_id}",
                headers=headers,
                params={"user_id": user_email},
            )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Phiên chat không tồn tại")
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Middleware Delete Error: {response.status_code}")
        return response.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Lỗi khi gửi lệnh xóa: {str(e)}")

# --- 6. KNOWLEDGE ARCHIVE ENDPOINTS ---
@router.post("/upload")
async def upload_document(request: Request, current_user: dict = Depends(require_admin)):
    rag_url, rag_token = get_rag_config()
    headers = {"X-API-Token": rag_token}
    form = await request.form()
    file = form.get("file")
    if not file:
        raise HTTPException(status_code=400, detail="No file found")
    files = {"file": (file.filename, await file.read(), file.content_type)}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{rag_url}/upload", headers=headers, files=files)
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tải lên Gateway: {str(e)}")

@router.get("/documents")
async def list_documents(current_user: dict = Depends(get_current_user)):
    rag_url, rag_token = get_rag_config()
    headers = {"X-API-Token": rag_token}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{rag_url}/files", headers=headers)
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Middleware Document Error: {response.status_code}")
        data = response.json()
        docs = data.get("files") or []
        transformed = []
        for f in docs:
            transformed.append({
                "id": f.get("doc_id"),
                "filename": f.get("file_name") or f.get("filename"),
                "status": f.get("status"),
                "progress": f.get("progress_percent", 0),
                "size": f.get("size", 0),
                "chunks_count": f.get("chunk_num", 0),
                "uploaded_at": f.get("create_time")
            })
        return {"documents": transformed}
    except HTTPException:
        raise
    except Exception as e:
        print(f"!!! Sync Error: {str(e)}")
        raise HTTPException(status_code=502, detail="Không thể tải kho tài liệu")

@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, current_user: dict = Depends(require_admin)):
    rag_url, rag_token = get_rag_config()
    headers = {"X-API-Token": rag_token}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(f"{rag_url}/files/{doc_id}", headers=headers)
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/documents/{doc_id}/parse")
async def parse_document(doc_id: str, action: str = Body(..., embed=True), current_user: dict = Depends(require_admin)):
    rag_url, rag_token = get_rag_config()
    headers = {"X-API-Token": rag_token, "Content-Type": "application/json"}
    payload = {"doc_id": doc_id, "action": action}
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(f"{rag_url}/parse", headers=headers, json=payload)
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
