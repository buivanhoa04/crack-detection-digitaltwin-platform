import cbam_module
import cv2
import numpy as np
import os
import json
import logging
import time
from pymongo import MongoClient

logger = logging.getLogger("crack_api.direct_video_pipeline")

# MongoDB connection configuration (matching main.py setup)
MONGO_DETAILS = os.environ.get("MONGO_DETAILS", "mongodb://localhost:27017/")
client = MongoClient(MONGO_DETAILS)
db = client["digital_twin"]
tasks_collection = db["tasks"]
results_collection = db["crack_results"]

def get_file_url(file_path: str) -> str:
    """Helper to convert local absolute path to accessible URL."""
    norm_path = file_path.replace("\\", "/")
    if "sources/" in norm_path:
        rel_path = norm_path.split("sources/")[-1]
    else:
        root_dir = "/data/file/sources"
        rel_path = os.path.relpath(norm_path, root_dir)
        
    return "/files/" + rel_path.replace(os.sep, "/")

def calculate_iou(boxA, boxB):
    """Tính toán IoU giữa 2 bounding box [x1, y1, x2, y2]."""
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    
    interArea = max(0.0, xB - xA) * max(0.0, yB - yA)
    boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    
    iou = interArea / float(boxAArea + boxBArea - interArea + 1e-5)
    return iou


def calculate_polygon_iou(poly_a, poly_b) -> float:
    """IoU for convex OBB polygons; fall back to zero for invalid polygons."""
    try:
        a = np.asarray(poly_a, dtype=np.float32).reshape(-1, 2)
        b = np.asarray(poly_b, dtype=np.float32).reshape(-1, 2)
        if len(a) < 3 or len(b) < 3:
            return 0.0
        area_a = abs(float(cv2.contourArea(a)))
        area_b = abs(float(cv2.contourArea(b)))
        if area_a <= 0.0 or area_b <= 0.0:
            return 0.0
        inter, _ = cv2.intersectConvexConvex(a, b)
        union = area_a + area_b - float(inter)
        return float(inter) / union if union > 0.0 else 0.0
    except Exception:
        return 0.0


def calculate_detection_overlap(det_a, det_b) -> float:
    """Prefer native OBB overlap; use xyxy IoU for legacy detections."""
    poly_a = det_a.get("polygon") if isinstance(det_a, dict) else None
    poly_b = det_b.get("polygon") if isinstance(det_b, dict) else None
    if poly_a and poly_b and len(poly_a) >= 3 and len(poly_b) >= 3:
        return calculate_polygon_iou(poly_a, poly_b)
    return calculate_iou(det_a["bbox"], det_b["bbox"])

def apply_per_frame_nms(detections: list, iou_thresh: float = 0.30) -> list:
    """
    Triệt tiêu các box đè chồng chéo trên CÙNG 1 khung hình (Per-Frame NMS).
    """
    if not detections:
        return []
        
    detections.sort(key=lambda x: x["confidence"], reverse=True)
    keep = []
    
    while len(detections) > 0:
        best = detections.pop(0)
        keep.append(best)
        
        remaining = []
        for det in detections:
            iou = calculate_detection_overlap(best, det)
            if iou > iou_thresh:
                continue
            remaining.append(det)
        detections = remaining
        
    return keep

