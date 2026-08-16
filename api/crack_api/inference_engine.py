"""
Unified SOTA Inference Engine — Lõi suy luận AI chuẩn hóa dùng chung cho:
1. Ảnh Đơn (Image API & Celery Task)
2. Video Offline (Video API & Direct Video Pipeline)
3. RTSP Live Stream (WebSocket Real-time Stream)

Tích hợp:
- SAHI SOTA (obss/sahi) với GREEDYNMM merge & IOS metric
- TTA (Test-Time Augmentation) đa góc lật / tỷ lệ
- Hỗ trợ trọn vẹn OBB (Oriented Bounding Box) cho Đường & Segment cho Cầu
- Hỗ trợ trọn vẹn TensorRT (.engine), ONNX (.onnx), và PyTorch (.pt)
- BoT-SORT với GMC (Global Motion Compensation - Bù chuyển động Drone)
- Preprocessing CLAHE contrast enhancement
"""

import cbam_module
import sys
import os
import cv2
import time
import logging
import torch
import torch.nn as nn
import numpy as np
from ultralytics import YOLO
import ultralytics.nn.modules as ultralytics_modules
from sahi.models.ultralytics import UltralyticsDetectionModel
from sahi.predict import get_sliced_prediction
from sahi.slicing import slice_image
from sahi.postprocess.combine import GreedyNMMPostprocess

logger = logging.getLogger("crack_api.inference_engine")

# ================================================================
# ĐẮNG KÝ CBAM ATTENTION MODULE (SOTA ATTENTION FOR OBB & SEGMENT)
# ================================================================
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


# Đăng ký CBAM vào Ultralytics NN Modules và __main__ namespace
for mod in [ultralytics_modules, sys.modules["__main__"]]:
    setattr(mod, "CBAM", CBAM)
    setattr(mod, "ChannelAttention", ChannelAttention)
    setattr(mod, "SpatialAttention", SpatialAttention)

# Global Cache cho SAHI và YOLO Models
_sahi_model_cache = {}
_yolo_model_cache = {}


def _polygon_from_sahi_prediction(pred, task: str | None) -> list[list[float]]:
    """
    Preserve native OBB vertices.

    SAHI exposes an OBB through ``pred.mask.segmentation``. Rasterizing that
    polygon into ``bool_mask`` and finding its contour again changes a
    four-corner OBB into a jagged 5-8 point polygon, especially at image
    boundaries. Segmentation models still use the contour path.
    """
    mask = getattr(pred, "mask", None)
    if mask is None:
        return []

    def segmentation_points() -> list[list[float]]:
        segmentation = getattr(mask, "segmentation", None)
        if not segmentation:
            return []
        candidate = segmentation[0] if isinstance(segmentation, list) else segmentation
        if not candidate:
            return []
        if isinstance(candidate[0], (list, tuple, np.ndarray)):
            return [
                [float(point[0]), float(point[1])]
                for point in candidate
                if len(point) >= 2
            ]
        return [
            [float(candidate[index]), float(candidate[index + 1])]
            for index in range(0, len(candidate) - 1, 2)
        ]

    if task == "obb":
        points = segmentation_points()
        if len(points) == 4:
            return points
        if len(points) >= 3:
            # Defensive fallback for adapters that clip/rasterize at borders.
            rect = cv2.minAreaRect(np.asarray(points, dtype=np.float32))
            return [
                [float(point[0]), float(point[1])]
                for point in cv2.boxPoints(rect)
            ]
        return []

    if getattr(mask, "bool_mask", None) is not None:
        mask_arr = (mask.bool_mask * 255).astype(np.uint8)
        contours, _ = cv2.findContours(
            mask_arr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if contours:
            contour = max(contours, key=cv2.contourArea)
            epsilon = 0.005 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)
            return [
                [float(point[0][0]), float(point[0][1])]
                for point in approx
            ]
    return segmentation_points()


