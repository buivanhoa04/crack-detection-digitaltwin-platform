from fastapi import APIRouter, UploadFile, File, Form, Depends, Request, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, Query
from fastapi.responses import StreamingResponse, FileResponse, Response, JSONResponse
from typing import Optional, List, Set
import httpx
import json
import os
import uuid
import shutil
import time
import anyio
import asyncio
import hashlib
import re
import unicodedata
from pymongo import ReplaceOne
from datetime import datetime, timedelta

from app.middleware.auth import get_current_user
from app.utils.security import decode_token
from app.models.user import get_user_by_id
from app.database import get_db
from app.config import settings
from app.models.crack import add_task, get_history, update_task_results, delete_task
from app.models.config_store import load_config
from app.services.storage import storage_service
from app.services.notifier import notifier

router = APIRouter()

UPLOAD_DIR = settings.LOCAL_SOURCES_DIR
print(f"[STORAGE] Active Local Path for Uploads: {UPLOAD_DIR}")

# Strong references keep recovery jobs alive. MongoDB leases make them safe
# across multiple backend instances and retryable after container restarts.
_BATCH_RECOVERY_TASKS: Set[asyncio.Task] = set()


def _normalize_vlm_report(text: str) -> str:
    """Turn the middleware's one-line markdown into safe readable text."""
    value = str(text or "").replace("\r", "")
    # The VLM frequently emits headings/lists/tables on one physical line.
    value = re.sub(r"\s+#{1,6}\s*", "\n\n", value)
    value = re.sub(r"\s+(-\s+|\d+\.\s+)", r"\n\1", value)
    value = re.sub(r"\*{1,3}", "", value)
    # Tables produced by the model are not source-validated and often contain
    # invented year suffixes; the authoritative standards are rendered by the
    # backend fields below, so remove that duplicated section from prose.
    value = re.sub(r"\[?Bảng Markdown\]?\s*.*?(?=\[?Chú Ý\]?|$)", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def _clean_vlm_field(value, max_length: int = 2400) -> str:
    """Normalize one structured VLM field without letting markdown leak into the UI."""
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"```(?:json)?|```", "", text, flags=re.IGNORECASE)
    text = re.sub(r"#{1,6}\s*|\*{1,3}|\[(?:Phân tích Phát Hiện|Bảng Kết Quả Phát Hiện|Khuyến Nghị Kỹ Thuật|Lưu Ý)\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -:|\t")
    return text[:max_length].strip()


def _fold_semantic_text(value) -> str:
    """Accent-insensitive text used only for VLM contract validation."""
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def _parse_vlm_json_answer(answer) -> dict:
    """Parse the JSON-only VLM contract, including middleware code-fence wrappers."""
    if isinstance(answer, dict):
        return answer
    raw = str(answer or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    # Some middleware versions prepend a short explanation before the JSON.
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start:end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _confidence_fraction(value) -> float:
    try:
        confidence = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if confidence > 1.0:
        confidence /= 100.0
    return max(0.0, min(1.0, confidence))


def _detection_roi_summary(detection: dict) -> tuple[list, str, float]:
    """Return a compact normalized envelope, human-readable region, and area %."""
    bbox = detection.get("bbox") or []
    coords = []
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            coords = [float(value) for value in bbox]
        except (TypeError, ValueError):
            coords = []

    if not coords:
        polygon = detection.get("polygon") or detection.get("segmentation") or detection.get("obb") or []
        points = []
        if isinstance(polygon, (list, tuple)):
            if polygon and all(isinstance(value, (int, float)) for value in polygon) and len(polygon) >= 4:
                points = list(zip(polygon[0::2], polygon[1::2]))
            else:
                points = [
                    (point[0], point[1])
                    for point in polygon
                    if isinstance(point, (list, tuple)) and len(point) >= 2
                ]
        try:
            if points:
                xs = [float(point[0]) for point in points]
                ys = [float(point[1]) for point in points]
                coords = [min(xs), min(ys), max(xs), max(ys)]
        except (TypeError, ValueError):
            coords = []

    if not coords:
        return [], "không xác định", 0.0

    x1, y1, x2, y2 = coords
    # Stored task detections are normally normalized. Clamp bad values so a
    # malformed ROI cannot create nonsensical spatial prose.
    if max(abs(value) for value in coords) > 1.05:
        return [round(value, 2) for value in coords], "tọa độ pixel trong ảnh", 0.0
    x1, y1, x2, y2 = [max(0.0, min(1.0, value)) for value in (x1, y1, x2, y2)]
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2
    horizontal = "bên trái" if center_x < 0.34 else "bên phải" if center_x > 0.66 else "ở giữa"
    vertical = "phía trên" if center_y < 0.34 else "phía dưới" if center_y > 0.66 else "khu vực giữa"
    area_percent = max(0.0, (x2 - x1) * (y2 - y1) * 100.0)
    return [round(value, 4) for value in (x1, y1, x2, y2)], f"{vertical}, {horizontal} ảnh", round(area_percent, 2)


async def _authenticate_websocket(websocket: WebSocket) -> Optional[dict]:
    """Authenticate browser WebSockets using the short-lived access token."""
    token = websocket.query_params.get("token")
    payload = decode_token(token) if token else None
    if not payload or payload.get("type") != "access" or not payload.get("sub"):
        await websocket.close(code=4401, reason="Authentication required")
        return None

    user = await get_user_by_id(payload["sub"])
    if not user or not user.get("is_active", True):
        await websocket.close(code=4403, reason="Account is disabled")
        return None
    return user

def _crack_headers() -> dict:
    """Build headers with the Crack API auth token."""
    conf = load_config()
    token = conf.get("crack_api_token") or settings.CRACK_API_TOKEN
    return {"Authorization": f"Bearer {token}"}


def _twin_headers() -> dict:
    conf = load_config()
    token = conf.get("twin_api_token") or settings.TWIN_API_TOKEN or settings.CRACK_API_TOKEN or "secure_token_CrackAPI_12345678@@"
    return {"Authorization": f"Bearer {token}"}

def _get_client(request: Request) -> httpx.AsyncClient:
    """Returns the shared httpx.AsyncClient from app state with optimized limits."""
    if not hasattr(request.app.state, "http_client"):
        request.app.state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=15.0),
            limits=httpx.Limits(max_keepalive_connections=100, max_connections=300)
        )
    return request.app.state.http_client

def range_file_response(file_path: str, request: Request, media_type: str = "video/mp4") -> Response:
    """
    Custom response generator to support HTTP Range requests for video seeking.
    Returns HTTP 206 Partial Content when Range header is present.
    """
    import re
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("range")
    
    if not range_header:
        return FileResponse(file_path, media_type=media_type)
        
    match = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not match:
        return FileResponse(file_path, media_type=media_type)
        
    start_str, end_str = match.groups()
    
    if start_str:
        start = int(start_str)
    else:
        start = 0
        
    if end_str:
        end = int(end_str)
    else:
        end = file_size - 1
        
    if start >= file_size:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
            content="Requested Range Not Satisfiable",
        )
        
    end = min(end, file_size - 1)
    content_length = end - start + 1
    
    def file_iterator():
        chunk_size = 1024 * 256 # 256KB chunks
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk_to_read = min(chunk_size, remaining)
                data = f.read(chunk_to_read)
                if not data:
                    break
                yield data
                remaining -= len(data)
                
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": media_type,
    }
    
    return StreamingResponse(file_iterator(), status_code=206, headers=headers)

# ── Offline Detection ────────────────────────────────────

