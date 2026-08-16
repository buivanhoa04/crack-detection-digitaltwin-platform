import cbam_module
import os
import json
import string
import cv2
import torch
import asyncio
import time
import logging
import threading
import supervision as sv
import numpy as np
from typing import Literal, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, WebSocket, WebSocketDisconnect, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

# ── TENSORRT 10.0+ COMPATIBILITY PATCH ──
# TensorRT 10/11 deprecated and removed NetworkDefinitionCreationFlag.EXPLICIT_BATCH and Builder.platform_has_fast_fp16/int8.
# We patch them here to prevent Ultralytics export failures.
try:
    import tensorrt as trt
    
    # 1. Patch NetworkDefinitionCreationFlag.EXPLICIT_BATCH
    if hasattr(trt, "NetworkDefinitionCreationFlag"):
        flag_class = trt.NetworkDefinitionCreationFlag
        if not hasattr(flag_class, "EXPLICIT_BATCH"):
            class Metaclass(type):
                def __getattr__(cls, name):
                    if name == "EXPLICIT_BATCH":
                        return 0
                    return getattr(flag_class, name)

            class PatchedNetworkDefinitionCreationFlag(metaclass=Metaclass):
                EXPLICIT_BATCH = 0

            trt.NetworkDefinitionCreationFlag = PatchedNetworkDefinitionCreationFlag
            logging.getLogger("crack_api").info("✅ Patched TensorRT NetworkDefinitionCreationFlag.EXPLICIT_BATCH for compatibility.")

    # 2. Patch Builder.platform_has_fast_fp16 and platform_has_fast_int8
    if hasattr(trt, "Builder"):
        original_builder = trt.Builder
        # Check if missing properties
        if not hasattr(original_builder, "platform_has_fast_fp16") or not hasattr(original_builder, "platform_has_fast_int8"):
            class PatchedBuilder(original_builder):
                @property
                def platform_has_fast_fp16(self):
                    return True
                @property
                def platform_has_fast_int8(self):
                    return True
            trt.Builder = PatchedBuilder
            logging.getLogger("crack_api").info("✅ Patched TensorRT Builder platform_has_fast_fp16/int8 for compatibility.")
except ImportError:
    pass
except Exception as patch_err:
    logging.getLogger("crack_api").warning(f"⚠️ Failed to patch TensorRT: {patch_err}")

import sys
import torch.nn as nn
from ultralytics import YOLO
import ultralytics.nn.modules as ultralytics_modules

