from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator, ValidationInfo
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
import httpx
import os
import asyncio
import tempfile
import re
import json
from contextlib import asynccontextmanager
from fastapi import Request, status, Depends, Query
from fastapi.responses import JSONResponse
from typing import Optional, List

# --- CẤU HÌNH DATABASE ---
DB_DSN = os.getenv("DB_DSN")
if not DB_DSN:
    raise RuntimeError("Biến môi trường DB_DSN chưa được cấu hình!")

# Chỉ cho phép origin cụ thể (đọc từ env, mặc định localhost dev)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")

# Chống truy cập trái phép: Token và IP
API_TOKEN = os.getenv("API_TOKEN", "")
if not API_TOKEN:
    raise RuntimeError("API_TOKEN must be configured")
_allowed_ips_env = os.getenv("ALLOWED_IPS", "")
ALLOWED_IPS = [ip.strip() for ip in _allowed_ips_env.split(",")] if _allowed_ips_env else []

# --- CẤU HÌNH KẾT NỐI CRACK API ---
CRACK_API_URL = os.getenv("CRACK_API_URL", "http://host.docker.internal:8000")
CRACK_API_TOKEN = os.getenv("CRACK_API_TOKEN", "")

# --- CONNECTION POOL ---
db_pool: ThreadedConnectionPool = None

def get_db():
    """Dependency cung cấp connection nội bộ tự động giải phóng qua yield."""
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database chưa sẵn sàng")
    
    conn = None
    try:
        conn = db_pool.getconn()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Không thể kết nối Database: {str(e)}")
        
    try:
        yield conn
    finally:
        if db_pool and conn:
            try:
                db_pool.putconn(conn)
            except Exception:
                pass

def get_db_conn_legacy():
    """Dùng cho lifespan khi không gọi qua HTTP request"""
    global db_pool
    if db_pool is None:
        raise Exception("Database chưa sẵn sàng")
    return db_pool.getconn()

def release_db_conn_legacy(conn):
    global db_pool
    if db_pool and conn:
        try:
            db_pool.putconn(conn)
        except Exception:
            pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    print("⏳ Đang khởi tạo Database Middleware...")
    for attempt in range(5):
        try:
            # Khởi tạo connection pool (min=2, max=20)
            db_pool = ThreadedConnectionPool(2, 20, DB_DSN)

            conn = get_db_conn_legacy()
            try:
                cur = conn.cursor()

                # Bảng quản lý tài liệu
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        id SERIAL PRIMARY KEY, 
                        doc_id VARCHAR(100) UNIQUE,
                        file_name VARCHAR(255), 
                        status VARCHAR(50) DEFAULT 'uploaded'
                    )
                """)
                
                # Bảng cấu hình hệ thống
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        id SERIAL PRIMARY KEY,
                        ragflow_api_key VARCHAR(255),
                        dataset_id VARCHAR(255),
                        ragflow_base_url VARCHAR(255)
                    )
                """)
                
                # Thực hiện Migration động: Thêm các cột cho cấu hình kép (Text vs VLM) nếu chưa có
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='settings'")
                existing_cols = [r[0] for r in cur.fetchall()]
                
                if "text_api_key" not in existing_cols:
                    print("⚙️ Thêm cột 'text_api_key' vào bảng settings...")
                    cur.execute("ALTER TABLE settings ADD COLUMN text_api_key VARCHAR(255) DEFAULT ''")
                if "text_dataset_id" not in existing_cols:
                    print("⚙️ Thêm cột 'text_dataset_id' vào bảng settings...")
                    cur.execute("ALTER TABLE settings ADD COLUMN text_dataset_id VARCHAR(255) DEFAULT ''")
                if "text_chat_id" not in existing_cols:
                    print("⚙️ Thêm cột 'text_chat_id' vào bảng settings...")
                    cur.execute("ALTER TABLE settings ADD COLUMN text_chat_id VARCHAR(255) DEFAULT ''")
                if "vlm_api_key" not in existing_cols:
                    print("⚙️ Thêm cột 'vlm_api_key' vào bảng settings...")
                    cur.execute("ALTER TABLE settings ADD COLUMN vlm_api_key VARCHAR(255) DEFAULT ''")
                if "vlm_dataset_id" not in existing_cols:
                    print("⚙️ Thêm cột 'vlm_dataset_id' vào bảng settings...")
                    cur.execute("ALTER TABLE settings ADD COLUMN vlm_dataset_id VARCHAR(255) DEFAULT ''")
                if "vlm_chat_id" not in existing_cols:
                    print("⚙️ Thêm cột 'vlm_chat_id' vào bảng settings...")
                    cur.execute("ALTER TABLE settings ADD COLUMN vlm_chat_id VARCHAR(255) DEFAULT ''")
                
                # Bảng lịch sử session chat
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        session_id VARCHAR(100) PRIMARY KEY,
                        user_id VARCHAR(100) DEFAULT 'default_user',
                        title VARCHAR(255) DEFAULT 'New Chat',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Bảng chi tiết dòng chat
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id SERIAL PRIMARY KEY,
                        session_id VARCHAR(100) REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                        role VARCHAR(20),
                        content TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Đảm bảo luôn có 1 dòng cấu hình gốc
                cur.execute("SELECT COUNT(*) FROM settings")
                if cur.fetchone()[0] == 0:
                    cur.execute("""
                        INSERT INTO settings (
                            ragflow_api_key, dataset_id, ragflow_base_url,
                            text_api_key, text_dataset_id, vlm_api_key, vlm_dataset_id
                        ) VALUES ('', '', '', '', '', '', '')
                    """)
                else:
                    # Đồng bộ dữ liệu cũ sang cấu hình text nếu rỗng
                    cur.execute("""
                        UPDATE settings 
                        SET text_api_key = ragflow_api_key 
                        WHERE (text_api_key IS NULL OR text_api_key = '') AND ragflow_api_key IS NOT NULL AND ragflow_api_key != ''
                    """)
                    cur.execute("""
                        UPDATE settings 
                        SET text_dataset_id = dataset_id 
                        WHERE (text_dataset_id IS NULL OR text_dataset_id = '') AND dataset_id IS NOT NULL AND dataset_id != ''
                    """)

                conn.commit()
                cur.close()
            finally:
                release_db_conn_legacy(conn)

            print("✅ Database Middleware đã sẵn sàng hoạt động!")
            break
        except Exception as e:
            print(f"⚠️ Chờ kết nối Database... Lỗi: {e}")
            if db_pool:
                try:
                    db_pool.closeall()
                except Exception:
                    pass
                db_pool = None
            if attempt == 4:
                print("❌ Quá 5 lần không thể kết nối DB. API sẽ chạy với tính năng hạn chế.")
            await asyncio.sleep(2)

    yield

    if db_pool:
        db_pool.closeall()
        db_pool = None
        print("🛑 Đã đóng tất cả kết nối Database.")

app = FastAPI(title="Middleware API", lifespan=lifespan)

# --- BẢO MẬT & CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def check_access(request: Request, call_next):
    if request.url.path in ["/docs", "/openapi.json", "/redoc", "/health", "/api/v1/health"]:
        return await call_next(request)

    client_ip = request.client.host if request.client else ""
    if ALLOWED_IPS and client_ip in ALLOWED_IPS:
        return await call_next(request)

    auth_header = request.headers.get("Authorization")
    token = request.headers.get("X-API-Token", "")
    
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]

    if token == API_TOKEN:
        return await call_next(request)

    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Unauthorized: Invalid or missing token/IP"}
    )