@router.post("/detect")
async def detect_cracks(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    model_type: str = Form("road"),
    generate_3d: bool = Form(False),
    survey_id: Optional[str] = Form(None),
    task_id: Optional[str] = Form(None),
    is_last_chunk: bool = Form(True),
    segmentation_enabled: Optional[bool] = Form(None),
    color_normalization_enabled: Optional[bool] = Form(None),
    batch_index: Optional[int] = Form(None),
    batch_count: Optional[int] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Unified AI Pipeline supporting chunked uploads and batch sub-task workflow.
    Saves chunks sequentially under a unique task_id and triggers AI processing
    only when the last chunk is received.
    """
    # Use client-provided task_id if present, else generate new
    task_id = task_id or f"task_{uuid.uuid4().hex[:8]}"
    year_month_day = datetime.now().strftime("%Y/%m/%d")
    category_dir = "bridge" if any(k in model_type.lower() for k in ["bridge", "pier", "concrete"]) else "road"
    task_dir = os.path.join(UPLOAD_DIR, year_month_day.replace('/', os.sep), category_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)

    print(
        f"\n[DETECT_CHUNK] {task_id} | Files: {len(files)} | "
        f"Batch: {batch_index}/{batch_count} | Last Chunk: {is_last_chunk} | Model: {model_type}"
    )
    
    saved_paths = []
    for file_index, file in enumerate(files):
        original_filename = os.path.basename(file.filename or "")
        if not original_filename:
            raise HTTPException(status_code=400, detail="Tên tệp không hợp lệ")
        safe_filename = original_filename.replace(' ', '_')
        task_safe_filename = f"{task_id}_{safe_filename}"
        final_local_path = os.path.join(task_dir, task_safe_filename)
        
        if file_index == 0 or file_index == len(files) - 1 or (file_index + 1) % 100 == 0:
            print(
                f"[STORAGE] {task_id}: Saving {file_index + 1}/{len(files)} "
                f"({original_filename})"
            )
        try:
            with open(final_local_path, "wb") as buffer:
                await anyio.to_thread.run_sync(shutil.copyfileobj, file.file, buffer, 1024*1024)
            saved_paths.append(final_local_path)
        except Exception as e:
            print(f"[STORAGE ERROR] {task_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Lỗi khi lưu file {file.filename}: {str(e)}")

    db = get_db()
    existing_task = None
    if db is not None:
        existing_task = await db.tasks.find_one({"task_id": task_id})

    # If first chunk, add main task
    if not existing_task:
        display_name = files[0].filename
        if len(files) > 1 or not is_last_chunk:
            display_name = f"{files[0].filename} (và các tệp khác)"
        
        status = "queued" if is_last_chunk else "transferring"
        await add_task(task_id, display_name, model_type, current_user.get("id"), task_dir, status=status, survey_id=survey_id)
    else:
        # Update main task status to queued if last chunk is finished
        if is_last_chunk and db is not None:
            await db.tasks.update_one(
                {
                    "task_id": task_id,
                    "batch_dispatch_started": {"$ne": True},
                },
                {"$set": {"status": "queued"}},
            )

    # Register sub-tasks for files in this chunk
    batch_sub_task_ids = []
    if db is not None:
        for file_index, final_local_path in enumerate(saved_paths):
            filename = os.path.basename(final_local_path)
            # Stable IDs make retries idempotent when a proxy response is lost.
            if batch_index is not None:
                sub_task_id = f"{task_id}_b{batch_index:06d}_f{file_index:04d}"
            else:
                fingerprint = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:16]
                sub_task_id = f"{task_id}_{fingerprint}"
            batch_sub_task_ids.append(sub_task_id)
            
            await db.tasks.update_one(
                {"task_id": sub_task_id},
                {
                    "$set": {
                    "filename": filename,
                    "parent_task_id": task_id,
                    "local_path": final_local_path,
                    "model_type": model_type,
                    "infrastructure_category": model_type,
                    "user_id": current_user.get("id"),
                    "upload_batch_index": batch_index,
                    "upload_file_index": file_index,
                    "color_normalization_enabled": bool(color_normalization_enabled),
                    },
                    "$setOnInsert": {
                        "task_id": sub_task_id,
                        "status": "queued",
                        "dispatch_state": "queued",
                        "created_at": datetime.utcnow(),
                    },
                },
                upsert=True
            )

    # Trigger AI only when last chunk is uploaded
    should_dispatch = is_last_chunk
    if is_last_chunk and db is not None:
        # A retry of the final batch must not start a second orchestration job.
        claim = await db.tasks.update_one(
            {
                "task_id": task_id,
                "batch_dispatch_started": {"$ne": True},
            },
            {
                "$set": {
                    "batch_dispatch_started": True,
                    "batch_dispatch_started_at": datetime.utcnow(),
                    "batch_upload_complete": True,
                }
            },
        )
        should_dispatch = claim.modified_count == 1

    if not is_last_chunk and batch_sub_task_ids:
        # Start AI as soon as this upload batch is durable. Upload and
        # inference now overlap instead of waiting for the entire folder.
        background_tasks.add_task(
            trigger_uploaded_batch,
            request,
            task_id,
            batch_sub_task_ids,
            model_type,
            year_month_day,
            category_dir,
            segmentation_enabled,
            color_normalization_enabled,
        )

    if should_dispatch:
        all_saved_paths = []
        if db is not None:
            sub_tasks = await db.tasks.find({"parent_task_id": task_id}).to_list(length=100000)
            all_saved_paths = [st["local_path"] for st in sub_tasks if st.get("local_path")]
        
        if not all_saved_paths:
            all_saved_paths = saved_paths # Fallback
            
        print(f"[BACKGROUND] {task_id}: Triggering batch processing for {len(all_saved_paths)} files")
        background_tasks.add_task(
            process_batch_upload_and_trigger_ai,
            request, task_id, all_saved_paths, model_type, 
            task_dir, year_month_day, category_dir, current_user, generate_3d,
            segmentation_enabled, color_normalization_enabled
        )

    return {
        "task_id": task_id,
        "status": "queued" if is_last_chunk else "transferring",
        "message": "File(s) saved successfully. AI analysis started." if is_last_chunk else "Chunk saved successfully."
    }

from pydantic import BaseModel

class DetectLocalRequest(BaseModel):
    file_path: str
    model_type: str = "road"
    generate_3d: bool = False
    survey_id: Optional[str] = None
    segmentation_enabled: Optional[bool] = None
    color_normalization_enabled: Optional[bool] = None

@router.post("/detect-local")
async def detect_local_file(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: DetectLocalRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Triggers AI pipeline directly on an existing file on the server disk.
    Avoids HTTP upload overhead for heavy files (tens/hundreds of GBs).
    """
    file_path = payload.file_path
    model_type = payload.model_type
    generate_3d = payload.generate_3d
    survey_id = payload.survey_id

    # 1. Resolve path
    if os.path.isabs(file_path):
        resolved_path = file_path
    else:
        # e.g., if user inputs "import/my_video.mp4", resolve to "D:\crack_api\sources\import\my_video.mp4"
        resolved_path = os.path.join(UPLOAD_DIR, file_path)
        
    resolved_path = os.path.abspath(resolved_path)
    
    if not os.path.exists(resolved_path) or not os.path.isfile(resolved_path):
        raise HTTPException(
            status_code=400, 
            detail=f"File không tồn tại trên server tại đường dẫn: {resolved_path}"
        )
        
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    
    # 2. Register task in DB
    db = get_db()
    if db is not None:
        await add_task(
            task_id, 
            os.path.basename(resolved_path), 
            model_type, 
            current_user.get("id"), 
            resolved_path, 
            status="queued", 
            survey_id=survey_id
        )
        
    # 3. Construct the container path (e.g. replacing local windows root with internal linux mount)
    # E.g. D:\crack_api\sources\import\my_video.mp4 -> /data/file/sources/import/my_video.mp4
    rel_path = os.path.relpath(resolved_path, UPLOAD_DIR).replace("\\", "/")
    crack_api_filepath = f"{settings.INTERNAL_AI_DATA_ROOT}/{rel_path}".replace("//", "/")
    
    # 4. Trigger AI in background
    # Since the file is already on disk, we can call _trigger_ai_analysis directly in background
    async def run_analysis():
        try:
            print(f"[BACKGROUND-LOCAL] {task_id}: Triggering AI for local file {crack_api_filepath}")
            await notifier.emit(task_id, "queued", progress=70, message="Local file located. Starting AI...")
            await _trigger_ai_analysis(
                request,
                task_id,
                model_type,
                crack_api_filepath,
                payload.segmentation_enabled,
                payload.color_normalization_enabled,
            )
            await notifier.emit(task_id, "processing", progress=85, message="AI scanning in progress...")
            if generate_3d:
                print(f"[BACKGROUND-LOCAL] {task_id}: Waiting for YOLO to complete before triggering 3D Twin...")
                start_t = time.time()
                db = get_db()
                while time.time() - start_t < 1800:
                    task_doc = await db.tasks.find_one({"task_id": task_id})
                    if task_doc:
                        status_val = task_doc.get("status")
                        proc_status = task_doc.get("processingStatus") or ""
                        if status_val == "done" or "xong" in proc_status.lower():
                            print(f"[BACKGROUND-LOCAL] {task_id}: YOLO finished. Triggering 3D Twin now...")
                            await _trigger_3d_twin(request, task_id, model_type, crack_api_filepath)
                            break
                        elif status_val == "error" or "lỗi" in proc_status.lower():
                            print(f"[BACKGROUND-LOCAL] {task_id}: YOLO failed. Skipping 3D Twin.")
                            break
                    await asyncio.sleep(5)
        except Exception as e:
            print(f"[SYSTEM ERROR-LOCAL] {task_id}: {str(e)}")
            await notifier.emit(task_id, "error", progress=0, message=f"Lỗi AI: {str(e)}")
            if db is not None: 
                await db.tasks.update_one({"task_id": task_id}, {"$set": {"status": "error", "message": str(e)}})

    background_tasks.add_task(run_analysis)
    
    return {
        "status": "success", 
        "task_id": task_id, 
        "message": f"Đã nhận diện file cục bộ ({os.path.basename(resolved_path)}) và đưa vào hàng đợi AI."
    }

@router.post("/upload-chunk")
async def upload_file_chunk(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    task_id: str = Form(...),
    model_type: str = Form("road"),
    generate_3d: bool = Form(False),
    survey_id: Optional[str] = Form(None),
    segmentation_enabled: Optional[bool] = Form(None),
    color_normalization_enabled: Optional[bool] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Accepts byte chunks of a single large file, appends them, 
    and triggers AI processing when the last chunk is received.
    """
    year_month_day = datetime.now().strftime("%Y/%m/%d")
    category_dir = "bridge" if any(k in model_type.lower() for k in ["bridge", "pier", "concrete"]) else "road"
    task_dir = os.path.join(UPLOAD_DIR, year_month_day.replace('/', os.sep), category_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    
    safe_filename = filename.replace(' ', '_')
    task_safe_filename = f"{task_id}_{safe_filename}"
    final_local_path = os.path.join(task_dir, task_safe_filename)
    
    # Mode: 'wb' for the first chunk to reset/create file, 'ab' for subsequent chunks
    mode = "wb" if chunk_index == 0 else "ab"
    
    try:
        with open(final_local_path, mode) as buffer:
            await anyio.to_thread.run_sync(shutil.copyfileobj, file.file, buffer, 1024*1024)
    except Exception as e:
        print(f"[CHUNK ERROR] task={task_id} chunk={chunk_index}: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi lưu chunk {chunk_index}: {str(e)}")
        
    db = get_db()
    
    # Update main task status/progress
    if db is not None:
        percent = int(((chunk_index + 1) / total_chunks) * 100)
        existing_task = await db.tasks.find_one({"task_id": task_id})
        if not existing_task:
            await add_task(
                task_id, 
                filename, 
                model_type, 
                current_user.get("id"), 
                task_dir, 
                status="transferring", 
                survey_id=survey_id
            )
        
        # Emit upload progress to UI
        await notifier.emit(task_id, "transferring", progress=percent, message=f"Đang tải lên: {percent}%")
        
        # If last chunk, update task status to queued and trigger AI
        if chunk_index == total_chunks - 1:
            await db.tasks.update_one({"task_id": task_id}, {"$set": {"status": "queued", "local_path": final_local_path}})
            
            # Register a sub-task for this file
            sub_task_id = f"{task_id}_0"
            await db.tasks.update_one(
                {"task_id": sub_task_id},
                {"$set": {
                    "task_id": sub_task_id,
                    "filename": task_safe_filename,
                    "parent_task_id": task_id,
                    "status": "queued",
                    "local_path": final_local_path,
                    "model_type": model_type,
                    "infrastructure_category": model_type,
                    "user_id": current_user.get("id"),
                    "created_at": datetime.utcnow()
                }},
                upsert=True
            )
            
            # Trigger AI
            crack_api_filepath = final_local_path.replace(UPLOAD_DIR, settings.INTERNAL_AI_DATA_ROOT).replace("\\", "/")
            background_tasks.add_task(
                process_upload_and_trigger_ai,
                request,
                task_id,
                filename,
                model_type,
                final_local_path,
                year_month_day,
                category_dir,
                current_user,
                generate_3d,
                segmentation_enabled,
                color_normalization_enabled,
            )
            
    return {"status": "success", "chunk_index": chunk_index, "message": "Chunk saved"}


async def process_upload_and_trigger_ai(
    request: Request,
    task_id: str,
    original_filename: str,
    model_type: str,
    final_local_path: str,
    year_month_day: str,
    category_dir: str,
    current_user: dict,
    generate_3d: bool = False,
    segmentation_enabled: Optional[bool] = None,
    color_normalization_enabled: Optional[bool] = None,
):
    """
    Triggers AI analysis now that the file is safely on disk.
    """
    try:
        # File is already at final_local_path from the router
        print(f"[BACKGROUND] {task_id}: Triggering AI analysis for {final_local_path}")
        await notifier.emit(task_id, "queued", progress=70, message="File saved. Starting AI...")

        # Construct the internal AI path (cấu trúc mới: .../category/task_id/filename)
        safe_filename = original_filename.replace(' ', '_')
        task_safe_filename = f"{task_id}_{safe_filename}"
        crack_api_filepath = f"{settings.INTERNAL_AI_DATA_ROOT}/{year_month_day}/{category_dir}/{task_id}/{task_safe_filename}".replace('//', '/')
        
        await _trigger_ai_analysis(
            request,
            task_id,
            model_type,
            crack_api_filepath,
            segmentation_enabled,
            color_normalization_enabled,
        )
        await notifier.emit(task_id, "processing", progress=85, message="AI scanning in progress...")
        
        if generate_3d:
            print(f"[BACKGROUND] {task_id}: Waiting for YOLO to complete before triggering 3D Twin...")
            start_t = time.time()
            db = get_db()
            while time.time() - start_t < 1800:
                task_doc = await db.tasks.find_one({"task_id": task_id})
                if task_doc:
                    status_val = task_doc.get("status")
                    proc_status = task_doc.get("processingStatus") or ""
                    if status_val == "done" or "xong" in proc_status.lower():
                        print(f"[BACKGROUND] {task_id}: YOLO finished. Triggering 3D Twin now...")
                        await _trigger_3d_twin(request, task_id, model_type, crack_api_filepath)
                        break
                    elif status_val == "error" or "lỗi" in proc_status.lower():
                        print(f"[BACKGROUND] {task_id}: YOLO failed. Skipping 3D Twin.")
                        break
                await asyncio.sleep(5)

    except Exception as e:
        print(f"[SYSTEM ERROR] {task_id}: {str(e)}")
        await notifier.emit(task_id, "error", progress=0, message=f"Lỗi AI: {str(e)}")
        db = get_db()
        if db: await db.tasks.update_one({"task_id": task_id}, {"$set": {"status": "error", "message": str(e)}})

async def trigger_uploaded_batch(
    request: Request,
    parent_task_id: str,
    sub_task_ids: List[str],
    model_type: str,
    year_month_day: str,
    category_dir: str,
    segmentation_enabled: Optional[bool] = None,
    color_normalization_enabled: Optional[bool] = None,
):
    """Dispatch one acknowledged upload batch while later batches upload."""
    db = get_db()
    if db is None or not sub_task_ids:
        return

    docs = await db.tasks.find(
        {
            "task_id": {"$in": sub_task_ids},
            "dispatch_state": {"$ne": "sent"},
        }
    ).to_list(length=len(sub_task_ids))
    if model_type != "road":
        # Bridge uses instance-segmentation masks with variable source shapes.
        # Keep its proven single-image path until a separate mask-batch
        # equivalence suite is available.
        sem = asyncio.Semaphore(
            max(1, min(int(os.getenv("AI_TRIGGER_CONCURRENCY", "3")), 8))
        )

        async def dispatch_single(doc: dict):
            sub_id = doc.get("task_id")
            filename = doc.get("filename")
            if not sub_id or not filename:
                return
            filepath = (
                f"{settings.INTERNAL_AI_DATA_ROOT}/{year_month_day}/"
                f"{category_dir}/{parent_task_id}/{filename}"
            ).replace("//", "/")
            async with sem:
                try:
                    await _trigger_ai_analysis(
                        request,
                        sub_id,
                        model_type,
                        filepath,
                        segmentation_enabled,
                        color_normalization_enabled,
                    )
                    await db.tasks.update_one(
                        {"task_id": sub_id},
                        {
                            "$set": {
                                "dispatch_state": "sent",
                                "dispatched_at": datetime.utcnow(),
                            }
                        },
                    )
                except Exception as exc:
                    await db.tasks.update_one(
                        {"task_id": sub_id},
                        {
                            "$set": {
                                "dispatch_state": "error",
                                "status": "error",
                                "processingStatus": f"lỗi: {exc}",
                            }
                        },
                    )

        await asyncio.gather(*(dispatch_single(doc) for doc in docs))
        return

    jobs = []
    for doc in docs:
        sub_id = doc.get("task_id")
        filename = doc.get("filename")
        if not sub_id or not filename:
            continue
        jobs.append(
            {
                "RequestId": sub_id,
                "FilePath": (
                    f"{settings.INTERNAL_AI_DATA_ROOT}/{year_month_day}/"
                    f"{category_dir}/{parent_task_id}/{filename}"
                ).replace("//", "/"),
                "ModelType": model_type,
                "segmentation_enabled": segmentation_enabled,
                "color_normalization_enabled": bool(color_normalization_enabled),
            }
        )

    request_batch_size = max(
        1, min(int(os.getenv("AI_IMAGE_REQUEST_BATCH_SIZE", "8")), 32)
    )
    for start in range(0, len(jobs), request_batch_size):
        batch = jobs[start : start + request_batch_size]
        batch_ids = [job["RequestId"] for job in batch]
        try:
            await _trigger_ai_batch_analysis(request, batch)
            await db.tasks.update_many(
                {"task_id": {"$in": batch_ids}},
                {
                    "$set": {
                        "dispatch_state": "sent",
                        "dispatched_at": datetime.utcnow(),
                    }
                },
            )
        except Exception as exc:
            await db.tasks.update_many(
                {"task_id": {"$in": batch_ids}},
                {
                    "$set": {
                        "dispatch_state": "error",
                        "status": "error",
                        "processingStatus": f"lỗi: {exc}",
                    }
                },
            )
    print(
        f"[STREAM DISPATCH] {parent_task_id}: "
        f"{len(docs)}/{len(sub_task_ids)} files sent to AI"
    )


async def process_batch_upload_and_trigger_ai(
    request: Request,
    task_id: str,
    saved_paths: List[str],
    model_type: str,
    task_dir: str,
    year_month_day: str,
    category_dir: str,
    current_user: dict,
    generate_3d: bool = False,
    segmentation_enabled: Optional[bool] = None,
    color_normalization_enabled: Optional[bool] = None,
):
    """
    Spawns temporary sub-tasks for each file in the batch, triggers AI detection,
    polls until all are finished, and aggregates the results into a single main task.
    """
    db = get_db()
    if db is None:
        print(f"[BATCH ERROR] {task_id}: Database offline")
        return

    from app.routers.archive import discover_best_frames
    sub_task_ids = []
    
    # 1. Fetch existing sub-tasks registered during upload
    sub_tasks = await db.tasks.find({"parent_task_id": task_id}).to_list(length=100000)
    sub_task_ids = []
    
    # Keep fan-out close to the single GPU worker's real drain capacity.
    trigger_concurrency = max(1, min(int(os.getenv("AI_TRIGGER_CONCURRENCY", "3")), 8))
    sem = asyncio.Semaphore(trigger_concurrency)
    
    async def _safe_trigger(sub_id: str, fname: str, filepath: str):
        async with sem:
            try:
                await _trigger_ai_analysis(
                    request,
                    sub_id,
                    model_type,
                    filepath,
                    segmentation_enabled,
                    color_normalization_enabled,
                )
                await db.tasks.update_one(
                    {"task_id": sub_id},
                    {
                        "$set": {
                            "dispatch_state": "sent",
                            "dispatched_at": datetime.utcnow(),
                        }
                    },
                )
                print(f"[BATCH TRIGGER] Sub-task {sub_id} triggered for {fname}")
            except Exception as e:
                print(f"[BATCH TRIGGER ERROR] Sub-task {sub_id} failed to trigger: {e}")
                await db.tasks.update_one(
                    {"task_id": sub_id},
                    {"$set": {"status": "error", "processingStatus": f"lỗi: {str(e)}"}}
                )
            await asyncio.sleep(0.02)

    if sub_tasks:
        print(f"[BATCH TRIGGER] Found {len(sub_tasks)} existing sub-tasks in DB to process.")
        sub_task_ids = [st["task_id"] for st in sub_tasks]
        await trigger_uploaded_batch(
            request,
            task_id,
            sub_task_ids,
            model_type,
            year_month_day,
            category_dir,
            segmentation_enabled,
            color_normalization_enabled,
        )
    else:
        # Fallback to index-based generation if no sub-tasks are staged in database
        print(f"[BATCH TRIGGER] No pre-staged sub-tasks found. Generating index-based sub-tasks.")
        sub_task_ids = []
        trigger_coroutines = []
        for idx, final_local_path in enumerate(saved_paths):
            sub_task_id = f"{task_id}_{idx}"
            sub_task_ids.append(sub_task_id)
            filename = os.path.basename(final_local_path)
            
            await db.tasks.update_one(
                {"task_id": sub_task_id},
                {"$set": {
                    "task_id": sub_task_id,
                    "filename": filename,
                    "parent_task_id": task_id,
                    "status": "queued",
                    "local_path": final_local_path,
                    "model_type": model_type,
                    "infrastructure_category": model_type,
                    "user_id": current_user.get("id"),
                    "created_at": datetime.utcnow()
                }},
                upsert=True
            )
            
            sub_crack_api_filepath = f"{settings.INTERNAL_AI_DATA_ROOT}/{year_month_day}/{category_dir}/{task_id}/{filename}".replace('//', '/')
            trigger_coroutines.append(_safe_trigger(sub_task_id, filename, sub_crack_api_filepath))
            
        await asyncio.gather(*trigger_coroutines)

    # Update main task status to processing
    await db.tasks.update_one({"task_id": task_id}, {"$set": {"status": "processing"}})
    await notifier.emit(task_id, "processing", progress=10, message="Processing batch...")

    # 2. Poll MongoDB until all sub-tasks are completed (done or error)
    start_time = time.time()
    timeout = 14400 # 4 hours timeout for 52,000+ image folders
    completed_filter = {
        "parent_task_id": task_id,
        "$or": [
            {"status": {"$in": ["done", "error"]}},
            {"processingStatus": {"$in": ["xử lý xong", "lỗi"]}},
            {"processingStatus": {"$regex": "^lỗi:", "$options": "i"}},
        ],
    }
    completed_count = 0

    while completed_count < len(sub_task_ids):
        # Avoid infinite loop
        if time.time() - start_time > timeout:
            print(f"[BATCH TIMEOUT] {task_id}: Processing timed out after {timeout}s")
            break

        await asyncio.sleep(3) # Poll every 3 seconds
        
        # Counting is O(index) and avoids transferring an ever-growing set of
        # tens of thousands of documents every three seconds.
        completed_count = await db.tasks.count_documents(completed_filter)
        total_files = len(sub_task_ids)
        elapsed = time.time() - start_time
        fps = round(completed_count / elapsed, 1) if elapsed > 0 else 0
        remaining_count = max(0, total_files - completed_count)
        eta_seconds = int(remaining_count / fps) if fps > 0 else 0
        progress_val = int(10 + (completed_count / total_files) * 85)
        
        proc_msg = f"Đang xử lý: {completed_count}/{total_files} tệp | {fps} ảnh/s"
        
        telemetry = {
            "progress": progress_val,
            "processingStatus": proc_msg,
            "fps": fps,
            "eta_seconds": eta_seconds,
            "elapsed_seconds": int(elapsed),
            "processed_count": completed_count,
            "total_count": total_files,
        }
        await db.tasks.update_one({"task_id": task_id}, {"$set": telemetry})
        await notifier.emit(task_id, "processing", progress=progress_val, message=proc_msg, **telemetry)

    # 3. Aggregate all results into the main task document
    completed_docs = await db.tasks.find(completed_filter).to_list(length=100000)
    completed_sub_tasks = {}
    successful_count = 0
    error_count = 0
    for sub_doc in completed_docs:
        sub_id = sub_doc.get("task_id")
        if not sub_id:
            continue
        status_val = sub_doc.get("status")
        proc_status = sub_doc.get("processingStatus") or ""
        is_done = status_val == "done" or "xong" in proc_status.lower()
        if is_done:
            successful_count += 1
        else:
            error_count += 1
        completed_sub_tasks[sub_id] = {
            "status": "done" if is_done else "error",
            "doc": sub_doc,
        }

    # MongoDB documents are limited to 16 MB. Persist large-folder results in
    # a paginated collection and keep only a small preview on the parent task.
    result_collection = db.batch_results
    await result_collection.delete_many({"task_id": task_id})
    all_best_frames = []
    result_count = 0
    result_writes = []
    
    for sub_id in sub_task_ids:
        result = completed_sub_tasks.get(sub_id)
        if not result or result["status"] == "error":
            continue
            
        sub_doc = result["doc"]
        sub_best_frames = sub_doc.get("best_frames", [])
        
        # Try extracting from sub_doc's datas field first (native AI Service format)
        if not sub_best_frames:
            sub_datas = sub_doc.get("datas")
            if sub_datas and isinstance(sub_datas, list) and len(sub_datas) > 0:
                images = sub_datas[0].get("images", [])
                if images:
                    sub_best_frames = []
                    for img in images:
                        detections = img.get("detections", [])
                        frame_file_path = img.get("frameFilePath")
                        
                        normalized_detections = []
                        for det in detections:
                            bbox = det.get("bbox")
                            polygon = det.get("polygon")
                            local_p = sub_doc.get("local_path")
                            if local_p and os.path.exists(local_p):
                                try:
                                    import cv2
                                    temp_img = cv2.imread(local_p)
                                    if temp_img is not None:
                                        h, w = temp_img.shape[:2]
                                        if bbox and len(bbox) == 4:
                                            if any(v > 1.05 for v in bbox):
                                                bbox = [
                                                    bbox[0] / w,
                                                    bbox[1] / h,
                                                    bbox[2] / w,
                                                    bbox[3] / h
                                                ]
                                        if polygon and isinstance(polygon, list):
                                            valid_points = [
                                                p for p in polygon
                                                if isinstance(p, (list, tuple)) and len(p) >= 2
                                            ]
                                            if valid_points and any(
                                                float(p[0]) > 1.05 or float(p[1]) > 1.05
                                                for p in valid_points
                                            ):
                                                polygon = [
                                                    [float(p[0]) / w, float(p[1]) / h]
                                                    for p in valid_points
                                                ]
                                except Exception as norm_err:
                                    print(f"[NORM ERROR] Failed to normalize: {norm_err}")
                            
                            normalized_detections.append({
                                "class": det.get("class"),
                                "class_id": det.get("class_id"),
                                "raw_class_id": det.get("raw_class_id", det.get("class_id")),
                                "raw_class_name": det.get("raw_class_name", det.get("class")),
                                "class_mapping_applied": bool(det.get("class_mapping_applied", False)),
                                "confidence": float(det.get("confidence") or 0),
                                "bbox": bbox,
                                "polygon": polygon
                            })
                        
                        filename = os.path.basename(sub_doc.get("local_path", ""))
                        sub_best_frames.append({
                            "id": filename,
                            "frame_index": img.get("frame_index", 0),
                            "timestamp": img.get("timestamp", "00:00"),
                            "frameFilePath": frame_file_path,
                            "url": frame_file_path,
                            "status": "pending",
                            "detections": normalized_detections
                        })
        
        # If best_frames is empty but AI finished, try discovering from disk
        if not sub_best_frames:
            local_p_raw = sub_doc.get("local_path")
            if local_p_raw:
                from pathlib import Path
                p_obj = Path(local_p_raw)
                iso_folder = p_obj if p_obj.is_dir() else p_obj.parent
                sub_best_frames = await anyio.to_thread.run_sync(
                    discover_best_frames, sub_id, str(iso_folder), model_type, sub_doc.get("confidence", 0)
                )
                
        for frame in sub_best_frames:
            result_index = result_count
            result_count += 1
            # Every image in a folder batch must have a globally unique frame
            # index. Child image tasks naturally use frame_index=0, which
            # otherwise makes the review endpoint always select the first
            # image when Vision AI is requested for image 2, 3, ...
            frame["frame_index"] = result_index
            frame["batch_result_index"] = result_index
            result_id = f"{task_id}:{sub_id}:{result_index}"
            result_doc = {
                "_id": result_id,
                "task_id": task_id,
                "sub_task_id": sub_id,
                "result_index": result_index,
                "has_detection": bool(frame.get("detections")),
                "frame": frame,
                "created_at": datetime.utcnow(),
            }
            result_writes.append(
                ReplaceOne({"_id": result_id}, result_doc, upsert=True)
            )
            if len(all_best_frames) < 200:
                all_best_frames.append(frame)
            if len(result_writes) >= 500:
                await result_collection.bulk_write(result_writes, ordered=False)
                result_writes = []

    if result_writes:
        await result_collection.bulk_write(result_writes, ordered=False)

    # 4. Commit the parent before deleting source records. The old ordering
    # could permanently lose recoverable results if the backend restarted
    # between sub-task deletion and this parent update.
    pending_count = max(0, len(sub_task_ids) - successful_count - error_count)
    # A clean image with zero detections is a successful AI result, not an error.
    final_status = "done" if successful_count > 0 and pending_count == 0 else "error"
    
    if generate_3d and final_status == "done" and len(saved_paths) > 0:
        try:
            first_filename = os.path.basename(saved_paths[0])
            first_ai_filepath = f"{settings.INTERNAL_AI_DATA_ROOT}/{year_month_day}/{category_dir}/{task_id}/{first_filename}".replace('//', '/')
            await _trigger_3d_twin(request, task_id, model_type, first_ai_filepath)
        except Exception as twin_e:
            print(f"[BATCH 3D TRIGGER WARNING] Could not trigger 3D Twin: {twin_e}")

    update_payload = {
        "status": final_status,
        "best_frames": all_best_frames,
        "processed_count": successful_count,
        "error_count": error_count,
        "pending_count": pending_count,
        "result_count": result_count,
        "preview_count": len(all_best_frames),
        "trackingDataUrl": f"/api/crack/tracking/{task_id}",
        "updated_at": datetime.utcnow()
    }
    
    await db.tasks.update_one({"task_id": task_id}, {"$set": update_payload})

    # 5. Clean up only after the durable parent/result documents are committed.
    try:
        completed_ids = list(completed_sub_tasks.keys())
        if completed_ids:
            await db.tasks.delete_many({"task_id": {"$in": completed_ids}})
        print(f"[BATCH CLEANUP] Deleted {len(completed_ids)} completed temporary sub-task documents")
    except Exception as cleanup_e:
        print(f"[BATCH CLEANUP WARNING] Could not delete temporary sub-task records: {cleanup_e}")
    
    if final_status == "done":
        await notifier.emit(task_id, "done", progress=100, message="Hoàn tất phân tích cả thư mục!")
    else:
        await notifier.emit(task_id, "error", progress=0, message="Lỗi phân tích thư mục")

@router.get("/upload-url")
async def get_upload_url(filename: str, current_user: dict = Depends(get_current_user)):
    """Generates a presigned URL for direct large-file uploads to storage."""
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    object_name = f"uploads/{task_id}_{filename.replace(' ', '_')}"
    
    url = storage_service.generate_presigned_url(object_name)
    if not url:
        raise HTTPException(status_code=500, detail="Could not generate upload URL")
        
    return {"task_id": task_id, "upload_url": url, "object_name": object_name, "method": "PUT"}

@router.websocket("/ws/task/{task_id}")
async def task_status_websocket(websocket: WebSocket, task_id: str):
    """WebSocket gateway for real-time task progress updates."""
    user = await _authenticate_websocket(websocket)
    if not user:
        return
    await websocket.accept()
    r = await notifier.get_redis()
    pubsub = r.pubsub()
    
    channel = f"task_updates:{task_id}"
    await pubsub.subscribe(channel)
    
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                if data.get("task_id") == task_id:
                    await websocket.send_json(data)
                    if data.get("status") in ["done", "error", "completed", "failed"]:
                        break
    except Exception as e:
        print(f"[WS ERROR] {e}")
    finally:
        await pubsub.unsubscribe(channel)
        await websocket.close()

@router.post("/import-local")
async def import_local_file(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    """Import an existing file or folder from local disk to the AI pipeline."""
    source_path = payload.get("filepath")
    model_type = payload.get("model_type", "road")
    survey_id = payload.get("survey_id")
    segmentation_enabled = payload.get("segmentation_enabled")
    color_normalization_enabled = payload.get("color_normalization_enabled")
    
    if not source_path or not os.path.exists(source_path):
        raise HTTPException(status_code=400, detail="Đường dẫn cục bộ không tồn tại")
    
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    year_month_day = datetime.now().strftime("%Y/%m/%d")
    category_dir = "bridge" if any(k in model_type.lower() for k in ["bridge", "pier", "concrete"]) else "road"
    
    # Lưu vào thư mục cha của task
    task_dir = os.path.join(UPLOAD_DIR, year_month_day.replace('/', os.sep), category_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    
    is_dir = os.path.isdir(source_path)
    
    if is_dir:
        original_filename = os.path.basename(source_path.rstrip(os.sep)) or "local_folder"
        final_local_path = task_dir  # Đường dẫn tới thư mục task
        status = "queued"
        safe_filename = ""
    else:
        original_filename = os.path.basename(source_path)
        safe_filename = original_filename.replace(' ', '_')
        task_safe_filename = f"{task_id}_{safe_filename}"
        final_local_path = os.path.join(task_dir, task_safe_filename)
        status = "transferring"

    await add_task(task_id, original_filename, model_type, current_user["id"], final_local_path, status=status, survey_id=survey_id)

    background_tasks.add_task(
        process_local_import_background,
        request, task_id, original_filename, model_type, 
        source_path, final_local_path, year_month_day, safe_filename, current_user,
        is_dir, survey_id, segmentation_enabled, color_normalization_enabled
    )

    return {"task_id": task_id, "status": "queued", "message": "Import started..."}

async def process_local_import_background(
    request: Request,
    task_id: str,
    original_filename: str,
    model_type: str,
    source_path: str,
    final_local_path: str,
    year_month_day: str,
    safe_filename: str,
    current_user: dict,
    is_dir: bool = False,
    survey_id: Optional[str] = None,
    segmentation_enabled: Optional[bool] = None,
    color_normalization_enabled: Optional[bool] = None,
):
    """Worker for local disk imports."""
    try:
        if is_dir:
            # Scanning directory recursively for image and video formats
            media_files = []
            supported_exts = {".jpg", ".jpeg", ".png", ".bmp", ".mp4", ".avi", ".mov", ".mkv"}
            for root, dirs, files in os.walk(source_path):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in supported_exts:
                        media_files.append(os.path.join(root, f))
            
            if not media_files:
                raise Exception("Không tìm thấy tệp ảnh hoặc video hợp lệ (.jpg, .jpeg, .png, .bmp, .mp4, .avi, .mov, .mkv) trong thư mục.")

            total_files = len(media_files)
            await notifier.emit(task_id, "transferring", progress=20, message=f"Tìm thấy {total_files} tệp. Bắt đầu sao chép...")
            
            saved_paths = []
            category_dir = "bridge" if any(k in model_type.lower() for k in ["bridge", "pier", "concrete"]) else "road"
            for idx, file_path in enumerate(media_files):
                rel_path = os.path.relpath(file_path, source_path)
                safe_rel_name = rel_path.replace(os.sep, '_').replace(' ', '_')
                dest_path = os.path.join(final_local_path, f"{task_id}_{safe_rel_name}")
                
                # Copy file
                await anyio.to_thread.run_sync(shutil.copy2, file_path, dest_path)
                saved_paths.append(dest_path)
                
                # Emit progress every 100 files or at completion
                if (idx + 1) % 100 == 0 or (idx + 1) == total_files:
                    progress = int(20 + 45 * (idx + 1) / total_files)
                    await notifier.emit(task_id, "transferring", progress=progress, message=f"Đang sao chép tệp ({idx+1}/{total_files})...")
            
            # Now trigger batch processing
            await notifier.emit(task_id, "processing", progress=70, message="Khởi tạo hàng đợi phân tích AI...")
            await process_batch_upload_and_trigger_ai(
                request, task_id, saved_paths, model_type,
                final_local_path, year_month_day, category_dir, current_user, generate_3d=False,
                segmentation_enabled=segmentation_enabled,
                color_normalization_enabled=color_normalization_enabled,
            )
        else:
            await notifier.emit(task_id, "transferring", progress=10, message="Localizing file...")
            os.makedirs(os.path.dirname(final_local_path), exist_ok=True)

            if os.path.abspath(source_path) != os.path.abspath(final_local_path):
                with open(source_path, "rb") as src, open(final_local_path, "wb") as dst:
                    await anyio.to_thread.run_sync(shutil.copyfileobj, src, dst, 1024*1024)
            
            await notifier.emit(task_id, "processing", progress=60, message="Starting AI Inference...")
            
            # Construct the internal AI path (cấu trúc mới: .../category/task_id/filename)
            category_dir = "bridge" if any(k in model_type.lower() for k in ["bridge", "pier", "concrete"]) else "road"
            task_safe_filename = f"{task_id}_{safe_filename}"
            crack_api_filepath = f"{settings.INTERNAL_AI_DATA_ROOT}/{year_month_day}/{category_dir}/{task_id}/{task_safe_filename}".replace('//', '/')
            
            await _trigger_ai_analysis(
                request,
                task_id,
                model_type,
                crack_api_filepath,
                segmentation_enabled,
                color_normalization_enabled,
            )
            await notifier.emit(task_id, "processing", progress=80, message="Analysis in progress...")

    except Exception as e:
        err_msg = str(e)
        print(f"[IMPORT ERROR] {task_id}: {err_msg}")
        await notifier.emit(task_id, "error", progress=0, message=err_msg)
        db = get_db()
        if db is not None:
            await db.tasks.update_one({"task_id": task_id}, {"$set": {"status": "error", "message": err_msg}})

async def _trigger_ai_analysis(
    request,
    task_id,
    model_type,
    crack_api_filepath,
    segmentation_enabled: Optional[bool] = None,
    color_normalization_enabled: Optional[bool] = None,
):
    """Calls the AI Service endpoint."""
    client = _get_client(request)
    
    conf = load_config()
    api_url = settings.CRACK_API_URL or conf.get("crack_api_url")
    
    endpoint = f"{api_url}/api/v1/detect/image"
    if any(fmt in crack_api_filepath.lower() for fmt in [".mp4", ".avi", ".mov", ".mkv"]):
        endpoint = f"{api_url}/api/v1/detect/video"
        
    for attempt in range(5):
        try:
            payload = {"FilePath": crack_api_filepath, "RequestId": task_id, "ModelType": model_type}
            if segmentation_enabled is not None:
                payload["segmentation_enabled"] = segmentation_enabled
            if color_normalization_enabled is not None:
                payload["color_normalization_enabled"] = (
                    color_normalization_enabled
                )
            print(f"[AI TRIGGER] POST {endpoint} | File: {crack_api_filepath} (Attempt {attempt+1}/5)")
            response = await client.post(endpoint, headers=_crack_headers(), json=payload, timeout=180.0)
            
            if response.status_code == 200:
                return {"task_id": task_id, "status": "queued"}
            
            # If AI Server/Proxy returns transient 502/503/504, wait and retry instead of failing immediately
            if response.status_code in (502, 503, 504) and attempt < 4:
                print(f"[AI TRIGGER WARN] Received {response.status_code} from AI server, retrying in {(attempt + 1) * 1.5}s...")
                await asyncio.sleep((attempt + 1) * 1.5)
                continue
                
            raise HTTPException(status_code=response.status_code, detail=f"AI Error: {response.status_code}")
            
        except httpx.ConnectError:
            if attempt == 4:
                raise HTTPException(status_code=503, detail="AI Service is offline (Connection refused)")
            await asyncio.sleep(1.0 * (attempt + 1))
        except HTTPException as he:
            if he.status_code in (502, 503, 504) and attempt < 4:
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            raise
        except Exception as exc:
            if attempt == 4:
                raise HTTPException(status_code=500, detail=str(exc))
            await asyncio.sleep(1.0 * (attempt + 1))


async def _trigger_ai_batch_analysis(request: Request, jobs: List[dict]):
    """Submit a bounded image group to one Celery high-quality batch task."""
    if not jobs:
        return
    client = _get_client(request)
    conf = load_config()
    api_url = settings.CRACK_API_URL or conf.get("crack_api_url")
    endpoint = f"{api_url}/api/v1/detect/images-batch"
    for attempt in range(5):
        try:
            response = await client.post(
                endpoint,
                headers=_crack_headers(),
                json={"Requests": jobs},
                timeout=180.0,
            )
            if response.status_code == 200:
                return response.json()
            if response.status_code in (429, 502, 503, 504) and attempt < 4:
                await asyncio.sleep(min(1.5 * (2 ** attempt), 12.0))
                continue
            raise HTTPException(
                status_code=response.status_code,
                detail=f"AI batch error: {response.status_code}",
            )
        except (httpx.ConnectError, httpx.TimeoutException):
            if attempt == 4:
                raise HTTPException(
                    status_code=503,
                    detail="AI batch service is unavailable",
                )
            await asyncio.sleep(min(1.5 * (2 ** attempt), 12.0))
        except HTTPException as he:
            if he.status_code in (502, 503, 504) and attempt < 4:
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            raise
        except Exception as e:
            if attempt == 4:
                raise HTTPException(status_code=500, detail=str(e))
            await asyncio.sleep(1.0 * (attempt + 1))

async def _trigger_3d_twin(request, task_id, model_type, crack_api_filepath):
    """Triggers the 3D Digital Twin reconstruction."""
    client = _get_client(request)
    
    # URL của Meshroom Master API. Nếu đang chạy docker-compose, nó ở port 8090
    twin_api_url = settings.TWIN_API_URL
    endpoint = f"{twin_api_url}/api/v1/twin/create-from-local"
    
    payload = {
        "file_path": crack_api_filepath,
        "model_type": model_type,
        "job_id": task_id  # Đồng bộ ID: Meshroom dùng cùng ID với Crack
    }
    
    try:
        print(f"[TWIN TRIGGER] POST {endpoint} | File: {crack_api_filepath}")
        response = await client.post(
            endpoint,
            headers=_twin_headers(),
            json=payload,
            timeout=30.0,
        )
        
        if response.status_code == 200:
            twin_res = response.json()
            job_id = twin_res.get("job_id")
            print(f"[TWIN TRIGGER] Success: {job_id}")
            # Optional: Lưu twin_job_id vào DB để sau này Web Frontend query
            db = get_db()
            if db is not None:
                await db.tasks.update_one(
                    {"task_id": task_id}, 
                    {"$set": {"twin_job_id": job_id, "twin_status": "queued"}}
                )
        else:
            try:
                error_detail = response.json().get("detail") or response.text
            except Exception:
                error_detail = response.text
            error_message = f"Twin API {response.status_code}: {error_detail}"
            print(f"[TWIN TRIGGER] {error_message}")
            db = get_db()
            if db is not None:
                await db.tasks.update_one(
                    {"task_id": task_id},
                    {"$set": {"twin_status": "failed", "twin_error": error_message}},
                )
            return {"status": False, "error": error_message}
    except httpx.ConnectError:
        error_message = f"Twin API is offline ({endpoint})"
        print(f"[TWIN TRIGGER] Error: {error_message}")
        db = get_db()
        if db is not None:
            await db.tasks.update_one(
                {"task_id": task_id},
                {"$set": {"twin_status": "failed", "twin_error": error_message}},
            )
        return {"status": False, "error": error_message}
    except Exception as e:
        error_message = str(e)
        print(f"[TWIN TRIGGER] Exception: {error_message}")
        db = get_db()
        if db is not None:
            await db.tasks.update_one(
                {"task_id": task_id},
                {"$set": {"twin_status": "failed", "twin_error": error_message}},
            )
        return {"status": False, "error": error_message}

@router.get("/tracking/{task_id}")
async def serve_tracking_data(
    task_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Serves and NORMALIZES the tracking_data.json file from the local task folder.
    Ensures bboxes are in 0..1 range for FE compatibility.
    """
    local_dir = await get_task_local_dir(task_id, find_video=False)
    if not local_dir:
        raise HTTPException(status_code=404, detail="Task folder not found")
    
    # v72.6: Deep search for JSON results if standard names fail
    possible_names = ["tracking_data.json", "tracking.json", "detections.json", "results.json"]
    json_path = None
    
    # 1. Standard check
    for name in possible_names:
        test_path = os.path.join(local_dir, name)
        if os.path.exists(test_path):
            json_path = test_path
            break
            
    # 2. Deep search fallback
    if not json_path:
        from pathlib import Path
        all_jsons = list(Path(local_dir).glob("**/*.json"))
        if all_jsons:
            # Pick the most likely one (containing 'track' or the largest one)
            json_path = str(next((p for p in all_jsons if "track" in p.name.lower()), all_jsons[0]))

    if not json_path:
        raise HTTPException(status_code=404, detail="Tracking JSON not found after deep search")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        frames = data.get("frames", {})
        if not frames:
            return data

        # Get video dimensions for normalization
        video_path = await get_task_local_dir(task_id, find_video=True)
        width, height = 1920, 1080 # Default
        if video_path and os.path.exists(video_path):
            try:
                import cv2
                cap = cv2.VideoCapture(video_path)
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
                cap.release()
            except: pass

        # Perform normalization on all frames
        for frame_id in frames:
            frames[frame_id] = normalize_detections(frames[frame_id], width, height)
        
        return data
    except Exception as e:
        print(f"[TRACKING ERROR] {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def _batch_terminal_filter(task_id: str) -> dict:
    """Filter used by both normal aggregation and orphan recovery."""
    return {
        "parent_task_id": task_id,
        "$or": [
            {"status": {"$in": ["done", "error"]}},
            {
                "processingStatus": {
                    "$regex": "(xong|lỗi|error|failed)",
                    "$options": "i",
                }
            },
        ],
    }


def _subtask_succeeded(sub_doc: dict) -> bool:
    status = str(sub_doc.get("status") or "").lower()
    processing_status = str(sub_doc.get("processingStatus") or "").lower()
    return status == "done" or "xong" in processing_status


def _frames_from_subtask(sub_doc: dict) -> list:
    """Normalize the AI service document into the Web batch-result shape."""
    frames = sub_doc.get("best_frames")
    if isinstance(frames, list) and frames:
        return frames

    datas = sub_doc.get("datas")
    if not isinstance(datas, list) or not datas:
        return []
    images = datas[0].get("images", [])
    if not isinstance(images, list):
        return []

    filename = os.path.basename(
        str(sub_doc.get("local_path") or sub_doc.get("filename") or "")
    )
    normalized = []
    for image_index, image in enumerate(images):
        if not isinstance(image, dict):
            continue
        frame_path = image.get("frameFilePath")
        detections = image.get("detections")
        if not isinstance(detections, list):
            detections = []
        normalized.append(
            {
                "id": filename or f"{sub_doc.get('task_id')}:{image_index}",
                "frame_index": image.get("frame_index", image_index),
                "timestamp": image.get("timestamp", "00:00"),
                "frameFilePath": frame_path,
                "url": frame_path,
                "status": "pending",
                "detections": detections,
            }
        )
    return normalized


async def _recover_completed_batch(task_id: str) -> None:
    """
    Rebuild a completed large-folder task from durable sub-task documents.

    Results use deterministic keys and a generation marker, so interrupted
    recovery can safely restart without duplicating or deleting good data.
    """
    db = get_db()
    if db is None:
        return

    generation = uuid.uuid4().hex
    terminal_filter = _batch_terminal_filter(task_id)
    try:
        total = await db.tasks.count_documents({"parent_task_id": task_id})
        completed = await db.tasks.count_documents(terminal_filter)
        if total <= 0 or completed < total:
            await db.tasks.update_one(
                {"task_id": task_id},
                {
                    "$set": {
                        "aggregation_state": "waiting",
                        "aggregation_completed": completed,
                        "aggregation_total": total,
                    },
                    "$unset": {"aggregation_lease_until": ""},
                },
            )
            return

        successful_count = 0
        error_count = 0
        result_count = 0
        preview = []
        writes = []

        cursor = db.tasks.find(terminal_filter).sort("task_id", 1)
        async for sub_doc in cursor:
            sub_id = sub_doc.get("task_id")
            if not sub_id:
                continue
            if not _subtask_succeeded(sub_doc):
                error_count += 1
                continue

            successful_count += 1
            for frame_index, frame in enumerate(_frames_from_subtask(sub_doc)):
                result_id = f"{task_id}:{sub_id}:{frame_index}"
                writes.append(
                    ReplaceOne(
                        {"_id": result_id},
                        {
                            "_id": result_id,
                            "task_id": task_id,
                            "sub_task_id": sub_id,
                            "result_index": result_count,
                            "has_detection": bool(frame.get("detections")),
                            "frame": frame,
                            "aggregation_generation": generation,
                            "created_at": datetime.utcnow(),
                        },
                        upsert=True,
                    )
                )
                result_count += 1
                if len(preview) < 200:
                    preview.append(frame)

                if len(writes) >= 500:
                    await db.batch_results.bulk_write(writes, ordered=False)
                    writes = []
                    await db.tasks.update_one(
                        {"task_id": task_id},
                        {
                            "$set": {
                                "aggregation_lease_until": (
                                    datetime.utcnow() + timedelta(minutes=15)
                                ),
                                "processingStatus": (
                                    f"Đang phục hồi kết quả: {result_count} ảnh..."
                                ),
                                "progress": 98,
                            }
                        },
                    )

        if writes:
            await db.batch_results.bulk_write(writes, ordered=False)

        # Remove leftovers only after the replacement generation is complete.
        await db.batch_results.delete_many(
            {
                "task_id": task_id,
                "aggregation_generation": {"$ne": generation},
            }
        )

        pending_count = max(0, total - successful_count - error_count)
        final_status = (
            "done" if successful_count > 0 and pending_count == 0 else "error"
        )
        
        # Lấy tổng số lượng hư hỏng (detections) từ db.batch_results sau khi tổng hợp
        total_detections = 0
        try:
            pipeline = [
                {"$match": {"task_id": task_id, "has_detection": True}},
                {"$project": {"detection_count": {"$size": {"$ifNull": ["$frame.detections", []]}}}},
                {"$group": {"_id": None, "total": {"$sum": "$detection_count"}}}
            ]
            agg_result = await db.batch_results.aggregate(pipeline).to_list(1)
            if agg_result:
                total_detections = agg_result[0].get("total", 0)
        except Exception as agg_e:
            print(f"[BATCH RECOVERY] {task_id}: Failed to aggregate total_detections: {agg_e}")

        await db.tasks.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "status": final_status,
                    "processingStatus": (
                        "xử lý xong" if final_status == "done" else "lỗi"
                    ),
                    "progress": 100 if final_status == "done" else 0,
                    "best_frames": preview,
                    "processed_count": successful_count,
                    "error_count": error_count,
                    "pending_count": pending_count,
                    "result_count": result_count,
                    "preview_count": len(preview),
                    "total_detections": total_detections,
                    "trackingDataUrl": f"/api/crack/tracking/{task_id}",
                    "aggregation_state": "completed",
                    "aggregation_generation": generation,
                    "updated_at": datetime.utcnow(),
                },
                "$unset": {
                    "aggregation_lease_until": "",
                    "aggregation_error": "",
                },
            },
        )
        await notifier.emit(
            task_id,
            final_status,
            progress=100 if final_status == "done" else 0,
            message="Đã phục hồi và tổng hợp kết quả phân tích.",
        )
        print(
            f"[BATCH RECOVERY] {task_id}: status={final_status}, "
            f"processed={successful_count}, errors={error_count}, "
            f"results={result_count}"
        )
    except Exception as exc:
        print(f"[BATCH RECOVERY ERROR] {task_id}: {exc}")
        await db.tasks.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "aggregation_state": "error",
                    "aggregation_error": str(exc),
                    "processingStatus": (
                        "Lỗi tổng hợp kết quả, hệ thống sẽ tự thử lại."
                    ),
                    "updated_at": datetime.utcnow(),
                },
                "$unset": {"aggregation_lease_until": ""},
            },
        )


