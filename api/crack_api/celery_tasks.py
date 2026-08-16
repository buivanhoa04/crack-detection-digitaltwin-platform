import cbam_module
import os
import sys
import cv2

# Đảm bảo thư mục hiện tại có trong sys.path để tránh lỗi ModuleNotFoundError khi Celery worker fork tiến trình
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)
import time
import logging
import numpy as np
import torch
import supervision as sv

from celery_app import celery_app
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from ultralytics import YOLO

# Setup Celery Task Logger
logger = logging.getLogger("crack_api.celery_tasks")

# MongoDB connection
MONGO_URL = os.getenv("MONGO_DETAILS", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db = client["digital_twin"]
tasks_collection = db["tasks"]
results_collection = db["crack_results"]
catalog_collection = db["defect_catalog"]

# Lazy-loaded instances in the worker process
_models = {}
_model_paths = {}
_model_tasks = {}
_model_names = {}
_class_mappers = {}
_segmenter = None
_sr_enhancer = None

# Class names cache
class_names = {
    "road": {0: "longitudinal_crack", 1: "transverse_crack", 2: "alligator_crack", 3: "pothole", 4: "patched_area", 5: "oblique_crack"},
    "bridge": {0: "crack", 1: "exposed_rebar", 2: "honeycomb", 3: "rust", 4: "seepage", 5: "spalling"}
}
EXCLUDED_CLASSES = {
    "road": {"road_manhole", "road_patched", "road_white_line_blur"},
    # These classes are not part of the bridge inspection deliverable. They
    # must be filtered before persistence so they cannot leak into archive,
    # map, review or 3D results.
    "bridge": {
        "bridge_patched",
        "Control Point",
        "Pothole Asphalt",
        "Biological_Growth",
    },
}


def _polygon_iou(poly_a, poly_b):
    """Return mask IoU for two absolute-coordinate polygons."""
    try:
        a = np.asarray(poly_a, dtype=np.float32).reshape(-1, 2)
        b = np.asarray(poly_b, dtype=np.float32).reshape(-1, 2)
        if len(a) < 3 or len(b) < 3:
            return 0.0
        area_a = abs(float(cv2.contourArea(a)))
        area_b = abs(float(cv2.contourArea(b)))
        if area_a <= 0 or area_b <= 0:
            return 0.0
        intersection, _ = cv2.intersectConvexConvex(a, b)
        union = area_a + area_b - float(intersection)
        return float(intersection) / union if union > 0 else 0.0
    except Exception:
        return 0.0


def _bbox_iou(box_a, box_b):
    try:
        ax1, ay1, ax2, ay2 = map(float, box_a)
        bx1, by1, bx2, by2 = map(float, box_b)
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        return inter / (area_a + area_b - inter + 1e-6)
    except Exception:
        return 0.0


def _deduplicate_image_detections(detections, overlap_threshold=0.50):
    """Remove overlapping SAHI duplicates within one image only.

    Detections of different classes and defects from different images are
    intentionally never merged. For segmentation outputs we retain the best
    polygon instead of unioning masks, which avoids inventing geometry.
    """
    ordered = sorted(detections, key=lambda d: float(d.get("confidence", 0.0)), reverse=True)
    kept = []
    for candidate in ordered:
        duplicate = False
        for existing in kept:
            if candidate.get("class") != existing.get("class"):
                continue
            poly_a, poly_b = candidate.get("polygon"), existing.get("polygon")
            overlap = (
                _polygon_iou(poly_a, poly_b)
                if poly_a and poly_b
                else _bbox_iou(candidate.get("bbox", []), existing.get("bbox", []))
            )
            if overlap >= overlap_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return kept

def get_device():
    return "0" if torch.cuda.is_available() else "cpu"

def load_worker_models(model_type="road"):
    """Initialize YOLO model and SegFormer lazily in the Celery worker."""
    global _models, _model_paths, _model_tasks, _model_names, _class_mappers, _segmenter
    device = get_device()
    
    # 1. Load YOLO Model
    if model_type not in _models:
        from main import get_class_mapper, load_validated_model_artifact

        model, names, target_path, task = load_validated_model_artifact(
            model_type, device
        )
        _models[model_type] = model
        _model_names[model_type] = names
        _model_paths[model_type] = target_path
        _model_tasks[model_type] = task
        _class_mappers[model_type] = get_class_mapper(model_type)
        logger.info(
            "Loaded validated YOLO model %s (task=%s, classes=%s) from %s",
            model_type,
            task,
            names,
            target_path,
        )

    # 2. Road Segmenter will be loaded lazily in the task if needed.
    pass



def get_save_dir(file_path: str, request_id: str) -> str:
    """Helper to generate output directory for task snapshots."""
    ROOT_SOURCES_DIR = "/data/file/sources"
    source_dir = os.path.dirname(file_path)
    parent_basename = os.path.basename(source_dir)
    if parent_basename == request_id:
        save_dir = os.path.join(source_dir, "snapshot")
    else:
        save_dir = os.path.join(source_dir, request_id)
    os.makedirs(save_dir, exist_ok=True)
    return save_dir

@celery_app.task(name="crack_tasks.process_cascade_image")
def process_cascade_image_task(
    request_id: str,
    file_path: str,
    model_type: str,
    segmentation_enabled: bool = None,
    color_normalization_enabled: bool = False,
):
    """
    Celery task running the complete Cascade Image Pipeline:
    1. Preprocessing (batch mode)
    2. Road Segmentation (Segformer/PIDNet)
    3. YOLO26n Coarse Scan
    4. Suspect identification (conf [0.25, 0.55] & (area < 0.05% OR aspect_ratio > 4.0))
    5. Crop suspects with 15% padding -> Super-Resolution upscale 4x -> YOLO26m/s refine
    6. WBF merge detections (Fast YOLO26n + Refined YOLO26m)
    7. Vision LLM reports generation
    8. MongoDB update & RAGFlow integration
    """
    global _models, _segmenter
    logger.info(f"🚀 Celery task started: process_cascade_image for request_id={request_id}")
    
    # 1. Update status
    tasks_collection.update_one(
        {"$or": [{"_id": request_id}, {"task_id": request_id}]},
        {"$set": {
            "processingStatus": "đang xử lý",
            "started_at_epoch": time.time(),
            "progress": "5%",
            "processed_count": 0,
            "total_count": 1,
            "elapsed_seconds": 0,
            "eta_seconds": 0,
            "color_normalization_enabled": (
                model_type == "road" and bool(color_normalization_enabled)
            ),
        }},
    )
    
    try:
        # Load models in worker process
        load_worker_models(model_type)
        
        # Read image
        frame = cv2.imread(file_path)
        if frame is None:
            tasks_collection.update_one({"$or": [{"_id": request_id}, {"task_id": request_id}]}, {"$set": {"processingStatus": "lỗi", "ErrorCode": "INVALID_IMAGE"}})
            return
            
        from main import MODEL_CONFIGS
        config = MODEL_CONFIGS.get(model_type, {})
        imgsz = config.get("imgsz", 640)  # ★ Khớp đúng training resolution (640)

        img_h, img_w = frame.shape[:2]
        total_area = img_h * img_w
        area_threshold = 0.0005 * total_area  # 0.05% of image area
        
        from inference_engine import UnifiedSOTAInferenceEngine
        engine = UnifiedSOTAInferenceEngine(config)
        infer_frame = engine.preprocess_frame(
            frame,
            model_type=model_type,
            color_normalization_enabled=bool(color_normalization_enabled),
        )
        
        # 3. Road Segmentation: CHỈ CHẠY CHO LUỒNG ĐƯỜNG (road) KHI BẬT TÙY CHỌN
        enable_seg = (model_type == "road") and (config.get("segmentation_enabled", False) if segmentation_enabled is None else segmentation_enabled)
        cached_mask = None
        if enable_seg:
            if _segmenter is None:
                try:
                    from segmentation import RoadSegmenter
                    _segmenter = RoadSegmenter(device="cuda" if torch.cuda.is_available() else "cpu")
                    if not _segmenter.load_model():
                        _segmenter = None
                        raise RuntimeError("SEGMENTATION_MODEL_UNAVAILABLE")
                    logger.info("✅ Road Segmenter loaded successfully in worker.")
                except Exception as e:
                    logger.error(f"❌ Failed to load RoadSegmenter in worker: {e}")
            
            if _segmenter is not None:
                # Segmentation always receives the original frame. Only the
                # YOLO input follows the user-selected colour normalization.
                cached_mask = _segmenter.get_road_mask(frame)
                if cached_mask is None or not np.any(cached_mask):
                    raise RuntimeError("EMPTY_ROAD_SEGMENTATION_MASK")
            else:
                raise RuntimeError("SEGMENTATION_MODEL_UNAVAILABLE")
                
        # 4. YOLO Inference — SAHI SOTA (thư viện chính thức obss/sahi)
        model = _models.get(model_type)
        if model is None:
            raise RuntimeError(f"YOLO model for {model_type} not found")
            
        # Names were validated against source .pt when the worker loaded.
        extracted_names = _model_names.get(model_type, {})

        # 4. YOLO Inference — Unified SOTA Inference Engine (SAHI SOTA / Direct)
        # SAHI Tiling: Luôn tự động bật khi ảnh lớn hơn kích thước train (640px)
        use_sahi = config.get("tiling_enabled", True) and max(img_h, img_w) > imgsz
        
        if use_sahi:
            sahi_device = f"cuda:{get_device()}" if get_device().isdigit() else get_device()
            class_thresholds = config.get("class_conf_thresholds", {})
            conf_val = min(
                [float(config.get("conf", 0.20))]
                + [float(v) for v in class_thresholds.values()]
            ) if class_thresholds else float(config.get("conf", 0.20))
            logger.info(f"[Celery Image] Unified SAHI SOTA inference ({model_type}) on {img_w}x{img_h} image (conf={conf_val}, slice={imgsz}, overlap=0.25)...")
            
            sahi_dets = engine.run_sahi_inference(
                frame=infer_frame,
                model_type=model_type,
                conf_thresh=conf_val,
                slice_size=imgsz,
                overlap_ratio=config.get("tile_overlap", 0.25),
                device=sahi_device,
                class_names_map=extracted_names,
                model_path_override=_model_paths.get(model_type),
                task_override=_model_tasks.get(model_type),
                class_mapper=_class_mappers.get(model_type),
                class_conf_thresholds=class_thresholds,
            )
            
            raw_dets = sahi_dets
        else:
            results_n = model(infer_frame, imgsz=imgsz, verbose=False, device=get_device())[0]
            result_task = _model_tasks.get(model_type)
            if result_task == "obb":
                # OBB Results do not populate results.boxes. Reading .boxes
                # here silently returned zero detections for images <= imgsz.
                obb = getattr(results_n, "obb", None)
                boxes_n = obb.xyxy.cpu().numpy() if obb is not None else np.array([])
                scores_n = obb.conf.cpu().numpy() if obb is not None else np.array([])
                labels_n = (
                    obb.cls.cpu().numpy().astype(int)
                    if obb is not None
                    else np.array([])
                )
                polygons_n = (
                    obb.xyxyxyxy.cpu().numpy()
                    if obb is not None
                    else []
                )
            else:
                boxes_n = results_n.boxes.xyxy.cpu().numpy() if results_n.boxes is not None else np.array([])
                scores_n = results_n.boxes.conf.cpu().numpy() if results_n.boxes is not None else np.array([])
                labels_n = results_n.boxes.cls.cpu().numpy().astype(int) if results_n.boxes is not None else np.array([])
                polygons_n = results_n.masks.xy if (hasattr(results_n, 'masks') and results_n.masks is not None) else []
            
            raw_dets = []
            for i in range(len(boxes_n)):
                raw_cls_idx = int(labels_n[i])
                raw_cls_name = extracted_names.get(raw_cls_idx)
                if not raw_cls_name:
                    continue
                class_thresholds = config.get("class_conf_thresholds", {})
                score = float(scores_n[i])
                threshold = float(
                    class_thresholds.get(raw_cls_name, config.get("conf", 0.20))
                )
                if score < threshold:
                    continue
                mapper = _class_mappers.get(model_type)
                if mapper is not None:
                    mapped_class = mapper.remap_detection(
                        raw_cls_idx, raw_cls_name
                    )
                else:
                    mapped_class = {
                        "raw_class_id": raw_cls_idx,
                        "raw_class_name": raw_cls_name,
                        "class_id": raw_cls_idx,
                        "class_name": raw_cls_name,
                        "class_mapping_applied": False,
                    }
                poly = polygons_n[i] if (i < len(polygons_n) and polygons_n[i] is not None) else None
                poly_list = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in poly] if poly is not None else None
                raw_dets.append({
                    "class": mapped_class["class_name"],
                    "class_id": mapped_class["class_id"],
                    "raw_class_id": mapped_class["raw_class_id"],
                    "raw_class_name": mapped_class["raw_class_name"],
                    "class_mapping_applied": mapped_class["class_mapping_applied"],
                    "confidence": score,
                    "bbox": [round(float(x), 2) for x in boxes_n[i]],
                    "polygon": poly_list
                })

        # 5. Format results and filter excluded classes / road bounds
        from main import determine_severity, map_analysis_compatibility, get_file_url
        
        current_detections = []
        track_counter = 1
        
        for d in raw_dets:
            cls_name = d.get("class")
            if not cls_name or cls_name in EXCLUDED_CLASSES.get(model_type, set()):
                continue
                
            conf = round(float(d.get("confidence", 0.0)), 4)
            bbox = d.get("bbox", [0, 0, 0, 0])
            bx1, by1, bx2, by2 = bbox
            
            # Road overlap filtering
            if _segmenter is not None and cached_mask is not None:
                x1_f, y1_f, x2_f, y2_f = map(int, [bx1, by1, bx2, by2])
                h_max, w_max = cached_mask.shape[:2]
                x1_f, y1_f = max(0, x1_f), max(0, y1_f)
                x2_f, y2_f = min(w_max, x2_f), min(h_max, y2_f)
                
                if x2_f > x1_f and y2_f > y1_f:
                    roi_mask = cached_mask[y1_f:y2_f, x1_f:x2_f]
                    overlap_ratio = np.mean(roi_mask > 0)
                    min_overlap = float(config.get("road_mask_min_overlap", 0.60))
                    if overlap_ratio < min_overlap:
                        continue
                else:
                    continue

            det_obj = {
                "track_id": track_counter,
                "class": cls_name,
                "class_id": d.get("class_id"),
                "raw_class_id": d.get("raw_class_id", d.get("class_id")),
                "raw_class_name": d.get("raw_class_name", cls_name),
                "class_mapping_applied": bool(d.get("class_mapping_applied", False)),
                "confidence": conf,
                "bbox": [round(float(x), 2) for x in bbox],
                "road_mask_overlap": round(float(overlap_ratio), 4) if cached_mask is not None else None,
            }
            if d.get("polygon") is not None:
                det_obj["polygon"] = d["polygon"]
            # Keep the single-image task contract identical to the batch
            # contract. The previous code built det_obj but never persisted it.
            det_obj["analysis"] = {}
            current_detections.append(det_obj)
            track_counter += 1

        # Remove only same-class overlaps from SAHI tile boundaries. This is
        # deliberately per image; detections from separate files are never
        # merged.
        current_detections = _deduplicate_image_detections(
            current_detections,
            overlap_threshold=float(config.get("dedup_overlap", 0.50)),
        )
        for index, item in enumerate(current_detections, start=1):
            item["track_id"] = index

        frame_file_path = get_file_url(file_path)
        
        result_data = {
            "processingStatus": "xử lý xong",
            "datas": [{
                "sourceFilePath": file_path,
                "images": [{
                    "frame_index": 0,
                    "timestamp": "00:00",
                    "frameFilePath": frame_file_path,
                    "detections": current_detections
                }]
            }],
            "ErrorCode": None
        }
        
        elapsed_seconds = max(0, int(time.time() - float(
            tasks_collection.find_one({"$or": [{"_id": request_id}, {"task_id": request_id}]}, {"started_at_epoch": 1}).get("started_at_epoch", time.time())
        )))
        result_data.update({
            "progress": "100%",
            "processed_count": 1,
            "total_count": 1,
            "elapsed_seconds": elapsed_seconds,
            "eta_seconds": 0,
        })
        tasks_collection.update_one({"$or": [{"_id": request_id}, {"task_id": request_id}]}, {"$set": result_data})
        

            
        logger.info(f"✅ Celery image task completed successfully: {request_id}")
        return {"status": True, "data_count": len(current_detections)}
        
    except Exception as e:
        logger.error(f"❌ Error processing Celery image task {request_id}: {e}", exc_info=True)
        tasks_collection.update_one({"$or": [{"_id": request_id}, {"task_id": request_id}]}, {"$set": {"processingStatus": "lỗi", "ErrorCode": "PROCESSING_ERROR"}})
        return {"status": False, "error": str(e)}