class ChannelAttention(nn.Module):
    def __init__(self, channels, reduction=16):
        super().__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.max_pool = nn.AdaptiveMaxPool2d(1)
        reduction_channels = max(1, channels // reduction)
        self.fc = nn.Sequential(
            nn.Conv2d(channels, reduction_channels, 1, bias=False),
            nn.ReLU(),
            nn.Conv2d(reduction_channels, channels, 1, bias=False)
        )
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avg_out = self.fc(self.avg_pool(x))
        max_out = self.fc(self.max_pool(x))
        out = avg_out + max_out
        return self.sigmoid(out) * x

class SpatialAttention(nn.Module):
    def __init__(self, kernel_size=7):
        super().__init__()
        self.conv = nn.Conv2d(2, 1, kernel_size, padding=kernel_size//2, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avg_out = torch.mean(x, dim=1, keepdim=True)
        max_out, _ = torch.max(x, dim=1, keepdim=True)
        out = torch.cat([avg_out, max_out], dim=1)
        out = self.conv(out)
        return self.sigmoid(out) * x

class CBAM(nn.Module):
    def __init__(self, channels, reduction=16, kernel_size=7):
        super().__init__()
        self.channel_attention = ChannelAttention(channels, reduction)
        self.spatial_attention = SpatialAttention(kernel_size)

    def forward(self, x):
        x = self.channel_attention(x)
        x = self.spatial_attention(x)
        return x

for mod in [ultralytics_modules, sys.modules["__main__"]]:
    setattr(mod, "CBAM", CBAM)
    setattr(mod, "ChannelAttention", ChannelAttention)
    setattr(mod, "SpatialAttention", SpatialAttention)

from fastapi.middleware.cors import CORSMiddleware
# from vision_analyzer import VisionAnalyzer (loại bỏ local Vision LLM)
from preprocessing import preprocess_frame, GPUPreprocessor
# tiling.py đã được thay thế bằng thư viện SAHI chính thức (obss/sahi)
from segmentation import RoadSegmenter
from class_canonical_mapper import ClassMappingError, ModelClassRemapper
import httpx

# CẤU HÌNH LIÊN KẾT CHATBOT MIDDLEWARE
CHATBOT_MIDDLEWARE_URL = os.getenv("CHATBOT_MIDDLEWARE_URL", "http://host.docker.internal:8088")
CHATBOT_ENABLED = os.getenv("CHATBOT_ENABLED", "false").lower() == "true"
CHATBOT_API_TOKEN = os.getenv("CHATBOT_API_TOKEN", "")
if CHATBOT_ENABLED and not CHATBOT_API_TOKEN:
    raise RuntimeError("CHATBOT_API_TOKEN must be configured when CHATBOT_ENABLED=true")

# CẤU HÌNH LOGGING
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("crack_api")

# BIẾN MÔI TRƯỜNG HỆ THỐNG
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
os.environ["OPENCV_FFMPEG_READ_ATTEMPTS"] = "10000"  # Ngăn tình trạng Zombie Thread kẹt mãi mãi
os.environ["OPENCV_FFMPEG_NETWORK_TIMEOUT"] = "50000" # Timeout mạng 50s nếu nghẽn cổ chai
os.environ["TORCH_LOAD_WEIGHTS_ONLY"] = "False"

# CHỐNG TRUY CẬP TRÁI PHÉP: TOKEN & IP
API_TOKEN = os.getenv("API_TOKEN", "").strip()
_allowed_ips_env = os.getenv("ALLOWED_IPS", "")
ALLOWED_IPS = [ip.strip() for ip in _allowed_ips_env.split(",")] if _allowed_ips_env else []
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

# KẾT NỐI MONGODB (Consolidated with Central Backend)
MONGO_URL = os.getenv("MONGO_DETAILS", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db = client["digital_twin"]
tasks_collection = db["tasks"]
results_collection = db["crack_results"]
catalog_collection = db["defect_catalog"]

def wait_for_mongodb() -> bool:
    logger.info("Đang kết nối MongoDB...")
    for i in range(5):
        try:
            client.admin.command('ping')
            logger.info("✅ MongoDB Connected!")
            return True
        except Exception:
            logger.warning(f"Retry kết nối MongoDB ({i+1}/5)...")
            time.sleep(2)
    logger.error("❌ Không thể kết nối MongoDB sau 5 lần thử.")
    return False

# CẤU HÌNH LƯU TRỮ
ROOT_SOURCES_DIR = "/data/file/sources"
os.makedirs(ROOT_SOURCES_DIR, exist_ok=True)

# CẤU HÌNH MODEL AI
MODEL_CONFIGS = {
    "road": {
        "path": "weights/crack_road.pt",
        "imgsz": 640,
        # Test-set F1 peaks at 0.341 for this checkpoint. The previous 0.20
        # threshold caused large numbers of low-confidence false positives.
        "conf": 0.34,
        # Initial per-class operating points read from the validation F1
        # curves. The low global gate is kept below these values so SAHI does
        # not discard candidates before class-specific filtering.
        "class_conf_thresholds": {
            "nut_ca_sau": 0.35,
            "nut": 0.25,
            "o_ga/bong_bat": 0.52,
        },
        "max_batch_size": int(
            os.environ.get(
                "ROAD_MAX_BATCH_SIZE",
                os.environ.get("MAX_BATCH_SIZE", 2),
            )
        ),
        # Image/video colour normalization is explicitly selectable by callers.
        "preprocessing": True,
        "training_domain": "source_controlled",
        "color_normalization_supported": True,
        "color_normalization_default": False,
        "trt_enabled": True,
        "preprocess_mode_stream": "stream",
        "preprocess_mode_offline": "offline",
        "preprocess_mode_image": "batch",
        # Temporal Confidence Fusion
        "temporal_ema_alpha": 0.3,
        "min_persistence": 2,                      
        # Full-frame inspection by default. Set VIDEO_FRAME_SKIP=2/4 only
        # when an operator explicitly accepts lower temporal recall.
        "video_frame_skip": max(1, int(os.environ.get("VIDEO_FRAME_SKIP", "1"))),
        # Tiling (Image API & Video)
        "tiling_enabled": True,                    
        "tile_size": 640,                          
        "tile_overlap": 0.25,                       
        "tile_min_size_ratio": 1.0,                # Tự động tile khi ảnh lớn hơn 640px
        "wbf_iou": 0.5,                            
        # Road Segmentation Pre-filter
        "segmentation_enabled": False,              # Tắt lọc mặt đường mặc định để không che đè ảnh
        "segmentation_interval": 60,               
        "segmentation_model_id": "chribark/segformer-b3-finetuned-UAVid",
        "segmentation_left_bound": 0.0,            
        "segmentation_right_bound": 1.0,           
        "road_mask_min_overlap": 0.60,
    },
    "bridge": {
        "path": "weights/crack_bridge.pt",
        "imgsz": 640,
        "conf": 0.20,
        # Per-class operating points read from the saved bridge BoxF1 curve.
        # Values below the global 0.20 gate are deliberately clamped to 0.20
        # to avoid turning the known background false-positive problem into a
        # much larger one. Control Point remains excluded from operations.
        "class_conf_thresholds": {
            "Crack": 0.20,
            "Efflorescence_Leaching": 0.67,
            "Exposed Rebar": 0.20,
            "Spalling": 0.29,
            "Staining_Infiltration": 0.20,
            "Corrosion": 0.32,
            "Biological_Growth": 0.20,
            "Pothole Asphalt": 0.20,
            "Expansion Joint": 0.20,
            "Guardrail Damaged": 0.32,
            "Control Point": 0.20,
        },
        "max_batch_size": int(os.environ.get("BRIDGE_MAX_BATCH_SIZE", 1)),
        # The current bridge attention/segmentation graph cannot build a
        # reliable TensorRT engine within the RTX 3060 12GB budget. Keep the
        # original PT model for maximum quality and deterministic startup.
        "trt_enabled": (
            os.environ.get("BRIDGE_TRT_ENABLED", "false").lower() == "true"
        ),
        "preprocessing": False,                    # Giữ nguyên dải BGR/RGB gốc lúc training
        "preprocess_mode_stream": "stream",
        "preprocess_mode_offline": "offline",
        "preprocess_mode_image": "stream",
        "temporal_ema_alpha": 0.3,
        "min_persistence": 2,
        "video_frame_skip": max(1, int(os.environ.get("VIDEO_FRAME_SKIP", "1"))),
        "tiling_enabled": True,
        "tile_size": 640,
        "tile_overlap": 0.25,
        "tile_min_size_ratio": 1.0,                # Tự động tile khi ảnh lớn hơn 640px
        "wbf_iou": 0.5,
        "segmentation_enabled": False, 
    },
}
models: dict[str, YOLO] = {}
class_names: dict[str, dict[int, str]] = {}  # {model_type: {class_id: normalized_name}}
class_mappers: dict[str, ModelClassRemapper] = {}
EXCLUDED_CLASSES = {
    "road": {"road_manhole", "road_patched", "road_white_line_blur"},
    "bridge": {
        "bridge_patched",
        "Control Point",
        "Pothole Asphalt",
        "Biological_Growth",
    }
}
model_locks = {
    "road": asyncio.Lock(),
    "bridge": asyncio.Lock()
}
device_name = "cpu"

# HÀM TIỆN ÍCH
class LatestFrameGrabber:
    """Thread đọc frame liên tục từ stream để xóa bỏ delay (buffer lag) của OpenCV."""
    def __init__(self, src):
        self.cap = cv2.VideoCapture(src)
        self.ret = False
        self.frame = None
        self.running = False
        self.lock = threading.Lock()
        
        if self.cap.isOpened():
            self.ret, self.frame = self.cap.read()
            self.msec = self.cap.get(cv2.CAP_PROP_POS_MSEC) if self.ret else 0.0
            self.running = True
            self.thread = threading.Thread(target=self._update, daemon=True)
            self.thread.start()

    def _update(self):
        try:
            while self.running:
                ret, frame = self.cap.read()
                msec = self.cap.get(cv2.CAP_PROP_POS_MSEC) if ret else 0.0
                with self.lock:
                    self.ret = ret
                    self.msec = msec
                    if ret:
                        self.frame = frame
                    else:
                        self.running = False
                        break
        finally:
            self.cap.release()

    def read(self):
        with self.lock:
            return self.ret, self.frame

    def release(self):
        self.running = False
        # Do NOT call self.cap.release() directly here. 
        # If thread is stuck in read(), calling release() concurrently causes Segfault.
        # The `finally` block in _update() will safely release it when the thread exits.
        if hasattr(self, 'thread') and self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def isOpened(self):
        return self.cap.isOpened()
        
    def get(self, prop_id):
        if prop_id == cv2.CAP_PROP_POS_MSEC:
            with self.lock:
                return getattr(self, 'msec', 0.0)
        return 0.0

def get_file_url(absolute_path: str) -> str:
    """Chuyển đường dẫn tuyệt đối thành URL path tương đối với static mount /files/."""
    rel_path = os.path.relpath(absolute_path, ROOT_SOURCES_DIR)
    return "/files/" + rel_path.replace(os.sep, "/")

# Ký tự hợp lệ cho tên class: chữ thường, số, dấu cách, gạch dưới
_ALLOWED_CHARS = set(string.ascii_lowercase + string.digits + ' _')

STANDARD_CLASS_MAPPING = {
    "road": {
        "nut": "nut",
        "nut_ca_sau": "nut_ca_sau",
        "o_ga/bong_bat": "o_ga/bong_bat",
        "o_ga": "o_ga/bong_bat",
        "bong_bat": "o_ga/bong_bat",
    },
    "bridge": {
        "crack": "Crack",
        "efflorescence_leaching": "Efflorescence_Leaching",
        "exposed rebar": "Exposed Rebar",
        "exposed_rebar": "Exposed Rebar",
        "spalling": "Spalling",
        "staining_infiltration": "Staining_Infiltration",
        "corrosion": "Corrosion",
        "biological_growth": "Biological_Growth",
        "pothole asphalt": "Pothole Asphalt",
        "expansion joint": "Expansion Joint",
        "guardrail damaged": "Guardrail Damaged",
        "control point": None,
    }
}

def normalize_class_name(name: str, model_type: str) -> str:
    """
    Chuẩn hóa tên class theo bộ nhãn chuẩn hóa người dùng quy định:
    Road: nut, nut_ca_sau, o_ga/bong_bat
    Bridge: Crack, Efflorescence_Leaching, Exposed Rebar, Spalling, Staining_Infiltration, Corrosion, Expansion Joint, Guardrail Damaged
    """
    if not name:
        return None
        
    raw_key = name.strip()
    lower_key = raw_key.lower()
    
    mapping = STANDARD_CLASS_MAPPING.get(model_type, {})
    if raw_key in mapping:
        return mapping[raw_key]
    if lower_key in mapping:
        return mapping[lower_key]
        
    if model_type == "bridge":
        for std_name in ["Crack", "Efflorescence_Leaching", "Exposed Rebar", "Spalling", "Staining_Infiltration", "Corrosion", "Biological_Growth", "Pothole Asphalt", "Expansion Joint", "Guardrail Damaged"]:
            if lower_key == std_name.lower() or lower_key.replace("_", " ") == std_name.lower().replace("_", " "):
                return std_name
                
        # Trả về nguyên bản để tương thích với một model bridge mới có
        # thêm defect class thật. Training-only class đã được map về None.
        return raw_key

    return None


def normalized_model_names(
    raw_names: dict,
    model_type: str,
    *,
    include_excluded: bool = False,
) -> dict[int, str]:
    """Read model metadata while optionally retaining training-only raw IDs."""
    normalized: dict[int, str] = {}
    for class_id, raw_name in (raw_names or {}).items():
        class_name = normalize_class_name(str(raw_name), model_type)
        if class_name:
            normalized[int(class_id)] = class_name
        elif include_excluded:
            normalized[int(class_id)] = str(raw_name)
    return normalized


def get_class_mapper(model_type: str) -> ModelClassRemapper | None:
    """Return the validated mapper registered for the loaded source model."""
    return class_mappers.get(model_type)


def canonicalize_model_class(
    model_type: str,
    raw_class_id: int,
    raw_class_name: str | None = None,
) -> dict:
    """Preserve raw output and apply an approved model-fingerprinted mapping."""
    raw_id = int(raw_class_id)
    if raw_class_name is None:
        raw_class_name = class_names.get(model_type, {}).get(raw_id, f"class_{raw_id}")
    mapper = get_class_mapper(model_type)
    if mapper is None:
        return {
            "raw_class_id": raw_id,
            "raw_class_name": str(raw_class_name),
            "class_id": raw_id,
            "class_name": str(raw_class_name),
            "class_mapping_applied": False,
        }
    return mapper.remap_detection(raw_id, str(raw_class_name))


def load_validated_model_artifact(model_type: str, device: str):
    """Nạp model và chống dùng nhầm TensorRT/ONNX cache của file .pt khác.

    Engine là derived artifact và thường còn tồn tại sau khi thay best.pt.
    Nếu metadata class không khớp tuyệt đối, hệ thống fallback về .pt thay
    vì dùng class ID của engine với tên đọc từ một model khác.
    """
    base_config = MODEL_CONFIGS.get(model_type)
    if not base_config:
        raise RuntimeError(f"Unknown model type: {model_type}")

    default_pt = base_config.get("path", f"weights/crack_{model_type}.pt")
    candidates = [
        default_pt,
        f"weights/crack_{model_type}.pt",
        f"weights/{model_type}_crack.pt",
        f"weights/{model_type}.pt",
        "weights/best.pt"
    ]
    pt_path = None
    for cand in candidates:
        if os.path.exists(cand):
            pt_path = cand
            break

    if not pt_path or not os.path.exists(pt_path):
        raise FileNotFoundError(f"No valid model weight found for {model_type} in candidates: {candidates}")

    source_model = YOLO(pt_path)
    source_task = source_model.task
    class_mappers[model_type] = ModelClassRemapper(
        model_type=model_type,
        model_path=pt_path,
        raw_names=source_model.names,
    )
    logger.info(
        "CLASS_MAPPING type=%s info=%s",
        model_type,
        class_mappers[model_type].info(),
    )
    source_names = normalized_model_names(
        source_model.names, model_type, include_excluded=True
    )
    if not source_names:
        raise RuntimeError(
            f"{model_type} model has no supported classes; raw names={source_model.names}"
        )

    selected_path = pt_path
    disable_trt = os.environ.get("DISABLE_TRT", "false").lower() == "true"
    if (
        device != "cpu"
        and not disable_trt
        and base_config.get("trt_enabled", True)
    ):
        engine_path = pt_path.replace(".pt", ".engine")
        onnx_path = pt_path.replace(".pt", ".onnx")
        if os.path.exists(engine_path):
            selected_path = engine_path
        elif os.path.exists(onnx_path):
            selected_path = onnx_path

    if selected_path == pt_path:
        return source_model, source_names, selected_path, source_task

    runtime_model = YOLO(selected_path, task=source_task)
    runtime_names = normalized_model_names(
        runtime_model.names, model_type, include_excluded=True
    )
    if runtime_names != source_names:
        logger.error(
            "MODEL_METADATA_MISMATCH type=%s artifact=%s source_names=%s "
            "runtime_names=%s; falling back to %s",
            model_type,
            selected_path,
            source_names,
            runtime_names,
            pt_path,
        )
        del runtime_model
        return source_model, source_names, pt_path, source_task

    del source_model
    return runtime_model, source_names, selected_path, source_task

def get_save_dir(file_path: str, request_id: str, save_folder_path: str = None) -> str:
    """
    Tạo thư mục lưu kết quả cùng cấp với file nguồn.
    - Có save_folder_path: dùng thư mục do stream truyền vào (ưu tiên)
    - Cấu trúc mới (video nằm trong thư mục task_xxx): lưu vào snapshot/
    - Cấu trúc cũ (video nằm flat): lưu vào {request_id}/
    - Stream URL (rtsp://, http://) không có save_folder: fallback về ROOT_SOURCES_DIR/streams/{request_id}/
    """
    if save_folder_path:
        save_dir = os.path.join(save_folder_path, request_id)
    elif "://" in file_path:
        # RTSP/HTTP stream → không thể dùng os.path.dirname, fallback về thư mục mặc định
        save_dir = os.path.join(ROOT_SOURCES_DIR, "streams", request_id)
    else:
        source_dir = os.path.dirname(file_path)
        # Kiểm tra cấu trúc mới: video nằm trong thư mục trùng tên request_id
        # Ví dụ: sources/2026/05/28/road/task_xxxx/task_xxxx_video.mp4
        # → source_dir = .../task_xxxx → basename = task_xxxx = request_id
        parent_basename = os.path.basename(source_dir)
        if parent_basename == request_id:
            # Cấu trúc mới: ghi vào snapshot/ bên trong thư mục task
            save_dir = os.path.join(source_dir, "snapshot")
        else:
            # Cấu trúc cũ: tạo thư mục con theo request_id
            save_dir = os.path.join(source_dir, request_id)
    os.makedirs(save_dir, exist_ok=True)
    return save_dir

def compile_trt_engine_via_python(onnx_path, engine_path, imgsz, max_export_batch, use_int8=False):
    import tensorrt as trt
    logger.info(f"🚀 Đang compile TensorRT engine bằng Python API: {onnx_path} -> {engine_path} (opt batch=1, max batch={max_export_batch})")
    try:
        TRT_LOGGER = trt.Logger(trt.Logger.WARNING)
        builder = trt.Builder(TRT_LOGGER)
        config = builder.create_builder_config()
        
        # Giới hạn bộ nhớ workspace để tránh lỗi OOM. Mặc định 4GB cho RTX 3060+
        try:
            workspace_gb = int(os.getenv("TRT_WORKSPACE_GB", "4"))
            workspace_size = workspace_gb * 1024 * 1024 * 1024  # Mặc định 4 GB
            if hasattr(trt, "MemoryPoolType") and hasattr(trt.MemoryPoolType, "WORKSPACE"):
                config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace_size)
                logger.info(f"Đã giới hạn workspace memory pool thành {workspace_gb}GB.")
            elif hasattr(config, "max_workspace_size"):
                config.max_workspace_size = workspace_size
                logger.info(f"Đã giới hạn max_workspace_size thành {workspace_gb}GB.")
        except Exception as ws_err:
            logger.warning(f"Không thể giới hạn workspace memory: {ws_err}")
            
        # Cấu hình FP16 (Tương thích cả TensorRT < 10 và TensorRT >= 10)
        if builder.platform_has_fast_fp16:
            for flag_name in ["kFP16", "FP16"]:
                if hasattr(trt.BuilderFlag, flag_name):
                    config.set_flag(getattr(trt.BuilderFlag, flag_name))
                    logger.info(f"Đã set flag FP16 bằng BuilderFlag.{flag_name}")
                    break
            
        # Cấu hình INT8 nếu cần
        if use_int8:
            for flag_name in ["kINT8", "INT8"]:
                if hasattr(trt.BuilderFlag, flag_name):
                    config.set_flag(getattr(trt.BuilderFlag, flag_name))
                    logger.info(f"Đã set flag INT8 bằng BuilderFlag.{flag_name}")
                    break
        # Tạo profile tối ưu với batch size động
        profile = builder.create_optimization_profile()
        profile.set_shape("images", (1, 3, imgsz, imgsz), (1, 3, imgsz, imgsz), (max_export_batch, 3, imgsz, imgsz))
        config.add_optimization_profile(profile)
        
        # Đọc đồ thị mạng từ ONNX
        network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
        parser = trt.OnnxParser(network, TRT_LOGGER)
        with open(onnx_path, 'rb') as model_file:
            if not parser.parse(model_file.read()):
                for error in range(parser.num_errors):
                    logger.error(f"❌ ONNX parse error: {parser.get_error(error)}")
                return False
                
        # Biên dịch sang TensorRT engine
        serialized_engine = builder.build_serialized_network(network, config)
        if serialized_engine is None:
            logger.error("❌ Build serialized network thất bại!")
            return False
            
        with open(engine_path, 'wb') as f:
            f.write(serialized_engine)
            
        logger.info(f"✅ Biên dịch thành công TensorRT engine: {engine_path}")
        return True
    except Exception as e:
        logger.error(f"❌ Lỗi khi compile bằng TensorRT Python API: {e}", exc_info=True)
        return False

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle hook thay thế on_event('startup') đã deprecated."""
    global device_name, vision_analyzer, road_segmenter

    # Uvicorn/API must fail closed, while Celery may safely import shared model
    # loading helpers without exposing an HTTP service.
    if not API_TOKEN:
        raise RuntimeError("API_TOKEN must be configured")

    # 1. Luôn xác định hardware và nạp các model AI khi khởi động hệ thống
    device_name = "0" if torch.cuda.is_available() else "cpu"
    logger.info(f"Hardware: {'NVIDIA GPU (TensorRT)' if device_name == '0' else 'CPU (ONNX)'}")

    for m_type, config in MODEL_CONFIGS.items():
        pt_path = config["path"]
        engine_path = pt_path.replace(".pt", ".engine")
        onnx_path = pt_path.replace(".pt", ".onnx")

        try:
            if not os.path.exists(pt_path):
                logger.warning(f"⚠️ Không tìm thấy file weights: {pt_path}")
                continue

            # Bước 1: Đọc metadata class từ file .pt gốc
            logger.info(f"Đang đọc class names từ {pt_path}...")
            base_model = YOLO(pt_path)
            task = base_model.task
            extracted_names = normalized_model_names(
                base_model.names, m_type, include_excluded=True
            )
            class_mappers[m_type] = ModelClassRemapper(
                model_type=m_type,
                model_path=pt_path,
                raw_names=base_model.names,
            )
            config["_class_mapper"] = class_mappers[m_type]
            logger.info(
                "CLASS_MAPPING type=%s info=%s",
                m_type,
                class_mappers[m_type].info(),
            )

            # Bước 2: Export & nạp model theo phần cứng
            disable_trt = os.environ.get("DISABLE_TRT", "false").lower() == "true"
            if (
                device_name == "0"
                and not disable_trt
                and config.get("trt_enabled", True)
            ):
                try:
                    # File lock to prevent concurrent export/compile between API and Celery worker
                    lock_path = pt_path + ".lock"
                    
                    # Clean up stale locks (older than 10 seconds)
                    if os.path.exists(lock_path):
                        import time
                        if time.time() - os.path.getmtime(lock_path) > 10:
                            try: os.remove(lock_path)
                            except: pass
                            
                    # Wait if another container/process is currently exporting/compiling (max 5s timeout)
                    wait_count = 0
                    while os.path.exists(lock_path) and wait_count < 5:
                        logger.info(f"⏳ Tác vụ export/compile {m_type} đang được xử lý bởi tiến trình khác. Đang đợi...")
                        import time
                        time.sleep(1)
                        wait_count += 1
                        
                    if os.path.exists(lock_path):
                        try: os.remove(lock_path)
                        except: pass
                        
                    if not os.path.exists(engine_path):
                        # Acquire lock
                        with open(lock_path, "w") as lf:
                            lf.write("locked")
                        
                        try:
                            # SOTA: Tối ưu hoá quá trình build TensorRT
                            use_int8 = False
                            # CHÚ Ý: TensorRT build với dynamic batch = 24 tốn TỚI > 30GB System RAM! 
                            # Do đó, chỉ nên dùng max_export_batch = max_batch_size thực tế cấu hình (thường là 2-4)
                            max_export_batch = max(
                                1, config.get("max_batch_size", 1)
                            )
                            cal_data = f"weights/calibration_{m_type}.yaml"
                            try:
                                if torch.cuda.is_available():
                                    vram_bytes = torch.cuda.get_device_properties(0).total_memory
                                    vram_gb = vram_bytes / (1024 ** 3)
                                    if vram_gb < 6.0:
                                        logger.info(f"⚠️ VRAM thấp ({vram_gb:.1f}GB < 6GB).")
                                    if os.path.exists(cal_data):
                                        use_int8 = True
                            except Exception as vram_err:
                                logger.warning(f"Lỗi kiểm tra VRAM: {vram_err}")
                                    
                            if use_int8:
                                logger.info(f"📊 Tìm thấy calibration config → Đang export INT8 TensorRT cho {m_type} (max batch={max_export_batch})...")
                                base_model.export(
                                    format="engine",
                                    imgsz=config["imgsz"],
                                    dynamic=True,
                                    batch=max_export_batch,
                                    device=0,
                                    int8=True,
                                    data=cal_data,
                                    half=True,
                                    workspace=4
                                )
                            else:
                                logger.info(f"🚀 Đang export FP16 TensorRT cho {m_type} (max batch={max_export_batch}) qua Ultralytics (tối ưu nhất)...")
                                base_model.export(
                                    format="engine",
                                    imgsz=config["imgsz"],
                                    dynamic=True,
                                    batch=max_export_batch,
                                    device=0,
                                    half=True,
                                    workspace=4
                                )
                                # Native Ultralytics export tự động tạo file .engine tại engine_path
                                if not os.path.exists(engine_path):
                                    logger.warning("⚠️ Export qua Ultralytics thất bại. Fallback sang nạp mô hình PyTorch (.pt) gốc...")
                                    engine_path = pt_path
                        finally:
                            # Release lock
                            if os.path.exists(lock_path):
                                try: os.remove(lock_path)
                                except: pass
                    
                    # SOTA: Chúng ta chỉ export/compile trong startup lifespan để chuẩn bị sẵn sàng,
                    # nhưng KHÔNG giữ model trong VRAM để tránh tranh chấp tài nguyên giữa API container và Celery worker.
                    # Model sẽ được nạp lười (lazy-load) khi thực sự có yêu cầu suy diễn trực tiếp (như stream).
                    logger.info(f"Đang chạy kiểm tra nạp thử model: {engine_path}")
                    test_model = YOLO(engine_path, task=task)
                    
                    # Chạy thử (dry-run) để kiểm tra tính tương thích của TensorRT engine
                    import numpy as np
                    imgsz = config["imgsz"]
                    dummy_img = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
                    for _warmup in range(3):
                        test_model(dummy_img, imgsz=imgsz, verbose=False)
                    logger.info(f"✅ Warmup TensorRT engine {m_type} thành công với resolution {imgsz}x{imgsz} x3 runs.")
                    
                    # Giải phóng VRAM ngay lập tức sau khi warmup thành công
                    del test_model
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception as trt_err:
                    logger.warning(f"⚠️ Không thể khởi tạo thử TensorRT cho {m_type} ({trt_err}).")
            else:
                logger.info(f"Mô hình PyTorch (.pt) gốc đã sẵn sàng để nạp lười.")

            # Bước 3: Lưu tên class đã chuẩn hóa vào dict riêng (không phụ thuộc model.names)
            class_names[m_type] = extracted_names
            logger.info(f"✅ Nạp {m_type} thành công | Classes: {list(extracted_names.values())}")

            # Bước 4: Giải phóng RAM/VRAM của base_model .pt
            try:
                del base_model
            except (NameError, UnboundLocalError):
                pass
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        except ClassMappingError:
            logger.critical(
                "Refusing startup because an enabled class mapping is invalid",
                exc_info=True,
            )
            raise
        except Exception as e:
            logger.error(f"❌ Lỗi nạp model {m_type}: {e}", exc_info=True)

    # 2. Khởi tạo Vision Analyzer & Road Segmenter (Đã loại bỏ local Vision LLM, tự động sử dụng catalog)
    vision_analyzer = None

    try:
        seg_model_id = MODEL_CONFIGS["road"].get("segmentation_model_id", "chribark/segformer-b3-finetuned-UAVid")
        road_segmenter = RoadSegmenter(model_id=seg_model_id, device="cuda" if torch.cuda.is_available() else "cpu")
        if any(conf.get("segmentation_enabled", False) for conf in MODEL_CONFIGS.values()):
            road_segmenter.load_model()
    except Exception as seg_err:
        logger.error(f"❌ Lỗi khởi tạo Road Segmenter: {seg_err}")

    # 3. Kết nối MongoDB và seed catalog dữ liệu
    if wait_for_mongodb():
        try:
            seed_catalog()
        except Exception as seed_err:
            logger.error(f"❌ Lỗi seed catalog: {seed_err}")
    else:
        logger.error("⚠️ Ứng dụng khởi động KHÔNG có MongoDB — các API và lưu kết quả phân tích sẽ không hoạt động.")

    # Giải phóng bộ nhớ trong API container để nhường VRAM/RAM cho Celery worker
    models.clear()
    road_segmenter = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("🧹 Đã giải phóng bộ nhớ model trong API container để nhường RAM/VRAM cho Celery worker.")

    yield  # Ứng dụng chạy ở đây

# Shutdown cleanup (nếu cần)
    logger.info("Ứng dụng đang tắt...")

# Các global instance
vision_analyzer = None
road_segmenter: RoadSegmenter = None

app = FastAPI(title="AI Crack Detection System V2 — Vision LLM", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials="*" not in ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def check_access(request: Request, call_next):
    # Bỏ qua xác thực cho tài liệu API và route public (như /files cho ảnh)
    path = request.url.path
    if path in ["/docs", "/openapi.json", "/redoc"] or path.startswith("/files/"):
        return await call_next(request)

    client_ip = request.client.host if request.client else ""
    if ALLOWED_IPS and client_ip in ALLOWED_IPS:
        return await call_next(request)

    auth_header = request.headers.get("Authorization")
    token = request.headers.get("X-API-Token") or request.query_params.get("token") or ""
    
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]

    if token == API_TOKEN:
        return await call_next(request)

    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Unauthorized: Invalid or missing token/IP"}
    )


app.mount("/files", StaticFiles(directory=ROOT_SOURCES_DIR), name="files")

# ================= MÔ HÌNH DỮ LIỆU ĐẦU VÀO =================

VALID_MODEL_TYPES = Literal["road", "bridge"]

class DetectRequest(BaseModel):
    FilePath: str
    RequestId: str
    ModelType: VALID_MODEL_TYPES
    segmentation_enabled: Optional[bool] = None
    color_normalization_enabled: Optional[bool] = None


class DetectBatchRequest(BaseModel):
    Requests: list[DetectRequest]




# ============================================================
# GLOBAL MOTION COMPENSATION (GMC) FOR TRACKING
# ============================================================

class GlobalMotionCompensator:
    """
    Tính toán chuyển động camera tích lũy và chuyển đổi giữa hệ tọa độ 
    khung hình hiện tại và hệ tọa độ tĩnh toàn cục (Frame 0).
    """
    def __init__(self, nfeatures: int = 500):
        self.orb = cv2.ORB_create(nfeatures=nfeatures)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        self.prev_gray = None
        self.prev_kp = None
        self.prev_des = None
        # Ma trận biến đổi ngược tích lũy (3x3): maps frame t -> frame 0
        self.H_inv_cum = np.eye(3, dtype=np.float32)
        # Ma trận biến đổi thuận tích lũy (3x3): maps frame 0 -> frame t
        self.H_cum = np.eye(3, dtype=np.float32)
        self.lock = threading.Lock()
        self.last_translation = (0.0, 0.0)

    def reset(self):
        with self.lock:
            self.prev_gray = None
            self.prev_kp = None
            self.prev_des = None
            self.H_inv_cum = np.eye(3, dtype=np.float32)
            self.H_cum = np.eye(3, dtype=np.float32)
            self.last_translation = (0.0, 0.0)

    def update(self, frame: np.ndarray):
        """Tính toán và tích lũy ma trận dịch chuyển cho frame mới."""
        if frame is None:
            return
        
        with self.lock:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            kp, des = self.orb.detectAndCompute(gray, None)
            
            if self.prev_gray is None or self.prev_des is None or des is None or len(kp) < 10:
                self.prev_gray = gray
                self.prev_kp = kp
                self.prev_des = des
                self.last_translation = (0.0, 0.0)
                return
            
            # Match các đặc trưng giữa frame t-1 và frame t
            matches = self.matcher.match(self.prev_des, des)
            if len(matches) < 10:
                self.prev_gray = gray
                self.prev_kp = kp
                self.prev_des = des
                self.last_translation = (0.0, 0.0)
                return
                
            # Lấy tọa độ các điểm khớp
            src_pts = np.float32([self.prev_kp[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
            dst_pts = np.float32([kp[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
            
            # Tính ma trận Affine (2x3) từ t-1 -> t bằng RANSAC
            M, inliers = cv2.estimateAffinePartial2D(src_pts, dst_pts, method=cv2.RANSAC, ransacReprojThreshold=5.0)
            
            if M is not None:
                self.last_translation = (float(M[0, 2]), float(M[1, 2]))
                # Tạo ma trận đồng nhất 3x3 cho dịch chuyển t-1 -> t
                H_t = np.eye(3, dtype=np.float32)
                H_t[:2, :3] = M
                
                # Tính ma trận dịch chuyển ngược t -> t-1
                M_inv = cv2.invertAffineTransform(M)
                H_inv_t = np.eye(3, dtype=np.float32)
                H_inv_t[:2, :3] = M_inv
                
                # Tích lũy ma trận:
                # H_cum_t = H_t * H_cum_t-1  ==> maps 0 -> t
                self.H_cum = np.dot(H_t, self.H_cum)
                # H_inv_cum_t = H_inv_cum_t-1 * H_inv_t ==> maps t -> 0
                self.H_inv_cum = np.dot(self.H_inv_cum, H_inv_t)
            
            self.prev_gray = gray
            self.prev_kp = kp
            self.prev_des = des

    def to_global_coords(self, bbox: list) -> list:
        """Chuyển bounding box từ frame hiện tại về hệ tọa độ Frame 0."""
        with self.lock:
            return self._warp_bbox(bbox, self.H_inv_cum[:2, :3])

    def to_local_coords(self, bbox: list) -> list:
        """Chuyển bounding box từ hệ tọa độ Frame 0 về frame hiện tại."""
        with self.lock:
            return self._warp_bbox(bbox, self.H_cum[:2, :3])

    def _warp_bbox(self, bbox: list, M: np.ndarray) -> list:
        """Dịch chuyển 4 góc của bbox bằng ma trận Affine M và trả về axis-aligned bbox mới."""
        x1, y1, x2, y2 = bbox
        corners = np.float32([[x1, y1], [x2, y1], [x2, y2], [x1, y2]]).reshape(-1, 1, 2)
        warped = cv2.transform(corners, M).reshape(-1, 2)
        wx1 = float(np.min(warped[:, 0]))
        wy1 = float(np.min(warped[:, 1]))
        wx2 = float(np.max(warped[:, 0]))
        wy2 = float(np.max(warped[:, 1]))
        return [wx1, wy1, wx2, wy2]


# ================= SOTA VIDEO TRANSCODING HELPER =================

def transcode_video_web_faststart(video_path: str):
    """
    SOTA Video Transcoding:
    Converts video to H.264 Web FastStart (CRF 23, fast preset) and generates HLS playlist (.m3u8).
    Enables instant playback < 0.1s over Web/Tailscale.
    """
    if not os.path.exists(video_path):
        return
    
    save_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    faststart_path = os.path.join(save_dir, f"{base_name}_faststart.mp4")
    hls_m3u8_path = os.path.join(save_dir, "stream.m3u8")
    
    import subprocess
    try:
        logger.info(f"⚡ [SOTA Video] Đang nén Web FastStart & tạo HLS stream cho {base_name}...")
        # 1. FastStart MP4 (CRF 23)
        cmd_faststart = [
            "ffmpeg", "-y", "-i", video_path,
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-movflags", "+faststart", "-c:a", "aac", "-b:a", "128k",
            faststart_path
        ]
        subprocess.run(cmd_faststart, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if os.path.exists(faststart_path) and os.path.getsize(faststart_path) > 0:
            os.replace(faststart_path, video_path)
            logger.info(f"✅ [SOTA Video] Đã tối ưu Web FastStart thành công: {video_path}")
            
        # 2. HLS Playlist (.m3u8 & .ts)
        cmd_hls = [
            "ffmpeg", "-y", "-i", video_path,
            "-c:v", "copy", "-c:a", "copy",
            "-hls_time", "2", "-hls_playlist_type", "vod",
            "-hls_segment_filename", os.path.join(save_dir, "segment_%03d.ts"),
            hls_m3u8_path
        ]
        subprocess.run(cmd_hls, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(hls_m3u8_path):
            logger.info(f"🚀 [SOTA Video] Tạo HLS Stream .m3u8 thành công: {hls_m3u8_path}")

    except Exception as e:
        logger.warning(f"⚠️ [SOTA Video] Lỗi transcode video: {e}")


# ================= XỬ LÝ VIDEO OFFLINE =================

def process_ai_offline(
    req_id: str,
    file_path: str,
    model_type: str,
    segmentation_enabled: bool = None,
    color_normalization_enabled: bool = False,
):
    """Background task xử lý video AI offline với ByteTrack + Preprocessing + Temporal Fusion."""
    # Kiểm tra xem task đã bị người dùng xóa trước khi xử lý hay chưa
    res = tasks_collection.update_one(
        {"$or": [{"_id": req_id}, {"task_id": req_id}]},
        {
            "$set": {
                "processingStatus": "đang xử lý",
                "color_normalization_enabled": (
                    model_type == "road" and bool(color_normalization_enabled)
                ),
            }
        },
    )
    if res.matched_count == 0:
        logger.warning(f"Task {req_id} has been deleted before starting. Aborting offline processing.")
        return

    global device_name, models, road_segmenter, class_names
    
    if device_name == "cpu" and torch.cuda.is_available():
        device_name = "0"

    local_device = device_name

    base_config = MODEL_CONFIGS.get(model_type)
    if not base_config:
        tasks_collection.update_one({"$or": [{"_id": req_id}, {"task_id": req_id}]}, {"$set": {"processingStatus": "lỗi", "ErrorCode": "MODEL_NOT_FOUND"}})
        return
    # Per-request copy: never mutate the process-global model configuration.
    config = dict(base_config)
    effective_segmentation = (
        model_type == "road"
        and (
            bool(base_config.get("segmentation_enabled", False))
            if segmentation_enabled is None
            else bool(segmentation_enabled)
        )
    )
    config["segmentation_enabled"] = effective_segmentation
    # Shared image/video option; each request explicitly selects it.
    config["color_normalization_enabled"] = (
        model_type == "road" and bool(color_normalization_enabled)
    )
    config["_excluded_classes"] = EXCLUDED_CLASSES.get(model_type, set())

    # Khởi động nạp model động nếu chưa có sẵn (chạy trong Celery worker)
    if model_type not in models:
        try:
            model, names, target_path, model_task = load_validated_model_artifact(
                model_type, local_device
            )
            logger.info(
                "Offline worker loading validated model %s from %s (task=%s)...",
                model_type,
                target_path,
                model_task,
            )
            models[model_type] = model
            class_names[model_type] = names
            config["_runtime_model_path"] = target_path
            config["_runtime_task"] = model_task
            config["_class_mapper"] = get_class_mapper(model_type)
        except Exception as err:
            logger.error(f"Lỗi nạp model trong offline worker: {err}")

    # Đảm bảo class_names của model_type được nạp đầy đủ
    if model_type not in class_names:
        model = models.get(model_type)
        if model and hasattr(model, 'names'):
            class_names[model_type] = normalized_model_names(
                model.names, model_type, include_excluded=True
            )
            logger.info(f"✅ Loaded class names for {model_type} in offline worker: {class_names[model_type]}")

    # Nạp Road Segmenter nếu chưa có sẵn
    if effective_segmentation and (not road_segmenter or not road_segmenter.is_loaded):
        try:
            from segmentation import RoadSegmenter
            seg_model_id = config.get("segmentation_model_id", "chribark/segformer-b3-finetuned-UAVid")
            road_segmenter = RoadSegmenter(model_id=seg_model_id, device="cuda" if torch.cuda.is_available() else "cpu")
            if not road_segmenter.load_model():
                raise RuntimeError("Road segmentation model could not be loaded")
            logger.info("✅ Road Segmenter loaded successfully in offline worker.")
        except Exception as e:
            logger.error(f"❌ Failed to load RoadSegmenter in offline worker: {e}")
            tasks_collection.update_one(
                {"$or": [{"_id": req_id}, {"task_id": req_id}]},
                {"$set": {"processingStatus": "lỗi", "ErrorCode": "SEGMENTATION_MODEL_UNAVAILABLE"}},
            )
            return

    model = models.get(model_type)
    if not model:
        return

def map_analysis_compatibility(final_analysis: dict, catalog_entry: dict = None) -> dict:
    """Đồng bộ ngược các khóa của analysis để tương thích với Frontend và Backend cũ."""
    if not final_analysis or not isinstance(final_analysis, dict):
        return final_analysis
    
    import re

    def clean_text_prefixes(text: str) -> str:
        if not isinstance(text, str):
            return ""
        text = re.sub(r'^(Luận điểm|Luận điểm dẫn chiếu|Kiến nghị|Kiến nghị cụ thể)\s*(dẫn chiếu)?\s*\d+:\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^(Point|Argument|Recommendation)\s*\d+:\s*', '', text, flags=re.IGNORECASE)
        return text.strip()

    def ensure_string(val) -> str:
        if val is None:
            return ""
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            return ", ".join([ensure_string(item) for item in val if item is not None])
        if isinstance(val, dict):
            parts = []
            for k, v in val.items():
                parts.append(f"{k}: {ensure_string(v)}")
            return " | ".join(parts)
        return str(val)

    def ensure_string_list(val) -> list:
        if val is None:
            return []
        if isinstance(val, list):
            return [ensure_string(item) for item in val if item is not None]
        return [ensure_string(val)]

    # 1. Map visual_assessment <=> current_status_details / description
    if "visual_assessment" in final_analysis and final_analysis["visual_assessment"]:
        val_str = clean_text_prefixes(ensure_string(final_analysis["visual_assessment"]))
        final_analysis["visual_assessment"] = val_str
        final_analysis["description"] = val_str
        final_analysis["current_status_details"] = val_str
    elif "current_status_details" in final_analysis:
        val_str = clean_text_prefixes(ensure_string(final_analysis["current_status_details"]))
        final_analysis["description"] = val_str
        final_analysis["visual_assessment"] = val_str

    # 2. Map possible_causes <=> causes / technical_analysis.causes
    if "possible_causes" in final_analysis and final_analysis["possible_causes"]:
        causes_list = ensure_string_list(final_analysis["possible_causes"])
        final_analysis["causes"] = causes_list
        final_analysis["probable_causes"] = causes_list
        if "technical_analysis" not in final_analysis or not isinstance(final_analysis["technical_analysis"], dict):
            final_analysis["technical_analysis"] = {}
        final_analysis["technical_analysis"]["causes"] = causes_list
    elif "causes" in final_analysis:
        causes_list = ensure_string_list(final_analysis["causes"])
        final_analysis["causes"] = causes_list
        final_analysis["probable_causes"] = causes_list
        if "technical_analysis" not in final_analysis or not isinstance(final_analysis["technical_analysis"], dict):
            final_analysis["technical_analysis"] = {}
        final_analysis["technical_analysis"]["causes"] = causes_list

    # 3. Map tcvn_reference <=> tcvn_references / technical_analysis.tcvn_references
    if "tcvn_reference" in final_analysis and final_analysis["tcvn_reference"]:
        tcvn_list = ensure_string_list(final_analysis["tcvn_reference"])
        final_analysis["tcvn_references"] = tcvn_list
        if "technical_analysis" not in final_analysis or not isinstance(final_analysis["technical_analysis"], dict):
            final_analysis["technical_analysis"] = {}
        final_analysis["technical_analysis"]["tcvn_references"] = tcvn_list

    # 4. Map recommendations <=> recommendations_to_contractor / recommended_actions
    if "recommendations" in final_analysis and final_analysis["recommendations"]:
        recs_list = ensure_string_list(final_analysis["recommendations"])
        final_analysis["recommendations"] = recs_list
        final_analysis["recommendations_to_contractor"] = recs_list
        final_analysis["recommended_actions"] = recs_list
    elif "recommendations_to_contractor" in final_analysis:
        recs_list = ensure_string_list(final_analysis["recommendations_to_contractor"])
        final_analysis["recommendations"] = recs_list
        final_analysis["recommended_actions"] = recs_list

    # 5. Map conclusion_and_repair_plan <=> structural_impact
    if "structural_impact" in final_analysis and final_analysis["structural_impact"]:
        val_str = ensure_string(final_analysis["structural_impact"])
        final_analysis["structural_impact"] = val_str
        final_analysis["conclusion_and_repair_plan"] = val_str
    elif "conclusion_and_repair_plan" in final_analysis:
        val_str = ensure_string(final_analysis["conclusion_and_repair_plan"])
        final_analysis["conclusion_and_repair_plan"] = val_str
        final_analysis["structural_impact"] = val_str

    final_analysis["analysis_source"] = "vision_llm" if "visual_assessment" in final_analysis else "catalog"
    return final_analysis


    # 3. Map conclusion_and_repair_plan <=> structural_impact
    if "conclusion_and_repair_plan" in final_analysis:
        final_analysis["conclusion_and_repair_plan"] = ensure_string(final_analysis["conclusion_and_repair_plan"])
        final_analysis["structural_impact"] = final_analysis["conclusion_and_repair_plan"]
    elif "structural_impact" in final_analysis:
        final_analysis["structural_impact"] = ensure_string(final_analysis["structural_impact"])
        final_analysis["conclusion_and_repair_plan"] = final_analysis["structural_impact"]

    # 4. Map recommendations_to_contractor <=> recommended_actions
    if "recommendations_to_contractor" in final_analysis:
        final_analysis["recommended_actions"] = final_analysis["recommendations_to_contractor"]
    elif "recommended_actions" in final_analysis:
        final_analysis["recommendations_to_contractor"] = final_analysis["recommended_actions"]

    return final_analysis

# ================= API ENDPOINTS =================

@app.post("/api/v1/detect/video")
async def detect_video(req: DetectRequest):
    existing = tasks_collection.find_one({"$or": [{"_id": req.RequestId}, {"task_id": req.RequestId}]})
    if existing:
        proc_status = existing.get("processingStatus")
        if proc_status in ["chờ xử lý", "đang xử lý"]:
            return {
                "status": True,
                "task_id": req.RequestId,
                "processingStatus": proc_status,
                "idempotent_replay": True,
            }
        if proc_status == "xử lý xong":
            return {
                "status": True,
                "task_id": req.RequestId,
                "processingStatus": proc_status,
                "already_completed": True,
            }
        if proc_status in ["lỗi", None]:
            claimed = tasks_collection.update_one(
                {"_id": existing["_id"], "processingStatus": proc_status},
                {"$set": {"processingStatus": "chờ xử lý", "ErrorCode": None, "datas": [], "progress": "0%", "started_at_epoch": time.time(), "elapsed_seconds": 0, "eta_seconds": 0, "processed_count": 0, "total_count": 1}}
            )
            if claimed.modified_count != 1:
                return {"status": True, "task_id": req.RequestId, "idempotent_replay": True}
        else:
            return {"status": False, "ErrorCode": "DUPLICATE_ID"}
    else:
        try:
            tasks_collection.insert_one({
                "_id": req.RequestId,
                "processingStatus": "chờ xử lý",
                "datas": [],
                "ErrorCode": None,
                "progress": "0%",
                "started_at_epoch": time.time(),
                "elapsed_seconds": 0,
                "eta_seconds": 0,
                "processed_count": 0,
                "total_count": 1
            })
        except DuplicateKeyError:
            return {"status": False, "ErrorCode": "DUPLICATE_ID"}

    from celery_tasks import process_video_offline_task
    process_video_offline_task.delay(
        req.RequestId,
        req.FilePath,
        req.ModelType,
        req.segmentation_enabled,
        False
        if req.color_normalization_enabled is None
        else req.color_normalization_enabled,
    )
    return {"status": True}

@app.post("/api/v1/detect/images-batch")
async def detect_images_batch(req: DetectBatchRequest):
    """Queue bounded image groups for quality-equivalent batched SAHI."""
    if not req.Requests:
        return {"status": False, "ErrorCode": "EMPTY_BATCH"}
    if len(req.Requests) > 64:
        return JSONResponse(
            status_code=413,
            content={"status": False, "ErrorCode": "BATCH_TOO_LARGE"},
        )

    model_types = {item.ModelType for item in req.Requests}
    segmentation_values = {item.segmentation_enabled for item in req.Requests}
    color_normalization_values = {
        False if item.color_normalization_enabled is None else bool(item.color_normalization_enabled)
        for item in req.Requests
    }
    if (
        len(model_types) != 1
        or len(segmentation_values) != 1
        or len(color_normalization_values) != 1
    ):
        return JSONResponse(
            status_code=400,
            content={"status": False, "ErrorCode": "MIXED_BATCH_CONFIG"},
        )

    queued_jobs = []
    replay_count = 0
    for item in req.Requests:
        existing = tasks_collection.find_one(
            {"$or": [{"_id": item.RequestId}, {"task_id": item.RequestId}]}
        )
        if existing:
            processing_status = existing.get("processingStatus")
            if processing_status in ["chờ xử lý", "đang xử lý", "xử lý xong"]:
                replay_count += 1
                continue
            claimed = tasks_collection.update_one(
                {
                    "_id": existing["_id"],
                    "processingStatus": processing_status,
                },
                {
                    "$set": {
                        "processingStatus": "chờ xử lý",
                        "ErrorCode": None,
                        "datas": [],
                        "progress": "0%",
                        "started_at_epoch": time.time(),
                        "elapsed_seconds": 0,
                        "eta_seconds": 0,
                        "processed_count": 0,
                        "total_count": 1,
                    }
                },
            )
            if claimed.modified_count != 1:
                replay_count += 1
                continue
        else:
            try:
                tasks_collection.insert_one(
                    {
                        "_id": item.RequestId,
                        "processingStatus": "chờ xử lý",
                        "datas": [],
                        "ErrorCode": None,
                        "progress": "0%",
                        "started_at_epoch": time.time(),
                        "elapsed_seconds": 0,
                        "eta_seconds": 0,
                        "processed_count": 0,
                        "total_count": 1,
                    }
                )
            except DuplicateKeyError:
                replay_count += 1
                continue

        queued_jobs.append(
            {
                "request_id": item.RequestId,
                "file_path": item.FilePath,
            }
        )

    if queued_jobs:
        from celery_tasks import process_cascade_image_batch_task

        process_cascade_image_batch_task.delay(
            queued_jobs,
            next(iter(model_types)),
            next(iter(segmentation_values)),
            next(iter(color_normalization_values)),
        )
    return {
        "status": True,
        "queued": len(queued_jobs),
        "idempotent_replays": replay_count,
    }


@app.post("/api/v1/detect/image")
async def detect_image(req: DetectRequest):
    existing = tasks_collection.find_one({"$or": [{"_id": req.RequestId}, {"task_id": req.RequestId}]})
    if existing:
        proc_status = existing.get("processingStatus")
        if proc_status in ["chờ xử lý", "đang xử lý"]:
            return {
                "status": True,
                "task_id": req.RequestId,
                "processingStatus": proc_status,
                "idempotent_replay": True,
            }
        if proc_status == "xử lý xong":
            return {
                "status": True,
                "task_id": req.RequestId,
                "processingStatus": proc_status,
                "already_completed": True,
            }
        if proc_status in ["lỗi", None]:
            claimed = tasks_collection.update_one(
                {"_id": existing["_id"], "processingStatus": proc_status},
                {"$set": {"processingStatus": "chờ xử lý", "ErrorCode": None, "datas": [], "progress": "0%", "started_at_epoch": time.time(), "elapsed_seconds": 0, "eta_seconds": 0, "processed_count": 0, "total_count": 1}}
            )
            if claimed.modified_count != 1:
                return {"status": True, "task_id": req.RequestId, "idempotent_replay": True}
        else:
            return {"status": False, "ErrorCode": "DUPLICATE_ID"}
    else:
        try:
            tasks_collection.insert_one({
                "_id": req.RequestId,
                "processingStatus": "chờ xử lý",
                "datas": [],
                "ErrorCode": None,
                "progress": "0%",
                "started_at_epoch": time.time(),
                "elapsed_seconds": 0,
                "eta_seconds": 0,
                "processed_count": 0,
                "total_count": 1
            })
        except DuplicateKeyError:
            return {"status": False, "ErrorCode": "DUPLICATE_ID"}

    from celery_tasks import process_cascade_image_task
    process_cascade_image_task.delay(
        req.RequestId,
        req.FilePath,
        req.ModelType,
        req.segmentation_enabled,
        False
        if req.color_normalization_enabled is None
        else bool(req.color_normalization_enabled),
    )
    return {"status": True, "task_id": req.RequestId}


@app.get("/api/v1/status/{request_id}")
async def get_status(request_id: str):
    task = tasks_collection.find_one({"$or": [{"_id": request_id}, {"task_id": request_id}]})
    if not task:
        return {"status": False, "ErrorCode": "NOT_FOUND"}
        
    task.pop("_id", None)
    
    # Query mảng kết quả crack_results
    images = list(results_collection.find({"task_id": request_id}, {"_id": 0, "task_id": 0}))
    
    # Reconstruct trả về dạng chuẩn JSON list theo đúng file format cũ
    if len(images) > 0:
        source_path = task.get("sourceFilePath", "")
        if not source_path and len(task.get("datas", [])) > 0:
            source_path = task["datas"][0].get("sourceFilePath", "")
        task["datas"] = [{"sourceFilePath": source_path, "images": images}]
        
    return {"status": True, "data": task}

# WEBSOCKET STREAM
@app.websocket("/api/v1/ws/stream")
async def websocket_stream(
    websocket: WebSocket,
    file_path: str,
    model_type: str,
    request_id: str = "LIVE_STREAM",
    save_folder_path: str = None,
    color_normalization_enabled: bool = False,
):
    global road_segmenter, models, device_name, class_names
    # Xác thực Token & IP thủ công cho WebSocket (vì BaseHTTPMiddleware bỏ qua scope websocket)
    client_ip = websocket.client.host if websocket.client else ""
    token = websocket.query_params.get("token") or websocket.headers.get("X-API-Token") or ""
    auth_header = websocket.headers.get("Authorization")
    
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]

    # Kiểm tra IP/Token
    is_authorized = False
    if ALLOWED_IPS and client_ip in ALLOWED_IPS:
        is_authorized = True
    elif token == API_TOKEN:
        is_authorized = True

    if not is_authorized:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # Kiểm tra trùng ID
    try:
        tasks_collection.insert_one({
            "_id": request_id,
            "processingStatus": "đang stream",
            "datas": [],
            "ErrorCode": None
        })
    except DuplicateKeyError:
        await websocket.send_json({"status": False, "ErrorCode": "DUPLICATE_ID"})
        await websocket.close()
        return

    # Never mutate the process-global model configuration for one live request.
    config = dict(MODEL_CONFIGS.get(model_type) or {})
    if not config:
        await websocket.send_json({"status": False, "ErrorCode": "MODEL_NOT_FOUND"})
        tasks_collection.update_one({"_id": request_id}, {"$set": {"processingStatus": "lỗi", "ErrorCode": "MODEL_NOT_FOUND"}})
        await websocket.close()
        return
    config["_excluded_classes"] = EXCLUDED_CLASSES.get(model_type, set())

    model = models.get(model_type)
    if not model:
        try:
            model, names, target_path, model_task = load_validated_model_artifact(
                model_type, device_name
            )
            logger.info(
                "Lazy loading validated model %s for stream WS from %s (task=%s)...",
                model_type,
                target_path,
                model_task,
            )
            models[model_type] = model
            class_names[model_type] = names
            config["_runtime_model_path"] = target_path
            config["_runtime_task"] = model_task
            config["_class_mapper"] = get_class_mapper(model_type)
        except Exception as err:
            logger.error(f"Lỗi nạp model trong stream WS: {err}")
            await websocket.send_json({"status": False, "ErrorCode": "MODEL_NOT_FOUND"})
            tasks_collection.update_one({"_id": request_id}, {"$set": {"processingStatus": "lỗi", "ErrorCode": "MODEL_NOT_FOUND"}})
            await websocket.close()
            return

    # Chỉ kiểm tra file tồn tại với đường dẫn local, bỏ qua stream URL (rtsp://, http://)
    is_stream = "://" in file_path
    if not is_stream and not os.path.exists(file_path):
        await websocket.send_json({"status": False, "ErrorCode": "FILE_NOT_FOUND"})
        tasks_collection.update_one({"_id": request_id}, {"$set": {"processingStatus": "lỗi", "ErrorCode": "FILE_NOT_FOUND"}})
        await websocket.close()
        return

    save_dir = get_save_dir(file_path, request_id, save_folder_path)
    best_tracks_conf = {} # Tránh RAM leak
    
    # Dùng LatestFrameGrabber để tối ưu ultra-low latency cho Stream (tránh lag với go2rtc)
    is_stream = "://" in file_path
    if is_stream:
        cap = LatestFrameGrabber(file_path)
    else:
        cap = cv2.VideoCapture(file_path)

    if not cap.isOpened():
        await websocket.send_json({"status": False, "ErrorCode": "CANNOT_OPEN_VIDEO"})
        tasks_collection.update_one({"_id": request_id}, {"$set": {"processingStatus": "lỗi", "ErrorCode": "CANNOT_OPEN_VIDEO"}})
        await websocket.close()
        return

    # Sử dụng tracking tích hợp BoT-SORT trong model YOLO
    tracker = None
    
    frame_idx = 0
    last_db_write = 0.0  # Throttle: ghi MongoDB tasks status
    DB_WRITE_INTERVAL = 2.0
    
    tasks_collection.update_one({"_id": request_id}, {"$set": {"sourceFilePath": file_path}})

    # Temporal Fusion state cho stream
    track_ema = {}
    track_persistence = {}
    ema_alpha = config.get("temporal_ema_alpha", 0.3)
    min_persist = config.get("min_persistence", 3)
    from inference_engine import UnifiedSOTAInferenceEngine
    stream_preprocessor = UnifiedSOTAInferenceEngine(config)

    # Khởi tạo Global Motion Compensator và Tracking State cho stream
    gmc = GlobalMotionCompensator()
    cached_mask = None
    segmentation_interval = config.get("segmentation_interval", 10)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # ── 1. GLOBAL MOTION COMPENSATION UPDATE ──
            gmc.update(frame)

            # ── 2. PREPROCESSING PIPELINE (stream mode = ultra-fast) ──
            infer_frame = stream_preprocessor.preprocess_frame(
                frame,
                model_type,
                color_normalization_enabled=(
                    model_type == "road" and bool(color_normalization_enabled)
                ),
            )

            # ── 3. ROAD SEGMENTATION & CACHING (No full blackout to preserve YOLO inputs) ──
            road_poly = []
            if config.get("segmentation_enabled", False):
                if not road_segmenter:
                    try:
                        from segmentation import RoadSegmenter
                        seg_model_id = config.get("segmentation_model_id", "chribark/segformer-b3-finetuned-UAVid")
                        road_segmenter = RoadSegmenter(model_id=seg_model_id, device="cuda" if torch.cuda.is_available() else "cpu")
                        road_segmenter.load_model()
                        logger.info("✅ Lazy loaded RoadSegmenter for stream WS.")
                    except Exception as e:
                        logger.error(f"Lỗi nạp RoadSegmenter trong stream WS: {e}")
                if road_segmenter:
                    if frame_idx % segmentation_interval == 0 or cached_mask is None:
                        left_ratio = config.get("segmentation_left_bound", 0.0)
                        right_ratio = config.get("segmentation_right_bound", 1.0)
                        cached_mask = road_segmenter.get_road_mask(frame, left_ratio, right_ratio)
                
                if cached_mask is not None:
                    try:
                        contours, _ = cv2.findContours(cached_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        if contours:
                            largest_contour = max(contours, key=cv2.contourArea)
                            epsilon = 0.005 * cv2.arcLength(largest_contour, True)
                            approx_poly = cv2.approxPolyDP(largest_contour, epsilon, True)
                            pts = approx_poly.squeeze().tolist()
                            if len(pts) > 0 and isinstance(pts[0], list):
                                road_poly = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in pts]
                            elif len(pts) > 0 and len(pts) == 2 and isinstance(pts[0], (int, float)):
                                road_poly = [[round(float(pts[0]), 2), round(float(pts[1]), 2)]]
                    except Exception:
                        pass

            class_thresholds = config.get("class_conf_thresholds", {})
            stream_gate = min(
                [float(config["conf"])]
                + [float(v) for v in class_thresholds.values()]
            ) if class_thresholds else float(config["conf"])
            results_list = await asyncio.to_thread(
                model.track, infer_frame, imgsz=config["imgsz"], conf=stream_gate, persist=True, tracker="botsort_drone_sota.yaml", verbose=False, device=device_name
            )
            results = results_list[0]
            
            # Road is an OBB model. Ultralytics stores its predictions in
            # results.obb (not results.boxes/results.masks). Build the
            # supervision object explicitly so live inference keeps the
            # rotated four-corner polygon and tracker ids.
            obb = getattr(results, "obb", None)
            is_obb = model_type == "road" or config.get("_runtime_task") == "obb"
            if is_obb and obb is not None:
                original_boxes = obb.xyxy.detach().cpu().numpy().tolist()
                original_polygons = obb.xyxyxyxy.detach().cpu().numpy().tolist()
                obb_conf = obb.conf.detach().cpu().numpy()
                obb_cls = obb.cls.detach().cpu().numpy().astype(int)
                obb_ids = getattr(obb, "id", None)
                tracker_ids = (
                    obb_ids.detach().cpu().numpy().astype(int)
                    if obb_ids is not None
                    else None
                )
                detections = sv.Detections(
                    xyxy=np.asarray(original_boxes, dtype=np.float32),
                    confidence=np.asarray(obb_conf, dtype=np.float32),
                    class_id=np.asarray(obb_cls, dtype=int),
                    tracker_id=tracker_ids,
                )
                masks_xy = []
            else:
                original_boxes = results.boxes.xyxy.tolist() if (hasattr(results, 'boxes') and results.boxes is not None) else []
                original_polygons = []
                masks_xy = results.masks.xy if (hasattr(results, 'masks') and results.masks is not None) else []
                detections = sv.Detections.from_ultralytics(results)

            # Lọc các class bị loại trừ
            if len(detections) > 0:
                excluded_ids = set()
                if model_type in class_names:
                    for cid, raw_name in class_names[model_type].items():
                        mapped_name = canonicalize_model_class(
                            model_type, cid, raw_name
                        )["class_name"]
                        if mapped_name in EXCLUDED_CLASSES.get(model_type, {}):
                            excluded_ids.add(cid)
                
                if excluded_ids:
                    keep_mask = ~np.isin(detections.class_id, list(excluded_ids))
                    detections = detections[keep_mask]
                    
                    keep_indices = np.where(keep_mask)[0]
                    original_boxes = [original_boxes[idx] for idx in keep_indices]
                    if original_polygons:
                        original_polygons = [original_polygons[idx] for idx in keep_indices]
                    if isinstance(masks_xy, list):
                        masks_xy = [masks_xy[idx] for idx in keep_indices]
                    elif isinstance(masks_xy, np.ndarray):
                        masks_xy = masks_xy[keep_indices]

            # ── 4. PROJECT DETECTIONS TO GLOBAL COORDINATES ──
            if len(detections) > 0:
                detections_global_xyxy = []
                for box in detections.xyxy:
                    bbox_global = gmc.to_global_coords(box.tolist())
                    detections_global_xyxy.append(bbox_global)
                detections.xyxy = np.array(detections_global_xyxy, dtype=np.float32)

            # ── 4. UPDATE TRACKER ──
            tracked_detections = detections

            # Apply class-specific operating points after OBB extraction.
            # The model gate above remains the minimum threshold so lower
            # class thresholds are still recoverable.
            if len(tracked_detections) > 0 and class_thresholds:
                keep = []
                for idx, cid in enumerate(tracked_detections.class_id):
                    raw_name = class_names.get(model_type, {}).get(int(cid), str(int(cid)))
                    threshold = float(class_thresholds.get(raw_name, config["conf"]))
                    keep.append(float(tracked_detections.confidence[idx]) >= threshold)
                keep_mask = np.asarray(keep, dtype=bool)
                tracked_detections = tracked_detections[keep_mask]
                keep_indices = np.where(keep_mask)[0]
                original_boxes = [original_boxes[i] for i in keep_indices]
                if original_polygons:
                    original_polygons = [original_polygons[i] for i in keep_indices]
                if isinstance(masks_xy, list):
                    masks_xy = [masks_xy[i] for i in keep_indices]
                elif isinstance(masks_xy, np.ndarray):
                    masks_xy = masks_xy[keep_indices]

            current_detections = []
            db_update_needed = False
            current_frame_path = None
            current_track_ids = set()

            msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            timestamp_str = f"{int(msec / 60000):02d}:{int((msec / 1000) % 60):02d}" if msec > 0 else time.strftime("%H:%M:%S")

            for i in range(len(tracked_detections)):
                t_id = -1
                if hasattr(tracked_detections, 'tracker_id') and tracked_detections.tracker_id is not None:
                    t_id = int(tracked_detections.tracker_id[i])
                raw_conf = round(float(tracked_detections.confidence[i]), 4)
                raw_class_id = int(tracked_detections.class_id[i])
                raw_class_name = class_names.get(model_type, {}).get(raw_class_id)
                if not raw_class_name:
                    # Ignore training-only/unsupported classes such as Control Point.
                    continue
                mapped_class = canonicalize_model_class(
                    model_type, raw_class_id, raw_class_name
                )
                class_id = mapped_class["class_id"]
                class_name = mapped_class["class_name"]
                
                # ── 6. PROJECT BBOX BACK TO LOCAL SPACE ──
                bbox_global = tracked_detections.xyxy[i].tolist()
                raw_bbox = gmc.to_local_coords(bbox_global)
                h_max, w_max = frame.shape[:2]
                x1 = max(0.0, min(float(w_max), raw_bbox[0]))
                y1 = max(0.0, min(float(h_max), raw_bbox[1]))
                x2 = max(0.0, min(float(w_max), raw_bbox[2]))
                y2 = max(0.0, min(float(h_max), raw_bbox[3]))
                bbox = [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)]

                # ── 7. BỘ LỌC TỌA ĐỘ TRONG LÒNG ĐƯỜNG (SPATIAL FILTERING VIA OVERLAP RATIO) ──
                if config.get("segmentation_enabled", False) and road_segmenter and cached_mask is not None:
                    x1, y1, x2, y2 = map(int, [bbox[0], bbox[1], bbox[2], bbox[3]])
                    h_max, w_max = cached_mask.shape[:2]
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(w_max, x2), min(h_max, y2)
                    
                    if x2 > x1 and y2 > y1:
                        roi_mask = cached_mask[y1:y2, x1:x2]
                        overlap_ratio = np.mean(roi_mask > 0)
                        min_overlap = float(config.get("road_mask_min_overlap", 0.60))
                        if overlap_ratio < min_overlap:
                            continue
                    else:
                        continue

                current_track_ids.add(t_id)

                # ── TEMPORAL EMA ──
                track_ema[t_id] = (1.0 - ema_alpha) * track_ema.get(t_id, raw_conf) + ema_alpha * raw_conf
                track_persistence[t_id] = track_persistence.get(t_id, 0) + 1
                ema_conf = round(track_ema[t_id], 4)

                # Trích xuất Polygon tương ứng cho tracked object thông qua IoU (so sánh trong local space!)
                polygon = None
                matched_raw_box = None
                if (original_polygons or masks_xy) and original_boxes:
                    best_idx = -1
                    best_iou = -1.0
                    for idx, obox in enumerate(original_boxes):
                        x1 = max(bbox[0], obox[0])
                        y1 = max(bbox[1], obox[1])
                        x2 = min(bbox[2], obox[2])
                        y2 = min(bbox[3], obox[3])
                        
                        if x2 <= x1 or y2 <= y1:
                            iou = 0.0
                        else:
                            intersection = (x2 - x1) * (y2 - y1)
                            area1 = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                            area2 = (obox[2] - obox[0]) * (obox[3] - obox[1])
                            union = area1 + area2 - intersection
                            iou = intersection / union if union > 0 else 0.0
                            
                        if iou > best_iou:
                            best_iou = iou
                            best_idx = idx
                            
                    if best_iou > 0.05:
                        matched_raw_box = original_boxes[best_idx]
                        polygon_source = original_polygons or masks_xy
                        if best_idx < len(polygon_source):
                            poly = polygon_source[best_idx]
                            if len(poly) > 0:
                                polygon = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in poly]
                    else:
                        # Fallback: Match by center distance when IoU is low (Kalman filter drift / fast motion)
                        best_dist = float('inf')
                        best_dist_idx = -1
                        c_bbox = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
                        bbox_diag = max(50.0, ((bbox[2] - bbox[0]) ** 2 + (bbox[3] - bbox[1]) ** 2) ** 0.5)
                        for idx, obox in enumerate(original_boxes):
                            c_obox = [(obox[0] + obox[2]) / 2, (obox[1] + obox[3]) / 2]
                            dist = ((c_bbox[0] - c_obox[0]) ** 2 + (c_bbox[1] - c_obox[1]) ** 2) ** 0.5
                            if dist < best_dist and dist < bbox_diag * 3.0:
                                best_dist = dist
                                best_dist_idx = idx
                        if best_dist_idx != -1:
                            matched_raw_box = original_boxes[best_dist_idx]
                            polygon_source = original_polygons or masks_xy
                            if best_dist_idx < len(polygon_source):
                                poly = polygon_source[best_dist_idx]
                                if len(poly) > 0:
                                    polygon = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in poly]

                # Detection-First Bbox alignment: Use raw YOLO prediction box which hugs the crack perfectly when matched
                if matched_raw_box is not None:
                    rx1 = max(0.0, min(float(w_max), matched_raw_box[0]))
                    ry1 = max(0.0, min(float(h_max), matched_raw_box[1]))
                    rx2 = max(0.0, min(float(w_max), matched_raw_box[2]))
                    ry2 = max(0.0, min(float(h_max), matched_raw_box[3]))
                    bbox = [round(rx1, 2), round(ry1, 2), round(rx2, 2), round(ry2, 2)]

                det_item = {
                    "track_id": t_id,
                    "class": class_name,
                    "class_id": class_id,
                    "raw_class_id": mapped_class["raw_class_id"],
                    "raw_class_name": mapped_class["raw_class_name"],
                    "class_mapping_applied": mapped_class["class_mapping_applied"],
                    "confidence": ema_conf,
                    "bbox": bbox
                }
                if polygon:
                    det_item["polygon"] = polygon

                current_detections.append(det_item)

            # Pass 2: Save snapshots & update database (draw ALL detected boxes in green, same style)
            for det_item in current_detections:
                t_id = det_item["track_id"]
                class_name = det_item["class"]
                ema_conf = det_item["confidence"]
                bbox = det_item["bbox"]
                polygon = det_item.get("polygon")

                if (track_persistence[t_id] >= min_persist
                    and (t_id not in best_tracks_conf or ema_conf > best_tracks_conf[t_id])):
                    best_tracks_conf[t_id] = ema_conf

                    img_name = f"track_{t_id}.jpeg"
                    img_path = os.path.join(save_dir, img_name)

                    # Lưu ảnh snapshot gốc sạch (không vẽ đè khung nhận diện) để Vision LLM và FE hoạt động tối ưu
                    cv2.imwrite(img_path, frame)

                    current_frame_path = get_file_url(img_path)

                    doc = {
                        "frame_index": frame_idx,
                        "timestamp": timestamp_str,
                        "frameFilePath": current_frame_path,
                        "detections": current_detections,
                        "road_contour": road_poly
                    }
                    results_collection.update_one(
                        {"task_id": request_id, "track_id": t_id},
                        {"$set": doc},
                        upsert=True
                    )
                    db_update_needed = True

            # Reset persistence cho track biến mất
            for tid in list(track_persistence.keys()):
                if tid not in current_track_ids:
                    track_persistence[tid] = 0

            now = time.monotonic()
            if db_update_needed and (now - last_db_write) >= DB_WRITE_INTERVAL:
                tasks_collection.update_one(
                    {"_id": request_id},
                    {"$set": {"processingStatus": "đang stream"}}
                )
                last_db_write = now

            await websocket.send_json({
                "status": True,
                "data": {
                    "processingStatus": "đang stream",
                    "datas": [{
                        "sourceFilePath": file_path,
                        "images": [{
                            "frame_index": frame_idx,
                            "timestamp": timestamp_str,
                            "frameFilePath": current_frame_path,
                            "detections": current_detections,
                            "road_contour": road_poly
                        }]
                    }]
                },
                "ErrorCode": None
            })

            frame_idx += 1
            await asyncio.sleep(0.001)

        # Video/stream kết thúc bình thường
        cap.release()
        tasks_collection.update_one(
            {"_id": request_id},
            {"$set": {"processingStatus": "xử lý xong"}}
        )

        await websocket.close()

    except WebSocketDisconnect:
        cap.release()
        tasks_collection.update_one(
            {"_id": request_id},
            {"$set": {"processingStatus": "ngắt kết nối"}}
        )
        logger.info(f"WebSocket ngắt kết nối: {request_id}")

    except Exception as e:
        logger.error(f"Lỗi WebSocket stream {request_id}: {e}", exc_info=True)
        cap.release()
        tasks_collection.update_one(
            {"_id": request_id},
            {"$set": {
                "processingStatus": "lỗi",
                "ErrorCode": "STREAM_ERROR"
            }}
        )
        try:
            await websocket.close()
        except Exception:
            pass  # WebSocket có thể đã đóng rồi

# ================= DEFECT CATALOG & ANALYSIS =================

import pathlib
from datetime import datetime, timezone

SEED_CATALOG_PATH = pathlib.Path(__file__).parent / "seed_catalog.json"

def seed_catalog():
    """Đồng bộ catalog chuẩn mà không xóa dữ liệu do người vận hành nhập."""
    if not SEED_CATALOG_PATH.exists():
        logger.warning(f"⚠️ Không tìm thấy {SEED_CATALOG_PATH}")
        return
    try:
        with open(SEED_CATALOG_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
        now = datetime.now(timezone.utc).isoformat()
        for item in items:
            class_name = item["class_name"]
            canonical = {
                **item,
                "_id": class_name,
                "active": True,
                "deprecated": False,
                "updated_at": now,
            }
            catalog_collection.update_one(
                {"_id": class_name},
                {
                    "$set": canonical,
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )

        canonical_ids = [item["class_name"] for item in items]
        stale = catalog_collection.update_many(
            {"_id": {"$nin": canonical_ids}},
            {
                "$set": {
                    "active": False,
                    "deprecated": True,
                    "updated_at": now,
                }
            },
        )
        logger.info(
            "✅ Catalog synchronized: %s canonical classes, %s stale entries deprecated.",
            len(items),
            stale.modified_count,
        )
    except Exception as e:
        logger.error(f"❌ Lỗi seed catalog: {e}", exc_info=True)



# --- Analysis Endpoints ---

def determine_severity(confidence: float, severity_levels: list) -> dict:
    """Xác định mức độ nghiêm trọng dựa trên confidence và bảng severity_levels."""
    for sl in severity_levels:
        r = sl.get("confidence_range", [0, 0])
        if len(r) == 2 and r[0] <= confidence <= r[1]:
            return {"level": sl.get("level", "unknown"), "label": sl.get("label", "")}
    # Fallback: nếu confidence nằm ngoài range, lấy mức cao nhất hoặc thấp nhất
    if severity_levels:
        if confidence >= severity_levels[-1].get("confidence_range", [0, 1])[0]:
            last = severity_levels[-1]
            return {"level": last.get("level", "unknown"), "label": last.get("label", "")}
        first = severity_levels[0]
        return {"level": first.get("level", "unknown"), "label": first.get("label", "")}
    return {"level": "unknown", "label": "Không xác định"}



# VISION LLM HEALTH CHECK
# HEALTH CHECK
@app.get("/api/v1/health")
async def health_check():
    """Kiểm tra trạng thái hệ thống."""
    try:
        client.admin.command('ping')
        mongo_ok = True
    except Exception:
        mongo_ok = False

    payload = {
        "status": mongo_ok,
        "mongodb": "connected" if mongo_ok else "disconnected",
        "device": "GPU" if device_name == "0" else "CPU",
        "models_loaded": list(models.keys()),
        "models_available": list(MODEL_CONFIGS.keys())
    }
    if not mongo_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/api/v1/model-info")
async def model_info():
    """Expose auditable source-model identity for deployment verification."""
    import hashlib

    artifacts = {}
    for model_type, config in MODEL_CONFIGS.items():
        pt_path = config["path"]
        if not os.path.exists(pt_path):
            artifacts[model_type] = {
                "available": False,
                "path": pt_path,
            }
            continue

        digest = hashlib.sha256()
        with open(pt_path, "rb") as model_file:
            for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
                digest.update(chunk)

        source_model = YOLO(pt_path)
        artifacts[model_type] = {
            "available": True,
            "path": pt_path,
            "size": os.path.getsize(pt_path),
            "sha256": digest.hexdigest(),
            "task": source_model.task,
            "raw_names": {
                str(class_id): str(class_name)
                for class_id, class_name in source_model.names.items()
            },
            "active_names": {
                str(class_id): class_name
                for class_id, class_name in normalized_model_names(
                    source_model.names, model_type
                ).items()
            },
            "engine_present": os.path.exists(pt_path.replace(".pt", ".engine")),
            "onnx_present": os.path.exists(pt_path.replace(".pt", ".onnx")),
            "class_mapping": (
                get_class_mapper(model_type).info()
                if get_class_mapper(model_type)
                else ModelClassRemapper(
                    model_type=model_type,
                    model_path=pt_path,
                    raw_names=source_model.names,
                ).info()
            ),
        }
        del source_model

    return {"status": True, "models": artifacts}
