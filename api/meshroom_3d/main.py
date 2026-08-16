import os
from typing import Optional
import shutil
import json
import asyncio
import uuid
import shlex
import aiohttp
import subprocess
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import numpy as np

# ──────────────────────────────────────────────────────────
# [FIX #11] Logging chuẩn – có timestamp, phân cấp, gắn job_id
# ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("digital_twin")

# ──────────────────────────────────────────────────────────
# Khởi tạo FastAPI
# ──────────────────────────────────────────────────────────
app = FastAPI(title="Smart Infrastructure Digital Twin - Master API")

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]
API_TOKEN = os.getenv("API_TOKEN") or os.getenv("YOLO_API_TOKEN", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials="*" not in ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/ui", StaticFiles(directory="static", html=True), name="static")


@app.middleware("http")
async def check_access(request: Request, call_next):
    if request.url.path in ["/docs", "/openapi.json", "/redoc", "/api/v1/health"]:
        return await call_next(request)
    if not API_TOKEN:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "API_TOKEN is not configured"},
        )
    auth_header = request.headers.get("Authorization", "")
    supplied = request.headers.get("X-API-Token", "")
    if auth_header.startswith("Bearer "):
        supplied = auth_header.removeprefix("Bearer ").strip()
    if supplied != API_TOKEN:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Unauthorized"},
        )
    return await call_next(request)


# ──────────────────────────────────────────────────────────
# Cấu hình đường dẫn (100% từ biến môi trường)
# ──────────────────────────────────────────────────────────
INTERNAL_SOURCES_DIR = os.getenv("INTERNAL_SOURCES_DIR", "/data/file/sources")
HOST_SOURCES_DIR = os.getenv("HOST_SOURCES_DIR", "/data/file/sources")
YOLO_API_URL = os.getenv("YOLO_API_URL", "http://localhost:8000/api/v1/detect/image")
YOLO_API_TOKEN = os.getenv("YOLO_API_TOKEN", "")
FRAME_EXTRACT_FPS = int(os.getenv("FRAME_EXTRACT_FPS", "1"))
RESOLUTION_LEVEL = int(os.getenv("RESOLUTION_LEVEL", "2"))
DOCKER_GPU_FLAG = os.getenv("DOCKER_GPU_FLAG", "all")

# [FIX #7] Chiều dài tối đa ảnh resize trước COLMAP (giảm tải VRAM)
MAX_IMAGE_DIM = int(os.getenv("MAX_IMAGE_DIM", "2048"))

# [FIX #18] Số frame tối thiểu để COLMAP dựng được mô hình 3D
MIN_FRAMES = int(os.getenv("MIN_FRAMES", "30"))

# [FIX #8] Ngưỡng % ảnh YOLO phải xử lý thành công (không bị Exception/timeout)
YOLO_SUCCESS_THRESHOLD = float(os.getenv("YOLO_SUCCESS_THRESHOLD", "0.7"))  # 70%

os.makedirs(INTERNAL_SOURCES_DIR, exist_ok=True)

# ──────────────────────────────────────────────────────────
# [FIX #19] Cấu trúc lưu trữ theo ngày YYYY/MM/DD/job_xxx
# Đồng bộ với YOLO API snapshot storage pattern
# ──────────────────────────────────────────────────────────
from starlette.responses import FileResponse, JSONResponse
from starlette.requests import Request

def _make_dated_job_folder(job_id: str) -> str:
    """Tạo đường dẫn job folder theo cấu trúc YYYY/MM/DD/job_xxx."""
    now = datetime.now()
    date_path = os.path.join(
        INTERNAL_SOURCES_DIR,
        str(now.year),
        f"{now.month:02d}",
        f"{now.day:02d}",
        job_id
    )
    os.makedirs(date_path, exist_ok=True)
    return date_path

def _resolve_job_folder(job_id: str) -> str:
    """
    Tìm thư mục thực tế của job_id.
    Hỗ trợ 3 cấu trúc:
      - Mới (đồng bộ ID): sources/YYYY/MM/DD/category/task_xxx/3d
      - Cũ dated: sources/YYYY/MM/DD/job_xxx
      - Cũ flat: sources/job_xxx
    Ưu tiên tìm trong job_store trước, sau đó quét thư mục.
    """
    # 1. Kiểm tra job_store (nhanh nhất)
    if job_id in job_store and "job_folder" in job_store[job_id]:
        folder = job_store[job_id]["job_folder"]
        if os.path.isdir(folder):
            return folder

    # 2. Kiểm tra cấu trúc flat cũ (backward compatible)
    flat_path = os.path.join(INTERNAL_SOURCES_DIR, job_id)
    if os.path.isdir(flat_path):
        return flat_path

    # 3. Quét cấu trúc dated + task_ (đồng bộ ID mới)
    # Tìm: sources/YYYY/MM/DD/*/job_id/3d hoặc sources/YYYY/MM/DD/job_id
    for year_dir in sorted(os.listdir(INTERNAL_SOURCES_DIR), reverse=True):
        year_path = os.path.join(INTERNAL_SOURCES_DIR, year_dir)
        if not os.path.isdir(year_path) or not year_dir.isdigit():
            continue
        for month_dir in sorted(os.listdir(year_path), reverse=True):
            month_path = os.path.join(year_path, month_dir)
            if not os.path.isdir(month_path):
                continue
            for day_dir in sorted(os.listdir(month_path), reverse=True):
                day_path = os.path.join(month_path, day_dir)
                if not os.path.isdir(day_path):
                    continue
                # 3a. Kiểm tra trực tiếp: sources/YYYY/MM/DD/job_xxx
                candidate = os.path.join(day_path, job_id)
                if os.path.isdir(candidate):
                    return candidate
                # 3b. Kiểm tra đồng bộ ID: sources/YYYY/MM/DD/category/task_xxx/3d
                if job_id.startswith("task_"):
                    for cat_dir in os.listdir(day_path):
                        cat_path = os.path.join(day_path, cat_dir)
                        if not os.path.isdir(cat_path):
                            continue
                        task_3d = os.path.join(cat_path, job_id, "3d")
                        if os.path.isdir(task_3d):
                            return task_3d

    # 4. Fallback: trả về flat path (có thể chưa tồn tại)
    return flat_path

# ──────────────────────────────────────────────────────────
# [FIX #4] Chỉ phục vụ file output – KHÔNG lộ frames/workspace/input
# ──────────────────────────────────────────────────────────
ALLOWED_OUTPUT_DIRS = {"3d_output", "ai_results"}

@app.api_route("/files/{job_id}/{subdir:path}", methods=["GET", "HEAD"])
async def serve_output_file(job_id: str, subdir: str):
    """Chỉ phục vụ file trong 3d_output và ai_results. Chặn truy cập frames/input/workspace."""
    top_dir = subdir.split("/")[0] if "/" in subdir else subdir
    
    if top_dir not in ALLOWED_OUTPUT_DIRS:
        raise HTTPException(status_code=403, detail="Access denied. Only output files are accessible.")
    
    # [FIX #19] Resolve job folder (hỗ trợ cả flat lẫn dated)
    job_folder = _resolve_job_folder(job_id)
    file_path = os.path.join(job_folder, subdir)
    
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found.")
    
    return FileResponse(file_path)

# ──────────────────────────────────────────────────────────
# [FIX #3] Semaphore giới hạn 1 job 3D đồng thời – bảo vệ GPU/VRAM
# ──────────────────────────────────────────────────────────
gpu_semaphore = asyncio.Semaphore(1)

# ──────────────────────────────────────────────────────────
# [FIX #9] Thread pool riêng cho tác vụ nặng – không cạn kiệt pool mặc định
# ──────────────────────────────────────────────────────────
heavy_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="heavy_task")