@celery_app.task(name="crack_tasks.process_cascade_image_batch")
def process_cascade_image_batch_task(
    jobs: list[dict],
    model_type: str,
    segmentation_enabled: bool = None,
    color_normalization_enabled: bool = False,
):
    """
    High-quality folder pipeline.

    Every image keeps the same full-frame + sliced SAHI coverage and
    GREEDYNMM merge as the single-image task. The optimization is limited to
    batching TensorRT tiles, so result quality is not reduced.
    """
    global _models, _segmenter
    if not jobs:
        return {"status": True, "processed": 0}

    request_ids = [str(job.get("request_id")) for job in jobs if job.get("request_id")]
    try:
        load_worker_models(model_type)
        from main import (
            MODEL_CONFIGS,
            determine_severity,
            get_file_url,
            map_analysis_compatibility,
        )
        from inference_engine import UnifiedSOTAInferenceEngine

        config = MODEL_CONFIGS.get(model_type, {})
        imgsz = int(config.get("imgsz", 640))
        device = get_device()
        engine = UnifiedSOTAInferenceEngine(config)
        model = _models.get(model_type)
        if model is None:
            raise RuntimeError(f"YOLO model for {model_type} not found")

        valid_jobs = []
        original_frames = []
        inference_frames = []
        road_masks = []

        enable_seg = (
            model_type == "road"
            and (
                config.get("segmentation_enabled", False)
                if segmentation_enabled is None
                else segmentation_enabled
            )
        )
        if enable_seg and _segmenter is None:
            from segmentation import RoadSegmenter

            _segmenter = RoadSegmenter(
                device="cuda" if torch.cuda.is_available() else "cpu"
            )
            if not _segmenter.load_model():
                _segmenter = None
                raise RuntimeError("SEGMENTATION_MODEL_UNAVAILABLE")

        for job in jobs:
            request_id = str(job.get("request_id") or "")
            file_path = str(job.get("file_path") or "")
            if not request_id or not file_path:
                continue
            tasks_collection.update_one(
                {"$or": [{"_id": request_id}, {"task_id": request_id}]},
                {"$set": {
                    "processingStatus": "đang xử lý",
                    "color_normalization_enabled": (
                        model_type == "road" and bool(color_normalization_enabled)
                    ),
                }},
            )
            frame = cv2.imread(file_path)
            if frame is None:
                tasks_collection.update_one(
                    {"$or": [{"_id": request_id}, {"task_id": request_id}]},
                    {
                        "$set": {
                            "processingStatus": "lỗi",
                            "ErrorCode": "INVALID_IMAGE",
                        }
                    },
                )
                continue
            mask = None
            if enable_seg:
                mask = _segmenter.get_road_mask(frame)
                if mask is None or not np.any(mask):
                    tasks_collection.update_one(
                        {"$or": [{"_id": request_id}, {"task_id": request_id}]},
                        {
                            "$set": {
                                "processingStatus": "lỗi",
                                "ErrorCode": "EMPTY_ROAD_SEGMENTATION_MASK",
                            }
                        },
                    )
                    continue
            valid_jobs.append({"request_id": request_id, "file_path": file_path})
            original_frames.append(frame)
            inference_frames.append(
                engine.preprocess_frame(
                    frame,
                    model_type=model_type,
                    color_normalization_enabled=bool(color_normalization_enabled),
                )
            )
            road_masks.append(mask)

        if not valid_jobs:
            return {"status": False, "processed": 0}

        class_map = _model_names.get(model_type, {})
        raw_results = engine.run_sahi_batch_inference(
            frames=inference_frames,
            model_type=model_type,
            conf_thresh=min(
                [float(config.get("conf", 0.20))]
                + [float(v) for v in config.get("class_conf_thresholds", {}).values()]
            ) if config.get("class_conf_thresholds") else float(config.get("conf", 0.20)),
            slice_size=imgsz,
            overlap_ratio=float(config.get("tile_overlap", 0.25)),
            device=f"cuda:{device}" if str(device).isdigit() else str(device),
            class_names_map=class_map,
            model_path_override=_model_paths.get(model_type),
            task_override=_model_tasks.get(model_type),
            class_mapper=_class_mappers.get(model_type),
            inference_batch_size=int(config.get("max_batch_size", 2)),
            class_conf_thresholds=config.get("class_conf_thresholds", {}),
        )

        total_detections = 0
        for job, frame, cached_mask, raw_dets in zip(
            valid_jobs, original_frames, road_masks, raw_results
        ):
            request_id = job["request_id"]
            file_path = job["file_path"]
            height, width = frame.shape[:2]
            current_detections = []
            track_counter = 1

            for detection in raw_dets:
                class_name = detection.get("class")
                if (
                    not class_name
                    or class_name in EXCLUDED_CLASSES.get(model_type, set())
                ):
                    continue
                bbox = detection.get("bbox", [0, 0, 0, 0])
                overlap_value = None
                if cached_mask is not None:
                    x1, y1, x2, y2 = map(int, bbox)
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(width, x2), min(height, y2)
                    if x2 <= x1 or y2 <= y1:
                        continue
                    overlap_value = float(
                        np.mean(cached_mask[y1:y2, x1:x2] > 0)
                    )
                    if overlap_value < float(
                        config.get("road_mask_min_overlap", 0.60)
                    ):
                        continue

                confidence = round(float(detection.get("confidence", 0.0)), 4)
                item = {
                    "track_id": track_counter,
                    "class": class_name,
                    "class_id": detection.get("class_id"),
                    "raw_class_id": detection.get(
                        "raw_class_id", detection.get("class_id")
                    ),
                    "raw_class_name": detection.get(
                        "raw_class_name", class_name
                    ),
                    "class_mapping_applied": bool(
                        detection.get("class_mapping_applied", False)
                    ),
                    "confidence": confidence,
                    "bbox": [round(float(value), 2) for value in bbox],
                    "road_mask_overlap": (
                        round(overlap_value, 4)
                        if overlap_value is not None
                        else None
                    ),
                }
                if detection.get("polygon") is not None:
                    item["polygon"] = detection["polygon"]

                catalog_entry = catalog_collection.find_one({"_id": class_name})
                static_analysis = {}
                if catalog_entry:
                    analysis = catalog_entry.get("analysis", {})
                    severity = determine_severity(
                        confidence,
                        catalog_entry.get("severity_levels", []),
                    )
                    severity_level = severity.get("level", "unknown")
                    standards = catalog_entry.get("tcvn_codes", [])
                    standards_text = (
                        ", ".join(standards) if standards else "Không xác định"
                    )
                    static_analysis = {
                        "observed_object": (
                            "Mặt đường bê tông nhựa nóng"
                            if model_type == "road"
                            else "Cấu kiện cầu (Bê tông cốt thép)"
                        ),
                        "defect_code_mapping": (
                            f"[{catalog_entry.get('defect_code', 'N/A')}] "
                            f"{catalog_entry.get('defect_name', class_name)}"
                        ),
                        "current_status_details": (
                            f"Nhận diện tự động từ AI: Phát hiện khuyết tật "
                            f"{catalog_entry.get('defect_name', class_name)} "
                            f"với độ tin cậy {confidence:.1%}. Phân cấp mức độ "
                            f"sơ bộ: {severity.get('label', severity_level)}."
                        ),
                        "technical_analysis": {
                            "tcvn_references": [
                                f"Theo tiêu chuẩn áp dụng {standards_text}.",
                                f"Chi tiết kỹ thuật: {analysis.get('technical_detail', '')}",
                                f"Cơ chế hư hỏng: {analysis.get('description', '')}",
                            ]
                        },
                        "conclusion_and_repair_plan": (
                            f"Hư hỏng mức độ {severity_level}."
                        ),
                        "recommendations_to_contractor": catalog_entry.get(
                            "recommendations", []
                        ),
                    }
                item["analysis"] = map_analysis_compatibility(static_analysis)
                current_detections.append(item)
                track_counter += 1

            # SAHI already performs tile-level NMM, but masks touching slice
            # boundaries can still survive as duplicate detections. Apply a
            # conservative, same-class pass per image before persisting the
            # result. Do not merge across images: those may be different
            # physical defects and have no reliable spatial correspondence.
            current_detections = _deduplicate_image_detections(
                current_detections,
                overlap_threshold=float(config.get("dedup_overlap", 0.50)),
            )
            for index, item in enumerate(current_detections, start=1):
                item["track_id"] = index

            total_detections += len(current_detections)
            result_data = {
                "processingStatus": "xử lý xong",
                "datas": [
                    {
                        "sourceFilePath": file_path,
                        "images": [
                            {
                                "frame_index": 0,
                                "timestamp": "00:00",
                                "frameFilePath": get_file_url(file_path),
                                "detections": current_detections,
                            }
                        ],
                    }
                ],
                "ErrorCode": None,
            }
            tasks_collection.update_one(
                {"$or": [{"_id": request_id}, {"task_id": request_id}]},
                {"$set": result_data},
            )

        logger.info(
            "✅ High-quality batch completed: %s images, %s detections",
            len(valid_jobs),
            total_detections,
        )
        return {
            "status": True,
            "processed": len(valid_jobs),
            "detections": total_detections,
        }
    except Exception as exc:
        logger.error("❌ Batch image task failed: %s", exc, exc_info=True)
        if request_ids:
            tasks_collection.update_many(
                {
                    "$or": [
                        {"_id": {"$in": request_ids}},
                        {"task_id": {"$in": request_ids}},
                    ]
                },
                {
                    "$set": {
                        "processingStatus": "lỗi",
                        "ErrorCode": "PROCESSING_ERROR",
                    }
                },
            )
        return {"status": False, "error": str(exc)}