class CustomUltralyticsDetectionModel(UltralyticsDetectionModel):
    """
    Wrapper SAHI cho phép nạp trực tiếp mô hình TensorRT (.engine), ONNX (.onnx) và PyTorch (.pt).
    Tự động thiết lập category_mapping, hỗ trợ TTA (Test-Time Augmentation) và tham số task (detect, segment, obb).
    """
    def __init__(self, model_path=None, task=None, use_tta=True, **kwargs):
        self.task = task
        self.use_tta = use_tta
        super().__init__(model_path=model_path, **kwargs)

    def load_model(self):
        if self.task:
            model = YOLO(self.model_path, task=self.task)
        else:
            model = YOLO(self.model_path)

        # Thiết lập cờ task cho SAHI để bóc tách chính xác Mask (Segment) và Polygon (OBB)
        if self.task == "segment":
            self._has_mask = True
            self._is_obb = False
        elif self.task == "obb":
            self._has_mask = False
            self._is_obb = True
        else:
            self._has_mask = getattr(model, "overrides", {}).get("task") == "segment"
            self._is_obb = getattr(model, "overrides", {}).get("task") == "obb"

        # Khởi tạo category_mapping từ model.names nếu chưa được cung cấp
        if self.category_mapping is None:
            names = getattr(model, "names", None)
            if names is None and hasattr(model, "model") and hasattr(model.model, "names"):
                names = model.model.names
            if names:
                if isinstance(names, dict):
                    self.category_mapping = {str(k): str(v) for k, v in names.items()}
                elif isinstance(names, (list, tuple)):
                    self.category_mapping = {str(i): str(v) for i, v in enumerate(names)}

        # Chỉ gọi .to(device) với file trọng số PyTorch .pt
        if hasattr(model, "to") and str(self.model_path).endswith(".pt"):
            try:
                model.to(self.device)
            except Exception as e:
                logger.warning(f"Could not move PyTorch model to {self.device}: {e}")

        self.model = model

    def perform_batch_inference(self, images: list[np.ndarray]) -> None:
        """
        Thực hiện suy luận batch kết hợp TTA (Test-Time Augmentation) SOTA.
        """
        if self.model is None:
            raise ValueError("Model is not loaded, load it by calling .load_model()")

        kwargs = {
            "cfg": self.config_path,
            "verbose": False,
            "conf": self.confidence_threshold,
            "device": self.device,
        }

        # Kích hoạt TTA (Test-Time Augmentation) cho PyTorch weights để tăng độ nhạy vết nứt nhỏ
        if self.use_tta and str(self.model_path).endswith(".pt"):
            kwargs["augment"] = True

        if self.image_size is not None:
            kwargs["imgsz"] = self.image_size

        # YOLO expects BGR convert each image
        images_bgr = [img[:, :, ::-1] for img in images]
        prediction_result = self.model(images_bgr, **kwargs)

        if self.has_mask:
            from ultralytics.engine.results import Masks

            converted = []
            for result in prediction_result:
                if not result.masks:
                    result.masks = Masks(
                        torch.tensor([], device=getattr(self.model, "device", "cpu")),
                        result.boxes.orig_shape,
                    )
                converted.append((result.boxes.data, result.masks.data))
            prediction_result = converted
        elif self.is_obb:
            converted = []
            device = getattr(self.model, "device", "cpu")
            for result in prediction_result:
                if result.obb is None:
                    converted.append(
                        (
                            torch.empty((0, 6), device=device),
                            torch.empty((0, 4, 2), device=device),
                        )
                    )
                else:
                    converted.append(
                        (
                            torch.cat(
                                [
                                    result.obb.xyxy,
                                    result.obb.conf.unsqueeze(-1),
                                    result.obb.cls.unsqueeze(-1),
                                ],
                                dim=1,
                            ),
                            result.obb.xyxyxyxy,
                        )
                    )
            prediction_result = converted
        else:
            prediction_result = [result.boxes.data for result in prediction_result]

        self._original_predictions = prediction_result
        self._original_shapes = [img.shape for img in images]
        # SAHI's current mask converter references the single-image attribute.
        # Folder batches are homogeneous; retain it for adapter compatibility.
        self._original_shape = images[0].shape