async def _start_batch_recovery_if_ready(db, task_record: dict) -> bool:
    """Acquire a MongoDB lease and start one idempotent recovery job."""
    task_id = task_record.get("task_id") or task_record.get("_id")
    if not task_id or not task_record.get("batch_dispatch_started"):
        return False

    total = await db.tasks.count_documents({"parent_task_id": task_id})
    if total <= 0:
        return False
    completed = await db.tasks.count_documents(_batch_terminal_filter(task_id))
    if completed < total:
        return False

    now = datetime.utcnow()
    claim = await db.tasks.update_one(
        {
            "task_id": task_id,
            "status": "processing",
            "$or": [
                {"aggregation_state": {"$ne": "running"}},
                {"aggregation_lease_until": {"$exists": False}},
                {"aggregation_lease_until": {"$lt": now}},
            ],
        },
        {
            "$set": {
                "aggregation_state": "running",
                "aggregation_lease_until": now + timedelta(minutes=15),
                "processingStatus": (
                    f"Đang tổng hợp kết quả: {completed}/{total} tệp..."
                ),
                "progress": 97,
                "updated_at": now,
            }
        },
    )
    if claim.modified_count != 1:
        return True

    recovery_task = asyncio.create_task(_recover_completed_batch(str(task_id)))
    _BATCH_RECOVERY_TASKS.add(recovery_task)
    recovery_task.add_done_callback(_BATCH_RECOVERY_TASKS.discard)
    return True