# ──────────────────────────────────────────────────────────
# [FIX #13] Bộ nhớ trạng thái Job
job_store: dict = {}

# ══════════════════════════════════════════════════════════
# BƯỚC 1: Cắt khung hình từ video
# [FIX #15] FFmpeg + [FIX #7] Resize ảnh tự động
# ══════════════════════════════════════════════════════════
def extract_frames(video_path: str, output_folder: str, fps: int = 1) -> int:
    """Bóc tách khung hình từ video bằng FFmpeg, đồng thời resize về kích thước tối ưu."""
    os.makedirs(output_folder, exist_ok=True)

    # [FIX #7] Kết hợp fps + resize trong 1 lệnh FFmpeg duy nhất
    # scale='min(MAX_DIM,iw)':-2 giữ tỉ lệ, chỉ thu nhỏ nếu ảnh lớn hơn MAX_DIM
    vf_filter = f"fps={fps},scale='min({MAX_IMAGE_DIM},iw)':-2"

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf_filter,
        "-q:v", "2",
        os.path.join(output_folder, "frame_%04d.jpg"),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr.decode() if e.stderr else e}")
        return 0

    saved_count = len([f for f in os.listdir(output_folder) if f.endswith(".jpg")])
    logger.info(f"📷 Cắt xong {saved_count} frames (FPS={fps}, max={MAX_IMAGE_DIM}px) từ video.")
    return saved_count


# ══════════════════════════════════════════════════════════
# BƯỚC 2a: Gọi YOLO AI nhận diện vết nứt
# [FIX #8] Retry 3 lần + exponential backoff
# ══════════════════════════════════════════════════════════
MAX_YOLO_RETRIES = 3

async def call_yolo_for_image(session, semaphore, image_path, req_id, model_type):
    """Gọi YOLO API cho từng ảnh, có retry 3 lần với exponential backoff."""
    # [FIX #14] Dùng os.path.relpath – an toàn mọi trường hợp
    rel_path = os.path.relpath(image_path, INTERNAL_SOURCES_DIR)
    docker_image_path = f"/data/file/sources/{rel_path.replace(os.sep, '/')}"

    payload = {
        "FilePath": docker_image_path,
        "RequestId": f"{req_id}_{os.path.basename(image_path)}",
        "ModelType": model_type,
    }

    headers = {}
    if YOLO_API_TOKEN:
        headers["Authorization"] = f"Bearer {YOLO_API_TOKEN}"

    async with semaphore:
        for attempt in range(1, MAX_YOLO_RETRIES + 1):
            try:
                async with session.post(YOLO_API_URL, json=payload, headers=headers, timeout=120) as response:
                    if response.status != 200:
                        logger.warning(f"YOLO API Error {response.status} (attempt {attempt}): {await response.text()}")
                        if attempt < MAX_YOLO_RETRIES:
                            await asyncio.sleep(2 ** attempt)  # Backoff: 2s, 4s, 8s
                            continue
                        return {"image": os.path.basename(image_path), "detections": [], "_success": False}

                    result = await response.json()
                    core_data = result["data"] if "data" in result else result

                    if "datas" in core_data and len(core_data["datas"]) > 0:
                        return {
                            "image": os.path.basename(image_path),
                            "detections": core_data["datas"][0]["images"][0]["detections"],
                            "_success": True,
                        }
                    # API trả về thành công nhưng không có detection → vẫn tính là thành công
                    return {"image": os.path.basename(image_path), "detections": [], "_success": True}

            except Exception as e:
                logger.warning(f"YOLO Exception (attempt {attempt}) for {os.path.basename(image_path)}: {e}")
                if attempt < MAX_YOLO_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue

        return {"image": os.path.basename(image_path), "detections": [], "_success": False}


async def run_ai_branch(frames_folder: str, job_id: str, model_type: str, result_json_path: str) -> bool:
    """Quét toàn bộ ảnh song song qua YOLO. Trả về True nếu đạt ngưỡng thành công."""
    images = sorted(
        [os.path.join(frames_folder, f) for f in os.listdir(frames_folder) if f.lower().endswith((".jpg", ".png"))]
    )
    semaphore = asyncio.Semaphore(1)
    total = len(images)
    completed = 0

    async def call_and_count(session, img):
        nonlocal completed
        res = await call_yolo_for_image(session, semaphore, img, job_id, model_type)
        completed += 1
        if completed % 50 == 0 or completed == total:
            logger.info(f"[{job_id}] AI Progress: {completed}/{total} images ({completed/total*100:.1f}%)")
        return res

    async with aiohttp.ClientSession() as session:
        tasks = [call_and_count(session, img) for img in images]
        all_detections = await asyncio.gather(*tasks)

    # [FIX #8] Kiểm tra ngưỡng % ảnh YOLO xử lý thành công
    success_count = sum(1 for d in all_detections if d.get("_success", False))
    success_rate = success_count / total if total > 0 else 0
    logger.info(f"[{job_id}] YOLO Success Rate: {success_count}/{total} ({success_rate*100:.1f}%)")

    # Loại bỏ trường _success trước khi lưu JSON
    clean_detections = [{"image": d["image"], "detections": d["detections"]} for d in all_detections]

    with open(result_json_path, "w") as f:
        json.dump(clean_detections, f, indent=4)
    logger.info(f"✅ [AI] Nhận diện xong {total} ảnh → {result_json_path}")

    if success_rate < YOLO_SUCCESS_THRESHOLD:
        logger.warning(
            f"⚠️ [{job_id}] YOLO success rate ({success_rate*100:.1f}%) thấp hơn ngưỡng "
            f"({YOLO_SUCCESS_THRESHOLD*100:.0f}%). Pipeline sẽ dừng để tránh lãng phí tài nguyên."
        )
        return False
    return True


# ══════════════════════════════════════════════════════════
# BƯỚC 2b: VẼ VẾT NỨT (TEXTURE BAKING PREPARATION)
# ══════════════════════════════════════════════════════════
def paint_yolo_on_frames(frames_folder: str, painted_folder: str, json_path: str):
    """Vẽ bounding box YOLO hoặc polygon màu đỏ lên ảnh để dán Texture 3D."""
    import cv2
    import numpy as np
    os.makedirs(painted_folder, exist_ok=True)

    if not os.path.exists(json_path):
        logger.warning(f"Không tìm thấy file JSON YOLO tại {json_path}")
        return

    with open(json_path, 'r') as f:
        yolo_data = json.load(f)

    detections_dict = {item['image']: item['detections'] for item in yolo_data}

    img_files = sorted([f for f in os.listdir(frames_folder) if f.lower().endswith((".jpg", ".png"))])
    total = len(img_files)

    for idx, img_name in enumerate(img_files):
        img_path = os.path.join(frames_folder, img_name)
        out_path = os.path.join(painted_folder, img_name)

        img = cv2.imread(img_path)
        if img is None:
            continue

        detections = detections_dict.get(img_name, [])
        if detections:
            overlay = img.copy()
            for det in detections:
                polygon = det.get('polygon')
                bbox = det.get('bbox')
                
                if polygon and len(polygon) >= 3:
                    pts = np.array(polygon, dtype=np.int32)
                    cv2.fillPoly(overlay, [pts], (0, 0, 255))
                elif bbox and len(bbox) == 4:
                    x1, y1, x2, y2 = map(int, bbox)
                    cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 0, 255), -1)

            img = cv2.addWeighted(overlay, 0.4, img, 0.6, 0)

            for det in detections:
                polygon = det.get('polygon')
                bbox = det.get('bbox')
                
                if polygon and len(polygon) >= 3:
                    pts = np.array(polygon, dtype=np.int32)
                    cv2.polylines(img, [pts], isClosed=True, color=(0, 0, 255), thickness=2)
                elif bbox and len(bbox) == 4:
                    x1, y1, x2, y2 = map(int, bbox)
                    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)

        cv2.imwrite(out_path, img)

        if (idx + 1) % 200 == 0 or (idx + 1) == total:
            logger.info(f"🎨 [Baking] Progress: {idx+1}/{total} ({(idx+1)/total*100:.1f}%)")

    logger.info(f"🎨 [Baking] Hoàn tất → {painted_folder}")