@app.get("/health")
@app.get("/api/v1/health")
async def health_check(conn=Depends(get_db)):
    """Readiness check for the middleware and its PostgreSQL dependency."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1")
        cur.fetchone()
        return {"status": True, "database": "connected"}
    finally:
        cur.close()


# --- KHAI BÁO MODEL DỮ LIỆU ---
class SettingsModel(BaseModel):
    ragflow_base_url: str
    text_api_key: str = ""
    text_dataset_id: str = ""
    text_chat_id: str = ""
    vlm_api_key: str = ""
    vlm_dataset_id: str = ""
    vlm_chat_id: str = ""
    # Tương thích ngược
    ragflow_api_key: str = ""
    dataset_id: str = ""

    @field_validator('ragflow_base_url')
    @classmethod
    def not_empty(cls, v: str, info: ValidationInfo):
        if not v or not v.strip():
            raise ValueError(f'{info.field_name} không được để trống')
        return v.strip()

class ChatRequest(BaseModel):
    question: str
    session_id: str = "default_session"
    stream: bool = False

    @field_validator('question')
    @classmethod
    def question_not_empty(cls, v: str):
        if not v or not v.strip():
            raise ValueError('Câu hỏi không được để trống')
        return v.strip()

class VlmChatRequest(BaseModel):
    question: Optional[str] = "Phân tích hình ảnh khuyết tật này."
    session_id: str = "default_session"
    stream: bool = False
    image_url: Optional[str] = None  # URL hoặc base64 data URI của hình ảnh phân tích
    detections: Optional[List] = None  # Danh sách tọa độ và thông tin bbox phát hiện từ YOLO
    direct_vlm: bool = False  # Báo cáo máy-máy: giữ nguyên prompt có cấu trúc và không qua LLM text lần hai

    @field_validator('question')
    @classmethod
    def question_not_empty(cls, v: Optional[str]):
        if v is None or not v.strip():
            return "Phân tích hình ảnh khuyết tật này."
        return v.strip()

class ParseActionRequest(BaseModel):
    doc_id: str
    action: str = "start"

# HÀM CỐT LÕI: LẤY VÀ XỬ LÝ CẤU HÌNH
def get_ragflow_config(conn, mode: str = "text"):
    cur = conn.cursor()
    cur.execute("""
        SELECT text_api_key, text_dataset_id, text_chat_id, ragflow_base_url,
               vlm_api_key, vlm_dataset_id, vlm_chat_id
        FROM settings WHERE id = 1
    """)
    row = cur.fetchone()
    cur.close()

    if not row:
        row = ("", "", "", "", "", "", "")

    db_base_url = row[3] if row and row[3] else ""

    if mode == "text":
        api_key = row[0] if row and row[0] else ""
        dataset_id = row[1] if row and row[1] else ""
        chat_id = row[2] if row and row[2] else ""
    else:
        api_key = row[4] if row and row[4] else ""
        dataset_id = row[5] if row and row[5] else ""
        chat_id = row[6] if row and row[6] else ""
        
        # Tự động fallback sang luồng text nếu luồng VLM chưa cấu hình riêng biệt
        if not api_key:
            api_key = row[0] if row and row[0] else ""
            dataset_id = row[1] if row and row[1] else ""
            chat_id = row[2] if row and row[2] else ""

    # Fallback to environment variables if DB settings are missing or empty
    if not api_key:
        if mode == "text":
            api_key = os.getenv("TEXT_API_KEY", "")
            dataset_id = os.getenv("TEXT_DATASET_ID", "")
            chat_id = os.getenv("TEXT_CHAT_ID", "")
        else:
            api_key = os.getenv("VLM_API_KEY", "")
            dataset_id = os.getenv("VLM_DATASET_ID", "")
            chat_id = os.getenv("VLM_CHAT_ID", "")

    # ── CRITICAL FIX: Luôn ưu tiên RAGFLOW_API_URL env var ──
    # Biến môi trường RAGFLOW_API_URL trỏ thẳng tới RAGFlow server (ragflow-gpu:9380).
    # ragflow_base_url từ DB có thể bị trỏ nhầm sang chính Middleware (port 8085).
    ragflow_env_url = os.getenv("RAGFLOW_API_URL", "")
    
    # Dùng env var làm URL chính, chỉ dùng DB URL khi env var trống
    if ragflow_env_url:
        api_url = ragflow_env_url.strip().rstrip('/')
    elif db_base_url:
        api_url = db_base_url.strip().rstrip('/')
    else:
        api_url = "http://host.docker.internal:9380/api/v1"

    if not api_key:
        raise HTTPException(
            status_code=400, 
            detail=f"Hệ thống chưa được cấu hình cho luồng '{mode}'. Hãy gọi POST /settings hoặc cấu hình biến môi trường!"
        )

    # Extract base_v1: strip /chats/... or /chats_openai/... suffix
    base_v1 = api_url.split('/chats_openai')[0].split('/chats')[0] if any(s in api_url for s in ['/chats_openai', '/chats']) else api_url
    base_v1 = base_v1.rstrip('/')

    # Tách BASE_ROOT
    if base_v1.endswith("/api/v1"):
        base_root = base_v1[:-len("/api/v1")]
    elif base_v1.endswith("/api/v1/"):
        base_root = base_v1[:-len("/api/v1/")]
    else:
        base_root = base_v1

    # Extract chat_id from DB ragflow_base_url if not defined explicitly
    if not chat_id or chat_id == "default":
        if db_base_url:
            match = re.search(r'/(?:chats_openai|chats)/([^/]+)', db_base_url)
            if match and match.group(1) != "default":
                chat_id = match.group(1)

    # ── AUTO-DISCOVER: Tự động tìm Chat Assistant từ RAGFlow nếu chưa có chat_id ──
    if not chat_id or chat_id == "default":
        try:
            discover_headers = {"Authorization": f"Bearer {api_key}"}
            resp = httpx.get(f"{base_v1}/chats", headers=discover_headers, timeout=10)
            if resp.status_code == 200:
                chats_data = resp.json().get("data", [])
                if chats_data:
                    # Chọn assistant đầu tiên
                    chat_id = chats_data[0]["id"]
                    print(f"🔍 [AUTO-DISCOVER] Tìm thấy Chat Assistant: {chat_id} ({chats_data[0].get('name', 'N/A')})")
                    # Lưu lại vào DB để lần sau không cần discover
                    try:
                        cur2 = conn.cursor()
                        if mode == "text":
                            cur2.execute("UPDATE settings SET text_chat_id = %s WHERE id = 1", (chat_id,))
                        else:
                            cur2.execute("UPDATE settings SET vlm_chat_id = %s WHERE id = 1", (chat_id,))
                        conn.commit()
                        cur2.close()
                        print(f"💾 [AUTO-DISCOVER] Đã lưu chat_id vào DB cho luồng '{mode}'")
                    except Exception as save_err:
                        print(f"⚠️ [AUTO-DISCOVER] Không thể lưu chat_id: {save_err}")
        except Exception as e:
            print(f"⚠️ [AUTO-DISCOVER] Không thể kết nối RAGFlow để tìm Assistant: {e}")

    if not chat_id or chat_id == "default":
        raise HTTPException(
            status_code=400,
            detail=f"Thiếu Assistant ID (chat_id) cho luồng '{mode}'. Hãy tạo Chat Assistant trên RAGFlow trước!"
        )

    print(f"📡 [CONFIG] mode={mode}, base_v1={base_v1}, chat_id={chat_id[:8]}...")
    return api_key, dataset_id, chat_id, api_url, base_v1, base_root

# 1. SETTINGS (CẤU HÌNH KÉP)
@app.post("/settings")
async def update_settings(settings: SettingsModel, conn = Depends(get_db)):
    text_api = settings.text_api_key.strip() if settings.text_api_key else ""
    if not text_api and settings.ragflow_api_key:
        text_api = settings.ragflow_api_key.strip()
        
    text_ds = settings.text_dataset_id.strip() if settings.text_dataset_id else ""
    if not text_ds and settings.dataset_id:
        text_ds = settings.dataset_id.strip()
        
    text_ch = settings.text_chat_id.strip() if settings.text_chat_id else ""
    vlm_api = settings.vlm_api_key.strip() if settings.vlm_api_key else ""
    vlm_ds = settings.vlm_dataset_id.strip() if settings.vlm_dataset_id else ""
    vlm_ch = settings.vlm_chat_id.strip() if settings.vlm_chat_id else ""
    base_url = settings.ragflow_base_url.strip()

    cur = conn.cursor()
    cur.execute("""
        UPDATE settings 
        SET ragflow_base_url = %s,
            text_api_key = %s, text_dataset_id = %s, text_chat_id = %s,
            vlm_api_key = %s, vlm_dataset_id = %s, vlm_chat_id = %s,
            ragflow_api_key = %s, dataset_id = %s
        WHERE id = 1
    """, (base_url, text_api, text_ds, text_ch, vlm_api, vlm_ds, vlm_ch, text_api, text_ds))
    conn.commit()
    cur.close()

    return {"message": "Đã lưu cấu hình thành công!"}

@app.get("/settings")
async def get_settings(conn = Depends(get_db)):
    cur = conn.cursor()
    cur.execute("""
        SELECT ragflow_base_url, 
               text_api_key, text_dataset_id, text_chat_id,
               vlm_api_key, vlm_dataset_id, vlm_chat_id,
               ragflow_api_key, dataset_id
        FROM settings WHERE id = 1
    """)
    row = cur.fetchone()
    cur.close()

    if not row:
        return {
            "ragflow_base_url": "",
            "text_api_key": "",
            "text_dataset_id": "",
            "text_chat_id": "",
            "vlm_api_key": "",
            "vlm_dataset_id": "",
            "vlm_chat_id": "",
            "ragflow_api_key": "",
            "dataset_id": ""
        }

    return {
        "ragflow_base_url": row[0] or "",
        "text_api_key": row[1] or "",
        "text_dataset_id": row[2] or "",
        "text_chat_id": row[3] or "",
        "vlm_api_key": row[4] or "",
        "vlm_dataset_id": row[5] or "",
        "vlm_chat_id": row[6] or "",
        "ragflow_api_key": row[7] or "",
        "dataset_id": row[8] or ""
    }

# 2. UPLOAD (Mặc định tải lên Text Dataset)
@app.post("/upload")
async def upload_and_parse(file: UploadFile = File(...), conn = Depends(get_db)):
    API_KEY, DATASET_ID, _, _, BASE_V1, _ = get_ragflow_config(conn, mode="text")
    HEADERS = {"Authorization": f"Bearer {API_KEY}"}
    original_filename = file.filename
    
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res_upload = await client.post(
                f"{BASE_V1}/datasets/{DATASET_ID}/documents",
                headers=HEADERS,
                files={'file': (original_filename, file.file, file.content_type)}
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Lỗi nối mạng tới RAGFlow: {str(e)}"
        )

    await file.close()

    if res_upload.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Lỗi RAGFlow Upload: {res_upload.text}")

    upload_json = res_upload.json()
    if upload_json.get("code") != 0:
        raise HTTPException(status_code=400, detail=f"Lỗi RAGFlow Upload: {res_upload.text}")

    doc_data = upload_json.get("data")
    if not doc_data:
        raise HTTPException(status_code=500, detail="RAGFlow trả về dữ liệu rỗng")

    if isinstance(doc_data, dict):
        doc_id = doc_data.get("id")
    elif isinstance(doc_data, list) and len(doc_data) > 0:
        doc_id = doc_data[0].get("id")
    else:
        raise HTTPException(status_code=500, detail="Định dạng trả về không hợp lệ")

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO documents (doc_id, file_name, status) VALUES (%s, %s, 'parsing') ON CONFLICT DO NOTHING",
        (doc_id, original_filename)
    )
    conn.commit()
    cur.close()

    # Kích hoạt băm
    json_headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"document_ids": [doc_id]}
    is_success = False

    async with httpx.AsyncClient(timeout=10) as client:
        url = f"{BASE_V1}/datasets/{DATASET_ID}/chunks"
        try:
            res_run = await client.post(url, headers=json_headers, json=payload)
            if res_run.status_code == 200 and res_run.json().get("code") == 0:
                is_success = True
        except Exception:
            pass

    return {
        "message": "Upload và kích hoạt băm thành công!" if is_success else "Upload thành công, nhưng lệnh băm bị nghẽn.",
        "doc_id": doc_id,
        "file_name": original_filename,
        "auto_parsed": is_success
    }

# 3. DANH SÁCH FILE
@app.get("/files")
async def list_files(conn = Depends(get_db)):
    API_KEY, DATASET_ID, _, _, BASE_V1, _ = get_ragflow_config(conn, mode="text")
    headers = {"Authorization": f"Bearer {API_KEY}"}
    ragflow_docs = {}
    sync_success = False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(f"{BASE_V1}/datasets/{DATASET_ID}/documents", headers=headers)
        if res.status_code == 200:
            docs_list = res.json().get("data", [])
            if isinstance(docs_list, dict):
                docs_list = docs_list.get("docs", [])
            
            for doc in docs_list:
                doc_id = str(doc.get("id"))
                file_name = doc.get("name", "")
                run_status = str(doc.get("run", "0"))
                progress_raw = float(doc.get("progress", 0.0))
                progress_percent = round(progress_raw * 100, 1)

                if run_status == "3" or progress_percent >= 100:
                    mapped_status = "success"
                    progress_percent = 100.0
                elif run_status == "4":
                    mapped_status = "fail"
                else:
                    mapped_status = "parsing"

                ragflow_docs[doc_id] = {
                    "name": file_name,
                    "status": mapped_status,
                    "progress": progress_percent,
                    "size": doc.get("size", 0),
                    "chunk_num": doc.get("chunk_count", doc.get("chunk_num", 0)),
                    "create_time": doc.get("create_time") or doc.get("create_date")
                }
            sync_success = True
    except Exception as e:
        print(f"Lỗi đồng bộ RAGFlow docs: {e}")

    cur = conn.cursor()
    cur.execute("SELECT doc_id, file_name, status FROM documents")
    rows = cur.fetchall()
    local_docs = {str(r[0]): {"file_name": r[1], "status": r[2]} for r in rows}
    files = []

    if sync_success:
        for local_doc_id in local_docs:
            if local_doc_id not in ragflow_docs:
                cur.execute("DELETE FROM documents WHERE doc_id = %s", (local_doc_id,))

        for doc_id, real_data in ragflow_docs.items():
            real_status = real_data["status"]
            real_progress = real_data["progress"]
            file_name = real_data["name"]

            if doc_id not in local_docs:
                cur.execute(
                    "INSERT INTO documents (doc_id, file_name, status) VALUES (%s, %s, %s)",
                    (doc_id, file_name, real_status)
                )
            else:
                if real_status != local_docs[doc_id]["status"] or file_name != local_docs[doc_id]["file_name"]:
                    cur.execute(
                        "UPDATE documents SET status = %s, file_name = %s WHERE doc_id = %s",
                        (real_status, file_name, doc_id)
                    )

            files.append({
                "doc_id": doc_id,
                "file_name": file_name,
                "status": real_status,
                "progress_percent": real_progress,
                "size": real_data.get("size", 0),
                "chunk_num": real_data.get("chunk_num", 0),
                "create_time": real_data.get("create_time")
            })
    else:
        for doc_id, data in local_docs.items():
            files.append({
                "doc_id": doc_id,
                "file_name": data["file_name"],
                "status": data["status"],
                "progress_percent": 0.0,
                "size": 0,
                "chunk_num": 0,
                "create_time": None
            })

    conn.commit()
    cur.close()
    return {"total": len(files), "files": files, "synced": sync_success}

# 4. CONTROL PARSE
@app.post("/parse")
async def control_parse(req: ParseActionRequest, conn = Depends(get_db)):
    doc_id = req.doc_id
    action = req.action

    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="action phải là 'start' hoặc 'stop'")

    API_KEY, DATASET_ID, _, _, BASE_V1, _ = get_ragflow_config(conn, mode="text")
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"document_ids": [doc_id]}
    url = f"{BASE_V1}/datasets/{DATASET_ID}/chunks"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if action == "start":
                res = await client.post(url, headers=headers, json=payload)
            else:
                res = await client.request("DELETE", url, headers=headers, json=payload)
                
            if res.status_code == 200 and res.json().get("code") == 0:
                return {"message": "OK", "action": action, "doc_id": doc_id}
            else:
                raise HTTPException(status_code=502, detail=f"RAGFlow trả về: {res.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Lỗi nối mạng RAGFlow: {str(e)}")

# 5. XÓA FILE
@app.delete("/files/{doc_id}")
async def delete_file(doc_id: str, conn = Depends(get_db)):
    API_KEY, DATASET_ID, _, _, BASE_V1, _ = get_ragflow_config(conn, mode="text")
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    delete_url = f"{BASE_V1}/datasets/{DATASET_ID}/documents"
    payload = {"ids": [doc_id]}
    ragflow_deleted = False
    ragflow_error = None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.request("DELETE", delete_url, headers=headers, json=payload)
        if res.status_code == 200 and res.json().get("code") == 0:
            ragflow_deleted = True
        else:
            ragflow_error = res.text
    except Exception as e:
        ragflow_error = str(e)

    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
        conn.commit()
        cur.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi DB local: {str(e)}")

    result = {"message": "Đã xóa file thành công!", "doc_id": doc_id}
    if not ragflow_deleted:
        result["warning"] = f"Đã xóa local, nhưng RAGFlow báo lỗi: {ragflow_error}"
    return result

# 8. HÀM TRỢ GIÚP PHÂN TÍCH THỊ GIÁC (VLM)
def _parse_json_object(raw_value: str) -> Optional[dict]:
    raw = str(raw_value or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(raw[start:end + 1])
                return value if isinstance(value, dict) else None
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
    return None


DIRECT_VLM_REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "observed_object",
        "current_condition",
        "technical_analysis",
        "conclusion",
        "recommendations",
    ],
    "properties": {
        "observed_object": {
            "type": "object",
            "additionalProperties": False,
            "required": ["component", "material", "visible_context"],
            "properties": {
                "component": {"type": "string", "minLength": 4},
                "material": {"type": "string", "minLength": 4},
                "visible_context": {"type": "string", "minLength": 30},
            },
        },
        "current_condition": {
            "type": "object",
            "additionalProperties": False,
            "required": ["overview", "visual_evidence"],
            "properties": {
                "overview": {"type": "string", "minLength": 240},
                "visual_evidence": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "defect_class",
                            "ai_validation",
                            "location",
                            "visual_characteristics",
                            "extent",
                            "engineering_significance",
                        ],
                        "properties": {
                            "defect_class": {"type": "string", "minLength": 3},
                            "ai_validation": {
                                "type": "string",
                                "enum": ["phù hợp", "nghi ngờ dương tính giả", "không đủ bằng chứng"],
                            },
                            "location": {"type": "string", "minLength": 25},
                            "visual_characteristics": {"type": "string", "minLength": 60},
                            "extent": {"type": "string", "minLength": 30},
                            "engineering_significance": {"type": "string", "minLength": 60},
                        },
                    },
                },
            },
        },
        "technical_analysis": {
            "type": "array",
            "minItems": 2,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "standard",
                    "applicable_scope",
                    "observed_evidence",
                    "assessment",
                    "limitation",
                ],
                "properties": {
                    "standard": {"type": "string", "minLength": 8},
                    "applicable_scope": {"type": "string", "minLength": 40},
                    "observed_evidence": {"type": "string", "minLength": 60},
                    "assessment": {"type": "string", "minLength": 100},
                    "limitation": {"type": "string", "minLength": 50},
                },
            },
        },
        "conclusion": {
            "type": "object",
            "additionalProperties": False,
            "required": ["condition_summary", "risk_screening", "required_confirmation"],
            "properties": {
                "condition_summary": {"type": "string", "minLength": 80},
                "risk_screening": {"type": "string", "minLength": 80},
                "required_confirmation": {"type": "string", "minLength": 80},
            },
        },
        "recommendations": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["priority", "action", "purpose", "method"],
                "properties": {
                    "priority": {"type": "string", "enum": ["Ưu tiên 1", "Ưu tiên 2", "Ưu tiên 3", "Theo dõi"]},
                    "action": {"type": "string", "minLength": 45},
                    "purpose": {"type": "string", "minLength": 35},
                    "method": {"type": "string", "minLength": 50},
                },
            },
        },
    },
}


def _direct_vlm_quality_errors(report: dict) -> List[str]:
    """Reject syntactically valid but operationally useless inspection reports."""
    errors: List[str] = []
    observed = report.get("observed_object") or {}
    condition = report.get("current_condition") or {}
    conclusion = report.get("conclusion") or {}
    findings = report.get("technical_analysis") or []
    recommendations = report.get("recommendations") or []

    if not isinstance(observed, dict) or len(str(observed.get("component") or "").strip()) < 4:
        errors.append("thiếu tên cấu kiện quan trắc")
    if not isinstance(condition, dict) or len(str(condition.get("overview") or "").strip()) < 240:
        errors.append("mô tả tổng quan dưới 240 ký tự")
    evidence = condition.get("visual_evidence") if isinstance(condition, dict) else []
    if not isinstance(evidence, list) or not evidence:
        errors.append("thiếu bằng chứng thị giác theo từng hư hỏng")
    else:
        for index, item in enumerate(evidence, start=1):
            if not isinstance(item, dict):
                errors.append(f"bằng chứng {index} sai cấu trúc")
                continue
            detail_length = sum(
                len(str(item.get(key) or "").strip())
                for key in ("location", "visual_characteristics", "extent", "engineering_significance")
            )
            if detail_length < 150:
                errors.append(f"bằng chứng {index} quá sơ sài")

    if not isinstance(findings, list) or len(findings) < 2:
        errors.append("thiếu phân tích tiêu chuẩn độc lập")
    else:
        for index, finding in enumerate(findings, start=1):
            if not isinstance(finding, dict):
                errors.append(f"phân tích tiêu chuẩn {index} sai cấu trúc")
                continue
            detail_length = sum(
                len(str(finding.get(key) or "").strip())
                for key in ("applicable_scope", "observed_evidence", "assessment", "limitation")
            )
            if detail_length < 220 or len(str(finding.get("assessment") or "").strip()) < 90:
                errors.append(f"phân tích tiêu chuẩn {index} quá ngắn")

    if not isinstance(conclusion, dict) or sum(
        len(str(conclusion.get(key) or "").strip())
        for key in ("condition_summary", "risk_screening", "required_confirmation")
    ) < 240:
        errors.append("kết luận chưa đủ căn cứ/rủi ro/xác minh")

    if not isinstance(recommendations, list) or len(recommendations) < 3:
        errors.append("cần ít nhất 3 kiến nghị có thứ tự ưu tiên")
    else:
        for index, item in enumerate(recommendations, start=1):
            if not isinstance(item, dict) or sum(
                len(str(item.get(key) or "").strip()) for key in ("action", "purpose", "method")
            ) < 120:
                errors.append(f"kiến nghị {index} quá sơ sài")
    return errors


async def get_direct_vlm_analysis(image_url: str, prompt: str) -> str:
    """Call the configured vision model directly, bypassing RAGFlow chat assistants."""
    provider = os.getenv("DIRECT_VLM_PROVIDER", "ollama").strip().lower()
    base_url = os.getenv("DIRECT_VLM_BASE_URL", "http://ollama:11434").strip().rstrip("/")
    model = os.getenv("DIRECT_VLM_MODEL", "qwen2.5vl:7b").strip()
    api_key = os.getenv("DIRECT_VLM_API_KEY", "").strip()

    if not image_url or not image_url.startswith("data:image/") or "," not in image_url:
        raise HTTPException(status_code=400, detail="Ảnh VLM phải là data URI hợp lệ")
    if not model:
        raise HTTPException(status_code=503, detail="DIRECT_VLM_MODEL chưa được cấu hình")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if provider == "ollama":
        image_b64 = image_url.split(",", 1)[1]
        endpoint = f"{base_url}/api/chat"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Bạn là kỹ sư kiểm định hạ tầng dùng mô hình thị giác. Quan sát trực tiếp ảnh, viết báo cáo kỹ thuật tiếng Việt theo bằng chứng, tuân thủ tuyệt đối JSON Schema và không suy diễn điều không nhìn thấy.",
                },
                {"role": "user", "content": prompt, "images": [image_b64]},
            ],
            "stream": False,
            "format": DIRECT_VLM_REPORT_SCHEMA,
            "keep_alive": -1,
            "options": {"temperature": 0.0, "num_ctx": 12288, "num_predict": 3600},
        }
    elif provider in {"openai", "openai_compatible"}:
        endpoint = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Bạn là kỹ sư kiểm định hạ tầng dùng mô hình thị giác. Quan sát trực tiếp ảnh, viết báo cáo kỹ thuật tiếng Việt theo bằng chứng, tuân thủ tuyệt đối JSON Schema và không suy diễn điều không nhìn thấy.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            "stream": False,
            "temperature": 0.0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "infrastructure_inspection_report", "strict": True, "schema": DIRECT_VLM_REPORT_SCHEMA},
            },
        }
    else:
        raise HTTPException(status_code=503, detail=f"DIRECT_VLM_PROVIDER không được hỗ trợ: {provider}")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(115.0, connect=10.0)) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"VLM trực tiếp quá thời gian xử lý: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Không kết nối được API VLM trực tiếp: {exc}") from exc

    if response.status_code != 200:
        detail = response.text[:600]
        if response.status_code == 404 and provider == "ollama":
            detail = f"Model '{model}' chưa có trên Ollama. Chạy: ollama pull {model}. Phản hồi: {detail}"
        raise HTTPException(status_code=502, detail=f"API VLM trực tiếp trả lỗi {response.status_code}: {detail}")

    try:
        response_json = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="API VLM trực tiếp trả dữ liệu không phải JSON") from exc
    if provider == "ollama":
        answer = (response_json.get("message") or {}).get("content", "")
    else:
        choices = response_json.get("choices") or []
        answer = ((choices[0].get("message") or {}).get("content", "") if choices else "")

    parsed = _parse_json_object(answer)
    required = {"observed_object", "current_condition", "technical_analysis", "conclusion", "recommendations"}
    if not parsed or not required.issubset(parsed):
        raise HTTPException(
            status_code=502,
            detail="VLM trực tiếp không trả đúng JSON báo cáo; kết quả đã bị từ chối và không được lưu",
        )
    quality_errors = _direct_vlm_quality_errors(parsed)
    if quality_errors:
        raise HTTPException(
            status_code=422,
            detail="Báo cáo VLM chưa đạt hợp đồng chất lượng: " + "; ".join(quality_errors[:8]),
        )
    return json.dumps(parsed, ensure_ascii=False)


@app.get("/api/v1/vlm/health")
async def direct_vlm_health():
    """Check direct vision-model configuration without sending an inference image."""
    provider = os.getenv("DIRECT_VLM_PROVIDER", "ollama").strip().lower()
    base_url = os.getenv("DIRECT_VLM_BASE_URL", "http://ollama:11434").strip().rstrip("/")
    model = os.getenv("DIRECT_VLM_MODEL", "qwen2.5vl:7b").strip()
    result = {"status": False, "provider": provider, "model": model, "base_url": base_url}

    if provider != "ollama":
        result.update({"status": bool(model and base_url), "note": "OpenAI-compatible endpoint is checked during inference"})
        return result

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            response = await client.get(f"{base_url}/api/tags")
        response.raise_for_status()
        models = [item.get("name", "") for item in response.json().get("models", [])]
        installed = model in models or any(name.split(":", 1)[0] == model.split(":", 1)[0] for name in models)
        result.update({"status": installed, "reachable": True, "installed": installed, "available_models": models})
    except Exception as exc:
        result.update({"reachable": False, "installed": False, "error": str(exc)[:300]})
    return result


async def get_vlm_visual_description(
    image_url: str,
    detections: Optional[List],
    conn,
    analysis_prompt: Optional[str] = None,
    include_detection_context: bool = True,
) -> str:
    """Gọi mô hình VLM Qwen 2.5-VL qua RAGFlow để phân tích hình ảnh và trả về mô tả trực quan."""
    try:
        api_key, _, chat_id, api_url, base_v1, _ = get_ragflow_config(conn, mode="vlm")
    except Exception as e:
        print(f"⚠️ Chưa cấu hình VLM trong settings, bỏ qua phân tích ảnh: {e}")
        return ""

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    
    # Automated inspection reports need the exact structured prompt to reach
    # the vision model. Previously this function discarded req.question and
    # the image model only received one generic sentence.
    vlm_prompt = (
        analysis_prompt.strip()
        if analysis_prompt and analysis_prompt.strip()
        else "Hãy phân tích hình ảnh khuyết tật công trình này."
    )
    
    # Kết hợp thông tin YOLO nếu có
    if detections and include_detection_context:
        det_lines = []
        for i, det in enumerate(detections):
            if isinstance(det, dict):
                cls_name = det.get("class") or det.get("cls_name") or "không xác định"
                conf = det.get("confidence") or det.get("conf") or 0.0
                bbox = det.get("polygon") or det.get("segmentation") or det.get("obb") or det.get("bbox") or []
            else:
                cls_name = getattr(det, "class", getattr(det, "cls_name", "không xác định"))
                conf = getattr(det, "confidence", getattr(det, "conf", 0.0))
                bbox = getattr(det, "polygon", None) or getattr(det, "segmentation", None) or getattr(det, "obb", None) or getattr(det, "bbox", [])
            try:
                conf_value = float(conf)
            except (TypeError, ValueError):
                conf_value = 0.0
            if conf_value > 1.0:
                conf_value /= 100.0
            bbox_str = f"[{', '.join(map(str, bbox))}]" if bbox else "N/A"
            det_lines.append(f"- YOLO phát hiện {i+1}: {cls_name} (Độ tin cậy: {conf_value:.2%}, Hình học ROI: {bbox_str})")
        vlm_prompt += "\n\nThông tin hỗ trợ phát hiện từ mô hình YOLO:\n" + "\n".join(det_lines)

    # Gọi RAGFlow bằng OpenAI endpoint hoặc native endpoint
    if "/chats_openai" in api_url or "/openai" in api_url:
        chat_endpoint = f"{base_v1}/chats_openai/{chat_id}/chat/completions"
        content_payload = [
            {"type": "text", "text": vlm_prompt},
            {"type": "image_url", "image_url": {"url": image_url}}
        ]
        payload = {
            "model": "ragflow",
            "messages": [{"role": "user", "content": content_payload}],
            "stream": False
        }
    else:
        chat_endpoint = f"{base_v1}/chats/{chat_id}/completions"
        payload = {
            "question": f"{vlm_prompt}\n[Hình ảnh phân tích]: {image_url}",
            "stream": False
        }

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            res = await client.post(chat_endpoint, headers=headers, json=payload)
        if res.status_code == 200:
            res_json = res.json()
            if "choices" in res_json and len(res_json["choices"]) > 0:
                return res_json["choices"][0].get("message", {}).get("content", "")
            elif "data" in res_json and isinstance(res_json["data"], dict) and "answer" in res_json["data"]:
                return res_json["data"].get("answer", "")
            elif "answer" in res_json:
                return res_json.get("answer", "")
            else:
                print(f"⚠️ Định dạng phản hồi VLM không xác định: {res_json}")
        else:
            print(f"⚠️ VLM API trả về lỗi {res.status_code}: {res.text}")
    except Exception as e:
        print(f"⚠️ Lỗi kết nối tới VLM RAGFlow: {e}")
    
    return ""

# 8. LUỒNG CHAT CHUNG (CHAT CORE ENGINE)
async def execute_ragflow_chat(req: BaseModel, conn, mode: str, db_question: Optional[str] = None, user_id: str = "default_user"):
    API_KEY, _, CHAT_ID, API_URL, BASE_V1, _ = get_ragflow_config(conn, mode=mode)
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    
    # 2. Xử lý Payload và Endpoint (OpenAI hoặc RAGFlow Native)
    if "/chats_openai" in API_URL or "/openai" in API_URL:
        chat_endpoint = f"{BASE_V1}/chats_openai/{CHAT_ID}/chat/completions"
        
        # Hỗ trợ payload Vision cho luồng VLM
        if mode == "vlm" and hasattr(req, "image_url") and req.image_url:
            text_prompt = req.question
            detections = getattr(req, "detections", None)
            if detections:
                detection_details = []
                for i, det in enumerate(detections):
                    if isinstance(det, dict):
                        cls_name = det.get("class") or det.get("cls_name") or "không xác định"
                        conf = det.get("confidence") or det.get("conf") or 0.0
                        bbox = det.get("bbox") or []
                    else:
                        cls_name = getattr(det, "class", getattr(det, "cls_name", "không xác định"))
                        conf = getattr(det, "confidence", getattr(det, "conf", 0.0))
                        bbox = getattr(det, "bbox", [])
                    
                    bbox_str = f"[{', '.join(map(str, bbox))}]" if bbox else "N/A"
                    detection_details.append(
                        f"- Hư hại {i+1}: Loại: '{cls_name}', Độ tin cậy: {conf:.2%}, Tọa độ Bounding Box [x_min, y_min, x_max, y_max]: {bbox_str}"
                    )
                
                det_text = "\n".join(detection_details)
                text_prompt += (
                    f"\n\n[Thông tin bổ trợ phân tích từ mô hình YOLO phát hiện vật thể]\n"
                    f"Mô hình YOLO đã phát hiện các vết nứt/hư hại sau trong hình ảnh:\n"
                    f"{det_text}\n"
                    f"Hãy kết hợp thông tin tọa độ bounding box ở trên và hình ảnh để tập trung phân tích chi tiết các vị trí hư hại này, đồng thời đánh giá tổng thể mức độ hư hại của công trình/mặt đường."
                )

            content_payload = [
                {"type": "text", "text": text_prompt},
                {"type": "image_url", "image_url": {"url": req.image_url}}
            ]
        else:
            content_payload = req.question

        payload = {
            "model": "ragflow",
            "messages": [{"role": "user", "content": content_payload}],
            "stream": req.stream
        }
    else:
        chat_endpoint = f"{BASE_V1}/chats/{CHAT_ID}/completions"
        text_prompt = req.question
        if mode == "vlm" and hasattr(req, "image_url") and req.image_url:
            text_prompt += f"\n[Hình ảnh phân tích]: {req.image_url}"
            detections = getattr(req, "detections", None)
            if detections:
                detection_details = []
                for i, det in enumerate(detections):
                    if isinstance(det, dict):
                        cls_name = det.get("class") or det.get("cls_name") or "không xác định"
                        conf = det.get("confidence") or det.get("conf") or 0.0
                        bbox = det.get("bbox") or []
                    else:
                        cls_name = getattr(det, "class", getattr(det, "cls_name", "không xác định"))
                        conf = getattr(det, "confidence", getattr(det, "conf", 0.0))
                        bbox = getattr(det, "bbox", [])
                    
                    bbox_str = f"[{', '.join(map(str, bbox))}]" if bbox else "N/A"
                    detection_details.append(
                        f"- Hư hại {i+1}: Loại: '{cls_name}', Độ tin cậy: {conf:.2%}, Tọa độ Bounding Box [x_min, y_min, x_max, y_max]: {bbox_str}"
                    )
                
                det_text = "\n".join(detection_details)
                text_prompt += (
                    f"\n\n[Thông tin bổ trợ phân tích từ mô hình YOLO phát hiện vật thể]\n"
                    f"Mô hình YOLO đã phát hiện các vết nứt/hư hại sau trong hình ảnh:\n"
                    f"{det_text}\n"
                    f"Hãy kết hợp thông tin tọa độ bounding box ở trên và hình ảnh để tập trung phân tích chi tiết các vị trí hư hại này, đồng thời đánh giá tổng thể mức độ hư hại của công trình/mặt đường."
                )

        payload = {
            "question": text_prompt,
            "session_id": req.session_id,
            "stream": req.stream
        }

    # BƯỚC A: Lưu câu hỏi User vào PostgreSQL
    session_id = req.session_id
    if not session_id or session_id == "default":
        raise HTTPException(status_code=400, detail="Cần tạo phiên chat trước khi gửi tin nhắn")
    cur = conn.cursor()
    
    # Chọn câu hỏi ghi nhận lịch sử (db_question nếu được cung cấp)
    q_to_save = db_question if db_question is not None else req.question
    
    title = q_to_save[:50] + "..." if len(q_to_save) > 50 else q_to_save
    cur.execute("""
        INSERT INTO chat_sessions (session_id, user_id, title)
        VALUES (%s, %s, %s)
        ON CONFLICT (session_id) DO NOTHING
    """, (session_id, user_id, title))
    cur.execute(
        "SELECT user_id FROM chat_sessions WHERE session_id = %s",
        (session_id,),
    )
    owner = cur.fetchone()
    if not owner or owner[0] != user_id:
        conn.rollback()
        cur.close()
        # Do not reveal whether the identifier belongs to another user.
        raise HTTPException(status_code=404, detail="Phiên chat không tồn tại")
    cur.execute("""
        UPDATE chat_sessions
        SET title = %s
        WHERE session_id = %s
          AND user_id = %s
          AND title IN ('Cuộc trò chuyện mới', 'New Chat')
    """, (title, session_id, user_id))
    
    cur.execute("""
        INSERT INTO chat_messages (session_id, role, content) 
        VALUES (%s, 'user', %s)
    """, (session_id, q_to_save))
    conn.commit()

    # BƯỚC B: Gọi RAGFlow
    if req.stream:
        from fastapi.responses import StreamingResponse
        import json
        
        async def stream_generator():
            nonlocal session_id, chat_endpoint, payload, headers
            try:
                is_healed = False
                accumulated_answer = ""
                
                async with httpx.AsyncClient(timeout=180.0) as client:
                    async with client.stream("POST", chat_endpoint, headers=headers, json=payload) as response:
                        if response.status_code == 200:
                            # Đọc dòng đầu tiên để check lỗi session
                            lines_iter = response.aiter_lines()
                            first_line = ""
                            async for l in lines_iter:
                                first_line = l
                                break
                                
                            if "don't own the session" in first_line or '"code":102' in first_line.replace(" ", "") or '"code": 102' in first_line:
                                is_healed = True
                            else:
                                if first_line.strip():
                                    if first_line.startswith("data:"):
                                        data_str = first_line[5:].strip()
                                        if data_str != "[DONE]":
                                            try:
                                                chunk_json = json.loads(data_str)
                                                ans_delta = ""
                                                if "choices" in chunk_json and len(chunk_json["choices"]) > 0:
                                                    ans_delta = chunk_json["choices"][0].get("delta", {}).get("content", "")
                                                    if ans_delta:
                                                        accumulated_answer = ans_delta
                                                elif "data" in chunk_json and isinstance(chunk_json["data"], dict) and "answer" in chunk_json["data"]:
                                                    full_ans = chunk_json["data"].get("answer", "")
                                                    ans_delta = full_ans
                                                    accumulated_answer = full_ans
                                                elif "answer" in chunk_json:
                                                    full_ans = chunk_json.get("answer", "")
                                                    ans_delta = full_ans
                                                    accumulated_answer = full_ans
                                                if ans_delta:
                                                    yield f"data: {json.dumps({'delta': ans_delta})}\n\n"
                                            except Exception:
                                                pass
                                
                                async for line in lines_iter:
                                    if not line.strip():
                                        continue
                                    if line.startswith("data:"):
                                        data_str = line[5:].strip()
                                        if data_str == "[DONE]":
                                            break
                                        try:
                                            chunk_json = json.loads(data_str)
                                            ans_delta = ""
                                            if "choices" in chunk_json and len(chunk_json["choices"]) > 0:
                                                ans_delta = chunk_json["choices"][0].get("delta", {}).get("content", "")
                                                if ans_delta:
                                                    accumulated_answer += ans_delta
                                            elif "data" in chunk_json and isinstance(chunk_json["data"], dict) and "answer" in chunk_json["data"]:
                                                full_ans = chunk_json["data"].get("answer", "")
                                                if len(full_ans) > len(accumulated_answer):
                                                    ans_delta = full_ans[len(accumulated_answer):]
                                                    accumulated_answer = full_ans
                                            elif "answer" in chunk_json:
                                                full_ans = chunk_json.get("answer", "")
                                                if len(full_ans) > len(accumulated_answer):
                                                    ans_delta = full_ans[len(accumulated_answer):]
                                                    accumulated_answer = full_ans
                                            if ans_delta:
                                                yield f"data: {json.dumps({'delta': ans_delta})}\n\n"
                                        except Exception:
                                            pass
                        else:
                            is_healed = True
                
                if is_healed:
                    print(f"🔄 [SESSION WORKAROUND-STREAM] Session {session_id} không hợp lệ. Tiến hành tự sửa...")
                    async with httpx.AsyncClient(timeout=15.0) as client_sess:
                        s_res = await client_sess.post(f"{BASE_V1}/chats/{CHAT_ID}/sessions", headers={"Authorization": f"Bearer {API_KEY}"})
                    if s_res.status_code == 200:
                        new_rag_id = s_res.json()["data"]["id"]
                        print(f"✅ [SESSION WORKAROUND-STREAM] Tạo phiên RAGFlow thành công: {new_rag_id}")
                        cur2 = conn.cursor()
                        cur2.execute("""
                            INSERT INTO chat_sessions (session_id, user_id, title)
                            SELECT %s, user_id, title FROM chat_sessions WHERE session_id = %s
                            ON CONFLICT (session_id) DO NOTHING
                        """, (new_rag_id, session_id))
                        cur2.execute("UPDATE chat_messages SET session_id = %s WHERE session_id = %s", (new_rag_id, session_id))
                        cur2.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))
                        conn.commit()
                        cur2.close()
                        
                        payload["session_id"] = new_rag_id
                        async with httpx.AsyncClient(timeout=180.0) as client_retry:
                            async with client_retry.stream("POST", chat_endpoint, headers=headers, json=payload) as response_retry:
                                async for line in response_retry.aiter_lines():
                                    if not line.strip():
                                        continue
                                    if line.startswith("data:"):
                                        data_str = line[5:].strip()
                                        if data_str == "[DONE]":
                                            break
                                        try:
                                            chunk_json = json.loads(data_str)
                                            ans_delta = ""
                                            if "choices" in chunk_json and len(chunk_json["choices"]) > 0:
                                                ans_delta = chunk_json["choices"][0].get("delta", {}).get("content", "")
                                                if ans_delta:
                                                    accumulated_answer += ans_delta
                                            elif "data" in chunk_json and isinstance(chunk_json["data"], dict) and "answer" in chunk_json["data"]:
                                                full_ans = chunk_json["data"].get("answer", "")
                                                if len(full_ans) > len(accumulated_answer):
                                                    ans_delta = full_ans[len(accumulated_answer):]
                                                    accumulated_answer = full_ans
                                            elif "answer" in chunk_json:
                                                full_ans = chunk_json.get("answer", "")
                                                if len(full_ans) > len(accumulated_answer):
                                                    ans_delta = full_ans[len(accumulated_answer):]
                                                    accumulated_answer = full_ans
                                            if ans_delta:
                                                yield f"data: {json.dumps({'delta': ans_delta})}\n\n"
                                        except Exception:
                                            pass
                
                if accumulated_answer:
                    cur_stream = conn.cursor()
                    cur_stream.execute("""
                        INSERT INTO chat_messages (session_id, role, content) 
                        VALUES (%s, 'assistant', %s)
                    """, (session_id, accumulated_answer))
                    conn.commit()
                    cur_stream.close()
                    
            except Exception as stream_err:
                yield f"data: {json.dumps({'error': str(stream_err)})}\n\n"
                
        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    ai_answer = ""
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            res = await client.post(chat_endpoint, headers=headers, json=payload)

        # ── SELF-HEALING: Tự động sửa lỗi Session không thuộc quyền sở hữu (Code 102) ──
        is_session_error = False
        try:
            res_data = res.json()
            if res_data.get("code") == 102 or "don't own the session" in str(res_data.get("message", "")):
                is_session_error = True
        except Exception:
            if "don't own the session" in res.text or "You don't own" in res.text:
                is_session_error = True

        if is_session_error:
            print(f"🔄 [SESSION WORKAROUND] Session {session_id} không hợp lệ trên RAGFlow. Tiến hành tự sửa lỗi tạo mới...")
            try:
                # Gọi RAGFlow để cấp phát phiên mới
                async with httpx.AsyncClient(timeout=15.0) as client_sess:
                    s_res = await client_sess.post(f"{BASE_V1}/chats/{CHAT_ID}/sessions", headers={"Authorization": f"Bearer {API_KEY}"})
                if s_res.status_code == 200:
                    new_rag_id = s_res.json()["data"]["id"]
                    print(f"✅ [SESSION WORKAROUND] Tạo phiên RAGFlow thành công: {new_rag_id}")
                    # Đồng bộ đổi ID trong PostgreSQL để tự sửa lỗi phiên cũ (Tránh lỗi khóa ngoại)
                    cur2 = conn.cursor()
                    cur2.execute("""
                        INSERT INTO chat_sessions (session_id, user_id, title)
                        SELECT %s, user_id, title FROM chat_sessions WHERE session_id = %s
                        ON CONFLICT (session_id) DO NOTHING
                    """, (new_rag_id, session_id))
                    cur2.execute("UPDATE chat_messages SET session_id = %s WHERE session_id = %s", (new_rag_id, session_id))
                    cur2.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))
                    conn.commit()
                    cur2.close()
                    
                    # Cập nhật tham số gửi đi và gọi lại
                    payload["session_id"] = new_rag_id
                    session_id = new_rag_id
                    async with httpx.AsyncClient(timeout=180) as client_retry:
                        res = await client_retry.post(chat_endpoint, headers=headers, json=payload)
            except Exception as workaround_err:
                print(f"⚠️ [SESSION WORKAROUND] Gặp lỗi trong quá trình tự sửa: {workaround_err}")

        if res.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={"error": "RAGFlow Engine Error", "upstream_status": res.status_code, "upstream_response": res.text}
            )

        res_json = res.json()
        if "choices" in res_json and len(res_json["choices"]) > 0:
            ai_answer = res_json["choices"][0].get("message", {}).get("content", "")
        elif "data" in res_json and isinstance(res_json["data"], dict) and "answer" in res_json["data"]:
            ai_answer = res_json["data"].get("answer", "")
        elif "answer" in res_json:
            ai_answer = res_json.get("answer", "")

        # Auto-clean RAGFlow default empty-document fallback text if present
        fallback_phrases = [
            "Dựa trên tài liệu hiện có trong hệ thống, tôi không tìm thấy thông tin chính xác để trả lời câu hỏi này.",
            "Dựa trên thông tin hiện có, tôi không tìm thấy câu trả lời phù hợp trong tài liệu."
        ]
        for phrase in fallback_phrases:
            if phrase in ai_answer:
                ai_answer = ai_answer.replace(phrase, "").strip()

        if ai_answer.startswith("---"):
            ai_answer = ai_answer.lstrip("-").strip()

        if not ai_answer:
            raise HTTPException(
                status_code=502,
                detail={"error": "AI trả về nội dung rỗng.", "raw": res_json}
            )

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Hệ thống AI xử lý vượt quá thời gian cho phép (180s)."
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Lỗi nối mạng tới AI: {str(e)}")

    # BƯỚC C: Lưu câu trả lời của AI vào PostgreSQL
    cur.execute("""
        INSERT INTO chat_messages (session_id, role, content) 
        VALUES (%s, 'assistant', %s)
    """, (session_id, ai_answer))
    conn.commit()
    cur.close()

    return {
        "session_id": session_id,
        "answer": ai_answer,
        "status": "success"
    }

# 9. ĐẦU CUỐI API CHATBOT

@app.post("/api/v1/chat/text")
async def chat_text(
    req: ChatRequest,
    user_id: str = Query(..., min_length=1),
    conn = Depends(get_db),
):
    """API Chat văn bản thuần (RAG tiêu chuẩn & Báo cáo)"""
    return await execute_ragflow_chat(req, conn, mode="text", user_id=user_id)

@app.post("/api/v1/chat/vlm")
async def chat_vlm(
    req: VlmChatRequest,
    user_id: str = Query(..., min_length=1),
    conn = Depends(get_db),
):
    """API Chat VLM (Kiến trúc lai Hybrid: VLM mô tả ➔ LLM RAG đối chiếu tiêu chuẩn)"""
    if req.image_url and req.direct_vlm:
        direct_answer = await get_direct_vlm_analysis(req.image_url, req.question or "")
        return {
            "session_id": req.session_id,
            "answer": direct_answer,
            "status": "success",
            "analysis_source": "direct_model_api",
        }

    if req.image_url:
        # Bước 1: Gọi VLM phân tích thị giác hình ảnh
        visual_desc = await get_vlm_visual_description(
            req.image_url,
            req.detections,
            conn,
            analysis_prompt=None,
            include_detection_context=True,
        )
        
        if visual_desc:
            # Bước 2: Chuẩn bị câu hỏi gộp gửi tới LLM RAG
            user_q = req.question if req.question and req.question.strip() else "Phân tích khuyết tật và đối chiếu TCVN."
            composite_prompt = (
                f"[BÁO CÁO PHÂN TÍCH THỊ GIÁC HIỆN TRƯỜNG TỪ MÔ HÌNH VLM]:\n{visual_desc}\n\n"
                f"Nhiệm vụ của bạn: Hãy phân tích kỹ báo cáo thị giác ở trên. Tìm kiếm trong tài liệu TCVN được "
                f"trích xuất từ Dataset để đưa ra câu trả lời chuẩn xác nhất kèm khuyến nghị kỹ thuật cho yêu cầu: {user_q}"
            )
            
            # Request mô phỏng gửi sang LLM RAG (chế độ "text")
            llm_req = ChatRequest(
                question=composite_prompt,
                session_id=req.session_id,
                stream=req.stream
            )
            
            # Ghi nhận lịch sử trò chuyện bằng câu hỏi gốc
            db_q = req.question if req.question and req.question.strip() else "Phân tích hình ảnh khuyết tật tự động"
            
            # Bước 3: Gọi RAG + LLM và lưu vào DB với câu hỏi gốc tinh gọn
            return await execute_ragflow_chat(
                llm_req,
                conn,
                mode="text",
                db_question=db_q,
                user_id=user_id,
            )

    # Fallback: Nếu không có ảnh hoặc VLM lỗi, chuyển hướng sang luồng RAG Văn bản thuần
    fallback_q = req.question if req.question and req.question.strip() else "Phân tích hình ảnh khuyết tật tự động"
    llm_req = ChatRequest(
        question=fallback_q,
        session_id=req.session_id,
        stream=req.stream
    )
    return await execute_ragflow_chat(llm_req, conn, mode="text", user_id=user_id)

# 10. QUẢN LÝ PHIÊN CHAT
@app.post("/sessions")
async def create_session(user_id: str = "default_user", conn = Depends(get_db)):
    try:
        api_key, _, chat_id, _, base_v1, _ = get_ragflow_config(conn, mode="text")
    except Exception as e:
        print(f"⚠️ RAGFlow config check failed for create_session, using local fallback: {e}")
        import uuid
        local_id = f"sess_{uuid.uuid4().hex[:8]}"
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO chat_sessions (session_id, user_id, title)
            VALUES (%s, %s, %s)
        """, (local_id, user_id, "Cuộc trò chuyện mới"))
        conn.commit()
        cur.close()
        return {"session_id": local_id, "status": "created"}

    # Call RAGFlow to create a session
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(f"{base_v1}/chats/{chat_id}/sessions", headers=headers)
        
        if res.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"RAGFlow error creating session [{res.status_code}]: {res.text}"
            )
        
        rag_session_id = res.json()["data"]["id"]
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO chat_sessions (session_id, user_id, title)
            VALUES (%s, %s, %s)
        """, (rag_session_id, user_id, "Cuộc trò chuyện mới"))
        conn.commit()
        cur.close()
        return {"session_id": rag_session_id, "status": "created"}
    except Exception as e:
        print(f"⚠️ RAGFlow session creation failed, using local fallback: {e}")
        import uuid
        local_id = f"sess_{uuid.uuid4().hex[:8]}"
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO chat_sessions (session_id, user_id, title)
            VALUES (%s, %s, %s)
        """, (local_id, user_id, "Cuộc trò chuyện mới"))
        conn.commit()
        cur.close()
        return {"session_id": local_id, "status": "created"}