@celery_app.task(name="crack_tasks.process_video_offline")
def process_video_offline_task(
    request_id: str,
    file_path: str,
    model_type: str,
    segmentation_enabled: bool = None,
    color_normalization_enabled: bool = False,
):
    """
    Celery task running offline video defect detection and tracking (replacing BackgroundTasks).
    """
    logger.info(f"🚀 Celery video task started: process_video_offline for request_id={request_id}")
    
    try:
        from main import process_ai_offline
        # We delegate the tracking processing to the original well-built process_ai_offline function
        # Since it runs fine-grained frames, tracking (ByteTrack), GMC, and EMA.
        succeeded = process_ai_offline(
            request_id,
            file_path,
            model_type,
            segmentation_enabled,
            color_normalization_enabled,
        )
        if succeeded is False:
            logger.error(f"Offline video task failed: {request_id}")
            return {"status": False, "error": "PROCESSING_ERROR"}
        logger.info(f"✅ Celery video task completed successfully: {request_id}")
        return {"status": True}
    except Exception as e:
        logger.error(f"❌ Error processing Celery video task {request_id}: {e}", exc_info=True)
        tasks_collection.update_one({"$or": [{"_id": request_id}, {"task_id": request_id}]}, {"$set": {"processingStatus": "lỗi", "ErrorCode": "PROCESSING_ERROR"}})
        return {"status": False, "error": str(e)}