def cluster_detections_into_defects(raw_detections_list: list, min_hits: int = 2) -> list:
    """
    Gộp (Cluster) các phát hiện trùng lặp theo Chuyển động Drone chuẩn SOTA.
    Bao gồm:
    1. Khớp nối cùng loại hư hỏng (class_id matching).
    2. Theo vết chuyển động camera Drone dọc làn đường (y-axis displacement window up to 650px).
    3. Lọc bỏ nhiễu nhấp nháy 1 khung hình (min_hits >= 2).
    4. Gộp (2nd-pass merge) các cụm vết nứt gần nhau về không-thời gian.
    """
    if not raw_detections_list:
        return []
        
    raw_detections_list.sort(key=lambda d: d["frame_index"])
    clusters = []
    
    for det in raw_detections_list:
        assigned = False
        f_idx = det["frame_index"]
        c_id = det["class_id"]
        bbox = det["bbox"]
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        
        for cluster in clusters:
            # 1. Bắt buộc cùng loại hư hại
            if cluster["class_id"] != c_id:
                continue
                
            last_f_idx = cluster["last_frame_index"]
            f_diff = f_idx - last_f_idx
            
            # Cho phép khoảng cách lên tới 120 frame (~4 giây)
            if 0 <= f_diff <= 120:
                last_bbox = cluster["last_bbox"]
                last_cx = (last_bbox[0] + last_bbox[2]) / 2
                last_cy = (last_bbox[1] + last_bbox[3]) / 2
                
                dx = abs(cx - last_cx)
                dy = abs(cy - last_cy)
                iou = calculate_iou(bbox, last_bbox)
                
                # Drone di chuyển dọc làn đường: dx nhỏ (ngang < 250px), dy xuôi (dọc < 650px)
                if iou > 0.10 or (dx < 250.0 and dy < 650.0):
                    cluster["detections"].append(det)
                    cluster["last_frame_index"] = f_idx
                    cluster["last_bbox"] = bbox
                    det["global_defect_id"] = cluster["defect_id"]
                    
                    det_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                    best_bbox = cluster["best_bbox"]
                    best_area = (best_bbox[2] - best_bbox[0]) * (best_bbox[3] - best_bbox[1])
                    
                    if det["confidence"] > cluster["best_score"] or (abs(det["confidence"] - cluster["best_score"]) < 0.05 and det_area > best_area):
                        cluster["best_score"] = det["confidence"]
                        cluster["best_frame_index"] = f_idx
                        cluster["best_bbox"] = bbox
                        cluster["best_det"] = det
                        
                    assigned = True
                    break
                    
        if not assigned:
            new_id = len(clusters) + 1
            det["global_defect_id"] = new_id
            new_cluster = {
                "defect_id": new_id,
                "class_id": c_id,
                "class_name": det["class"],
                "last_frame_index": f_idx,
                "last_bbox": bbox,
                "best_frame_index": f_idx,
                "best_score": det["confidence"],
                "best_bbox": bbox,
                "best_det": det,
                "detections": [det]
            }
            clusters.append(new_cluster)
            
    # ── PASS 2: LỌC NHIỄU & GỘP CÁC CỤM TRÙNG LẶP GẦN NHAU VỀ KHÔNG-THỜI GIAN ──
    merged_clusters = []
    # Nếu tổng số raw_detections < 10 (video ngắn), giữ min_hits = 1, ngược lại min_hits = 2
    effective_min_hits = min_hits if len(raw_detections_list) >= 10 else 1
    
    for c in clusters:
        # Lọc bỏ nhiễu nhấp nháy 1 khung hình (chỉ giữ vết nứt xuất hiện ít nhất 2-3 khung hình trở lên)
        if len(c["detections"]) < effective_min_hits:
            continue
            
        merged = False
        for mc in merged_clusters:
            if mc["class_id"] == c["class_id"]:
                f_gap = abs(mc["best_frame_index"] - c["best_frame_index"])
                b1 = mc["best_bbox"]
                b2 = c["best_bbox"]
                iou = calculate_iou(b1, b2)
                cx1, cy1 = (b1[0]+b1[2])/2, (b1[1]+b1[3])/2
                cx2, cy2 = (b2[0]+b2[2])/2, (b2[1]+b2[3])/2
                
                if f_gap <= 150 and (iou > 0.10 or (abs(cx1-cx2) < 250 and abs(cy1-cy2) < 450)):
                    mc["detections"].extend(c["detections"])
                    if c["best_score"] > mc["best_score"]:
                        mc["best_score"] = c["best_score"]
                        mc["best_frame_index"] = c["best_frame_index"]
                        mc["best_bbox"] = c["best_bbox"]
                        mc["best_det"] = c["best_det"]
                    mc["last_frame_index"] = max(mc["last_frame_index"], c["last_frame_index"])
                    merged = True
                    break
        if not merged:
            merged_clusters.append(c)

    # Đánh lại ID liên tục từ 1..N
    for new_idx, c in enumerate(merged_clusters, start=1):
        c["defect_id"] = new_idx
        for d in c["detections"]:
            d["global_defect_id"] = new_idx
            
    return merged_clusters