# ══════════════════════════════════════════════════════════
# BƯỚC 3: COLMAP + OpenMVS (Docker-out-of-Docker)
# [FIX #5] shlex.quote() chống command injection
# ══════════════════════════════════════════════════════════
def embed_texture_in_glb(glb_path: str, tex_path: str) -> bool:
    """
    Tự động nhúng tệp ảnh Texture (JPEG/PNG) vào tệp GLB nhị phân.
    Khắc phục lỗi trimesh export GLB bị thiếu/lỗi texture khiến mô hình bị trắng xóa.
    """
    import struct
    import json
    from PIL import Image
    from io import BytesIO
    
    if not os.path.exists(glb_path) or not os.path.exists(tex_path):
        logger.warning(f"[GLB Texture Binder] File missing: glb={glb_path}, tex={tex_path}")
        return False
        
    try:
        logger.info(f"✨ [GLB Texture Binder] Đang nhúng texture {os.path.basename(tex_path)} vào {os.path.basename(glb_path)}...")
        
        # 1. Đọc và tối ưu hóa ảnh texture thành JPEG bytes
        img = Image.open(tex_path)
        if img.width > 4096 or img.height > 4096:
            img.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
        
        img_buffer = BytesIO()
        img = img.convert("RGB")
        img.save(img_buffer, format="JPEG", quality=85)
        img_bytes = img_buffer.getvalue()
        
        # 2. Đọc file GLB
        with open(glb_path, "rb") as f:
            glb_data = f.read()
            
        # Kiểm tra magic number
        magic, version, total_length = struct.unpack_from("<III", glb_data, 0)
        if magic != 0x46546C67:
            logger.error("[GLB Texture Binder] Invalid GLB magic number.")
            return False
            
        json_chunk_length = struct.unpack_from("<I", glb_data, 12)[0]
        json_str = glb_data[20:20+json_chunk_length].decode("utf-8")
        gltf = json.loads(json_str)
        
        # Parse BIN chunk
        bin_offset = 20 + json_chunk_length
        bin_chunk_length = struct.unpack_from("<I", glb_data, bin_offset)[0]
        bin_data = bytearray(glb_data[bin_offset + 8:bin_offset + 8 + bin_chunk_length])
        
        if "images" not in gltf or len(gltf["images"]) == 0:
            logger.warning("[GLB Texture Binder] No images metadata found in GLB JSON.")
            return False
            
        img_info = gltf["images"][0]
        if "bufferView" not in img_info:
            logger.warning("[GLB Texture Binder] First image metadata does not have a bufferView.")
            return False
            
        img_bv_index = img_info["bufferView"]
        img_bv = gltf["bufferViews"][img_bv_index]
        old_img_offset = img_bv["byteOffset"]
        old_img_length = img_bv["byteLength"]
        
        # 3. Tạo BIN chunk mới - Thay thế fake image bằng JPEG bytes thật
        new_bin = bytearray()
        new_bin += bin_data[:old_img_offset]
        new_bin += img_bytes
        
        # Padding cho bội số 4
        while len(new_bin) % 4 != 0:
            new_bin += b'\x00'
            
        new_img_length = len(img_bytes)
        after_img_offset = old_img_offset + old_img_length
        
        # Copy các dữ liệu sau đó
        remaining = bin_data[after_img_offset:]
        new_remaining_offset = len(new_bin)
        new_bin += remaining
        
        while len(new_bin) % 4 != 0:
            new_bin += b'\x00'
            
        # 4. Cập nhật thông tin JSON
        img_bv["byteLength"] = new_img_length
        
        # Cập nhật byteOffset cho tất cả bufferViews đứng sau ảnh
        for i, bv in enumerate(gltf["bufferViews"]):
            if i != img_bv_index and bv.get("byteOffset", 0) >= after_img_offset:
                bv["byteOffset"] = bv["byteOffset"] + (new_remaining_offset - after_img_offset)
                
        # Sửa mimeType
        gltf["images"][0]["mimeType"] = "image/jpeg"
        
        # Đảm bảo baseColorFactor là màu trắng [1, 1, 1, 1] và metallic = 0
        if "materials" in gltf:
            for mat in gltf["materials"]:
                if "pbrMetallicRoughness" in mat:
                    mat["pbrMetallicRoughness"]["baseColorFactor"] = [1, 1, 1, 1]
                    mat["pbrMetallicRoughness"]["metallicFactor"] = 0
                    mat["pbrMetallicRoughness"]["roughnessFactor"] = 0.9
                    
        gltf["buffers"][0]["byteLength"] = len(new_bin)
        
        # 5. Pack lại thành GLB nhị phân mới
        new_json_str = json.dumps(gltf, separators=(',', ':'))
        while len(new_json_str) % 4 != 0:
            new_json_str += ' '
        new_json_bytes = new_json_str.encode("utf-8")
        
        new_glb = bytearray()
        total = 12 + 8 + len(new_json_bytes) + 8 + len(new_bin)
        new_glb += struct.pack("<III", 0x46546C67, 2, total)
        new_glb += struct.pack("<II", len(new_json_bytes), 0x4E4F534A)
        new_glb += new_json_bytes
        new_glb += struct.pack("<II", len(new_bin), 0x004E4942)
        new_glb += new_bin
        
        with open(glb_path, "wb") as f:
            f.write(new_glb)
            
        logger.info(f"✅ [GLB Texture Binder] Đã nhúng xong! GLB mới: {len(new_glb)/1024/1024:.2f} MB")
        return True
    except Exception as e:
        logger.error(f"❌ [GLB Texture Binder] Lỗi trong quá trình nhúng texture: {e}")
        return False


def _safe_docker_path(local_path: str) -> str:
    """Chuyển đường dẫn local sang đường dẫn Docker an toàn (chống injection)."""
    rel = os.path.relpath(local_path, INTERNAL_SOURCES_DIR).replace(os.sep, '/')
    return shlex.quote(f"/data/file/sources/{rel}")