@router.get("/batch-results/{task_id}")
async def get_batch_results(
    task_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    detected_only: bool = Query(True),
    current_user: dict = Depends(get_current_user),
):
    """Return large-folder results without exceeding MongoDB document limits."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")

    task = await db.tasks.find_one(
        {"task_id": task_id},
        {"user_id": 1},
    )
    if not task:
        raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ")
    if current_user.get("role") != "admin" and task.get("user_id") != current_user.get("id"):
        raise HTTPException(status_code=403, detail="Không có quyền truy cập tác vụ")

    query = {"task_id": task_id}
    if detected_only:
        query["has_detection"] = True
    total = await db.batch_results.count_documents(query)
    docs = await (
        db.batch_results.find(query, {"_id": 0, "frame": 1, "result_index": 1})
        .sort("result_index", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(length=page_size)
    )
    return {
        "task_id": task_id,
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [
            {
                **doc.get("frame", {}),
                # Stable global index used by the paginated approval workflow.
                # The local index inside one UI page is not a database key.
                "_batch_result_index": doc.get("result_index"),
            }
            for doc in docs
        ],
    }


@router.get("/status/{task_id}")
async def get_detection_status(task_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """Polls AI status and syncs results to local DB."""
    db = get_db()
    task_record = None
    if db is not None:
        # Match by either _id or task_id
        task_record = await db.tasks.find_one({"$or": [{"_id": task_id}, {"task_id": task_id}]})
        if task_record:
            status_val = task_record.get("status")
            proc_status = task_record.get("processingStatus")
            
            # If done, return done immediately
            if status_val == "done":
                best_frames = task_record.get("best_frames", [])
                local_tracking_url = f"/api/crack/tracking/{task_id}"
                return {
                    "status": "done",
                    "best_frames": best_frames,
                    "survey_id": task_record.get("survey_id"),
                    "infrastructure_category": task_record.get("infrastructure_category") or task_record.get("model_type") or "road",
                    "route_name": task_record.get("route_name", ""),
                    "trackingDataUrl": local_tracking_url,
                    "result_count": task_record.get("result_count", len(best_frames)),
                    "processed_count": task_record.get("processed_count"),
                    "error_count": task_record.get("error_count", 0),
                    "progress": task_record.get("progress", 100),
                    "fps": task_record.get("fps", 0),
                    "eta_seconds": task_record.get("eta_seconds", 0),
                    "elapsed_seconds": task_record.get("elapsed_seconds", 0),
                    "total_count": task_record.get("total_count", task_record.get("processed_count", 1)),
                }
            
            # If processing (e.g. batch task aggregating sub-tasks)
            if status_val == "processing":
                # FastAPI BackgroundTasks are not durable. If the backend was
                # restarted after Celery finished, rebuild the parent from
                # MongoDB without re-uploading or re-running inference.
                recovering = await _start_batch_recovery_if_ready(db, task_record)
                if recovering:
                    refreshed = await db.tasks.find_one(
                        {"task_id": task_record.get("task_id") or task_id},
                        {
                            "processingStatus": 1,
                            "progress": 1,
                            "fps": 1,
                            "eta_seconds": 1,
                            "elapsed_seconds": 1,
                            "processed_count": 1,
                            "total_count": 1,
                            "aggregation_state": 1,
                        },
                    )
                    return {
                        "status": "processing",
                        "processingStatus": (refreshed or {}).get(
                            "processingStatus", "Đang tổng hợp kết quả..."
                        ),
                        "progress": (refreshed or {}).get("progress", 97),
                        "fps": (refreshed or {}).get("fps", 0),
                        "eta_seconds": (refreshed or {}).get("eta_seconds", 0),
                        "elapsed_seconds": (refreshed or {}).get("elapsed_seconds", 0),
                        "processed_count": (refreshed or {}).get("processed_count", 0),
                        "total_count": (refreshed or {}).get("total_count", 0),
                        "aggregation_state": (refreshed or {}).get(
                            "aggregation_state"
                        ),
                    }
                proc_msg = proc_status or "Đang xử lý..."
                raw_progress = task_record.get("progress", 50)
                try:
                    progress_value = float(str(raw_progress).rstrip("%"))
                except (TypeError, ValueError):
                    progress_value = 0
                return {
                    "status": "processing", 
                    "processingStatus": proc_msg, 
                    "progress": progress_value,
                    "fps": task_record.get("fps", 0),
                    "eta_seconds": task_record.get("eta_seconds", 0),
                    "elapsed_seconds": task_record.get("elapsed_seconds", 0),
                    "processed_count": task_record.get("processed_count", 0),
                    "total_count": task_record.get("total_count", 0),
                }
            
            # If error or "lỗi", return error immediately
            if status_val == "error" or (proc_status and "lỗi" in proc_status.lower()) or task_record.get("ErrorCode") == "PROCESSING_ERROR":
                if db is not None and status_val != "error":
                    await db.tasks.update_one(
                        {"$or": [{"_id": task_id}, {"task_id": task_id}]},
                        {"$set": {"status": "error", "processingStatus": "lỗi"}}
                    )
                return {"status": "error", "best_frames": [], "trackingDataUrl": None}

    conf = load_config()
    api_url = settings.CRACK_API_URL or conf.get("crack_api_url")
    client = _get_client(request)
    
    try:
        # Fail fast (3.0s instead of 15.0s) to prevent Cloudflare/NGINX 503 errors when AI server is busy/hanging
        response = await client.get(f"{api_url}/api/v1/status/{task_id}", headers=_crack_headers(), timeout=3.0)
        
        # If AI API returns 404/NOT_FOUND but local DB says we had a processing error or "lỗi"
        if response.status_code == 404:
            if task_record:
                proc_status = task_record.get("processingStatus")
                if proc_status and "lỗi" in proc_status.lower():
                    return {"status": "error"}
            return {"status": "queued"}
            
        data = response.json()
        
        # If AI API returns status with error code
        if not data.get("status") and data.get("ErrorCode") == "NOT_FOUND":
            if task_record:
                proc_status = task_record.get("processingStatus")
                if proc_status and "lỗi" in proc_status.lower():
                    return {"status": "error"}
            return {"status": "queued"}

        ai_data = data.get("data", {})
        ps = (ai_data.get("processingStatus") or "").lower()
        raw_progress = ai_data.get("progress", 0)
        try:
            progress_value = float(str(raw_progress).rstrip("%"))
        except (TypeError, ValueError):
            progress_value = 0
        telemetry = {
            "progress": progress_value,
            "fps": ai_data.get("fps", 0),
            "eta_seconds": ai_data.get("eta_seconds", 0),
            "elapsed_seconds": ai_data.get("elapsed_seconds", 0),
            "processed_count": ai_data.get("processed_count", 0),
            "total_count": ai_data.get("total_count", 0),
        }
        
        if "xong" in ps or "done" in ps: mapped_status = "done"
        elif "ang" in ps or "processing" in ps or "stream" in ps: mapped_status = "processing"
        elif "error" in ps or "fail" in ps or "lỗi" in ps: mapped_status = "error"
        else: mapped_status = "queued"

        best_frames = ai_data.get("datas", [{}])[0].get("images", []) if mapped_status == "done" else []
        
        # v72.0: ALWAYS USE LOCAL PROXY FOR TRACKING
        tracking_url = f"/api/crack/tracking/{task_id}" if mapped_status == "done" else None

        if mapped_status == "done":
            await update_task_results(
                task_id,
                {
                    "status": "done",
                    "best_frames": best_frames,
                    "trackingDataUrl": tracking_url,
                    **telemetry,
                },
            )
            await notifier.emit(task_id, "done", progress=100, message="AI Complete!")
        elif mapped_status == "error":
            # Sync error status to local DB so we don't query the AI API again next time
            if db is not None:
                await db.tasks.update_one(
                    {"$or": [{"_id": task_id}, {"task_id": task_id}]},
                    {"$set": {"status": "error", "processingStatus": "lỗi"}}
                )
        elif db is not None:
            # Keep single-image/video telemetry in the web DB so polling and
            # page reloads do not lose ETA information.
            await db.tasks.update_one(
                {"$or": [{"_id": task_id}, {"task_id": task_id}]},
                {"$set": telemetry},
            )
        
        return {
            "status": mapped_status,
            "best_frames": best_frames,
            "survey_id": task_record.get("survey_id") if task_record else None,
            "infrastructure_category": (task_record or {}).get("infrastructure_category") or (task_record or {}).get("model_type") or "road",
            "route_name": (task_record or {}).get("route_name", ""),
            "trackingDataUrl": tracking_url,
            **telemetry,
        }
        
    except Exception as e:
        print(f"[STATUS ERROR] {task_id}: {e}")
        status_fallback = "queued"
        if task_record:
            if task_record.get("status") == "error" or (task_record.get("processingStatus") and "lỗi" in task_record.get("processingStatus", "").lower()):
                status_fallback = "error"
            else:
                status_fallback = task_record.get("status", "queued")
        return {"status": status_fallback}

async def get_task_local_dir(task_id: str, find_video: bool = False) -> Optional[str]:
    """
    Intelligently resolves the directory or file path for a task.
    Supports both new structure (task_id/snapshot/) and old structure (task_id/ flat).
    """
    db = get_db()
    if db is None: return None
    from bson import ObjectId
    query_conditions = [{"_id": task_id}, {"task_id": task_id}]
    if ObjectId.is_valid(task_id):
        query_conditions.append({"_id": ObjectId(task_id)})
    task = await db.tasks.find_one({"$or": query_conditions})
    if not task or not task.get("local_path"): return None
    
    from pathlib import Path
    p = Path(task.get("local_path"))
    parent_dir = p.parent
    
    # Mode 1: Looking for the Video (always at the parent level)
    if find_video:
        if p.exists(): return str(p)
        return None

    # Mode 2: Looking for AI results (JSON, Snapshots)
    # Priority 1: Check snapshot/ subfolder (cấu trúc mới)
    snapshot_dir = parent_dir / "snapshot"
    if snapshot_dir.exists() and snapshot_dir.is_dir():
        return str(snapshot_dir)
    
    # Priority 2: Check for the task-specific result folder (created by AI server - cấu trúc cũ)
    task_dir = parent_dir / task_id
    if task_dir.exists() and task_dir.is_dir():
        return str(task_dir)
        
    # Priority 3: Fallback to the same folder as the video
    if parent_dir.exists():
        from pathlib import Path
        if list(parent_dir.glob("*.json")):
            return str(parent_dir)

    # Priority 4: Check INTERNAL_AI_DATA_ROOT (where AI server actually writes)
    try:
        if hasattr(settings, 'INTERNAL_AI_DATA_ROOT') and settings.INTERNAL_AI_DATA_ROOT:
            ai_parent_dir_str = str(parent_dir).replace(str(Path(UPLOAD_DIR)), str(Path(settings.INTERNAL_AI_DATA_ROOT)))
            # Kiểm tra snapshot/ trong AI data root (cấu trúc mới)
            ai_snapshot_dir = Path(ai_parent_dir_str) / "snapshot"
            if ai_snapshot_dir.exists() and ai_snapshot_dir.is_dir():
                return str(ai_snapshot_dir)
            # Kiểm tra task_id/ trong AI data root (cấu trúc cũ)
            ai_task_dir = Path(ai_parent_dir_str) / task_id
            if ai_task_dir.exists() and ai_task_dir.is_dir():
                return str(ai_task_dir)
    except Exception:
        pass
        
    if parent_dir.exists():
        return str(parent_dir)
        
    return None

def normalize_detections(detections, width, height):
    """v71.0: Converts Pixel coordinates to Normalized (0-1) for FE Visualizer compatibility."""
    for d in detections:
        bbox = d.get("bbox", [])
        if len(bbox) == 4:
            # Check if coordinates are raw pixels (e.g. > 1.0)
            if any(v > 1.05 for v in bbox):
                # Standardize to 0..1
                d["bbox"] = [
                    round(bbox[0] / width, 6), # x1
                    round(bbox[1] / height, 6), # y1
                    round(bbox[2] / width, 6), # x2
                    round(bbox[3] / height, 6)  # y2
                ]

        # Polygon and bbox can use different coordinate spaces. Normalize the
        # polygon independently so a normalized bbox never hides a pixel-space
        # segmentation mask.
        polygon = d.get("polygon")
        if polygon and isinstance(polygon, list):
            valid_points = [
                p for p in polygon
                if isinstance(p, (list, tuple)) and len(p) >= 2
            ]
            if valid_points:
                if any(float(p[0]) > 1.05 or float(p[1]) > 1.05 for p in valid_points):
                    d["polygon"] = [
                        [round(float(p[0]) / width, 6), round(float(p[1]) / height, 6)]
                        for p in valid_points
                    ]
                else:
                    d["polygon"] = [
                        [round(float(p[0]), 6), round(float(p[1]), 6)]
                        for p in valid_points
                    ]
    return detections

@router.get("/video/{task_id}")
async def serve_task_video(
    task_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Streams the original video file for a task.
    Supports hierarchical storage at the Category (parent) level.
    """
    # Try finding in the new Flat Category folder first
    video_path = await get_task_local_dir(task_id, find_video=True)
    
    if video_path and os.path.exists(video_path):
        print(f"[VIDEO] Loading DIRECT: {video_path}")
        return range_file_response(video_path, request, media_type="video/mp4")
        
    db = get_db()
    from bson import ObjectId
    query_conditions = [{"_id": task_id}, {"task_id": task_id}]
    if ObjectId.is_valid(task_id):
        query_conditions.append({"_id": ObjectId(task_id)})
    task = await db.tasks.find_one({"$or": query_conditions})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    local_path = task.get("local_path")
    if local_path and os.path.exists(local_path):
        print(f"[VIDEO] serving DIRECT LOCAL: {local_path}")
        # Use range_file_response for native Range request support
        return range_file_response(local_path, request, media_type="video/mp4")
  
    # Fallback: Extraction for Proxy
    print(f"[VIDEO] Local not found for {task_id}. Attempting proxy...")
    filename = task.get('filename', 'video.mp4')
    return await proxy_ai_file(path=f"task_{task_id}/{filename}", request=request)