# =============================================================================
# SAHI SLICED INFERENCE — CHUẨN SOTA (THƯ VIỆN CHÍNH THỨC obss/sahi)
#
# Quy trình chuẩn:
#   1. Chạy Full-Frame Inference  → bắt vết nứt TO (perform_standard_pred=True)
#   2. Chạy Sliced Inference      → bắt vết nứt NHỎ bị bỏ sót khi resize
#   3. MERGE kết quả bằng GREEDYNMM post-processing → loại box trùng thông minh
#
# Tham khảo: https://github.com/obss/sahi
# =============================================================================

def run_sahi_on_frame(
    model_path: str,
    frame_bgr: np.ndarray,
    conf_thresh: float = 0.35,
    slice_size: int = 640,
    overlap_ratio: float = 0.25,
    postprocess_match_threshold: float = 0.5,
    device: str = "cuda:0",
    class_names_map: dict = None,
) -> list:
    """
    Chạy SAHI Sliced Inference CHUẨN SOTA trên 1 frame.
    
    Sử dụng thư viện chính thức `sahi` (obss/sahi) với:
    - perform_standard_pred=True: Chạy CẢ full-frame LẪN sliced, rồi merge
    - postprocess_type="GREEDYNMM": Thuật toán merge chuẩn SOTA cho sliced inference
    - postprocess_match_threshold: Ngưỡng IoU để quyết định 2 box có trùng nhau hay không
    
    Returns: list of dicts [{class, class_id, confidence, bbox, polygon}, ...]
    """
    from sahi import AutoDetectionModel
    from sahi.predict import get_sliced_prediction
    
    cat_map = {str(k): str(v) for k, v in class_names_map.items()} if class_names_map else None
    detection_model = CustomUltralyticsDetectionModel(
        model_path=model_path,
        confidence_threshold=conf_thresh,
        device=device,
        category_mapping=cat_map,
    )
    
    # Chạy SAHI Sliced Prediction chuẩn SOTA
    result = get_sliced_prediction(
        image=frame_bgr,                           # Ảnh numpy BGR
        detection_model=detection_model,
        slice_height=slice_size,                    # Kích thước tile = imgsz lúc train
        slice_width=slice_size,
        overlap_height_ratio=overlap_ratio,         # Overlap 25% giữa các tile
        overlap_width_ratio=overlap_ratio,
        perform_standard_pred=True,                 # ★ CHẠY CẢ FULL-FRAME → bắt vết nứt TO
        postprocess_type="GREEDYNMM",              # ★ MERGE chuẩn SOTA (Greedy Non-Max Merging)
        postprocess_match_metric="IOS",             # Intersection over Smaller area (tốt cho box lồng nhau)
        postprocess_match_threshold=postprocess_match_threshold,  # Ngưỡng merge
        postprocess_class_agnostic=False,           # Chỉ merge cùng class
        verbose=0,                                  # Không spam log
    )
    
    # Parse kết quả từ SAHI object_prediction_list
    detections = []
    for pred in result.object_prediction_list:
        bbox = pred.bbox                            # sahi BoundingBox object
        x1, y1, x2, y2 = bbox.minx, bbox.miny, bbox.maxx, bbox.maxy
        score = pred.score.value
        class_id = pred.category.id
        
        # Map class name qua class_names_map nếu có (để giữ tên chuẩn hóa)
        if class_names_map and class_id in class_names_map:
            class_name = class_names_map[class_id]
        else:
            class_name = pred.category.name
        
        polygon = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        
        detections.append({
            "class": class_name,
            "class_id": class_id,
            "confidence": score,
            "bbox": [float(x1), float(y1), float(x2), float(y2)],
            "polygon": polygon,
        })
    
    return detections


from sahi.models.ultralytics import UltralyticsDetectionModel