def run_3d_branch(frames_folder: str, painted_folder: str, output_3d_folder: str, model_type: str):
    """Pipeline COLMAP + OpenMVS chạy bằng DooD."""
    job_id = os.path.basename(os.path.dirname(frames_folder))
    logger.info(f"🚀 [3D] Bắt đầu COLMAP + OpenMVS cho {job_id}")

    # [FIX #19] Workspace nằm cùng cấp job folder (hỗ trợ cấu trúc dated)
    job_folder = os.path.dirname(frames_folder)
    workspace_dir = os.path.join(os.path.dirname(job_folder), f"{job_id}_workspace")
    os.makedirs(workspace_dir, exist_ok=True)

    sources_mount = "/data/file/sources"

    # [FIX #5] Tất cả đường dẫn đều được escape bằng shlex.quote
    docker_frames = _safe_docker_path(frames_folder)
    docker_painted = _safe_docker_path(painted_folder)
    docker_workspace = _safe_docker_path(workspace_dir)

    db_path = f"{_safe_docker_path(workspace_dir).strip(chr(39))}/database.db"
    sparse_dir = f"{_safe_docker_path(workspace_dir).strip(chr(39))}/sparse"
    mvs_dir = f"{_safe_docker_path(workspace_dir).strip(chr(39))}/mvs"

    # Escape lại cho bash script
    db_path_q = shlex.quote(db_path)
    sparse_dir_q = shlex.quote(sparse_dir)
    mvs_dir_q = shlex.quote(mvs_dir)

    # Gắn name cho container để có thể kill nếu job bị hủy
    container_name = f"meshroom_{job_id}"
    base_cmd = ["docker", "run", "--name", container_name, "--rm", "-v", f"{HOST_SOURCES_DIR}:{sources_mount}"]
    if DOCKER_GPU_FLAG:
        base_cmd += ["--gpus", DOCKER_GPU_FLAG]

    try:
        # ── 1. COLMAP: Feature + Match (Sequential for Road, Spatial/VocabTree for Bridge Orbit 360°) + Mapper ──
        if model_type == "bridge":
            matcher_cmd = """colmap spatial_matcher \
    --database_path /tmp/database.db \
    --SpatialMatching.max_num_neighbors 50 || colmap vocab_tree_matcher \
    --database_path /tmp/database.db \
    --VocabTreeMatching.vocab_tree_path /usr/local/share/colmap/vocab_tree_flickr100K.bin"""
        else:
            matcher_cmd = """colmap sequential_matcher \
    --database_path /tmp/database.db \
    --SequentialMatching.overlap 20 \
    --SequentialMatching.loop_detection 1"""

        colmap_script = f"""set -e
colmap feature_extractor \
    --database_path /tmp/database.db \
    --image_path {docker_frames} \
    --ImageReader.single_camera 1 \
    --ImageReader.camera_model PINHOLE &&
{matcher_cmd} &&
mkdir -p {sparse_dir_q} &&
colmap mapper \
    --database_path /tmp/database.db \
    --image_path {docker_frames} \
    --output_path {sparse_dir_q} &&
cp /tmp/database.db {db_path_q}
"""
        logger.info(f"[{job_id}] COLMAP 1/2: Feature + Match + Mapper...")
        subprocess.run(
            base_cmd + ["--entrypoint", "bash", "colmap/colmap:latest", "-c", colmap_script],
            check=True,
        )

        # ── Tìm sub-model tốt nhất (nhiều điểm 3D nhất) ──
        local_sparse_dir = os.path.join(workspace_dir, "sparse")
        best_model_idx = "0"
        max_points_size = -1
        if os.path.exists(local_sparse_dir):
            for d in os.listdir(local_sparse_dir):
                d_path = os.path.join(local_sparse_dir, d)
                if os.path.isdir(d_path):
                    bin_path = os.path.join(d_path, "points3D.bin")
                    if os.path.exists(bin_path):
                        sz = os.path.getsize(bin_path)
                        if sz > max_points_size:
                            max_points_size = sz
                            best_model_idx = d
        logger.info(f"[{job_id}] Chọn sub-model tốt nhất của COLMAP: {best_model_idx} (kích thước points3D.bin = {max_points_size} bytes)")

        # Đường dẫn Docker của sub-model tốt nhất
        best_sparse_path_q = shlex.quote(f"{sparse_dir}/{best_model_idx}")

        # ── 1b. Convert best sub-model to TXT ──
        convert_script = f"""set -e
colmap model_converter \
    --input_path {best_sparse_path_q} \
    --output_path {best_sparse_path_q} \
    --output_type TXT
"""
        subprocess.run(
            base_cmd + ["--entrypoint", "bash", "colmap/colmap:latest", "-c", convert_script],
            check=True,
        )

        # ── 2. COLMAP: Undistort ──
        logger.info(f"[{job_id}] COLMAP 2/2: Image Undistorter...")
        undistort_script = (
            f"mkdir -p {mvs_dir_q} && colmap image_undistorter "
            f"--image_path {docker_painted} --input_path {best_sparse_path_q} "
            f"--output_path {mvs_dir_q} --output_type COLMAP && "
            f"colmap model_converter --input_path {mvs_dir_q}/sparse --output_path {mvs_dir_q}/sparse --output_type TXT"
        )
        subprocess.run(
            base_cmd + ["--entrypoint", "bash", "colmap/colmap:latest", "-c", undistort_script],
            check=True,
        )

        # ── 3. OpenMVS Pipeline ──
        logger.info(f"[{job_id}] OpenMVS: Pipeline (resolution-level={RESOLUTION_LEVEL})...")
        openmvs_script = f"""set -e
cd {mvs_dir_q}
InterfaceCOLMAP -i . -o scene.mvs
DensifyPointCloud -i scene.mvs -o scene_dense.mvs --resolution-level {RESOLUTION_LEVEL}
ReconstructMesh -i scene_dense.mvs -o scene_dense_mesh.mvs --decimate 0.15
TextureMesh -i scene_dense_mesh.mvs -o scene_dense_mesh_texture.obj --export-type obj --resolution-level {RESOLUTION_LEVEL}
"""
        subprocess.run(
            base_cmd + ["--entrypoint", "bash", "openmvs/openmvs-ubuntu:latest", "-c", openmvs_script],
            check=True,
        )

        # ── 4. Copy + Chuyển đổi GLB ──
        final_obj = os.path.join(workspace_dir, "mvs", "scene_dense_mesh_texture.obj")
        final_mtl = os.path.join(workspace_dir, "mvs", "scene_dense_mesh_texture.mtl")

        if os.path.exists(final_obj):
            shutil.copy2(final_obj, os.path.join(output_3d_folder, "texturedMesh.obj"))
            if os.path.exists(final_mtl):
                shutil.copy2(final_mtl, os.path.join(output_3d_folder, "texturedMesh.mtl"))

            for tex_file in os.listdir(os.path.join(workspace_dir, "mvs")):
                if tex_file.endswith((".png", ".jpg")) and "texture" in tex_file.lower():
                    img_path = os.path.join(workspace_dir, "mvs", tex_file)
                    
                    # [Tính năng mới] Bóp nhỏ Texture khổng lồ xuống mức an toàn cho WebGL (4096px)
                    try:
                        from PIL import Image
                        Image.MAX_IMAGE_PIXELS = None
                        with Image.open(img_path) as img:
                            if img.width > 4096 or img.height > 4096:
                                logger.info(f"📐 [3D] Tối ưu Texture {tex_file} ({img.width}x{img.height}) xuống 4096px...")
                                img.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
                                # Lưu đè lại ảnh đã bóp
                                img.save(img_path, optimize=True, quality=85)
                    except Exception as e:
                        logger.error(f"⚠️ [3D] Lỗi bóp texture: {e}")
                        
                    shutil.copy2(img_path, os.path.join(output_3d_folder, tex_file))

            logger.info("✅ [3D] Mesh 3D (.obj) hoàn tất!")

            obj_size_mb = os.path.getsize(final_obj) / (1024 * 1024)
            glb_path = os.path.join(output_3d_folder, "texturedMesh.glb")

            if False:
                logger.warning(f"⚠️ [GLB] File OBJ quá lớn. Bỏ qua GLB.")
            else:
                try:
                    import trimesh
                    from PIL import Image
                    Image.MAX_IMAGE_PIXELS = None
                    mesh = trimesh.load_mesh(final_obj, process=False)
                    
                    # SOTA Optimization: Mesh Decimation to 200,000 faces for ultra-smooth 60 FPS WebGL
                    max_faces = 200000
                    if len(mesh.faces) > max_faces:
                        logger.info(f"📐 [3D] Đang đơn giản hóa lưới mesh từ {len(mesh.faces)} đa giác xuống {max_faces}...")
                        try:
                            mesh = mesh.simplify_quadric_decimation(max_faces)
                            logger.info("📐 [3D] Đơn giản hóa mesh hoàn tất!")
                        except Exception as simp_err:
                            logger.error(f"⚠️ [3D] Lỗi đơn giản hóa mesh: {simp_err}")

                    mesh.export(glb_path)
                    logger.info(f"🌟 [GLB] Export GLB thành công! → {glb_path}")
                    
                    # Tìm file ảnh texture đã được copy vào output_3d_folder
                    tex_file_found = None
                    for tf in os.listdir(output_3d_folder):
                        if tf.lower().endswith((".png", ".jpg")) and "texture" in tf.lower():
                            tex_file_found = os.path.join(output_3d_folder, tf)
                            break
                    
                    if tex_file_found:
                        # Tối ưu hóa dung lượng texture (giới hạn tối đa 4096px để tránh quá tải GPU/network)
                        try:
                            with Image.open(tex_file_found) as img:
                                if max(img.width, img.height) > 4096:
                                    logger.info(f"🎨 [3D] Tối ưu dung lượng texture {img.size} -> Max 4096px...")
                                    img.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
                                    img.save(tex_file_found, quality=90, optimize=True)
                        except Exception as tex_opt_err:
                            logger.warning(f"⚠️ [3D] Lỗi tối ưu texture: {tex_opt_err}")

                        embed_texture_in_glb(glb_path, tex_file_found)
                    else:
                        logger.warning("⚠️ [GLB] Không tìm thấy tệp ảnh texture để nhúng.")
                except Exception as e:
                    logger.error(f"⚠️ [GLB] Lỗi chuyển đổi GLB: {e}")
        else:
            logger.error(f"❌ [3D] Không tìm thấy file OBJ: {final_obj}")

    except subprocess.CalledProcessError as e:
        logger.error(f"❌ [3D] Lỗi Docker subprocess: {e}")
        raise
    except Exception as e:
        logger.error(f"❌ [3D] Lỗi không xác định: {e}")
        raise