@router.get("/proxy-file")
async def proxy_ai_file(
    path: str,
    request: Request,
    task_id: str = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Securely proxies assets (Tracking JSON, snapshots) from AI Server.
    v72.6: Auto-extract task_id from path if missing.
    """
    import re
    p_norm = path.replace("\\", "/")
    
    # v72.6: Robust Task ID detection from path
    found_task_id = task_id
    if not found_task_id:
        match = re.search(r'task_([a-zA-Z0-9]+)', p_norm)
        if match:
            found_task_id = f"task_{match.group(1)}"
            
    if "\x00" in p_norm or any(part in {".", ".."} for part in p_norm.split("/")):
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Never serve a caller supplied absolute/local path. Resolve files only
    # through a validated task id and the server-side task storage mapping.
    local_file = None
    if found_task_id:
        filename = p_norm.split("/")[-1]
        
        # 1. Search child folder (Results: JSON, Snapshots)
        local_dir = await get_task_local_dir(found_task_id, find_video=False)
        if local_dir:
            temp_file = os.path.join(local_dir, filename)
            
            # v72.6: Fuzzy search if the specific filename doesn't exist but it's a JSON request
            if not os.path.exists(temp_file) and filename.lower().endswith(".json"):
                from pathlib import Path
                all_jsons = list(Path(local_dir).glob("**/*.json"))
                if all_jsons:
                    temp_file = str(next((p for p in all_jsons if "track" in p.name.lower()), all_jsons[0]))
            
            if os.path.exists(temp_file) and os.path.isfile(temp_file):
                local_file = temp_file
            else:
                # Fallback: check parent directory for original uploaded images
                from pathlib import Path
                parent_dir = str(Path(local_dir).parent) if "snapshot" in local_dir else local_dir
                temp_original = os.path.join(parent_dir, filename)
                if os.path.exists(temp_original) and os.path.isfile(temp_original):
                    local_file = temp_original

    if local_file and os.path.exists(local_file) and os.path.isfile(local_file):
        # v71.6: ON-THE-FLY Normalization for Video Tracking JSON
        filename = os.path.basename(local_file)
        if filename.lower().endswith(".json"):
            try:
                with open(local_file, "r", encoding="utf-8") as fj:
                    data = json.load(fj)
                
                # Check if this is a tracking file with frames
                frames = data.get("frames", {})
                if frames:
                    # We need width/height. Try to get from video or first image
                    video_path = await get_task_local_dir(found_task_id, find_video=True)
                    w, h = 1920, 1080 # Default
                    if video_path and os.path.exists(video_path):
                        try:
                            import cv2
                            cap = cv2.VideoCapture(video_path)
                            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
                            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
                            cap.release()
                        except: pass
                    
                    # Normalize all frames
                    for f_id, f_data in frames.items():
                        if isinstance(f_data, list):
                            frames[f_id] = normalize_detections(f_data, w, h)
                    
                    return JSONResponse(content=data)
            except Exception as e:
                print(f"[JSON PROXY ERROR] {e}")
        
        return FileResponse(local_file)
                
    if found_task_id:
        # 2. Search parent folder (Video/Originals)
        # v71.1 Fix: Video might be named 'video.mp4' in request but 'task_id_video.mp4' on disk
        video_full_path = await get_task_local_dir(found_task_id, find_video=True)
        if video_full_path and os.path.exists(video_full_path) and os.path.isfile(video_full_path):
            filename = p_norm.split("/")[-1]
            # If the requested filename is generic (e.g. video.mp4, source.mp4) 
            # or matches the task ID in ANY way, or is exactly the original file, serve it.
            if any(k in filename.lower() for k in ["video", "mp4", found_task_id]) or filename == os.path.basename(video_full_path):
                import mimetypes
                mtype, _ = mimetypes.guess_type(video_full_path)
                return range_file_response(video_full_path, request, media_type=mtype or "video/mp4")

    # Normalize Path for AI Server URL
    conf = load_config()
    api_url = settings.CRACK_API_URL or conf.get("crack_api_url")
    if "/files/" in p_norm: 
        clean_path = "/files/" + p_norm.split("/files/", 1)[1]
    elif "task_" in p_norm: 
        parts = p_norm.split("/")
        task_part = next((part for part in parts if part.startswith("task_")), "unknown")
        filename_part = parts[-1]
        clean_path = f"/files/{task_part}/{filename_part}"
    else: 
        clean_path = f"/files{p_norm if p_norm.startswith('/') else '/' + p_norm}"
    
    target_url = f"{api_url}{clean_path}"
    print(f"[PROXY] Forwarding to AI Server: {target_url}")
    
    client = _get_client(request)
    headers = _crack_headers()
    # Mirror range/streaming headers if present (for large files)
    for h in ["range", "accept", "if-range", "if-none-match"]:
        if h in request.headers: headers[h] = request.headers[h]

    try:
        req = client.build_request("GET", target_url, headers=headers)
        response = await client.send(req, stream=True, follow_redirects=True)
        if response.status_code >= 400:
            print(f"[PROXY ERROR] AI Server code {response.status_code}")
            await response.aclose()
            return Response(content="File Not Found on AI Server", status_code=response.status_code)

        async def stream_iterator():
            try:
                async for chunk in response.aiter_bytes(chunk_size=1024*1024): yield chunk
            finally: await response.aclose()

        prox_headers = {
            "Content-Type": response.headers.get("Content-Type", "application/octet-stream"), 
            "Accept-Ranges": "bytes"
        }
        for h in ["Content-Length", "Content-Range", "ETag", "Last-Modified"]:
            if h in response.headers: prox_headers[h] = response.headers[h]
        
        return StreamingResponse(stream_iterator(), status_code=response.status_code, headers=prox_headers)
    except Exception as e:
        print(f"[PROXY CRASH] {str(e)}")
        return Response(content=str(e), status_code=500)

@router.get("/history")
async def get_task_history(page: int = 1, limit: int = 100, current_user: dict = Depends(get_current_user)):
    """Full processing history."""
    db = get_db()
    if db is None: raise HTTPException(status_code=503)
    tasks = await get_history(current_user.get("id"), is_admin=current_user.get("role") == "admin")
    return {"tasks": tasks}

@router.delete("/tasks/{task_id}")
async def delete_detection_task(task_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """Soft deletion of tasks (moves to trash)."""
    from app.models.trash import soft_delete
    from bson import ObjectId
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")
        
    query_conditions = [{"_id": task_id}, {"task_id": task_id}]
    if ObjectId.is_valid(task_id):
        query_conditions.append({"_id": ObjectId(task_id)})
    query = {"$or": query_conditions}
    
    task = await db.tasks.find_one(query)
    if not task:
        raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ")
        
    is_admin = current_user.get("role") == "admin"
    if not is_admin and task.get("user_id") and task.get("user_id") != current_user.get("id"):
        raise HTTPException(status_code=403, detail="Bạn không có quyền xóa tác vụ này")
        
    trash_doc = await soft_delete("tasks", task_id, id_field="task_id", deleted_by=current_user.get("email", "user@digitaltwin.vn"))
    if not trash_doc:
        raise HTTPException(status_code=500, detail="Không thể chuyển tác vụ vào thùng rác")
        
    # Gọi API Meshroom để dừng tác vụ dựng 3D nếu đang chạy
    try:
        client = _get_client(request)
        twin_cancel_url = f"{settings.TWIN_API_URL}/api/v1/twin/cancel/{task_id}"
        print(f"[TWIN CANCEL] Calling POST {twin_cancel_url}")
        await client.post(twin_cancel_url, headers=_twin_headers(), timeout=5.0)
    except Exception as cancel_e:
        print(f"[TWIN CANCEL WARNING] Could not cancel twin job: {cancel_e}")
        
    return {"status": "success", "message": "Đã chuyển tác vụ vào thùng rác"}

@router.post("/tasks/{task_id}/retry")
async def retry_detection_task(
    task_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Re-runs a failed or stopped detection task."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")
        
    from bson import ObjectId
    query_conditions = [{"_id": task_id}, {"task_id": task_id}]
    if ObjectId.is_valid(task_id):
        query_conditions.append({"_id": ObjectId(task_id)})
    query = {"$or": query_conditions}
    
    task = await db.tasks.find_one(query)
    if not task:
        raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ")
        
    local_path = task.get("local_path")
    if not local_path or not os.path.exists(local_path):
        raise HTTPException(status_code=400, detail="Không tìm thấy tệp tin nguồn cục bộ. Không thể phân tích lại.")
        
    model_type = task.get("model_type", "road")
    
    try:
        rel_path = os.path.relpath(local_path, UPLOAD_DIR).replace("\\", "/")
        crack_api_filepath = f"{settings.INTERNAL_AI_DATA_ROOT}/{rel_path}".replace('//', '/')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi phân giải đường dẫn tệp: {str(e)}")
        
    # Clean up old snapshot, 3d and workspace directories to avoid duplicate/stale files
    parent_dir = os.path.dirname(local_path)
    snapshot_dir = os.path.join(parent_dir, "snapshot")
    m3d_dir = os.path.join(parent_dir, "3d")
    workspace_dir = os.path.join(os.path.dirname(parent_dir), f"{task_id}_workspace")
    
    for folder in [snapshot_dir, m3d_dir, workspace_dir]:
        if os.path.exists(folder) and os.path.isdir(folder):
            try:
                shutil.rmtree(folder)
                print(f"[RETRY CLEANUP] Deleted folder: {folder}")
            except Exception as e:
                print(f"[RETRY CLEANUP ERROR] Failed to delete {folder}: {e}")
                
    has_3d = task.get("twin_job_id") is not None or os.path.exists(m3d_dir)
        
    # Reset status in DB
    await db.tasks.update_one(
        query,
        {"$set": {
            "status": "queued",
            "processingStatus": "đang xếp hàng",
            "ErrorCode": None,
            "message": None,
            "best_frames": [],
            "trackingDataUrl": None,
            "updated_at": datetime.utcnow()
        }}
    )
    
    # Notify client via Redis pubsub/WebSocket
    await notifier.emit(task_id, "queued", progress=10, message="Đang thử lại tác vụ phân tích...")
    
    # Trigger AI analysis in background
    background_tasks.add_task(
        _trigger_ai_analysis,
        request, task_id, model_type, crack_api_filepath
    )
    
    if has_3d:
        # Reset twin status
        await db.tasks.update_one(
            query,
            {"$set": {"twin_status": "queued"}}
        )
        background_tasks.add_task(
            _trigger_3d_twin,
            request, task_id, model_type, crack_api_filepath
        )
    
    return {"status": "success", "message": "Đã bắt đầu phân tích lại tác vụ"}

@router.get("/alerts")
async def get_recent_alerts(current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None: return {"alerts": []}
    
    # Filter out invalid nameless/frame tasks
    query = {"task_id": {"$ne": None, "$exists": True}}
    tasks = await db.tasks.find(query).sort("created_at", -1).limit(5).to_list(5)
    alerts = []
    for t in tasks:
        # Determine status
        proc_status = t.get("processingStatus") or ""
        error_code = t.get("ErrorCode")
        status_val = t.get("status")
        
        if "lỗi" in proc_status.lower() or error_code in ["PROCESSING_ERROR", "STREAM_ERROR"]:
            status = "error"
        elif "xong" in proc_status.lower() or status_val == "done":
            status = "done"
        elif "xử lý" in proc_status.lower() or "stream" in proc_status.lower() or status_val == "processing":
            status = "processing"
        else:
            status = status_val or "queued"
                
        task_id = t.get("task_id") or t.get("_id") or "unknown"
        
        # Determine filename
        filename = t.get("filename")
        if not filename:
            source_path = t.get("sourceFilePath", "")
            if source_path:
                filename = os.path.basename(source_path)
            else:
                filename = "Tác vụ không tên"
                
        type_val = "info" if status == "done" else ("critical" if status == "error" or status == "lỗi" else "warning")
        
        alerts.append({
            "id": task_id,
            "type": type_val,
            "title": filename,
            "message": f"Tác vụ {task_id} đang ở trạng thái {status}",
            "timestamp": t.get("created_at", datetime.now().isoformat())
        })
    return {"alerts": alerts}

@router.get("/stats")
async def get_dashboard_stats(current_user: dict = Depends(get_current_user)):
    """Aggregated stats for the dashboard info cards."""
    db = get_db()
    if db is None: raise HTTPException(status_code=503)
    
    # Filter out invalid nameless/frame tasks
    valid_query = {"task_id": {"$ne": None, "$exists": True}}
    total = await db.tasks.count_documents(valid_query)
    done_query = {
        **valid_query,
        "$or": [
            {"status": {"$in": ["done", "done (auto-detected)"]}},
            {"processingStatus": "xử lý xong"}
        ]
    }
    done = await db.tasks.count_documents(done_query)
    
    # Calculate total cracks detected from best_frames metadata
    all_done_tasks = await db.tasks.find(done_query).to_list(None)
    cracks_count = 0
    for task in all_done_tasks:
        best_frames = task.get("best_frames", [])
        for frame in best_frames:
            # v72.1: Use detections list length instead of missing num_detections
            detections = frame.get("detections", [])
            cracks_count += len(detections)

    # Real Chat Sessions count from Middleware
    chat_sessions = 0
    try:
        from app.routers.chatbot import get_rag_config
        rag_url, rag_token = get_rag_config()
        if rag_url:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{rag_url}/sessions", headers={"X-API-Token": rag_token})
                if resp.status_code == 200:
                    chat_sessions = len(resp.json().get("sessions", []))
    except Exception: pass
    
    performance = round(done/total*100) if total > 0 else 100
    
    return {
        "totalScans": total, 
        "cracksDetected": cracks_count, 
        "chatSessions": chat_sessions, 
        "performance": performance
    }

@router.get("/activity")
async def get_activity_feed(current_user: dict = Depends(get_current_user)):
    """Generate real chart data for the last 7 days."""
    db = get_db()
    if db is None: return []
    
    from datetime import timedelta
    end_date = datetime.now()
    
    tasks = await db.tasks.find({"status": {"$in": ["done", "done (auto-detected)"]}}).to_list(1000)
    chart_data_map = {}
    for task in tasks:
        created_at_str = str(task.get("created_at", ""))
        # Parse day string e.g. '22' from '2026-07-22'
        if len(created_at_str) >= 10:
            day_key = created_at_str[8:10]
        else:
            day_key = datetime.now().strftime("%d")
            
        num_cracks = 0
        best_frames = task.get("best_frames", [])
        for bf in best_frames:
            num_cracks += len(bf.get("detections", []))
            
        if day_key not in chart_data_map:
            chart_data_map[day_key] = {"scans": 0, "cracks": 0}
            
        chart_data_map[day_key]["scans"] += 1
        chart_data_map[day_key]["cracks"] += num_cracks

    chart_data = []
    for i in range(7):
        d = end_date - timedelta(days=6-i)
        day_str = d.strftime("%d")
        record = chart_data_map.get(day_str, {"scans": 0, "cracks": 0})
        chart_data.append({
            "date": d.strftime("%d/%m"),
            "name": d.strftime("%d/%m"),
            "scans": record["scans"],
            "cracks": record["cracks"]
        })
    
    return chart_data

def interpolate_position(frame_idx: int, total_frames: int, start_pos: dict, end_pos: dict):
    """GPS spline interpolation for frame-exact mapping."""
    if total_frames <= 0: return start_pos
    ratio = frame_idx / total_frames
    if "lat" in start_pos: return {"lat": start_pos["lat"] + (end_pos["lat"] - start_pos["lat"]) * ratio, "lng": start_pos["lng"] + (end_pos["lng"] - start_pos["lng"]) * ratio}
    return start_pos

# ── AI Defect Analysis Proxies ────────────────────────

@router.get("/report/{task_id}")
async def get_analysis_report(task_id: str, current_user: dict = Depends(get_current_user)):
    """Lấy tất cả analysis reports từ local MongoDB (đã chuyển sang chatbot VLM)."""
    db = get_db()
    if db is None:
        return {"status": False, "data": []}
    try:
        cursor = db.defect_reports.find({"task_id": task_id})
        reports = await cursor.to_list(length=10000)
        for r in reports:
            r.pop("_id", None)
        return {"status": True, "data": reports}
    except Exception as e:
        return {"status": False, "error": str(e), "data": []}

@router.post("/analyze/snapshot")
async def analyze_snapshot_proxy(request: Request, payload: dict, current_user: dict = Depends(get_current_user)):
    """Kích hoạt Vision LLM (vLLM Qwen2.5-VL qua Chatbot Middleware) phân tích chi tiết snapshot vết nứt."""
    db = get_db()
    if db is None:
        return {"status": False, "error": "Database offline"}
        
    task_id = payload.get("task_id")
    track_id = payload.get("track_id")
    force_reanalyze = payload.get("force_reanalyze", False)
    
    if not task_id or track_id is None:
        return {"status": False, "error": "Thiếu tham số task_id hoặc track_id"}
        
    cache_key = f"{task_id}_{track_id}"
    
    # 1. Check cache
    if not force_reanalyze:
        cached = await db.defect_reports.find_one({"task_id": task_id, "track_id": track_id})
        if cached:
            cached.pop("_id", None)
            return {"status": True, "source": "cache", "data": cached}
            
    # 2. Lookup detection in tasks collection
    task_doc = await db.tasks.find_one({"task_id": task_id})
    infrastructure = str(task_doc.get("infrastructure_category") or task_doc.get("model_type") or "road").lower() if task_doc else "road"
    is_bridge = infrastructure in {"bridge", "cau", "cáº§u"}
    if not task_doc:
        return {"status": False, "error": f"Không tìm thấy tác vụ {task_id}"}
        
    infrastructure = str(task_doc.get("infrastructure_category") or task_doc.get("model_type") or "road").lower()
    is_bridge = infrastructure in {"bridge", "cau", "cáº§u"}
    best_frames = task_doc.get("best_frames", [])
    target_frame = None
    target_det = None
    
    for frame in best_frames:
        detections = frame.get("detections", [])
        for det in detections:
            if det.get("track_id") == track_id:
                target_frame = frame
                target_det = det
                break
        if target_frame:
            break
            
    if not target_frame or not target_det:
        return {"status": False, "error": f"Không tìm thấy track_id {track_id} trong tác vụ này"}
        
    frame_file_path = target_frame.get("frameFilePath")
    class_name = target_det.get("class", "crack")
    confidence = target_det.get("confidence", 0.0)
    bbox = target_det.get("bbox", [])
    
    # 3. Read image and encode to Base64
    clean_path = frame_file_path.replace("\\", "/")
    if clean_path.startswith("files/"):
        clean_path = clean_path[len("files/"):]
    elif clean_path.startswith("/files/"):
        clean_path = clean_path[len("/files/"):]
    clean_path = clean_path.lstrip("/")
    
    abs_img_path = os.path.join(UPLOAD_DIR, clean_path.replace("/", os.sep))
    if not os.path.exists(abs_img_path):
        return {"status": False, "error": f"Không tìm thấy ảnh tại {abs_img_path}"}
        
    try:
        import base64
        with open(abs_img_path, "rb") as img_f:
            encoded_str = base64.b64encode(img_f.read()).decode('utf-8')
        image_data_uri = f"data:image/jpeg;base64,{encoded_str}"
    except Exception as read_err:
        return {"status": False, "error": f"Lỗi đọc ảnh: {str(read_err)}"}
        
    # 4. Trigger Chatbot Middleware VLM Chat
    try:
        from app.routers.chatbot import get_rag_config
        rag_url, rag_token = get_rag_config()
        
        CLASS_TRANSLATIONS = {
            "alligator_crack": "Nứt lưới / Nứt da cá sấu",
            "longitudinal_crack": "Nứt dọc theo vệt bánh xe",
            "transverse_crack": "Nứt ngang do co ngót nhiệt",
            "pothole": "Ổ gà mặt đường nhựa",
            "rutting": "Hằn lún vệt bánh xe",
            "net_crack": "Nứt lưới đa giác",
            "crack": "Vết nứt bề mặt",
            "nut": "Vết nứt bề mặt",
            "nut_ca_sau": "Nứt da cá sấu / Nứt rạn mai rùa",
            "o_ga/bong_bat": "Ổ gà / Bong bật phối liệu",
            "Crack": "Vết nứt kết cấu bê tông cầu",
            "Efflorescence_Leaching": "Vôi hóa / Rò rỉ chất kết dính bê tông",
            "Exposed Rebar": "Lộ cốt thép chịu lực",
            "Spalling": "Bong tróc / Vỡ ốp bê tông",
            "Staining_Infiltration": "Ố màu / Thấm nước bề mặt",
            "Corrosion": "Rỉ sét cốt thép / dầm thép",
            "Biological_Growth": "Rêu mốc / Sinh trưởng sinh học",
            "Pothole Asphalt": "Ổ gà lớp phủ nhựa trên cầu",
            "Expansion Joint": "Hư hỏng khe co giãn",
            "Guardrail Damaged": "Hư hỏng lan can / rào chắn",
        }
        cls_vn = CLASS_TRANSLATIONS.get(class_name, class_name)
        
        vlm_prompt = (
            f"Bạn là chuyên gia giám định hạ tầng giao thông đường bộ và công trình cầu Việt Nam.\n"
            f"Hãy soi kỹ hình ảnh snapshot khuyết tật '{cls_vn}' tại tọa độ Bounding Box {bbox} (Độ tin cậy AI: {int(confidence*100) if confidence <= 1.0 else int(confidence)}%) và thực hiện:\n"
            f"1. Mô tả chi tiết hình thái tổn hại trực quan quan sát được trên ảnh.\n"
            f"2. Phân tích nguyên nhân gây nên hư hỏng (do tải trọng giao thông mỏi, nước thẩm thấu, co ngót nhiệt hay lão hóa liên kết).\n"
            f"3. Đánh giá mức độ nghiêm trọng và đối chiếu quy chuẩn kỹ thuật TCVN 8866 / TCVN 13567-1:2022 / Thông tư 41/2024/TT-BGTVT tương ứng.\n"
            f"4. Đề xuất phương án kỹ thuật sửa chữa và biện pháp bảo trì khắc phục chi tiết."
        )
        
        object_label = "bridge structural component" if is_bridge else "road pavement"
        standards_label = "TCVN 11823, TCVN 9345, TCVN 9346" if is_bridge else "TCVN 8866, TCVN 8859"
        vlm_prompt = (
            f"Analyze this image as a {object_label} inspection in Vietnam. "
            f"Use only visual evidence and the detections below; do not invent missing documents. "
            f"If uncertain, explicitly recommend field verification. Applicable standards: {standards_label}.\n\n"
            f"Detections:\n{defects_text}\n"
        )
        if is_bridge:
            vlm_prompt = (
                "Analyze this bridge structural component image in Vietnam. Use only visual evidence and supplied detections; do not invent missing documents. "
                "If uncertain, state that field verification is required. Applicable references: TCVN 11823, TCVN 9345, TCVN 9346.\n\n"
                f"Detections:\n{defects_text}\n"
            )
        vlm_payload = {
            "question": vlm_prompt,
            "session_id": f"analysis_{task_id}_track_{track_id}",
            "stream": False,
            "image_url": image_data_uri,
            "detections": [{
                "class": class_name,
                "confidence": confidence,
                "bbox": bbox
            }]
        }
        
        user_email = current_user.get("email", "admin@digitaltwin.vn")
        headers = {"X-API-Token": rag_token, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{rag_url}/api/v1/chat/vlm",
                headers=headers,
                json=vlm_payload,
                params={"user_id": user_email}
            )
            
        if response.status_code != 200:
            return {"status": False, "error": f"Lỗi Chatbot Middleware: {response.status_code} - {response.text}"}
            
        res_json = response.json()
        ai_answer = res_json.get("answer", "Không nhận được phản hồi phân tích từ VLM Chatbot.")
        
        # Determine severity label based on confidence or text
        sev_level = "high" if confidence > 0.75 else ("moderate" if confidence > 0.45 else "low")
        sev_label = "Nghiêm trọng (Cần bảo trì gấp)" if sev_level == "high" else ("Trung bình" if sev_level == "moderate" else "Nhẹ")

        # 5. Map response to DefectReport schema
        defect_report = {
            "task_id": task_id,
            "track_id": track_id,
            "class_name": class_name,
            "defect_code": "TC-ROAD" if "bridge" not in class_name.lower() and class_name not in ["Crack", "Spalling", "Exposed Rebar"] else "TC-BRIDGE",
            "defect_name": cls_vn,
            "confidence": confidence,
            "severity": {
                "level": sev_level,
                "label": sev_label
            },
            "analysis": {
                "description": ai_answer,
                "causes": ["Tải trọng xe lưu thông lặp lại vượt quá giới hạn mỏi.", "Nước mưa đọng thẩm thấu làm rã liên kết phối liệu."],
                "technical_detail": ai_answer,
                "conclusion_and_repair_plan": ai_answer
            },
            "analysis_source": "vision_llm",
            "tcvn_references": ["TCVN 8866:2011", "TCVN 13567-1:2022", "Thông tư 41/2024/TT-BGTVT"],
            "recommendations": ["Cấu cào bóc lớp nhựa hư hỏng", "Trám khe nứt bằng bitum polyme", "Thải nước bề mặt đường"],
            "frame_index": target_frame.get("frame_index", 0),
            "timestamp": target_frame.get("timestamp", "00:00"),
            "frameFilePath": frame_file_path,
            "bbox": bbox,
            "analyzed_at": datetime.utcnow().isoformat()
        }
        
        # Save to DB
        await db.defect_reports.update_one(
            {"task_id": task_id, "track_id": track_id},
            {"$set": defect_report},
            upsert=True
        )
        
        return {"status": True, "data": defect_report}
        
    except Exception as e:
        print(f"[VLM ERROR] {e}")
        return {"status": False, "error": f"Lỗi xử lý VLM: {str(e)}"}

@router.post("/analyze/frame")
async def analyze_frame_proxy(request: Request, payload: dict, current_user: dict = Depends(get_current_user)):
    """Kích hoạt Vision LLM phân tích tổng thể toàn bộ khung hình."""
    db = get_db()
    if db is None:
        return {"status": False, "error": "Database offline"}
        
    task_id = payload.get("task_id")
    frame_index = payload.get("frame_index")
    frame_position = payload.get("frame_position")
    force_reanalyze = payload.get("force_reanalyze", False)
    
    if not task_id or frame_index is None:
        return {"status": False, "error": "Thiếu tham số task_id hoặc frame_index"}
        
    # 1. Lookup task in MongoDB
    task_doc = await db.tasks.find_one({"task_id": task_id})
    if not task_doc:
        return {"status": False, "error": f"Không tìm thấy tác vụ {task_id}"}
        
    infrastructure = str(task_doc.get("infrastructure_category") or task_doc.get("model_type") or "road").lower()
    is_bridge = infrastructure in {"bridge", "cau", "cáº§u"}
    best_frames = task_doc.get("best_frames", [])
    target_frame = None
    target_frame_idx = -1

    # Prefer the stable position supplied by the review UI. Legacy batch
    # records often have frame_index=0 for every child image.
    if isinstance(frame_position, int) and 0 <= frame_position < len(best_frames):
        target_frame = best_frames[frame_position]
        target_frame_idx = frame_position
    
    for idx, f in enumerate(best_frames):
        if target_frame is None and f.get("frame_index") == frame_index:
            target_frame = f
            target_frame_idx = idx
            break
            
    if not target_frame and isinstance(frame_index, int) and 0 <= frame_index < len(best_frames):
        target_frame = best_frames[frame_index]
        target_frame_idx = frame_index
        
    if not target_frame or target_frame_idx == -1:
        return {"status": False, "error": f"Không tìm thấy frame_index {frame_index} trong tác vụ này"}
        
    # 2. Check cache
    if not force_reanalyze and target_frame.get("frame_analysis") and target_frame.get("frame_analysis_version") == 6:
        return {"status": True, "source": "cache", "data": {"analysis": target_frame.get("frame_analysis")}}
        
    frame_file_path = target_frame.get("frameFilePath")
    detections = target_frame.get("detections", [])
    
    # 3. Read image and encode to Base64
    clean_path = frame_file_path.replace("\\", "/")
    if clean_path.startswith("files/"):
        clean_path = clean_path[len("files/"):]
    elif clean_path.startswith("/files/"):
        clean_path = clean_path[len("/files/"):]
    clean_path = clean_path.lstrip("/")
    
    abs_img_path = os.path.join(UPLOAD_DIR, clean_path.replace("/", os.sep))
    if not os.path.exists(abs_img_path):
        return {"status": False, "error": f"Không tìm thấy ảnh tại {abs_img_path}"}
        
    try:
        import base64
        with open(abs_img_path, "rb") as img_f:
            encoded_str = base64.b64encode(img_f.read()).decode('utf-8')
        image_data_uri = f"data:image/jpeg;base64,{encoded_str}"
    except Exception as read_err:
        return {"status": False, "error": f"Lỗi đọc ảnh: {str(read_err)}"}
        
    # 4. Trigger Chatbot Middleware VLM Chat
    try:
        from app.routers.chatbot import get_rag_config
        rag_url, rag_token = get_rag_config()
        
        # Build list of detected defects for prompt context
        CLASS_TRANSLATIONS = {
            "alligator_crack": "Nứt lưới (nứt rạn mai rùa)",
            "longitudinal_crack": "Nứt dọc",
            "transverse_crack": "Nứt ngang",
            "pothole": "Ổ gà",
            "rutting": "Hằn lún vệt bánh xe",
            "net_crack": "Nứt lưới",
            "crack": "Vết nứt",
            "Crack": "Vết nứt kết cấu cầu",
            "Efflorescence_Leaching": "Vôi hóa / Rò rỉ chất kết dính",
            "Exposed Rebar": "Lộ cốt thép",
            "Spalling": "Bong tróc / Vỡ ốp bê tông",
            "Staining_Infiltration": "Ố màu / Thấm nước bề mặt",
            "Corrosion": "Rỉ sét cốt thép / dầm thép",
            "Biological_Growth": "Rêu mốc / Sinh trưởng sinh học",
            "Pothole Asphalt": "Ổ gà lớp phủ nhựa trên cầu",
            "Expansion Joint": "Hư hỏng khe co giãn",
            "Guardrail Damaged": "Hư hỏng lan can / rào chắn",
        }
        
        defect_descriptions = []
        for detection_index, det in enumerate(detections, start=1):
            cls = det.get("class", "crack")
            cls_vn = CLASS_TRANSLATIONS.get(cls, cls)
            conf = _confidence_fraction(det.get("confidence"))
            roi, location, area_percent = _detection_roi_summary(det)
            defect_descriptions.append(
                f"{detection_index}. class={cls}; ten_vi={cls_vn}; confidence={conf:.4f}; "
                f"roi_envelope={json.dumps(roi)}; vi_tri_tuong_doi={location}; dien_tich_khung_bao={area_percent:.2f}%"
            )

        defects_text = "\n".join(defect_descriptions) if defect_descriptions else "Không có phát hiện tự động."
        expected_defect_names = sorted({
            CLASS_TRANSLATIONS.get(det.get("class", "crack"), det.get("class", "crack"))
            for det in detections
        })
        allowed_standards = (
            ["TCVN 11823:2017", "TCVN 9345:2012", "TCVN 9346:2012"]
            if is_bridge
            else ["TCVN 8866:2011", "TCVN 13567-1:2022"]
        )
        infrastructure_label = "công trình cầu" if is_bridge else "mặt đường bộ"
        vlm_prompt = f"""
Bạn là kỹ sư kiểm định thị giác {infrastructure_label}. Hãy lập báo cáo RIÊNG cho đúng ảnh đang được gửi, theo văn phong hồ sơ giám định kỹ thuật; không trả lời như chatbot.

QUY TẮC NHẬN DẠNG CẤU KIỆN:
- Trụ cầu là gối đỡ trung gian giữa các nhịp, thường đứng trong/ven sông và không tiếp giáp nền đường dẫn.
- Mố cầu nằm ở đầu cầu, tiếp giáp nền đắp hoặc đường dẫn và có chức năng đỡ đầu nhịp. Không gọi một cấu kiện giữa dòng nước là mố cầu.
- Nếu ảnh cho thấy nhiều cấu kiện, ghi đủ các cấu kiện nhìn thấy; không chọn một tên theo suy đoán.

YÊU CẦU BẮT BUỘC:
1. Đối chiếu từng ROI với nội dung thật nhìn thấy. ROI nằm trên nước, bầu trời, cây cỏ hoặc nền không liên quan phải đánh dấu "nghi ngờ dương tính giả"; không biến nhãn AI thành sự thật đã xác nhận.
2. Với MỖI loại hư hỏng AI sau, tạo đúng một visual_evidence tổng hợp: {', '.join(expected_defect_names) if expected_defect_names else 'không có'}.
3. overview dài 5-8 câu: mô tả cấu kiện, vật liệu, bố cục ảnh, vị trí tương đối, màu sắc, hình thái bề mặt, hướng lan truyền và mối liên hệ giữa các dấu hiệu. Không dùng câu chung chung.
4. Mỗi visual_evidence phải nêu: vị trí trên cấu kiện, đặc điểm nhìn thấy, phạm vi tương đối, trạng thái đối chiếu AI và ý nghĩa kỹ thuật sàng lọc. Confidence chỉ là độ tin cậy mô hình, KHÔNG phải mức độ hư hỏng.
5. Mỗi tiêu chuẩn trong {', '.join(allowed_standards)} phải có đúng một mục riêng, gồm phạm vi áp dụng, bằng chứng quan sát liên quan, nhận định kỹ thuật 3-5 câu và giới hạn cần đo tại hiện trường. Không trộn tiêu chuẩn vào cùng một mục.
6. Không tuyên bố "vi phạm tiêu chuẩn", không bịa điều khoản, ngưỡng, kích thước, nguyên nhân, khả năng chịu tải hoặc kết quả thí nghiệm mà ảnh không chứng minh được.
7. conclusion phải tách rõ: tổng hợp tình trạng; sàng lọc rủi ro; nội dung bắt buộc xác minh trước khi kết luận.
8. Tạo 3-5 recommendations theo thứ tự ưu tiên. Mỗi mục phải có hành động, mục đích và phương pháp thực hiện cụ thể; chỉ đề xuất sửa chữa sau bước xác minh.
9. Không lời chào, không bảng Markdown, không câu mời cung cấp thêm thông tin, không lặp nguyên xi danh sách ROI/confidence.
10. Trả về DUY NHẤT JSON đúng schema dưới đây; toàn bộ nội dung chuỗi viết bằng tiếng Việt có dấu:
{{
  "observed_object": {{
    "component": "Tên đúng cấu kiện/đối tượng nhìn thấy",
    "material": "Vật liệu nhìn thấy hoặc chưa xác định",
    "visible_context": "Bối cảnh giúp phân biệt cấu kiện và vị trí của nó"
  }},
  "current_condition": {{
    "overview": "Mô tả hiện trạng chi tiết 5-8 câu, tối thiểu 240 ký tự",
    "visual_evidence": [
      {{
        "defect_class": "Tên tiếng Việt đúng nguyên văn trong danh sách loại hư hỏng AI",
        "ai_validation": "phù hợp | nghi ngờ dương tính giả | không đủ bằng chứng",
        "location": "Vị trí cụ thể trên cấu kiện và trong ảnh",
        "visual_characteristics": "Màu sắc, hình thái, kết cấu bề mặt, hướng/biên dấu hiệu nhìn thấy",
        "extent": "Phạm vi tương đối và phân bố; không bịa kích thước thực",
        "engineering_significance": "Ý nghĩa sàng lọc và điều chưa thể kết luận từ ảnh"
      }}
    ]
  }},
  "technical_analysis": [
    {{
      "standard": "Một mã đúng trong danh sách cho phép",
      "applicable_scope": "Phạm vi dùng tiêu chuẩn trong ca quan sát này",
      "observed_evidence": "Bằng chứng thị giác cụ thể liên quan",
      "assessment": "Nhận định kỹ thuật chi tiết 3-5 câu, không bịa điều khoản",
      "limitation": "Thông số/kiểm tra hiện trường còn thiếu để kết luận"
    }}
  ],
  "conclusion": {{
    "condition_summary": "Tổng hợp hiện trạng riêng của ảnh",
    "risk_screening": "Mức ưu tiên sàng lọc và lý do, không kết luận khả năng chịu lực",
    "required_confirmation": "Nội dung phải đo/kiểm tra để xác nhận"
  }},
  "recommendations": [
    {{
      "priority": "Ưu tiên 1 | Ưu tiên 2 | Ưu tiên 3 | Theo dõi",
      "action": "Hành động cụ thể",
      "purpose": "Mục đích kỹ thuật",
      "method": "Cách thực hiện/đo kiểm phù hợp"
    }}
  ]
}}

Các phát hiện AI của đúng ảnh này:
{defects_text}
""".strip()

        vlm_payload = {
            "session_id": f"analysis_{task_id}_frame_{target_frame_idx}_v6",
            "stream": False,
            "direct_vlm": True,
            "question": vlm_prompt,
            "image_url": image_data_uri,
            "detections": detections,
        }
        
        user_email = current_user.get("email", "admin@digitaltwin.vn")
        headers = {"X-API-Token": rag_token, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{rag_url}/api/v1/chat/vlm",
                headers=headers,
                json=vlm_payload,
                params={"user_id": user_email}
            )
            
        if response.status_code != 200:
            return {"status": False, "error": f"Lỗi Chatbot Middleware: {response.status_code} - {response.text}"}
            
        res_json = response.json()
        ai_answer = res_json.get("answer", {})
        structured_answer = _parse_vlm_json_answer(ai_answer)
        if res_json.get("analysis_source") != "direct_model_api":
            return {
                "status": False,
                "error": "Middleware máy C chưa dùng API model VLM trực tiếp; báo cáo không được lưu.",
            }
        if not structured_answer:
            return {
                "status": False,
                "error": "Model VLM không trả đúng JSON có cấu trúc; báo cáo không được lưu.",
            }

        default_object = (
            "Cấu kiện công trình cầu (vật liệu cần xác nhận tại hiện trường)"
            if is_bridge
            else "Mặt đường bộ / Cấu kiện hạ tầng giao thông"
        )
        raw_observed = structured_answer.get("observed_object") or {}
        if not isinstance(raw_observed, dict):
            return {"status": False, "error": "Báo cáo VLM dùng schema đối tượng cũ; kết quả không được lưu."}
        component = _clean_vlm_field(raw_observed.get("component"), 180)
        material = _clean_vlm_field(raw_observed.get("material"), 160)
        visible_context = _clean_vlm_field(raw_observed.get("visible_context"), 700)

        raw_condition = structured_answer.get("current_condition") or {}
        if not isinstance(raw_condition, dict):
            return {"status": False, "error": "Báo cáo VLM thiếu cấu trúc chi tiết hiện trạng; kết quả không được lưu."}
        current_condition = _clean_vlm_field(raw_condition.get("overview"), 2400)
        forbidden_reply_markers = ("xin chào", "trợ lý tcvn", "tôi có thể giúp", "hôm nay?", "vui lòng cung cấp")
        combined_report_text = json.dumps(structured_answer, ensure_ascii=False).casefold()
        if any(marker in combined_report_text for marker in forbidden_reply_markers):
            return {
                "status": False,
                "error": "API trả về lời thoại chatbot thay vì báo cáo giám định; kết quả đã bị từ chối.",
            }
        if not component or len(current_condition) < 240 or len(visible_context) < 30:
            return {
                "status": False,
                "error": "Báo cáo VLM thiếu cấu kiện, bối cảnh hoặc mô tả thị giác chi tiết; kết quả đã bị từ chối.",
            }
        if any(marker in component.casefold() for marker in ("chưa xác định", "không xác định", "không chắc")):
            observed_object = default_object
        else:
            observed_object = f"{component} ({material})" if material else component

        # Correct the common bridge-component contradiction only when the VLM's
        # own context is explicit. This avoids persisting "mố cầu giữa sông".
        context_folded = _fold_semantic_text(f"{component} {visible_context} {current_condition}")
        component_autocorrection = None
        river_markers = ("giua song", "giua dong", "trong song", "nuoc xung quanh")
        abutment_markers = ("dau cau", "nen dap", "duong dan", "tiep giap bo")
        if is_bridge and "mo cau" in _fold_semantic_text(component) and any(marker in context_folded for marker in river_markers):
            component_autocorrection = {"from": component, "to": "Trụ cầu", "reason": "bối cảnh giữa dòng nước"}
            component = "Trụ cầu"
            observed_object = f"{component} ({material})" if material else component
        elif is_bridge and "tru cau" in _fold_semantic_text(component) and any(marker in context_folded for marker in abutment_markers):
            component_autocorrection = {"from": component, "to": "Mố cầu", "reason": "bối cảnh đầu cầu tiếp giáp đường dẫn/nền đắp"}
            component = "Mố cầu"
            observed_object = f"{component} ({material})" if material else component

        visual_evidence = []
        raw_evidence = raw_condition.get("visual_evidence") or []
        if isinstance(raw_evidence, list):
            for evidence in raw_evidence[:12]:
                if not isinstance(evidence, dict):
                    continue
                item = {
                    "defect_class": _clean_vlm_field(evidence.get("defect_class"), 180),
                    "ai_validation": _clean_vlm_field(evidence.get("ai_validation"), 80),
                    "location": _clean_vlm_field(evidence.get("location"), 500),
                    "visual_characteristics": _clean_vlm_field(evidence.get("visual_characteristics"), 900),
                    "extent": _clean_vlm_field(evidence.get("extent"), 500),
                    "engineering_significance": _clean_vlm_field(evidence.get("engineering_significance"), 900),
                }
                detail_length = sum(len(item[key]) for key in ("location", "visual_characteristics", "extent", "engineering_significance"))
                if item["defect_class"] and detail_length >= 150:
                    visual_evidence.append(item)

        def _has_matching_evidence(expected_name: str) -> bool:
            expected_folded = _fold_semantic_text(expected_name)
            expected_tokens = {token for token in expected_folded.split() if len(token) >= 4}
            for evidence in visual_evidence:
                actual_folded = _fold_semantic_text(evidence.get("defect_class"))
                if expected_folded in actual_folded or actual_folded in expected_folded:
                    return True
                actual_tokens = {token for token in actual_folded.split() if len(token) >= 4}
                if len(expected_tokens & actual_tokens) >= min(2, max(1, len(expected_tokens))):
                    return True
            return False

        missing_evidence = [name for name in expected_defect_names if not _has_matching_evidence(name)]
        if not visual_evidence or missing_evidence:
            suffix = f": {', '.join(missing_evidence)}" if missing_evidence else ""
            return {
                "status": False,
                "error": f"Báo cáo VLM thiếu bằng chứng thị giác chi tiết theo loại hư hỏng{suffix}; kết quả không được lưu.",
            }

        technical_findings = []
        raw_findings = structured_answer.get("technical_analysis") or []
        if isinstance(raw_findings, dict):
            raw_findings = raw_findings.get("findings") or raw_findings.get("items") or []
        if isinstance(raw_findings, list):
            for finding in raw_findings[:6]:
                if not isinstance(finding, dict):
                    continue
                raw_standard = _clean_vlm_field(finding.get("standard"), 120)
                canonical_standard = next(
                    (standard for standard in allowed_standards if standard.split(":", 1)[0] in raw_standard),
                    None,
                )
                applicable_scope = _clean_vlm_field(finding.get("applicable_scope"), 700)
                observed_evidence = _clean_vlm_field(finding.get("observed_evidence"), 900)
                assessment = _clean_vlm_field(finding.get("assessment"), 1400)
                limitation = _clean_vlm_field(finding.get("limitation"), 800)
                mixed_standard = any(
                    other.split(":", 1)[0] in f"{applicable_scope} {observed_evidence} {assessment} {limitation}"
                    for other in allowed_standards
                    if other != canonical_standard
                )
                finding_length = sum(len(value) for value in (applicable_scope, observed_evidence, assessment, limitation))
                forbidden_claim = "vi phạm tiêu chuẩn" in assessment.casefold()
                if canonical_standard and len(assessment) >= 90 and finding_length >= 220 and not mixed_standard and not forbidden_claim:
                    technical_findings.append({
                        "standard": canonical_standard,
                        "applicable_scope": applicable_scope,
                        "observed_evidence": observed_evidence,
                        "assessment": assessment,
                        "limitation": limitation,
                    })

        findings_by_standard = {item["standard"]: item for item in technical_findings}
        missing_standards = [standard for standard in allowed_standards if standard not in findings_by_standard]
        if missing_standards:
            return {
                "status": False,
                "error": f"Báo cáo VLM thiếu phân tích độc lập cho: {', '.join(missing_standards)}; kết quả không được lưu.",
            }
        technical_findings = [findings_by_standard[standard] for standard in allowed_standards]

        raw_conclusion = structured_answer.get("conclusion") or {}
        if not isinstance(raw_conclusion, dict):
            return {"status": False, "error": "Báo cáo VLM dùng cấu trúc kết luận cũ; kết quả không được lưu."}
        conclusion_details = {
            "condition_summary": _clean_vlm_field(raw_conclusion.get("condition_summary"), 900),
            "risk_screening": _clean_vlm_field(raw_conclusion.get("risk_screening"), 900),
            "required_confirmation": _clean_vlm_field(raw_conclusion.get("required_confirmation"), 900),
        }
        conclusion = " ".join(value for value in conclusion_details.values() if value)
        raw_recommendations = structured_answer.get("recommendations") or []
        detailed_recommendations = []
        if isinstance(raw_recommendations, list):
            for recommendation in raw_recommendations[:6]:
                if not isinstance(recommendation, dict):
                    continue
                item = {
                    "priority": _clean_vlm_field(recommendation.get("priority"), 80),
                    "action": _clean_vlm_field(recommendation.get("action"), 700),
                    "purpose": _clean_vlm_field(recommendation.get("purpose"), 700),
                    "method": _clean_vlm_field(recommendation.get("method"), 900),
                }
                if item["priority"] and sum(len(item[key]) for key in ("action", "purpose", "method")) >= 120:
                    detailed_recommendations.append(item)
        recommendations = [
            f"[{item['priority']}] {item['action']} Mục đích: {item['purpose']} Phương pháp: {item['method']}"
            for item in detailed_recommendations
        ]
        if len(conclusion) < 240 or len(detailed_recommendations) < 3:
            return {
                "status": False,
                "error": "Báo cáo VLM thiếu kết luận ba phần hoặc kiến nghị chi tiết theo ưu tiên; kết quả không được lưu.",
            }

        holistic_analysis = {
            "report_version": 6,
            "observed_object": observed_object,
            "observed_context": visible_context,
            "defect_code_mapping": "TCVN 11823 / TCVN 9345 / TCVN 9346" if is_bridge else "TCVN 8866 / TCVN 13567-1",
            "current_status_details": current_condition,
            "visual_evidence": visual_evidence,
            "technical_findings": technical_findings,
            "conclusion_and_repair_plan": conclusion,
            "conclusion_details": conclusion_details,
            "recommendations_to_contractor": recommendations,
            "recommendations_detailed": detailed_recommendations,
            "technical_analysis": {"tcvn_references": allowed_standards},
            "analysis_trace": {
                "task_id": task_id,
                "frame_position": target_frame_idx,
                "frame_index": target_frame.get("frame_index"),
                "detection_count": len(detections),
                "source": res_json.get("analysis_source") or "vlm_structured_json",
                "json_contract_valid": bool(structured_answer),
                "component_autocorrection": component_autocorrection,
            },
        }

        if is_bridge:
            bridge_catalog = {
                "Crack": ("BR-CRK-001", "Vết nứt kết cấu cầu"),
                "Efflorescence_Leaching": ("BR-EFF-001", "Vôi hóa / Rò rỉ chất kết dính"),
                "Exposed Rebar": ("BR-RBR-001", "Lộ cốt thép"),
                "Spalling": ("BR-SPL-001", "Bong tróc / Vỡ ốp bê tông"),
                "Staining_Infiltration": ("BR-STN-001", "Ố màu / Thấm nước bề mặt"),
                "Corrosion": ("BR-CRS-001", "Rỉ sét cốt thép / dầm thép"),
                "Expansion Joint": ("BR-EXP-001", "Hư hỏng khe co giãn"),
                "Guardrail Damaged": ("BR-GRD-001", "Hư hỏng lan can / rào chắn"),
            }
            grouped_catalog = {}
            for detection_index, det in enumerate(detections):
                name = det.get("class")
                if name not in bridge_catalog:
                    continue
                code, label = bridge_catalog[name]
                group = grouped_catalog.setdefault(code, {
                    "code": code,
                    "name": label,
                    "count": 0,
                    "min_confidence": 1.0,
                    "max_confidence": 0.0,
                    "track_ids": [],
                })
                confidence = _confidence_fraction(det.get("confidence"))
                group["count"] += 1
                group["min_confidence"] = min(group["min_confidence"], confidence)
                group["max_confidence"] = max(group["max_confidence"], confidence)
                group["track_ids"].append(det.get("track_id", detection_index))

            catalog_items = []
            for group in grouped_catalog.values():
                group["min_confidence"] = round(group["min_confidence"], 4)
                group["max_confidence"] = round(group["max_confidence"], 4)
                # Backward compatibility for older frontend builds.
                group["confidence"] = group["max_confidence"]
                catalog_items.append(group)
            holistic_analysis["defect_catalog"] = catalog_items

        # Save to DB inside best_frames list
        await db.tasks.update_one(
            {"task_id": task_id},
            {"$set": {
                f"best_frames.{target_frame_idx}.frame_analysis": holistic_analysis,
                f"best_frames.{target_frame_idx}.frame_analysis_version": 6,
            }}
        )
        
        return {"status": True, "data": {"analysis": holistic_analysis}}
        
    except Exception as e:
        print(f"[VLM FRAME ERROR] {e}")
        return {"status": False, "error": f"Lỗi xử lý VLM: {str(e)}"}

@router.post("/calibrate")
async def calibrate_gsd(payload: dict, current_user: dict = Depends(get_current_user)):
    """Local GSD Calibration: Tính toán kích thước thực tế dựa trên manual_gsd."""
    detections = payload.get("detections", [])
    image_width = payload.get("image_width", 1920)
    image_height = payload.get("image_height", 1080)
    manual_gsd = payload.get("manual_gsd", 0.0) or 0.0
    
    is_calibrated = manual_gsd > 0.0
    calibrated_damages = []
    
    for det in detections:
        bbox = det.get("bbox", [])
        if bbox and len(bbox) == 4 and is_calibrated:
            # Tính toán kích thước pixel
            w_px = (bbox[2] - bbox[0]) * image_width
            h_px = (bbox[3] - bbox[1]) * image_height
            
            # Tính toán kích thước thực tế
            real_w = w_px * manual_gsd
            real_area = (w_px * h_px * (manual_gsd ** 2)) / 1000000.0
            
            det["real_width_mm"] = round(real_w, 2)
            det["real_area_m2"] = round(real_area, 4)
            det["pixel_width"] = round(w_px, 1)
            det["pixel_area"] = round(w_px * h_px, 1)
        else:
            det["real_width_mm"] = None
            det["real_area_m2"] = None
        calibrated_damages.append(det)
        
    return {
        "is_calibrated": is_calibrated,
        "gsd_mm_per_pixel": manual_gsd,
        "calibration_source": "manual",
        "calibration_source_name": "Nhập thủ công hệ số GSD",
        "calibration_confidence": 1.0 if is_calibrated else 0.0,
        "damages": calibrated_damages,
        "references_found": []
    }

@router.get("/calibration/standards")
async def get_calibration_standards(current_user: dict = Depends(get_current_user)):
    """Trả về danh mục tiêu chuẩn kích thước mẫu (Hiện tại dùng chế độ nhập tay)."""
    return []

@router.get("/twin/files/{job_id}/{subdir:path}")
async def proxy_twin_file(
    job_id: str,
    subdir: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Proxy GLB/OBJ files from Meshroom Master API (port 8090)."""
    twin_api_url = settings.TWIN_API_URL
    
    clean_path = f"/files/{job_id}/{subdir}"
    target_url = f"{twin_api_url.rstrip('/')}{clean_path}"
    print(f"[TWIN PROXY] Forwarding to Meshroom: {target_url}")
    
    client = _get_client(request)
    headers = _twin_headers()
    for h in ["range", "accept", "if-range", "if-none-match"]:
        if h in request.headers:
            headers[h] = request.headers[h]
            
    try:
        req = client.build_request("GET", target_url, headers=headers)
        response = await client.send(req, stream=True, follow_redirects=True)
        if response.status_code >= 400:
            print(f"[TWIN PROXY ERROR] Meshroom code {response.status_code}")
            await response.aclose()
            return Response(content="File Not Found on Meshroom", status_code=response.status_code)
            
        async def stream_iterator():
            try:
                async for chunk in response.aiter_bytes(chunk_size=1024*1024):
                    yield chunk
            finally:
                await response.aclose()
                
        prox_headers = {
            "Content-Type": response.headers.get("Content-Type", "application/octet-stream"),
            "Accept-Ranges": "bytes"
        }
        for h in ["Content-Length", "Content-Range", "ETag", "Last-Modified"]:
            if h in response.headers:
                prox_headers[h] = response.headers[h]
                
        return StreamingResponse(stream_iterator(), status_code=response.status_code, headers=prox_headers)
    except Exception as e:
        print(f"[TWIN PROXY CRASH] {str(e)}")
        return Response(content=str(e), status_code=500)

@router.websocket("/ws/stream")
async def websocket_crack_stream(websocket: WebSocket, stream_url: str = "0", model_type: str = "road"):
    """WebSocket tunnel for real-time live detection streaming."""
    user = await _authenticate_websocket(websocket)
    if not user:
        return
    await websocket.accept()
    try:
        import websockets as ws_lib
        api_url = (load_config().get("crack_api_url") or settings.CRACK_API_URL).replace('http', 'ws')
        async with ws_lib.connect(f"{api_url}/api/v1/ws/stream?file_path={stream_url}&model_type={model_type}", extra_headers=_crack_headers()) as ai_ws:
            async def fw_ui():
                async for msg in ai_ws: await websocket.send_text(msg)
            async def fw_ai():
                while True: await ai_ws.send(await websocket.receive_text())
            await asyncio.gather(fw_ui(), fw_ai())
    except Exception: pass