@app.get("/sessions")
async def list_sessions(user_id: str = "default_user", conn = Depends(get_db)):
    cur = conn.cursor()
    cur.execute("""
        SELECT session_id, title, created_at 
        FROM chat_sessions 
        WHERE user_id = %s 
        ORDER BY created_at DESC
    """, (user_id,))
    rows = cur.fetchall()
    cur.close()

    sessions = []
    for r in rows:
        sessions.append({
            "session_id": r[0],
            "title": r[1],
            "created_at": r[2]
        })
    return {"sessions": sessions}

@app.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    user_id: str = Query(..., min_length=1),
    conn = Depends(get_db),
):
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM chat_sessions WHERE session_id = %s AND user_id = %s",
        (session_id, user_id),
    )
    if not cur.fetchone():
        cur.close()
        raise HTTPException(status_code=404, detail="Phiên chat không tồn tại")
    cur.execute("""
        SELECT role, content, created_at 
        FROM chat_messages 
        WHERE session_id = %s 
        ORDER BY created_at ASC
    """, (session_id,))
    rows = cur.fetchall()
    cur.close()
    
    if not rows:
        return {"session_id": session_id, "messages": []}

    messages = []
    for r in rows:
        messages.append({
            "role": r[0],
            "content": r[1],
            "created_at": r[2]
        })
    return {"session_id": session_id, "messages": messages}

@app.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user_id: str = Query(..., min_length=1),
    conn = Depends(get_db),
):
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM chat_sessions WHERE session_id = %s AND user_id = %s",
        (session_id, user_id),
    )
    deleted = cur.rowcount
    conn.commit()
    cur.close()

    if deleted == 0:
        raise HTTPException(status_code=404, detail="Phiên chat không tồn tại")
    
    return {"message": "Đã xóa phiên chat", "session_id": session_id}