# ══════════════════════════════════════════════════════════
# Điều phối Pipeline chính
# ══════════════════════════════════════════════════════════
async def process_twin_job(job_id: str, video_path: str, model_type: str):
    """Hàm lõi điều phối toàn bộ workflow Digital Twin."""
    loop = asyncio.get_event_loop()

    # [FIX #19] Resolve job folder (đã tạo sẵn theo ngày trong endpoint)
    job_folder = _resolve_job_folder(job_id)
    frames_folder = os.path.join(job_folder, "frames")
    painted_folder = os.path.join(job_folder, "painted_frames")
    ai_results_folder = os.path.join(job_folder, "ai_results")
    output_3d_folder = os.path.join(job_folder, "3d_output")
    # Workspace tạm nằm cùng cấp job_folder (sẽ bị xóa sau)
    workspace_dir = os.path.join(os.path.dirname(job_folder), f"{job_id}_workspace")

    os.makedirs(frames_folder, exist_ok=True)
    os.makedirs(painted_folder, exist_ok=True)
    os.makedirs(ai_results_folder, exist_ok=True)
    os.makedirs(output_3d_folder, exist_ok=True)

    job_store[job_id] = {
        "status": "processing", "step": "init",
        "started_at": datetime.now().isoformat(), "error": None,
        "job_folder": job_folder  # [FIX #19] Lưu path thực tế
    }

    try:
        # ── BƯỚC 1: Cắt video + Resize ──
        job_store[job_id]["step"] = "extracting_frames"
        logger.info(f"[{job_id}] ═══ BƯỚC 1: Cắt video (FPS={FRAME_EXTRACT_FPS}, max={MAX_IMAGE_DIM}px) ═══")
        # [FIX #9] Dùng heavy_executor thay vì default thread pool
        frame_count = await loop.run_in_executor(heavy_executor, extract_frames, video_path, frames_folder, FRAME_EXTRACT_FPS)

        if frame_count == 0:
            raise RuntimeError("Không thể cắt frame từ video.")

        # [FIX #18] Kiểm tra số frame tối thiểu
        if frame_count < MIN_FRAMES:
            raise RuntimeError(
                f"Video quá ngắn: chỉ có {frame_count} frames (tối thiểu {MIN_FRAMES}). "
                f"COLMAP cần ít nhất {MIN_FRAMES} ảnh để dựng mô hình 3D."
            )
        logger.info(f"[{job_id}] ✅ {frame_count} frames đạt chuẩn (>= {MIN_FRAMES}).")

        # ── BƯỚC 2: AI YOLO nhận diện ──
        if job_store.get(job_id, {}).get("status") in ["failed", "cancelled"]:
            logger.info(f"[{job_id}] Job cancelled before step 2 (AI YOLO). Exiting pipeline.")
            return

        job_store[job_id]["step"] = "ai_detection"
        logger.info(f"[{job_id}] ═══ BƯỚC 2: Chạy AI YOLO ═══")
        json_path = os.path.join(ai_results_folder, "yolo_detections.json")
        # [FIX #8] Kiểm tra ngưỡng thành công
        ai_ok = await run_ai_branch(frames_folder, job_id, model_type, json_path)
        if not ai_ok:
            raise RuntimeError(
                f"YOLO API xử lý thất bại quá nhiều ảnh (dưới ngưỡng {YOLO_SUCCESS_THRESHOLD*100:.0f}%). "
                f"Kiểm tra kết nối tới YOLO API: {YOLO_API_URL}"
            )

        # ── BƯỚC 3: Vẽ vết nứt ──
        if job_store.get(job_id, {}).get("status") in ["failed", "cancelled"]:
            logger.info(f"[{job_id}] Job cancelled before step 3 (Texture Baking). Exiting pipeline.")
            return

        job_store[job_id]["step"] = "painting_textures"
        logger.info(f"[{job_id}] ═══ BƯỚC 3: Texture Baking ═══")
        await loop.run_in_executor(heavy_executor, paint_yolo_on_frames, frames_folder, painted_folder, json_path)

        # ── BƯỚC 4: COLMAP + OpenMVS (chờ hàng đợi GPU) ──
        if job_store.get(job_id, {}).get("status") in ["failed", "cancelled"]:
            logger.info(f"[{job_id}] Job cancelled before step 4 (COLMAP/OpenMVS). Exiting pipeline.")
            return

        job_store[job_id]["step"] = "waiting_gpu"
        logger.info(f"[{job_id}] ═══ BƯỚC 4: Chờ hàng đợi GPU... ═══")
        async with gpu_semaphore:
            if job_store.get(job_id, {}).get("status") in ["failed", "cancelled"]:
                logger.info(f"[{job_id}] Job cancelled during GPU wait. Exiting pipeline.")
                return
            job_store[job_id]["step"] = "building_3d"
            logger.info(f"[{job_id}] ═══ BƯỚC 4: COLMAP + OpenMVS ═══")
            await loop.run_in_executor(heavy_executor, run_3d_branch, frames_folder, painted_folder, output_3d_folder, model_type)

        job_store[job_id].update({"status": "completed", "step": "done", "completed_at": datetime.now().isoformat()})
        logger.info(f"🏁 [{job_id}] ═══ PIPELINE HOÀN TẤT ═══")

    except Exception as e:
        job_store[job_id].update({"status": "failed", "step": "error", "error": str(e)})
        logger.error(f"❌ [{job_id}] Pipeline thất bại: {e}")

    finally:
        # [FIX #6] Dọn dẹp workspace trung gian
        if os.path.exists(workspace_dir):
            try:
                shutil.rmtree(workspace_dir)
                logger.info(f"🧹 [{job_id}] Đã dọn workspace: {workspace_dir}")
            except Exception as e:
                logger.warning(f"⚠️ [{job_id}] Không thể dọn workspace: {e}")


# ══════════════════════════════════════════════════════════
# API Endpoints
# ══════════════════════════════════════════════════════════