def get_sahi_model(model_path: str, conf_thresh: float, device: str, class_names_map: dict = None, task: str = None, use_tta: bool = True):
    """
    Tái sử dụng hoặc nạp mới SAHI Detection Model với Model Caching & TTA.
    """
    cache_key = f"{model_path}_{device}_{conf_thresh}_{task}_{use_tta}"
    if cache_key not in _sahi_model_cache:
        logger.info(f"🚀 Initializing SOTA SAHI Engine (task={task}, TTA={use_tta}, path={model_path}, device={device})...")
        cat_map = {str(k): str(v) for k, v in class_names_map.items()} if class_names_map else None
        _sahi_model_cache[cache_key] = CustomUltralyticsDetectionModel(
            model_path=model_path,
            confidence_threshold=conf_thresh,
            device=device,
            category_mapping=cat_map,
            task=task,
            use_tta=use_tta,
        )
    return _sahi_model_cache[cache_key]


def get_yolo_model(model_path: str, task: str = None):
    """
    Tái sử dụng hoặc nạp mới YOLO Model instance cho tracking/direct prediction.
    """
    cache_key = f"{model_path}_{task}"
    if cache_key not in _yolo_model_cache:
        logger.info(f"🚀 Initializing YOLO Model (task={task}, path={model_path})...")
        if task:
            _yolo_model_cache[cache_key] = YOLO(model_path, task=task)
        else:
            _yolo_model_cache[cache_key] = YOLO(model_path)
    return _yolo_model_cache[cache_key]