class CustomUltralyticsDetectionModel(UltralyticsDetectionModel):
    """
    Custom SAHI Detection Model wrapper supporting TensorRT (.engine), ONNX (.onnx), and PyTorch (.pt).
    Safely sets category_mapping, passes explicit task (detect/segment/obb), and avoids invalid .to(device) calls.
    """
    def __init__(self, model_path=None, task=None, **kwargs):
        self.task = task
        super().__init__(model_path=model_path, **kwargs)

    def load_model(self):
        from ultralytics import YOLO
        if self.task:
            model = YOLO(self.model_path, task=self.task)
        else:
            model = YOLO(self.model_path)

        # Populate category_mapping from model.names if not already provided
        if self.category_mapping is None:
            names = getattr(model, "names", None)
            if names is None and hasattr(model, "model") and hasattr(model.model, "names"):
                names = model.model.names
            if names:
                if isinstance(names, dict):
                    self.category_mapping = {str(k): str(v) for k, v in names.items()}
                elif isinstance(names, (list, tuple)):
                    self.category_mapping = {str(i): str(v) for i, v in enumerate(names)}

        # Only call .to(device) for PyTorch .pt weights
        if hasattr(model, "to") and str(self.model_path).endswith(".pt"):
            try:
                model.to(self.device)
            except Exception as e:
                logger.warning(f"Could not move PyTorch model to {self.device}: {e}")

        self.model = model

# Cache SAHI detection model to avoid re-loading every frame
_sahi_model_cache = {}

def _get_sahi_detection_model(model_path: str, conf_thresh: float, device: str, class_names_map: dict = None, task: str = None):
    """
    Cache SAHI CustomUltralyticsDetectionModel (supports TensorRT .engine, ONNX, .pt).
    Model chỉ được khởi tạo 1 lần và tái sử dụng cho toàn bộ video.
    """
    cache_key = f"{model_path}_{device}_{conf_thresh}_{task}"
    if cache_key not in _sahi_model_cache:
        logger.info(f"Initializing SAHI CustomUltralyticsDetectionModel (task={task}, path={model_path}): {model_path}")
        cat_map = {str(k): str(v) for k, v in class_names_map.items()} if class_names_map else None
        _sahi_model_cache[cache_key] = CustomUltralyticsDetectionModel(
            model_path=model_path,
            confidence_threshold=conf_thresh,
            device=device,
            category_mapping=cat_map,
            task=task,
        )
    return _sahi_model_cache[cache_key]


def run_sahi_on_frame_cached(
    model_path: str,
    frame_bgr: np.ndarray,
    conf_thresh: float = 0.35,
    slice_size: int = 640,
    overlap_ratio: float = 0.25,
    postprocess_match_threshold: float = 0.5,
    device: str = "cuda:0",
    class_names_map: dict = None,
) -> list:
    """
    SAHI Sliced Inference với Model Cache (tối ưu tốc độ cho video).
    Tương tự run_sahi_on_frame nhưng không khởi tạo lại model mỗi frame.
    """
    from sahi.predict import get_sliced_prediction
    
    detection_model = _get_sahi_detection_model(model_path, conf_thresh, device, class_names_map)
    
    result = get_sliced_prediction(
        image=frame_bgr,
        detection_model=detection_model,
        slice_height=slice_size,
        slice_width=slice_size,
        overlap_height_ratio=overlap_ratio,
        overlap_width_ratio=overlap_ratio,
        perform_standard_pred=True,                 # ★ Full-frame + Sliced → Merge
        postprocess_type="GREEDYNMM",              # ★ SOTA merge
        postprocess_match_metric="IOS",
        postprocess_match_threshold=postprocess_match_threshold,
        postprocess_class_agnostic=False,
        verbose=0,
    )
    
    detections = []
    for pred in result.object_prediction_list:
        bbox = pred.bbox
        x1, y1, x2, y2 = bbox.minx, bbox.miny, bbox.maxx, bbox.maxy
        score = pred.score.value
        class_id = pred.category.id
        
        if class_names_map and class_id in class_names_map:
            class_name = class_names_map[class_id]
        else:
            class_name = pred.category.name
        
        polygon = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        
        detections.append({
            "class": class_name,
            "class_id": class_id,
            "confidence": score,
            "bbox": [float(x1), float(y1), float(x2), float(y2)],
            "polygon": polygon,
        })
    
    return detections