# [FIX #10] Healthcheck toàn diện – kiểm tra YOLO + Docker images
@app.get("/api/v1/health")
async def health_check():
    """Kiểm tra sức khỏe toàn diện: API + YOLO + Docker images."""
    checks = {
        "api": True,
        "auth_configured": bool(API_TOKEN),
        "yolo_api": False,
        "docker_colmap": False,
        "docker_openmvs": False,
    }

    # Ping YOLO API
    try:
        headers = {}
        if YOLO_API_TOKEN:
            headers["Authorization"] = f"Bearer {YOLO_API_TOKEN}"
        from urllib.parse import urlsplit, urlunsplit
        parsed = urlsplit(YOLO_API_URL)
        health_url = urlunsplit((parsed.scheme, parsed.netloc, "/api/v1/health", "", ""))
        async with aiohttp.ClientSession() as session:
            async with session.get(health_url, headers=headers, timeout=5) as resp:
                body = await resp.json(content_type=None)
                checks["yolo_api"] = resp.status == 200 and body.get("status") is True
    except Exception:
        checks["yolo_api"] = False

    # Kiểm tra Docker images tồn tại
    for img_name, key in [("colmap/colmap:latest", "docker_colmap"), ("openmvs/openmvs-ubuntu:latest", "docker_openmvs")]:
        try:
            result = subprocess.run(["docker", "image", "inspect", img_name], capture_output=True, timeout=10)
            checks[key] = result.returncode == 0
        except Exception:
            checks[key] = False

    all_ok = all(checks.values())
    payload = {
        "status": all_ok,
        "message": "All systems operational." if all_ok else "Some dependencies are unavailable.",
        "checks": checks,
    }
    if not all_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/api/v1/twin/status/{job_id}")
async def get_job_status(job_id: str):
    """Kiểm tra trạng thái xử lý của một Job. Khôi phục từ disk nếu restart."""
    job_folder = _resolve_job_folder(job_id)
    
    if job_id in job_store:
        job_info = job_store[job_id].copy()
    else:
        # Nếu RAM mất dữ liệu (do restart), kiểm tra xem folder thực tế có tồn tại không
        if not os.path.exists(job_folder):
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' không tồn tại.")
        
        # Tái tạo lại state cơ bản dựa trên file output
        glb_path = os.path.join(job_folder, "3d_output", "texturedMesh.glb")
        status = "completed" if os.path.exists(glb_path) else "unknown"
        job_info = {
            "status": status,
            "step": "done" if status == "completed" else "unknown",
            "job_folder": job_folder
        }

    glb_path = os.path.join(job_folder, "3d_output", "texturedMesh.glb")
    obj_path = os.path.join(job_folder, "3d_output", "texturedMesh.obj")
    json_path = os.path.join(job_folder, "ai_results", "yolo_detections.json")

    job_info["outputs"] = {
        "glb": f"/files/{job_id}/3d_output/texturedMesh.glb" if os.path.exists(glb_path) else None,
        "obj": f"/files/{job_id}/3d_output/texturedMesh.obj" if os.path.exists(obj_path) else None,
        "ai_json": f"/files/{job_id}/ai_results/yolo_detections.json" if os.path.exists(json_path) else None,
    }

    job_info["storage_path"] = job_folder

    return {"status": True, "data": job_info}


