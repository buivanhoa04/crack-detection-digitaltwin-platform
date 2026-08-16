import os
import cv2
import torch
import numpy as np
import logging
from PIL import Image

try:
    from transformers import SegformerImageProcessor, SegformerForSemanticSegmentation
except ImportError:
    SegformerImageProcessor = None
    SegformerForSemanticSegmentation = None

logger = logging.getLogger("crack_api")

class RoadSegmenter:
    """
    Module xử lý Road Segmentation hỗ trợ SegFormer và PIDNet-S (ONNX).
    Tạo Mask cho vùng mặt đường và làm đen bối cảnh xung quanh.
    """
    def __init__(self, model_id="chribark/segformer-b3-finetuned-UAVid", device="cuda", use_yolo_filter=False):
        self.device = torch.device(device)
        self.model_id = model_id
        self.processor = None
        self.model = None
        self.onnx_session = None
        self.onnx_input_name = None
        self.onnx_output_names = None
        
        # Nhận diện kiểu mô hình ONNX
        self.is_onnx = model_id.endswith(".onnx") or model_id.lower() == "pidnet-s"
        
        # Mặc định: 0 cho Cityscapes (SegFormer), 2 cho UAVid (PIDNet-S)
        self.road_class_id = 2 if self.is_onnx else 1
        self.is_loaded = False

        # Tải bộ lọc phương tiện giao thông (YOLO26n) để khử nhiễu xe cộ nếu bật cấu hình
        self.vehicle_detector = None
        if use_yolo_filter:
            yolo_weights = "weights/yolo26n.pt"
            if os.path.exists(yolo_weights):
                try:
                    from ultralytics import YOLO
                    self.vehicle_detector = YOLO(yolo_weights)
                    logger.info(f"✅ Load bộ lọc xe cộ YOLO26n thành công từ '{yolo_weights}'")
                except Exception as e:
                    logger.error(f"❌ Không thể khởi tạo bộ lọc xe cộ YOLO26n: {e}")
            else:
                logger.warning(f"⚠️ Không tìm thấy file weights bộ lọc xe cộ tại '{yolo_weights}'")

    def load_model(self):
        # ── LOAD MÔ HÌNH ONNX (PIDNet-S) ──
        if self.is_onnx:
            onnx_path = self.model_id if self.model_id.endswith(".onnx") else "weights/pidnet_s.onnx"
            if not os.path.exists(onnx_path):
                logger.warning(f"⚠️ Không tìm thấy file weights ONNX tại '{onnx_path}'. Tự động fallback về SegFormer...")
                self.is_onnx = False
                self.model_id = "chribark/segformer-b3-finetuned-UAVid"
                self.road_class_id = 1
            else:
                try:
                    import onnxruntime as ort
                    logger.info(f"Đang load PIDNet-S ONNX từ {onnx_path}...")
                    
                    # Chọn Provider theo phần cứng khả dụng
                    providers = ["CPUExecutionProvider"]
                    if self.device.type == "cuda" and "CUDAExecutionProvider" in ort.get_available_providers():
                        providers.insert(0, "CUDAExecutionProvider")
                        
                    self.onnx_session = ort.InferenceSession(onnx_path, providers=providers)
                    self.onnx_input_name = self.onnx_session.get_inputs()[0].name
                    self.onnx_output_names = [o.name for o in self.onnx_session.get_outputs()]
                    self.is_loaded = True
                    logger.info(f"✅ Load PIDNet-S ONNX thành công (Providers: {self.onnx_session.get_providers()})")
                    return True
                except Exception as e:
                    logger.error(f"❌ Lỗi load PIDNet-S ONNX: {e}. Thử fallback về SegFormer...", exc_info=True)
                    self.is_onnx = False
                    self.model_id = "chribark/segformer-b3-finetuned-UAVid"
                    self.road_class_id = 1

        # ── LOAD MÔ HÌNH HUGGING FACE (SegFormer) ──
        if not self.is_onnx:
            if SegformerImageProcessor is None or SegformerForSemanticSegmentation is None:
                logger.error("Thư viện 'transformers' chưa được cài đặt. Không thể load SegFormer.")
                return False

            actual_model_id = self.model_id
            local_path = "weights/segformer"
            if os.path.isdir(local_path):
                actual_model_id = local_path
                logger.info(f"Phát hiện mô hình SegFormer cục bộ tại '{local_path}'. Sẽ load offline từ đây.")

            logger.info(f"Đang load SegFormer từ {actual_model_id} lên {self.device}...")
            try:
                self.processor = SegformerImageProcessor.from_pretrained(actual_model_id)
                self.model = SegformerForSemanticSegmentation.from_pretrained(actual_model_id).to(self.device)
                self.model.eval()
                
                # ── SOTA: FP16 + torch.compile ──
                if self.device.type == "cuda":
                    self.model = self.model.half()
                    logger.info("✅ SegFormer converted to FP16 (half precision)")
                try:
                    import torch
                    should_compile = True
                    if torch.cuda.is_available():
                        vram_bytes = torch.cuda.get_device_properties(0).total_memory
                        vram_gb = vram_bytes / (1024 ** 3)
                        if vram_gb < 5.0:
                            should_compile = False
                            logger.info(f"⚠️ VRAM quá thấp ({vram_gb:.1f}GB < 5GB) → Bỏ qua torch.compile(reduce-overhead) trên máy test để tránh lag/OOM.")
                    
                    if should_compile and hasattr(torch, 'compile'):
                        self.model = torch.compile(self.model, mode="reduce-overhead")
                        logger.info("✅ SegFormer optimized with torch.compile(reduce-overhead)")
                except Exception as compile_err:
                    logger.warning(f"⚠️ torch.compile not available or failed: {compile_err}")
                
                # Tự động map ID lớp 'road' hoặc 'paved-area' nếu có
                if hasattr(self.model.config, 'label2id'):
                    label_keys = [k.lower() for k in self.model.config.label2id.keys()]
                    # Map các class phổ biến cho Road
                    if 'road' in label_keys:
                        for k, v in self.model.config.label2id.items():
                            if k.lower() == 'road': self.road_class_id = v
                    elif 'paved-area' in label_keys:
                        for k, v in self.model.config.label2id.items():
                            if k.lower() == 'paved-area': self.road_class_id = v
                    elif 'paved_area' in label_keys:
                        for k, v in self.model.config.label2id.items():
                            if k.lower() == 'paved_area': self.road_class_id = v
                
                self.is_loaded = True
                logger.info(f"✅ Load SegFormer thành công (Road Class ID: {self.road_class_id})")
                return True
            except Exception as e:
                logger.error(f"❌ Lỗi khi load SegFormer: {e}", exc_info=True)
                return False

    def get_road_mask(self, image_np: np.ndarray, left_bound_ratio: float = 0.0, right_bound_ratio: float = 1.0) -> np.ndarray:
        """
        Dự đoán và trả về nhị phân Mask (255 cho đường, 0 cho phần còn lại).
        Kích thước mask trả về bằng với kích thước ảnh gốc.
        """
        orig_h, orig_w = image_np.shape[:2]

        if not self.is_loaded:
            # Never fabricate a road polygon.  The caller must fail clearly
            # when lane filtering was requested but the model is unavailable.
            logger.error("Road segmentation requested before the model was loaded.")
            return None


        # ── SUY LUẬN BẰNG ONNX (PIDNet-S) ──
        if self.is_onnx:
            try:
                # 1. Chuyển ảnh BGR -> RGB
                rgb_image = cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
                
                # Lấy input shape động của model
                input_shape = self.onnx_session.get_inputs()[0].shape
                model_h = input_shape[2] if (len(input_shape) > 2 and isinstance(input_shape[2], int)) else 1024
                model_w = input_shape[3] if (len(input_shape) > 3 and isinstance(input_shape[3], int)) else 1024
                
                # 2. Resize ảnh về kích thước model yêu cầu
                resized_img = cv2.resize(rgb_image, (model_w, model_h))
                
                # 3. Chuẩn hóa (ImageNet Mean/Std)
                mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
                std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
                normalized_img = (resized_img.astype(np.float32) / 255.0 - mean) / std
                
                # 4. HWC -> CHW -> BCHW
                input_data = np.transpose(normalized_img, (2, 0, 1))
                input_data = np.expand_dims(input_data, axis=0)
                
                # 5. Chạy ONNX Session
                outputs = self.onnx_session.run(self.onnx_output_names, {self.onnx_input_name: input_data})
                logits = outputs[0]  # shape (1, num_classes, model_h, model_w)
                
                # 6. Lấy mask lớp có điểm cao nhất
                predicted_mask = np.argmax(logits, axis=1).squeeze().astype(np.uint8)
                
                # 7. Resize mask về kích thước ảnh gốc dùng INTER_NEAREST
                pred_mask_resized = cv2.resize(predicted_mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
                
                # 8. Trích xuất nhãn đường
                road_mask = (pred_mask_resized == self.road_class_id).astype(np.uint8) * 255
                
                # 1. Lấp lỗ hổng bên trong lòng đường
                kernel_close = np.ones((5, 5), np.uint8)
                road_mask = cv2.morphologyEx(road_mask, cv2.MORPH_CLOSE, kernel_close, iterations=2)
                
                # 2. Xóa nhiễu rác lân cận (Open)
                kernel_open = np.ones((9, 9), np.uint8)
                road_mask = cv2.morphologyEx(road_mask, cv2.MORPH_OPEN, kernel_open, iterations=2)
                
                # 3. Giữ lại vùng diện tích (component) lớn nhất (lọc bỏ đất/cỏ bị nhận diện nhầm xa đường)
                num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(road_mask, connectivity=8)
                if num_labels > 1:
                    largest_label = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
                    road_mask = (labels == largest_label).astype(np.uint8) * 255
                    
                # 4. Co nhỏ đường viền mạnh tay (Erode) dựa trên tỷ lệ ảnh để đảm bảo không bị tràn ra mép đất/cỏ
                erode_size = max(31, int(orig_w * 0.04)) # 4% chiều ngang ảnh (ví dụ ảnh 1920 thì erode ~ 76px)
                kernel_erode = np.ones((erode_size, erode_size), np.uint8)
                road_mask = cv2.erode(road_mask, kernel_erode, iterations=1)
                
                # Cắt biên lề đường hai bên trái/phải đối với ảnh góc nhìn drone nếu cấu hình
                if road_mask is not None and (left_bound_ratio > 0.0 or right_bound_ratio < 1.0):
                    left_bound = int(left_bound_ratio * orig_w)
                    right_bound = int(right_bound_ratio * orig_w)
                    road_mask[:, :left_bound] = 0
                    road_mask[:, right_bound:] = 0
                
                return self._apply_vehicle_mask(road_mask, image_np)
            except Exception as e:
                logger.error(f"❌ Lỗi chạy suy luận PIDNet-S ONNX: {e}", exc_info=True)
                return None

        # ── SUY LUẬN BẰNG PYTORCH HUGGING FACE (SegFormer) ──
        else:
            try:
                rgb_image = cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
                pil_image = Image.fromarray(rgb_image)
                
                with torch.no_grad():
                    inputs = self.processor(images=pil_image, return_tensors="pt").to(self.device)
                    # Cast inputs to FP16 if model is half precision
                    if next(self.model.parameters()).dtype == torch.float16:
                        inputs = {k: v.half() if v.is_floating_point() else v for k, v in inputs.items()}
                    outputs = self.model(**inputs)
                    logits = outputs.logits
                    
                    upsampled_logits = torch.nn.functional.interpolate(
                        logits,
                        size=(orig_h, orig_w),
                        mode="bilinear",
                        align_corners=False
                    )
                    
                    predicted_mask = upsampled_logits.argmax(dim=1).squeeze().cpu().numpy()
                    road_mask = (predicted_mask == self.road_class_id).astype(np.uint8) * 255
                    
                    # 1. Lấp lỗ hổng bên trong lòng đường
                    kernel_close = np.ones((5, 5), np.uint8)
                    road_mask = cv2.morphologyEx(road_mask, cv2.MORPH_CLOSE, kernel_close, iterations=2)
                    
                    # 2. Xóa nhiễu rác lân cận (Open)
                    kernel_open = np.ones((9, 9), np.uint8)
                    road_mask = cv2.morphologyEx(road_mask, cv2.MORPH_OPEN, kernel_open, iterations=2)
                    
                    # 3. Giữ lại vùng diện tích (component) lớn nhất (lọc bỏ đất/cỏ bị nhận diện nhầm xa đường)
                    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(road_mask, connectivity=8)
                    if num_labels > 1:
                        largest_label = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
                        road_mask = (labels == largest_label).astype(np.uint8) * 255
                        
                    # 4. Co nhỏ đường viền nhẹ (Erode 1%) vì model UAVid đã phân loại vỉa hè và cỏ rất tốt
                    erode_size = max(11, int(orig_w * 0.01))
                    kernel_erode = np.ones((erode_size, erode_size), np.uint8)
                    road_mask = cv2.erode(road_mask, kernel_erode, iterations=1)
                    
                    # Cắt biên lề đường hai bên trái/phải đối với ảnh góc nhìn drone nếu cấu hình
                    if road_mask is not None and (left_bound_ratio > 0.0 or right_bound_ratio < 1.0):
                        left_bound = int(left_bound_ratio * orig_w)
                        right_bound = int(right_bound_ratio * orig_w)
                        road_mask[:, :left_bound] = 0
                        road_mask[:, right_bound:] = 0
                    
                    return self._apply_vehicle_mask(road_mask, image_np)
            except Exception as e:
                logger.error(f"❌ Lỗi chạy suy luận SegFormer PyTorch: {e}", exc_info=True)
                return None

    def _apply_vehicle_mask(self, road_mask: np.ndarray, image_np: np.ndarray) -> np.ndarray:
        if road_mask is None or self.vehicle_detector is None:
            return road_mask
            
        orig_h, orig_w = road_mask.shape[:2]
        try:
            # Chạy YOLO26n phát hiện xe cộ
            results = self.vehicle_detector(image_np, verbose=False)
            if results and len(results) > 0:
                boxes = results[0].boxes
                for box in boxes:
                    cls_id = int(box.cls[0].item())
                    # class ID trong COCO: 2: car, 3: motorcycle, 5: bus, 7: truck
                    if cls_id in [2, 3, 5, 7]:
                        xyxy = box.xyxy[0].cpu().numpy()
                        x1, y1, x2, y2 = map(int, xyxy)
                        
                        # Thêm biên đệm pad = 15px để bao phủ bóng xe và góc khuất
                        pad = 15
                        x1 = max(0, x1 - pad)
                        y1 = max(0, y1 - pad)
                        x2 = min(orig_w, x2 + pad)
                        y2 = min(orig_h, y2 + pad)
                        
                        # Khoét rỗng (set mặt nạ về 0)
                        road_mask[y1:y2, x1:x2] = 0
        except Exception as e:
            logger.error(f"❌ Lỗi lọc phương tiện trong _apply_vehicle_mask: {e}")
            
        return road_mask

    def process(self, frame: np.ndarray) -> np.ndarray:
        """
        Đưa ảnh qua Segmentation, làm đen các pixel không phải mặt đường.
        Nếu model lỗi, trả về ảnh gốc.
        """
        if not self.is_loaded:
            return frame
            
        mask = self.get_road_mask(frame)
        if mask is None:
            return frame
            
        masked_frame = cv2.bitwise_and(frame, frame, mask=mask)
        return masked_frame