class UnifiedSOTAInferenceEngine:
    """
    Engine Suy luận lõi dùng chung cho Ảnh, Video Offline, và Live Stream.
    """
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))

    @staticmethod
    def class_confidence_threshold(
        class_conf_thresholds: dict | None,
        class_id: int,
        class_name: str,
        default: float,
    ) -> float:
        """Resolve a class-specific threshold with a safe global fallback."""
        if not class_conf_thresholds:
            return float(default)
        value = class_conf_thresholds.get(str(class_id), class_conf_thresholds.get(class_id))
        if value is None:
            value = class_conf_thresholds.get(class_name, default)
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return float(default)

    def preprocess_frame(
        self,
        frame: np.ndarray,
        model_type: str = "road",
        color_normalization_enabled: bool = False,
    ) -> np.ndarray:
        """
        Optionally normalize road image or video frames to three-channel grayscale.

        Every caller explicitly opts in. Bridge inputs are always preserved
        because the bridge segmentation model uses its original colour domain.
        """
        if frame is None or frame.size == 0:
            return frame

        if model_type == "road" and color_normalization_enabled:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        return frame.copy()

    def resolve_model_path(self, model_type: str, device: str) -> tuple[str, str]:
        """
        Xác định file weights chuẩn trong thư mục weights/ của API và kiểu task:
        - Road ➔ task = "obb"
        - Bridge ➔ task = "segment" (Instance Segmentation)
        Tự động tìm kiếm linh hoạt các tên file: crack_bridge.pt, bridge_crack.pt, bridge.pt, best.pt.
        """
        from main import MODEL_CONFIGS
        cfg = MODEL_CONFIGS.get(model_type, {})
        default_pt = cfg.get("path", f"weights/crack_{model_type}.pt")
        task = "obb" if model_type == "road" else "segment"

        candidates = [
            default_pt,
            f"weights/crack_{model_type}.pt",
            f"weights/{model_type}_crack.pt",
            f"weights/{model_type}.pt",
            "weights/best.pt"
        ]
        
        pt_path = default_pt
        for cand in candidates:
            if os.path.exists(cand):
                pt_path = cand
                break

        engine_path = pt_path.replace(".pt", ".engine")
        if (
            cfg.get("trt_enabled", True)
            and os.path.exists(engine_path)
            and device != "cpu"
        ):
            return engine_path, task

        return pt_path, task

    def run_sahi_batch_inference(
        self,
        frames: list[np.ndarray],
        model_type: str = "road",
        conf_thresh: float = 0.35,
        slice_size: int = 640,
        overlap_ratio: float = 0.25,
        device: str = "cuda:0",
        class_names_map: dict = None,
        use_tta: bool = True,
        model_path_override: str = None,
        task_override: str = None,
        class_mapper=None,
        inference_batch_size: int = 2,
        class_conf_thresholds: dict | None = None,
    ) -> list[list[dict]]:
        """
        Quality-equivalent SAHI for many images.

        It preserves full-frame + every overlapping tile + GREEDYNMM/IOS, but
        sends multiple tiles through TensorRT together. No source image is
        downscaled or skipped.
        """
        if not frames:
            return []
        if model_path_override:
            model_path = model_path_override
            task = task_override
        else:
            model_path, task = self.resolve_model_path(model_type, device)

        sahi_model = get_sahi_model(
            model_path=model_path,
            conf_thresh=float(conf_thresh),
            device=device,
            class_names_map=class_names_map,
            task=task,
            use_tta=use_tta,
        )
        tile_images = []
        tile_owners = []
        tile_shifts = []
        tile_shapes = []
        per_image_predictions = [[] for _ in frames]

        for owner_index, frame in enumerate(frames):
            height, width = frame.shape[:2]
            # Preserve the standard full-frame prediction used by the current
            # production SAHI configuration.
            tile_images.append(frame)
            tile_owners.append(owner_index)
            tile_shifts.append([0, 0])
            tile_shapes.append([height, width])

            sliced = slice_image(
                image=frame,
                slice_height=slice_size,
                slice_width=slice_size,
                overlap_height_ratio=overlap_ratio,
                overlap_width_ratio=overlap_ratio,
                auto_slice_resolution=False,
                verbose=False,
            )
            for tile, shift in zip(sliced.images, sliced.starting_pixels):
                tile_images.append(tile)
                tile_owners.append(owner_index)
                tile_shifts.append(list(shift))
                tile_shapes.append([height, width])

        effective_batch = max(1, int(inference_batch_size))
        for start in range(0, len(tile_images), effective_batch):
            end = min(start + effective_batch, len(tile_images))
            image_batch = tile_images[start:end]
            shift_batch = tile_shifts[start:end]
            shape_batch = tile_shapes[start:end]
            owner_batch = tile_owners[start:end]

            sahi_model.perform_batch_inference(image_batch)
            sahi_model.convert_original_predictions(
                shift_amount=shift_batch,
                full_shape=shape_batch,
            )
            converted = sahi_model.object_prediction_list_per_image
            for owner, predictions in zip(owner_batch, converted):
                per_image_predictions[owner].extend(
                    prediction.get_shifted_object_prediction()
                    for prediction in predictions
                )

        postprocess = GreedyNMMPostprocess(
            match_threshold=0.5,
            match_metric="IOS",
            class_agnostic=False,
        )
        output = []
        for object_predictions in per_image_predictions:
            merged = postprocess(object_predictions)
            detections = []
            for pred in merged:
                bbox = pred.bbox
                raw_class_id = int(pred.category.id)
                raw_class_name = (
                    class_names_map.get(raw_class_id)
                    if class_names_map and raw_class_id in class_names_map
                    else pred.category.name
                )
                if not raw_class_name:
                    continue
                if class_mapper is not None:
                    mapped_class = class_mapper.remap_detection(
                        raw_class_id, raw_class_name
                    )
                else:
                    mapped_class = {
                        "raw_class_id": raw_class_id,
                        "raw_class_name": str(raw_class_name),
                        "class_id": raw_class_id,
                        "class_name": str(raw_class_name),
                        "class_mapping_applied": False,
                    }

                polygon_points = _polygon_from_sahi_prediction(pred, task)
                score = float(pred.score.value)
                if score < self.class_confidence_threshold(
                    class_conf_thresholds,
                    raw_class_id,
                    str(raw_class_name),
                    float(conf_thresh),
                ):
                    continue

                detections.append(
                    {
                        "class": mapped_class["class_name"],
                        "class_id": mapped_class["class_id"],
                        "raw_class_id": mapped_class["raw_class_id"],
                        "raw_class_name": mapped_class["raw_class_name"],
                        "class_mapping_applied": mapped_class["class_mapping_applied"],
                        "confidence": score,
                        "bbox": [
                            round(float(bbox.minx), 2),
                            round(float(bbox.miny), 2),
                            round(float(bbox.maxx), 2),
                            round(float(bbox.maxy), 2),
                        ],
                        "polygon": polygon_points or None,
                    }
                )
            output.append(detections)
        return output

    def run_sahi_inference(
        self,
        frame: np.ndarray,
        model_type: str = "road",
        conf_thresh: float = 0.35,
        slice_size: int = 640,
        overlap_ratio: float = 0.25,
        device: str = "cuda:0",
        class_names_map: dict = None,
        use_tta: bool = True,
        model_path_override: str = None,
        task_override: str = None,
        class_mapper=None,
        class_conf_thresholds: dict | None = None,
    ) -> list:
        """
        Thực hiện SAHI Sliced Prediction chuẩn SOTA kết hợp TTA.
        Chạy CẢ full-frame LẪN sliced tiles ➔ Merge bằng GREEDYNMM + IOS.
        """
        if model_path_override:
            model_path = model_path_override
            task = task_override
        else:
            model_path, task = self.resolve_model_path(model_type, device)
        effective_conf = float(conf_thresh)
        
        sahi_model = get_sahi_model(
            model_path=model_path,
            conf_thresh=effective_conf,
            device=device,
            class_names_map=class_names_map,
            task=task,
            use_tta=use_tta,
        )
        
        sahi_result = get_sliced_prediction(
            image=frame,
            detection_model=sahi_model,
            slice_height=slice_size,
            slice_width=slice_size,
            overlap_height_ratio=overlap_ratio,
            overlap_width_ratio=overlap_ratio,
            perform_standard_pred=True,                 # ★ CHẠY CẢ FULL-FRAME VÀ SLICED
            postprocess_type="GREEDYNMM",               # ★ MERGE chuẩn SOTA
            postprocess_match_metric="IOS",              # Intersection Over Smaller Area
            postprocess_match_threshold=0.5,            # Ngưỡng merge IoU/IOS
            postprocess_class_agnostic=False,
            verbose=0,
        )
        
        detections = []
        for pred in sahi_result.object_prediction_list:
            bbox = pred.bbox
            x1, y1, x2, y2 = float(bbox.minx), float(bbox.miny), float(bbox.maxx), float(bbox.maxy)
            score = float(pred.score.value)
            raw_class_id = int(pred.category.id)
            
            if class_names_map and raw_class_id in class_names_map:
                raw_class_name = class_names_map[raw_class_id]
            else:
                raw_class_name = pred.category.name

            # Bỏ qua nếu tên lớp là None (do thuộc các lớp dư thừa không nằm trong 7 lớp Cầu / 3 lớp Đường)
            if not raw_class_name:
                continue

            if class_mapper is not None:
                mapped_class = class_mapper.remap_detection(
                    raw_class_id, raw_class_name
                )
            else:
                mapped_class = {
                    "raw_class_id": raw_class_id,
                    "raw_class_name": str(raw_class_name),
                    "class_id": raw_class_id,
                    "class_name": str(raw_class_name),
                    "class_mapping_applied": False,
                }

            if score < self.class_confidence_threshold(
                class_conf_thresholds,
                raw_class_id,
                str(raw_class_name),
                effective_conf,
            ):
                continue

            # Keep native four-corner OBB coordinates; only segmentation models
            # are converted from masks to contours.
            polygon_points = _polygon_from_sahi_prediction(pred, task)

            if not polygon_points:
                polygon_points = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

            det_obj = {
                "class": mapped_class["class_name"],
                "class_id": mapped_class["class_id"],
                "raw_class_id": mapped_class["raw_class_id"],
                "raw_class_name": mapped_class["raw_class_name"],
                "class_mapping_applied": mapped_class["class_mapping_applied"],
                "confidence": score,
                "bbox": [x1, y1, x2, y2],
                "polygon": polygon_points,
                "task_type": task,
            }
            
            detections.append(det_obj)
            
        return detections