@app.post("/api/v1/twin/convert-obj/{job_id}")
async def convert_obj_to_glb_endpoint(job_id: str):
    """Manually triggers OBJ to GLB conversion for a completed job if GLB was skipped."""
    job_folder = _resolve_job_folder(job_id)
    final_obj = os.path.join(job_folder, "3d_output", "texturedMesh.obj")
    glb_path = os.path.join(job_folder, "3d_output", "texturedMesh.glb")
    output_3d_folder = os.path.join(job_folder, "3d_output")
    
    if not os.path.exists(final_obj):
        raise HTTPException(status_code=404, detail=f"OBJ file not found at {final_obj}")
        
    try:
        import trimesh
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        
        logger.info(f"Loading mesh: {final_obj}")
        mesh = trimesh.load_mesh(final_obj, process=False)
        
        max_faces = 300000
        if len(mesh.faces) > max_faces:
            logger.info(f"📐 [3D] Đang đơn giản hóa lưới mesh từ {len(mesh.faces)} đa giác xuống {max_faces}...")
            try:
                mesh = mesh.simplify_quadric_decimation(max_faces)
                logger.info("📐 [3D] Đơn giản hóa mesh hoàn tất!")
            except Exception as simp_err:
                logger.error(f"⚠️ [3D] Lỗi đơn giản hóa mesh: {simp_err}")

        mesh.export(glb_path)
        logger.info(f"🌟 [GLB] Export GLB thành công! → {glb_path}")
        
        # Embed texture
        tex_file_found = None
        for tf in os.listdir(output_3d_folder):
            if tf.lower().endswith((".png", ".jpg")) and "texture" in tf.lower():
                tex_file_found = os.path.join(output_3d_folder, tf)
                break
                
        if tex_file_found:
            embed_texture_in_glb(glb_path, tex_file_found)
            
        return {"status": True, "message": "Conversion completed successfully!"}
    except Exception as e:
        logger.error(f"Error during manual GLB export: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────
# [NEW] CAMERA POSE LOADER & 3D RE-PROJECTION ENGINE (L2/L3 Digital Twin)
# ──────────────────────────────────────────────────────────

def load_colmap_camera_poses(images_txt_path: str) -> dict:
    """
    Parses COLMAP images.txt and converts camera pose quaternions to extrinsic matrices [R|t] 3x4.
    Returns:
        dict: {image_name: extrinsic_matrix_3x4}
    """
    from scipy.spatial.transform import Rotation
    import numpy as np
    
    poses = {}
    if not os.path.exists(images_txt_path):
        logger.error(f"COLMAP images.txt not found at: {images_txt_path}")
        return poses
        
    try:
        with open(images_txt_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
                
            # COLMAP format:
            # IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME
            tokens = line.split()
            if len(tokens) >= 10 and (tokens[-1].lower().endswith(('.png', '.jpg', '.jpeg', '.bmp'))):
                image_name = tokens[-1]
                try:
                    qw, qx, qy, qz = map(float, tokens[1:5])
                    tx, ty, tz = map(float, tokens[5:8])
                    
                    # 1. Create rotation matrix R (3x3) using scipy Rotation
                    # Scipy expects quaternions as [qx, qy, qz, qw]
                    r = Rotation.from_quat([qx, qy, qz, qw]).as_matrix()
                    
                    # 2. Translation vector t (3x1)
                    t = np.array([[tx], [ty], [tz]])
                    
                    # 3. Combine to [R|t] matrix (3x4)
                    extrinsic = np.hstack((r, t))
                    poses[image_name] = extrinsic.tolist()  # serialize for JSON storage
                except Exception as e:
                    logger.error(f"Error parsing camera pose for image {image_name}: {e}")
    except Exception as e:
        logger.error(f"Failed to read COLMAP images.txt: {e}")
                
    logger.info(f"Loaded {len(poses)} camera poses from {images_txt_path}")
    return poses


class Crack3DReprojector:
    """
    Ray-casts 2D crack coordinates onto 3D mesh via COLMAP camera poses.
    Integrates Poisson reconstruction for watertight mesh, ICP snapping, and decimation.
    """
    def __init__(self, mesh_path: str, decimate_factor: float = 0.1):
        self.mesh_path = mesh_path
        self.decimate_factor = decimate_factor
        self.mesh_watertight = None
        self.mesh_lowpoly = None
        self.load_and_preprocess_mesh()

    def load_and_preprocess_mesh(self):
        """Loads and processes the mesh (Poisson Reconstruction + Decimation)."""
        import open3d as o3d
        if not os.path.exists(self.mesh_path):
            logger.error(f"Mesh file not found at: {self.mesh_path}")
            return
            
        try:
            logger.info(f"Loading mesh for re-projection: {self.mesh_path}")
            o3d_mesh = o3d.io.read_triangle_mesh(self.mesh_path)
            
            # 1. Poisson Surface Reconstruction to make it Watertight
            o3d_mesh.compute_vertex_normals()
            pcd = o3d.geometry.PointCloud(o3d_mesh.vertices)
            pcd.normals = o3d_mesh.vertex_normals
            
            logger.info("Running Poisson Surface Reconstruction for watertight mesh...")
            poisson_mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=9)
            
            # Crop to original bounding box to remove ballooning artifacts
            bbox = o3d_mesh.get_axis_aligned_bounding_box()
            self.mesh_watertight = poisson_mesh.crop(bbox)
            logger.info(f"Poisson watertight mesh created: {len(self.mesh_watertight.triangles)} triangles")
            
            # 2. Mesh Decimation to Low-Poly for fast geodesic computations
            logger.info(f"Running Mesh Decimation (factor={self.decimate_factor})...")
            self.mesh_lowpoly = self.mesh_watertight.simplify_quadric_decimation(
                target_number_of_triangles=max(100, int(len(self.mesh_watertight.triangles) * self.decimate_factor))
            )
            logger.info(f"Low-poly mesh created: {len(self.mesh_lowpoly.triangles)} triangles")
            
        except Exception as e:
            logger.error(f"Error preprocessing mesh: {e}", exc_info=True)

    def reproject_cracks(self, crack_detections_2d: list, camera_poses: dict, intrinsics: np.ndarray) -> list:
        """
        Reprojects 2D crack pixel coordinates to 3D on the watertight mesh using ray-casting.
        """
        import open3d as o3d
        if self.mesh_watertight is None:
            logger.error("Watertight mesh is not loaded. Cannot reproject.")
            return []
            
        scene = o3d.t.geometry.RaycastingScene()
        mesh_t = o3d.t.geometry.TriangleMesh.from_legacy(self.mesh_watertight)
        scene.add_triangles(mesh_t)
        
        reprojected_entities = []
        
        for det in crack_detections_2d:
            image_name = det.get("image_name")
            pose = camera_poses.get(image_name)
            
            if pose is None:
                continue
                
            pose = np.array(pose) # 3x4 [R|t]
            fx = intrinsics[0, 0]
            fy = intrinsics[1, 1]
            cx = intrinsics[0, 2]
            cy = intrinsics[1, 2]
            
            R = pose[:3, :3]
            t = pose[:3, 3]
            
            # Camera center in world coords: C = -R^T * t
            camera_center = -R.T @ t
            
            polygon_2d = det.get("polygon", [])
            if not polygon_2d:
                bx1, by1, bx2, by2 = det.get("bbox", [0, 0, 0, 0])
                polygon_2d = [[(bx1+bx2)/2, (by1+by2)/2]]
                
            pts_3d = []
            
            for px, py in polygon_2d:
                # Project pixel to normalized space
                ray_dir_c = np.array([(px - cx) / fx, (py - cy) / fy, 1.0])
                ray_dir_c /= np.linalg.norm(ray_dir_c)
                
                # Transform direction to world space
                ray_dir_w = R.T @ ray_dir_c
                ray_dir_w /= np.linalg.norm(ray_dir_w)
                
                # Ray origin and direction
                ray = np.hstack((camera_center, ray_dir_w)).astype(np.float32)
                ans = scene.cast_rays(o3d.core.Tensor([ray], dtype=o3d.core.Dtype.Float32))
                
                t_hit = ans['t_hit'][0].item()
                if not np.isinf(t_hit) and t_hit > 0:
                    intersection_pt = camera_center + t_hit * ray_dir_w
                    pts_3d.append(intersection_pt.tolist())
            
            if len(pts_3d) > 0:
                # 3. Local KDTree snapping (fast ICP alternative)
                snapped_pts = self._local_icp_snap(pts_3d)
                
                # 4. Measure Geodesic Distance
                length_mm = self._measure_geodesic_length(snapped_pts)
                
                severity_level = det.get("severity", "unknown")
                width_mm = det.get("width_mm", 0.5)
                
                entity = {
                    "crack_id": det.get("track_id") or str(uuid.uuid4().hex[:8]),
                    "class": det.get("class", "crack"),
                    "points_3d": snapped_pts,
                    "length_mm": round(length_mm, 2),
                    "width_mm": width_mm,
                    "severity": severity_level,
                    "image_name": image_name,
                    "camera_pose": pose.tolist()
                }
                reprojected_entities.append(entity)
                
        return reprojected_entities

    def _local_icp_snap(self, pts_3d: list) -> list:
        """Snaps 3D crack points to the closest vertices on the watertight mesh."""
        snapped = []
        try:
            vertices = np.asarray(self.mesh_watertight.vertices)
            from scipy.spatial import KDTree
            kdtree = KDTree(vertices)
            
            for pt in pts_3d:
                dist, idx = kdtree.query(pt)
                # Snap to closest vertex if within 30cm
                if dist < 0.3:
                    snapped.append(vertices[idx].tolist())
                else:
                    snapped.append(pt)
            return snapped
        except Exception as e:
            logger.warning(f"Local snapping failed, returning original points: {e}")
            return pts_3d

    def _measure_geodesic_length(self, pts_3d: list) -> float:
        """Measures the geodesic distance of the 3D polyline on the decimated mesh."""
        if len(pts_3d) < 2:
            return 0.0
            
        try:
            vertices = np.asarray(self.mesh_lowpoly.vertices)
            faces = np.asarray(self.mesh_lowpoly.triangles)
            t_mesh = trimesh.Trimesh(vertices=vertices, faces=faces)
            
            total_dist = 0.0
            for i in range(len(pts_3d) - 1):
                p1 = pts_3d[i]
                p2 = pts_3d[i+1]
                
                v1_idx = t_mesh.kdtree.query(p1)[1]
                v2_idx = t_mesh.kdtree.query(p2)[1]
                
                # Shortest path on low-poly mesh graph
                path = trimesh.graph.shortest_path(t_mesh.vertex_adjacency_graph, v1_idx, v2_idx)
                if len(path) > 0:
                    path_vertices = t_mesh.vertices[path]
                    segment_dist = np.sum(np.linalg.norm(np.diff(path_vertices, axis=0), axis=1))
                    total_dist += segment_dist
                else:
                    # Fallback to Euclidean
                    total_dist += np.linalg.norm(np.array(p1) - np.array(p2))
            return total_dist * 1000.0  # meters to mm
        except Exception as e:
            # Fallback to 3D Euclidean distance
            total_dist = 0.0
            for i in range(len(pts_3d) - 1):
                total_dist += np.linalg.norm(np.array(pts_3d[i]) - np.array(pts_3d[i+1]))
            return total_dist * 1000.0


class ReprojectRequest(BaseModel):
    task_id: str
    job_id: str
    model_type: str = "road"
    intrinsics: list = None


@app.post("/api/v1/twin/reproject")
async def reproject_2d_to_3d(req: ReprojectRequest):
    """
    Ray-casts 2D crack detections from MongoDB task results onto the 3D mesh.
    """
    from pymongo import MongoClient
    
    # 1. Fetch task results from MongoDB
    MONGO_URL = os.getenv("MONGO_DETAILS", "mongodb://localhost:27017/")
    client = MongoClient(MONGO_URL)
    db = client["digital_twin"]
    tasks_col = db["tasks"]
    
    task_doc = tasks_col.find_one({"_id": req.task_id})
    if not task_doc:
        raise HTTPException(status_code=404, detail=f"Task {req.task_id} not found in MongoDB.")
        
    detections_2d = []
    try:
        datas = task_doc.get("datas", [])
        if datas:
            images = datas[0].get("images", [])
            for img in images:
                image_name = os.path.basename(img.get("frameFilePath", ""))
                image_name = image_name.split("?")[0]  # Strip parameters
                for det in img.get("detections", []):
                    det["image_name"] = image_name
                    detections_2d.append(det)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse detections from task doc: {e}")
        
    if not detections_2d:
        return {"status": True, "message": "No 2D detections found in task to reproject.", "entities": []}

    # 2. Resolve mesh and camera poses path
    job_folder = _resolve_job_folder(req.job_id)
    mesh_path = os.path.join(job_folder, "3d_output", "texturedMesh.obj")
    
    # Locate sparse images.txt
    images_txt_path = os.path.join(job_folder, "workspace", "sparse", "images.txt")
    if not os.path.exists(images_txt_path):
        images_txt_path = os.path.join(job_folder, "workspace", "dense", "sparse", "images.txt")
        
    if not os.path.exists(mesh_path):
        raise HTTPException(status_code=404, detail=f"3D Mesh not found at: {mesh_path}")
        
    # 3. Load Camera Poses
    camera_poses = load_colmap_camera_poses(images_txt_path)
    if not camera_poses:
        raise HTTPException(status_code=404, detail=f"No camera poses could be parsed from COLMAP sparse folder.")
        
    # 4. Camera Intrinsics
    if req.intrinsics is None:
        # Default: Zenmuse P1 typical intrinsics (fx, fy = 3000, cx = 1920, cy = 1080)
        intrinsics = np.array([
            [3000.0, 0.0, 1920.0],
            [0.0, 3000.0, 1080.0],
            [0.0, 0.0, 1.0]
        ])
    else:
        intrinsics = np.array(req.intrinsics)

    # 5. Run Reprojector
    try:
        reprojector = Crack3DReprojector(mesh_path)
        entities_3d = reprojector.reproject_cracks(detections_2d, camera_poses, intrinsics)
        
        # Save 3D entities to MongoDB Task document
        tasks_col.update_one(
            {"_id": req.task_id},
            {"$set": {"entities_3d": entities_3d}}
        )
        
        # Auto export to CityJSON format
        try:
            import sys
            # Add crack_api to sys.path so we can import cityjson_exporter
            # Meshroom is in d:\API\Meshroom, crack_api is in d:\API\crack_api
            api_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "crack_api"))
            if api_path not in sys.path:
                sys.path.append(api_path)
                
            from cityjson_exporter import CityJSONExporter
            output_cityjson_path = os.path.join(job_folder, "3d_output", "cracks.city.json")
            exporter = CityJSONExporter(req.task_id, db)
            exporter.export_to_cityjson(output_cityjson_path)
            
            tasks_col.update_one(
                {"_id": req.task_id},
                {"$set": {"cityjson_file_path": f"/files/{req.job_id}/3d_output/cracks.city.json"}}
            )
            logger.info(f"✅ Auto-exported cracks.city.json for task {req.task_id}")
        except Exception as export_err:
            logger.error(f"⚠️ Failed to auto-export CityJSON: {export_err}", exc_info=True)
        
        return {
            "status": True,
            "message": f"Successfully reprojected {len(entities_3d)} crack entities to 3D mesh.",
            "entities_count": len(entities_3d),
            "entities": entities_3d,
            "cityjson_url": f"/files/{req.job_id}/3d_output/cracks.city.json"
        }

    except Exception as e:
        logger.error(f"Error during re-projection: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Re-projection failed: {str(e)}")