def run_direct_video_pipeline(
    req_id: str,
    file_path: str,
    model_type: str,
    model,
    config: dict,
    save_dir: str,
    local_device: str,
    road_segmenter,
    class_names_map: dict
):
    """
    SOTA Video Inference Pipeline with SAHI (obss/sahi).
    
    Kết hợp:
    1. Optional caller-selected colour normalization (road: grayscale 3 channels)
    2. SAHI Sliced Inference chuẩn SOTA:
       - Full-frame inference (bắt vết nứt TO)
       - Sliced inference 640x640 (bắt vết nứt NHỎ)
       - GREEDYNMM merge (loại box trùng thông minh)
    3. Road Segmentation filtering (loại detection ngoài mặt đường)
    4. Per-frame NMS (loại box trùng còn sót)
    5. Cluster tracking (gộp vết nứt trùng lặp cross-frame)
    """
    logger.info(f"🚀 Starting SAHI SOTA Video Pipeline for task={req_id}")
    
    tasks_collection.update_one(
        {"$or": [{"_id": req_id}, {"task_id": req_id}]},
        {"$set": {"progress": "10%", "status_detail": "Đang nạp video và khởi chạy AI SAHI SOTA..."}}
    )
    
    cap = cv2.VideoCapture(file_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video file: {file_path}")
        
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    if fps <= 0: fps = 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    # Inspection mode must not silently drop 3/4 of the frames. Operators can
    # explicitly trade recall for throughput with VIDEO_FRAME_SKIP=2/4.
    frame_skip = max(1, int(config.get("frame_skip", config.get("video_frame_skip", 1))))
    sampled_total = max(1, int(np.ceil(total_frames / frame_skip)))
    started_at = time.time()
    tasks_collection.update_one(
        {"$or": [{"_id": req_id}, {"task_id": req_id}]},
        {"$set": {
            "started_at_epoch": started_at,
            "progress": "5%",
            "processed_count": 0,
            "total_count": sampled_total,
            "elapsed_seconds": 0,
            "eta_seconds": 0,
        }},
    )
    class_thresholds = config.get("class_conf_thresholds", {})
    # SAHI's candidate gate must not be higher than any class operating point;
    # otherwise low-confidence candidates (e.g. road ``nut`` at 0.25) are
    # discarded before the class-specific thresholds can be applied.
    base_gate = float(config.get("conf", 0.25 if model_type == "bridge" else 0.35))
    conf_thresh = min([base_gate] + [float(v) for v in class_thresholds.values()]) if class_thresholds else base_gate
    segmentation_requested = (model_type == "road") and bool(config.get("segmentation_enabled", False))
    segmenter_enabled = segmentation_requested and road_segmenter is not None and road_segmenter.is_loaded
    if segmentation_requested and not segmenter_enabled:
        raise RuntimeError("SEGMENTATION_MODEL_UNAVAILABLE")
    min_road_overlap = float(config.get("road_mask_min_overlap", 0.60))
    
    # SAHI config — khớp với training resolution
    sahi_slice_size = int(config.get("imgsz", 640))  # ★ Khớp đúng training resolution
    sahi_overlap = 0.25                                # 25% overlap giữa các tile
    sahi_postprocess_threshold = 0.5                   # Ngưỡng merge GREEDYNMM
    
    # SAHI SOTA (hỗ trợ TensorRT .engine, ONNX, và PyTorch .pt qua CustomUltralyticsDetectionModel)
    pt_path = config.get("path", f"weights/crack_{model_type}.pt")
    engine_path = pt_path.replace(".pt", ".engine")
    if os.path.exists(engine_path) and local_device != "cpu":
        sahi_model_path = engine_path
    else:
        sahi_model_path = pt_path
    sahi_device = f"cuda:{local_device}" if local_device.isdigit() else local_device
    
    tracking_data = {
        "fps": fps,
        "color_normalization_enabled": bool(
            model_type == "road"
            and config.get("color_normalization_enabled", False)
        ),
        "frames": {},
        "road_contours": {}
    }
    
    all_raw_detections = []
    frame_idx = 0
    last_valid_road_mask = None
    stale_road_mask_frames = 0
    max_stale_road_mask_frames = max(
        0, int(config.get("max_stale_road_mask_frames", 30))
    )
    segmentation_failure_count = 0
    from inference_engine import UnifiedSOTAInferenceEngine
    engine = UnifiedSOTAInferenceEngine(config)
    
    logger.info(f"Processing video ({model_type}) {w}x{h} with SAHI SOTA (slice={sahi_slice_size}, overlap={sahi_overlap}, conf={conf_thresh})...")
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        if frame_idx % frame_skip != 0:
            frame_idx += 1
            continue
            
        # 1. Video-only colour-domain normalization. Uploaded still images use
        # a separate path and always preserve their original colour.
        infer_frame = engine.preprocess_frame(
            frame,
            model_type,
            color_normalization_enabled=bool(
                config.get("color_normalization_enabled", False)
            ),
        )
        
        # 2. Road segmenter mask (CHỈ CHO LUỒNG ĐƯỜNG)
        road_mask = None
        road_poly = []
        if segmenter_enabled:
            try:
                candidate_mask = road_segmenter.get_road_mask(frame)
                if candidate_mask is None or not np.any(candidate_mask):
                    segmentation_failure_count += 1
                    if (
                        last_valid_road_mask is not None
                        and stale_road_mask_frames < max_stale_road_mask_frames
                    ):
                        road_mask = last_valid_road_mask.copy()
                        stale_road_mask_frames += 1
                        logger.warning(
                            "Empty road mask at frame %s; reusing previous mask "
                            "(%s/%s)",
                            frame_idx,
                            stale_road_mask_frames,
                            max_stale_road_mask_frames,
                        )
                    else:
                        # Do not abort the entire video. Infer this frame
                        # without lane filtering and let OBB detections pass.
                        road_mask = None
                        stale_road_mask_frames = 0
                        logger.warning(
                            "Empty road mask at frame %s; lane filter skipped "
                            "for this frame",
                            frame_idx,
                        )
                else:
                    road_mask = candidate_mask
                    last_valid_road_mask = candidate_mask.copy()
                    stale_road_mask_frames = 0
                contours = []
                if road_mask is not None:
                    contours, _ = cv2.findContours(road_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if road_mask is not None and contours:
                    largest = max(contours, key=cv2.contourArea)
                    if cv2.contourArea(largest) > 5000:
                        epsilon = 0.005 * cv2.arcLength(largest, True)
                        approx = cv2.approxPolyDP(largest, epsilon, True)
                        road_poly = approx.reshape(-1, 2).tolist()
                        tracking_data["road_contours"][str(frame_idx)] = road_poly
            except Exception as e:
                segmentation_failure_count += 1
                road_mask = None
                road_poly = []
                logger.warning(
                    "Road segmentation failed at frame %s; lane filter skipped: %s",
                    frame_idx,
                    e,
                )
                
        # 3. CHẠY SAHI SLICED INFERENCE CHUẨN SOTA VIA UNIFIED ENGINE
        sahi_dets = engine.run_sahi_inference(
            frame=infer_frame,
            model_type=model_type,
            conf_thresh=conf_thresh,
            slice_size=sahi_slice_size,
            overlap_ratio=sahi_overlap,
            device=sahi_device,
            class_names_map=class_names_map,
            model_path_override=config.get("_runtime_model_path"),
            task_override=config.get("_runtime_task"),
            class_mapper=config.get("_class_mapper"),
            class_conf_thresholds=class_thresholds,
        )
        
        raw_frame_dets = []
        for det in sahi_dets:
            if det["class"] in config.get("_excluded_classes", set()):
                continue
            x1, y1, x2, y2 = det["bbox"]
            
            # Require most of the detection footprint to be inside the
            # segmented road. A centre-point check allowed large boxes to
            # visibly extend outside the lane.
            road_overlap = None
            if road_mask is not None:
                polygon = det.get("polygon") or []
                if len(polygon) >= 3:
                    poly = np.asarray(polygon, dtype=np.int32).reshape(-1, 1, 2)
                    defect_mask = np.zeros_like(road_mask, dtype=np.uint8)
                    cv2.fillPoly(defect_mask, [poly], 1)
                    defect_area = int(np.count_nonzero(defect_mask))
                    road_overlap = (
                        float(np.count_nonzero((defect_mask > 0) & (road_mask > 0)))
                        / float(defect_area)
                        if defect_area > 0
                        else 0.0
                    )
                else:
                    ix1, iy1 = max(0, int(x1)), max(0, int(y1))
                    ix2, iy2 = min(w, int(np.ceil(x2))), min(h, int(np.ceil(y2)))
                    if ix2 <= ix1 or iy2 <= iy1:
                        continue
                    roi_mask = road_mask[iy1:iy2, ix1:ix2]
                    road_overlap = float(np.mean(roi_mask > 0))
                if road_overlap < min_road_overlap:
                    continue
                    
            raw_frame_dets.append({
                "frame_index": frame_idx,
                "class": det["class"],
                "class_id": det["class_id"],
                "raw_class_id": det.get("raw_class_id", det["class_id"]),
                "raw_class_name": det.get("raw_class_name", det["class"]),
                "class_mapping_applied": bool(det.get("class_mapping_applied", False)),
                "confidence": det["confidence"],
                "bbox": [x1, y1, x2, y2],
                "polygon": det["polygon"],
                "road_poly": road_poly,
                "road_mask_overlap": road_overlap,
            })
                    
        # 4. TRIỆT TIÊU BOX TRÙNG TRÊN CÙNG FRAME (safety net sau SAHI merge)
        clean_frame_dets = apply_per_frame_nms(raw_frame_dets, iou_thresh=0.30)
        all_raw_detections.extend(clean_frame_dets)
        
        # Cập nhật tiến độ
        if total_frames > 0 and frame_idx % (frame_skip * 20) == 0:
            pct = int(10 + (frame_idx / total_frames) * 65)
            processed_count = min(sampled_total, (frame_idx // frame_skip) + 1)
            elapsed_seconds = max(0.0, time.time() - started_at)
            throughput = processed_count / elapsed_seconds if elapsed_seconds > 0 else 0.0
            remaining_count = max(0, sampled_total - processed_count)
            eta_seconds = int(remaining_count / throughput) if throughput > 0 else 0
            tasks_collection.update_one(
                {"$or": [{"_id": req_id}, {"task_id": req_id}]},
                {"$set": {
                    "progress": f"{pct}%",
                    "status_detail": f"Đang nhận diện AI SAHI SOTA khung hình {frame_idx}/{total_frames}...",
                    "processed_count": processed_count,
                    "total_count": sampled_total,
                    "elapsed_seconds": int(elapsed_seconds),
                    "eta_seconds": eta_seconds,
                    "fps": round(throughput, 2),
                }}
            )
            
        frame_idx += 1
        
    cap.release()

    logger.info(
        "Road segmentation recoveries/skips for %s: %s frames",
        req_id,
        segmentation_failure_count,
    )
    
    # 5. GỘP VẾT NỨT TRÙNG LẶP THEO KHÔNG-THỜI GIAN
    logger.info(f"Clustering {len(all_raw_detections)} clean detections into unique defect entities...")
    clusters = cluster_detections_into_defects(all_raw_detections)
    logger.info(f"✅ Merged into {len(clusters)} UNIQUE DEFECTS for human auditing!")
    
    # Xây dựng lại tracking_data["frames"] chứa toàn bộ các box sạch trên từng frame
    for det in all_raw_detections:
        f_str = str(det["frame_index"])
        if f_str not in tracking_data["frames"]:
            tracking_data["frames"][f_str] = []
            
        tracking_data["frames"][f_str].append({
            "track_id": det.get("global_defect_id", 1),
            "class": det["class"],
            "class_id": det["class_id"],
            "raw_class_id": det.get("raw_class_id", det["class_id"]),
            "raw_class_name": det.get("raw_class_name", det["class"]),
            "class_mapping_applied": bool(det.get("class_mapping_applied", False)),
            "confidence": det["confidence"],
            "bbox": det["bbox"],
            "polygon": det["polygon"]
        })
        
    # 6. TRÍCH XUẤT SNAPSHOT VÀ LƯU TOÀN BỘ CÁC BBOX TRÊN KHUNG HÌNH ĐÓ VÀO MONGODB
    final_elapsed = max(0, int(time.time() - started_at))
    tasks_collection.update_one(
        {"$or": [{"_id": req_id}, {"task_id": req_id}]},
        {"$set": {"progress": "85%", "status_detail": f"Đang xuất {len(clusters)} ảnh sự cố cho người duyệt..."}}
    )
    
    results_collection.delete_many({"task_id": req_id})
    cap = cv2.VideoCapture(file_path)
    
    for cluster in clusters:
        defect_id = cluster["defect_id"]
        best_f_idx = cluster["best_frame_index"]
        best_det = cluster["best_det"]
        
        cap.set(cv2.CAP_PROP_POS_FRAMES, best_f_idx)
        ret, snap_frame = cap.read()
        if not ret or snap_frame is None:
            continue
            
        img_name = f"track_{defect_id}.jpeg"
        img_path = os.path.join(save_dir, img_name)
        cv2.imwrite(img_path, snap_frame)
        
        msec = (best_f_idx / fps) * 1000
        timestamp_str = f"{int(msec / 60000):02d}:{int((msec / 1000) % 60):02d}"
        
        # HIỂN THỊ TOÀN BỘ CÁC VẾT NỨT XUẤT HIỆN TRÊN SNAPSHOT ĐÓ
        frame_all_detections = tracking_data["frames"].get(str(best_f_idx), [{
            "track_id": defect_id,
            "class": best_det["class"],
            "class_id": best_det["class_id"],
            "raw_class_id": best_det.get("raw_class_id", best_det["class_id"]),
            "raw_class_name": best_det.get("raw_class_name", best_det["class"]),
            "class_mapping_applied": bool(best_det.get("class_mapping_applied", False)),
            "confidence": best_det["confidence"],
            "bbox": best_det["bbox"],
            "polygon": best_det["polygon"]
        }])
        
        doc = {
            "task_id": req_id,
            "track_id": defect_id,
            "frame_index": best_f_idx,
            "timestamp": timestamp_str,
            "frameFilePath": get_file_url(img_path),
            "detections": frame_all_detections,
            "road_contour": best_det.get("road_poly", [])
        }
        
        results_collection.insert_one(doc)
        
    cap.release()
    
    # 7. XUẤT FILE tracking_data.json
    tracking_file_name = "tracking_data.json"
    tracking_file_path = os.path.join(save_dir, tracking_file_name)
    with open(tracking_file_path, "w", encoding="utf-8") as f:
        json.dump(tracking_data, f, ensure_ascii=False)
        
    tracking_file_url = get_file_url(tracking_file_path)
    
    tasks_collection.update_one(
        {"$or": [{"_id": req_id}, {"task_id": req_id}]},
        {"$set": {
            "processingStatus": "xử lý xong",
            "trackingDataUrl": tracking_file_url,
                    "progress": "100%",
                    "processed_count": sampled_total,
                    "total_count": sampled_total,
                    "elapsed_seconds": final_elapsed,
                    "eta_seconds": 0,
            "status_detail": f"Hoàn thành xử lý SAHI SOTA! Tìm thấy {len(clusters)} sự cố độc nhất."
        }}
    )
    
    logger.info(f"🎉 SAHI SOTA video pipeline completed with {len(clusters)} unique defects for task={req_id}")
    return {"status": True, "trackingDataUrl": tracking_file_url}