@app.post("/api/v1/twin/create")
async def create_digital_twin(
    background_tasks: BackgroundTasks,
    model_type: str = Form("road"),
    video: UploadFile = File(...),
):
    """Nhận video Drone và kích hoạt Pipeline xử lý 3D."""
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    # [FIX #19] Tạo folder theo cấu trúc YYYY/MM/DD/job_xxx
    job_folder = _make_dated_job_folder(job_id)
    input_folder = os.path.join(job_folder, "input")
    os.makedirs(input_folder, exist_ok=True)

    # [FIX #1] Stream upload từng chunk 1MB
    video_path = os.path.join(input_folder, "raw_video.mp4")
    with open(video_path, "wb") as buffer:
        while chunk := await video.read(1024 * 1024):
            buffer.write(chunk)

    logger.info(f"📥 [{job_id}] Video uploaded: {video.filename} → {job_folder}")

    background_tasks.add_task(process_twin_job, job_id, video_path, model_type)

    return {
        "status": True,
        "job_id": job_id,
        "message": "Video uploaded. Pipeline started in background.",
        "status_url": f"/api/v1/twin/status/{job_id}",
    }

class CreateLocalTwinRequest(BaseModel):
    file_path: str
    model_type: str = "road"
    job_id: str = None  # Nếu truyền vào, dùng luôn thay vì sinh ngẫu nhiên

@app.post("/api/v1/twin/create-from-local")
async def create_twin_from_local(
    req: CreateLocalTwinRequest,
    background_tasks: BackgroundTasks,
):
    """Kích hoạt Pipeline 3D từ một file đã có sẵn trên ổ đĩa (tránh duplicate copy)."""
    # Sử dụng job_id từ bên ngoài nếu có (đồng bộ ID với Crack API)
    job_id = req.job_id if req.job_id else f"job_{uuid.uuid4().hex[:8]}"
    
    video_path = os.path.realpath(req.file_path)
    allowed_source_root = os.path.realpath(INTERNAL_SOURCES_DIR)
    try:
        is_allowed_source = os.path.commonpath([video_path, allowed_source_root]) == allowed_source_root
    except ValueError:
        is_allowed_source = False
    if not is_allowed_source:
        raise HTTPException(
            status_code=400,
            detail=f"file_path must be inside the shared source directory: {allowed_source_root}",
        )
    if not os.path.isfile(video_path):
        raise HTTPException(status_code=404, detail=f"Local file not found: {video_path}")
    
    # Nếu job_id có prefix task_ (đồng bộ từ Central Backend),
    # lưu kết quả 3D vào thư mục 3d/ cùng cấp với video
    if job_id.startswith("task_"):
        video_parent = os.path.dirname(video_path)
        job_folder = os.path.join(video_parent, "3d")
        os.makedirs(job_folder, exist_ok=True)
    else:
        # Cấu trúc cũ: YYYY/MM/DD/job_xxx
        job_folder = _make_dated_job_folder(job_id)
    
    # Lưu job_folder vào job_store ngay để _resolve_job_folder có thể tìm thấy
    job_store[job_id] = {"status": "queued", "job_folder": job_folder}
    
    logger.info(f"📥 [{job_id}] Local Video registered: {video_path} → {job_folder}")
    background_tasks.add_task(process_twin_job, job_id, video_path, req.model_type)
    
    return {
        "status": True,
        "job_id": job_id,
        "message": "Local video registered. Pipeline started in background.",
        "status_url": f"/api/v1/twin/status/{job_id}",
    }


@app.post("/api/v1/twin/cancel/{job_id}")
async def cancel_digital_twin_job(job_id: str):
    """Hủy tiến trình dựng 3D và kill docker container tương ứng nếu đang chạy."""
    if job_id not in job_store:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' không tồn tại trong bộ nhớ.")
        
    job_store[job_id]["status"] = "failed"
    job_store[job_id]["error"] = "Cancelled by user"
    logger.info(f"🛑 [{job_id}] Được yêu cầu hủy bởi người dùng.")

    try:
        container_name = f"meshroom_{job_id}"
        subprocess.run(["docker", "kill", container_name], capture_output=True)
        logger.info(f"💀 [{job_id}] Đã ra lệnh kill Docker container: {container_name}")
    except Exception as e:
        logger.warning(f"⚠️ [{job_id}] Không thể kill container {container_name}: {e}")
        
    return {"status": True, "message": f"Job '{job_id}' has been cancelled."}
