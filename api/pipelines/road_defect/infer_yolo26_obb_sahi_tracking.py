import os
import cv2
import math
import time
import numpy as np
import onnxruntime as ort
import supervision as sv
from tqdm import tqdm

# ================================================================
# CẤU HÌNH ĐƯỜNG DẪN & THAM SỐ
# ================================================================
MODEL_PATH = r"D:\API\yolo26_train_pipeline\output_obb_sota\yolo26m_cbam_obb_sota\weights\best.onnx"
INPUT_VIDEO_PATH = r"D:\Drone\test.MP4"
OUTPUT_VIDEO_PATH = r"D:\Drone\test_annotated_sahi_track.mp4"

# Tham số phát hiện & SAHI
CONF_THRESHOLD = 0.25     # Độ tin cậy tối thiểu
NMS_THRESHOLD = 0.40      # Ngưỡng đè lấp NMS hộp xoay
TILE_SIZE = (640, 640)    # Kích thước tile SAHI
OVERLAP_RATIO = 0.20      # Độ lấp chồng giữa các tile (20%)

# Định nghĩa các lớp hư hỏng mặt đường
CLASS_NAMES = {
    0: "Nut ca sau",       # Crocodile / Alligator Cracking
    1: "Nut doc/ngang",    # Longitudinal / Transverse Crack
    2: "O ga / Bong bat"   # Pothole / Peeling
}

# Bảng màu sắc trực quan (BGR Format)
CLASS_COLORS = {
    0: (0, 0, 255),      # Đỏ (Nứt cá sấu - Nguy hiểm cao)
    1: (0, 255, 255),    # Vàng (Vết nứt - Trung bình)
    2: (0, 165, 255)     # Cam (Ổ gà / Bóc tách - Cần vá)
}

# ================================================================
# CLASS ĐỐI TƯỢNG HỘP XOAY OBB (ORIENTED BOUNDING BOX)
# ================================================================
class OBBDetection:
    def __init__(self, cx, cy, w, h, angle_rad, score, class_id):
        self.cx = float(cx)
        self.cy = float(cy)
        self.w = float(w)
        self.h = float(h)
        self.angle_rad = float(angle_rad)
        self.angle_deg = float(math.degrees(angle_rad))
        self.score = float(score)
        self.class_id = int(class_id)
        self.class_name = CLASS_NAMES.get(self.class_id, f"Class {self.class_id}")
        self.track_id = -1  # Gán mặc định, sẽ được ByteTrack cập nhật

        # Tính toán 4 đỉnh của hình chữ nhật xoay (Rotated Polygon Corners)
        self.points = self._compute_corners()

        # Tính toán Envelope Bounding Box (Axis-Aligned Bounding Box - AABB) cho Tracker
        x_min = np.min(self.points[:, 0])
        y_min = np.min(self.points[:, 1])
        x_max = np.max(self.points[:, 0])
        y_max = np.max(self.points[:, 1])
        self.xyxy = np.array([x_min, y_min, x_max, y_max], dtype=np.float32)

    def _compute_corners(self):
        """Tính toán 4 góc (x, y) của khung chữ nhật bị xoay góc theta."""
        cos_a = math.cos(self.angle_rad)
        sin_a = math.sin(self.angle_rad)

        # 4 tọa độ tương đối từ tâm
        dx = np.array([-self.w / 2, self.w / 2, self.w / 2, -self.w / 2])
        dy = np.array([-self.h / 2, -self.h / 2, self.h / 2, self.h / 2])

        # Xoay tọa độ và cộng với tâm cx, cy
        x_pts = self.cx + dx * cos_a - dy * sin_a
        y_pts = self.cy + dx * sin_a + dy * cos_a
        return np.column_stack((x_pts, y_pts)).astype(np.int32)

# ================================================================
# ENGINE SUY LUẬN ONNX MODEL (YOLO26 OBB)
# ================================================================
class YOLO26OBBONNXEngine:
    def __init__(self, model_path):
        print(f"[ONNX] Dang khoi tao phien lam viec ONNXRuntime: {model_path}")
        
        # Thử nghiệm khởi tạo GPU CUDA tương thích CUDA 11.8
        try:
            self.session = ort.InferenceSession(model_path, providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
            active_p = self.session.get_providers()
            print(f"[ONNX SUCCESS] Active Providers: {active_p}")
            if 'CUDAExecutionProvider' in active_p:
                print("[ONNX GPU] Model dang chay TANG TOC TREN GPU CUDA (RTX GPU)!")
            else:
                print("[ONNX CPU] Dang chay tren CPU.")
        except Exception as e:
            print(f"[ONNX Warning] EP Error: {e}. Fallback CPU...")
            self.session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])

        self.input_name = self.session.get_inputs()[0].name
        self.input_shape = self.session.get_inputs()[0].shape  # [1, 3, 640, 640]
        self.img_h, self.img_w = self.input_shape[2], self.input_shape[3]
        print(f"[ONNX] Engine da san sang! Target Input Shape: {self.input_shape}")

    def predict_tile(self, tile_bgr, conf_thres=CONF_THRESHOLD):
        """
        Chạy inference trên 1 tile BGR 640x640.
        Trả về danh sách các OBBDetection trong không gian tọa độ cục bộ của tile.
        """
        th, tw = tile_bgr.shape[:2]
        
        # Nếu tile nhỏ hơn 640x640 (vùng mép ảnh gốc), tiến hành pad với màu xám
        if th != self.img_h or tw != self.img_w:
            padded = np.full((self.img_h, self.img_w, 3), 114, dtype=np.uint8)
            padded[:th, :tw] = tile_bgr
            tile_input = padded
        else:
            tile_input = tile_bgr

        # Tiền xử lý ảnh: BGR -> RGB, Chuẩn hóa [0..1], CHW
        img_rgb = cv2.cvtColor(tile_input, cv2.COLOR_BGR2RGB)
        img_data = img_rgb.astype(np.float32) / 255.0
        img_data = np.transpose(img_data, (2, 0, 1))  # (3, 640, 640)
        img_batch = np.expand_dims(img_data, axis=0)   # (1, 3, 640, 640)

        # Suy luận ONNX
        outputs = self.session.run(None, {self.input_name: img_batch})
        output0 = outputs[0][0]  # Shape: (8, 8400)
        output_trans = output0.T # Shape: (8400, 8)

        # Giải nén thông tin Tensor đầu ra: [cx, cy, w, h, score_c0, score_c1, score_c2, angle]
        boxes = output_trans[:, :4]       # cx, cy, w, h
        scores_all = output_trans[:, 4:7]  # 3 class scores
        angles = output_trans[:, 7]       # angle in radians

        class_ids = np.argmax(scores_all, axis=1)
        max_scores = np.max(scores_all, axis=1)

        # Lọc theo ngưỡng tự tin
        keep_mask = max_scores >= conf_thres
        if not np.any(keep_mask):
            return []

        boxes = boxes[keep_mask]
        max_scores = max_scores[keep_mask]
        class_ids = class_ids[keep_mask]
        angles = angles[keep_mask]

        detections = []
        for i in range(len(boxes)):
            cx, cy, w, h = boxes[i]
            # Loại bỏ dự đoán nằm ngoài phạm vi thực của tile (tránh vệt dư thừa do padding)
            if cx > tw or cy > th:
                continue
            det = OBBDetection(cx, cy, w, h, angles[i], max_scores[i], class_ids[i])
            detections.append(det)

        return detections

# ================================================================
# BỘ CẮT TILE SAHI (SLICED AIDED HYPER INFERENCE) & NMS HỘP XOAY
# ================================================================
class SAHISlicingEngine:
    def __init__(self, onnx_engine, tile_size=TILE_SIZE, overlap_ratio=OVERLAP_RATIO):
        self.engine = onnx_engine
        self.tile_w, self.tile_h = tile_size
        self.overlap_ratio = overlap_ratio

    def _generate_slices(self, frame_w, frame_h):
        """Tạo lưới tọa độ các tile cắt lấp chồng (Sliding Window Grid)."""
        stride_w = int(self.tile_w * (1.0 - self.overlap_ratio))
        stride_h = int(self.tile_h * (1.0 - self.overlap_ratio))

        x_coords = list(range(0, frame_w - self.tile_w + 1, stride_w))
        if x_coords[-1] + self.tile_w < frame_w:
            x_coords.append(frame_w - self.tile_w)

        y_coords = list(range(0, frame_h - self.tile_h + 1, stride_h))
        if y_coords[-1] + self.tile_h < frame_h:
            y_coords.append(frame_h - self.tile_h)

        slices = []
        for y in y_coords:
            for x in x_coords:
                x_end = min(x + self.tile_w, frame_w)
                y_end = min(y + self.tile_h, frame_h)
                slices.append((x, y, x_end, y_end))
        return slices

    def predict_frame(self, frame_bgr, conf_thres=CONF_THRESHOLD, nms_thres=NMS_THRESHOLD):
        """
        Chạy SAHI trên toàn bộ khung hình video drone.
        1. Cắt tile
        2. Chạy ONNX inference từng tile
        3. Chuyển đổi tọa độ cục bộ về tọa độ khung hình gốc
        4. Thực hiện OBB Non-Maximum Suppression (NMS) toàn cục
        """
        frame_h, frame_w = frame_bgr.shape[:2]
        slices = self._generate_slices(frame_w, frame_h)

        all_detections = []
        for (x1, y1, x2, y2) in slices:
            tile = frame_bgr[y1:y2, x1:x2]
            tile_dets = self.engine.predict_tile(tile, conf_thres=conf_thres)

            # Quy đổi tọa độ tâm về khung hình gốc
            for det in tile_dets:
                global_cx = det.cx + x1
                global_cy = det.cy + y1
                global_det = OBBDetection(global_cx, global_cy, det.w, det.h, det.angle_rad, det.score, det.class_id)
                all_detections.append(global_det)

        if not all_detections:
            return []

        # ================================================================
        # XỬ LÝ NMS HỘP XOAY (OBB NMS DÙNG OPENCV NMSBoxesRotated)
        # ================================================================
        r_boxes = []
        scores = []
        class_ids = []

        for det in all_detections:
            # Format OpenCV RotatedRect: ((cx, cy), (w, h), angle_deg)
            r_boxes.append(((det.cx, det.cy), (det.w, det.h), det.angle_deg))
            scores.append(det.score)
            class_ids.append(det.class_id)

        # Chạy NMSBoxesRotated cực nhanh trên C++ backend của OpenCV
        indices = cv2.dnn.NMSBoxesRotated(r_boxes, scores, score_threshold=conf_thres, nms_threshold=nms_threshold)

        filtered_detections = []
        if len(indices) > 0:
            indices = indices.flatten()
            for idx in indices:
                filtered_detections.append(all_detections[idx])

        return filtered_detections

# ================================================================
# BỘ HỖ TRỢ VẼ GIAO DIỆN TRỰC QUAN (VISUALIZER HUD)
# ================================================================
class HUDVisualizer:
    def __init__(self):
        self.tracked_objects_history = set()

    def draw_detections(self, frame, detections, fps=0, frame_idx=0, total_frames=0):
        """Vẽ các đa giác xoay (OBB), thông tin Tracking ID và bảng Dashboard tổng quan."""
        annotated = frame.copy()
        
        # Thống kê theo từng lớp cho frame hiện tại
        class_counts = {0: 0, 1: 0, 2: 0}

        for det in detections:
            class_counts[det.class_id] += 1
            if det.track_id != -1:
                self.tracked_objects_history.add(det.track_id)

            color = CLASS_COLORS.get(det.class_id, (0, 255, 0))
            pts = det.points.reshape((-1, 1, 2))

            # 1. Vẽ viền đa giác bọc xoay OBB
            cv2.polylines(annotated, [pts], isClosed=True, color=color, thickness=2, lineType=cv2.LINE_AA)
            
            # 2. Vẽ điểm đầu orient (hướng của vết nứt/hư hỏng)
            cv2.circle(annotated, tuple(pts[0][0]), 4, (255, 255, 255), -1, cv2.LINE_AA)

            # 3. Tạo nhãn văn bản hiển thị ID & Loại hư hỏng
            if det.track_id != -1:
                label = f"#{det.track_id} {det.class_name} {det.score:.2f}"
            else:
                label = f"{det.class_name} {det.score:.2f}"

            # Vẽ nền chữ semi-transparent để dễ đọc trên nền đường nhựa
            txt_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            txt_w, txt_h = txt_size
            rx, ry = pts[0][0][0], pts[0][0][1] - 8
            
            cv2.rectangle(annotated, (rx, ry - txt_h - 4), (rx + txt_w + 6, ry + 4), color, -1)
            cv2.putText(annotated, label, (rx + 3, ry - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

        # ================================================================
        # VẼ DASHBOARD HUD THỐNG KÊ GÓC TRÊN BÊN TRÁI
        # ================================================================
        overlay = annotated.copy()
        cv2.rectangle(overlay, (20, 20), (450, 190), (15, 15, 15), -1)
        cv2.addWeighted(overlay, 0.75, annotated, 0.25, 0, annotated)
        cv2.rectangle(annotated, (20, 20), (450, 190), (0, 255, 255), 1, cv2.LINE_AA)

        # Tiêu đề
        cv2.putText(annotated, "HE THONG GIAM SAT HA TANG MAT DUONG (DRONE)", (30, 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)

        # FPS & Tiến độ
        progress_pct = (frame_idx / total_frames * 100) if total_frames > 0 else 0
        cv2.putText(annotated, f"FPS: {fps:.1f}  |  Frame: {frame_idx}/{total_frames} ({progress_pct:.1f}%)", (30, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)

        # Thống kê chi tiết hư hỏng
        y_offset = 95
        for cid, cname in CLASS_NAMES.items():
            ccolor = CLASS_COLORS[cid]
            cnt = class_counts[cid]
            cv2.rectangle(annotated, (30, y_offset - 10), (42, y_offset + 2), ccolor, -1)
            cv2.putText(annotated, f"{cname}: {cnt} (Framing)", (50, y_offset),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
            y_offset += 22

        cv2.putText(annotated, f"TONG SO LUONG TRACKED OBJs: {len(self.tracked_objects_history)}", (30, y_offset + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)

        return annotated

# ================================================================
# TIẾN TRÌNH CHẠY CHÍNH (MAIN PIPELINE)
# ================================================================
def main():
    print("=" * 70)
    print("  CHUONG TRINH SUY LUAN YOLO26-OBB + SAHI + BYTETRACKING")
    print("=" * 70)
    print(f"  File Model ONNX:    {MODEL_PATH}")
    print(f"  File Video Dau Vao: {INPUT_VIDEO_PATH}")
    print(f"  File Video Dau Ra:  {OUTPUT_VIDEO_PATH}")
    print("=" * 70 + "\n")

    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"Khong tim thay file ONNX tai: {MODEL_PATH}")

    if not os.path.exists(INPUT_VIDEO_PATH):
        raise FileNotFoundError(f"Khong tim thay video test tai: {INPUT_VIDEO_PATH}")

    # 1. Khởi tạo Engine ONNX & SAHI
    onnx_engine = YOLO26OBBONNXEngine(MODEL_PATH)
    sahi_engine = SAHISlicingEngine(onnx_engine, tile_size=TILE_SIZE, overlap_ratio=OVERLAP_RATIO)

    # 2. Khởi tạo ByteTrack Tracker từ thư viện supervision (Tương thích mọi phiên bản)
    tracker = sv.ByteTrack(track_activation_threshold=0.25, lost_track_buffer=30, minimum_matching_threshold=0.8)

    # 3. Khởi tạo Bộ đọc/Ghi Video OpenCV
    cap = cv2.VideoCapture(INPUT_VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"[VIDEO] Size: {width}x{height} | FPS: {fps:.2f} | Total Frames: {total_frames}")

    # Tạo thư mục xuất nếu chưa có
    os.makedirs(os.path.dirname(OUTPUT_VIDEO_PATH), exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(OUTPUT_VIDEO_PATH, fourcc, fps, (width, height))

    visualizer = HUDVisualizer()

    frame_idx = 0
    t_start_total = time.time()

    print("\n[PROCESSING] Dang bat dau suy luan va theo doi vat the tren video...")
    pbar = tqdm(total=total_frames, desc="Processing Video Frames", unit="frame")

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        t0 = time.time()

        # Step 1: SAHI Inference & Local-to-Global Fusion
        detections = sahi_engine.predict_frame(frame, conf_thres=CONF_THRESHOLD, nms_thres=NMS_THRESHOLD)

        # Step 2: Object Tracking qua ByteTrack
        if len(detections) > 0:
            # Tạo supervision Detections object với bounding box bao ngoài (xyxy)
            xyxy_arr = np.array([d.xyxy for d in detections], dtype=np.float32)
            conf_arr = np.array([d.score for d in detections], dtype=np.float32)
            cls_arr = np.array([d.class_id for d in detections], dtype=np.int32)

            sv_dets = sv.Detections(
                xyxy=xyxy_arr,
                confidence=conf_arr,
                class_id=cls_arr
            )

            # Cập nhật ByteTrack
            tracked_sv_dets = tracker.update_with_detections(sv_dets)

            # Ánh xạ Track ID ngược lại danh sách OBBDetection ban đầu
            if len(tracked_sv_dets) > 0 and tracked_sv_dets.tracker_id is not None:
                for i, tracked_xyxy in enumerate(tracked_sv_dets.xyxy):
                    t_id = tracked_sv_dets.tracker_id[i]
                    # Tìm detection trùng khớp nhất về tọa độ xyxy
                    best_match_idx = -1
                    best_iou = -1.0
                    for j, det in enumerate(detections):
                        # Tính IoU đơn giản giữa 2 khung envelope
                        boxA, boxB = tracked_xyxy, det.xyxy
                        xA = max(boxA[0], boxB[0])
                        yA = max(boxA[1], boxB[1])
                        xB = min(boxA[2], boxB[2])
                        yB = min(boxA[3], boxB[3])
                        interArea = max(0, xB - xA) * max(0, yB - yA)
                        boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[0])
                        boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[0])
                        iou = interArea / float(boxAArea + boxBArea - interArea + 1e-6)

                        if iou > best_iou:
                            best_iou = iou
                            best_match_idx = j

                    if best_match_idx != -1 and best_iou > 0.3:
                        detections[best_match_idx].track_id = int(t_id)

        t_elapsed = time.time() - t0
        curr_fps = 1.0 / t_elapsed if t_elapsed > 0 else 0.0

        # Step 3: Render HUD & Bounding Boxes
        annotated_frame = visualizer.draw_detections(frame, detections, fps=curr_fps, frame_idx=frame_idx, total_frames=total_frames)

        # Ghi frame ra video xuất
        out.write(annotated_frame)
        pbar.update(1)

    cap.release()
    out.release()
    pbar.close()

    total_time = time.time() - t_start_total
    avg_fps = total_frames / total_time if total_time > 0 else 0

    print("\n" + "=" * 70)
    print("  HOAN THANH TIEN TRINH SUY LUAN & TRACKING VIDEO!")
    print("=" * 70)
    print(f"  Tong thoi gian thuc hien: {total_time:.2f} s")
    print(f"  FPS Trung binh:            {avg_fps:.2f} fps")
    print(f"  Tong so Track ID phat hien:{len(visualizer.tracked_objects_history)}")
    print(f"  File ket qua duoc luu tai: {OUTPUT_VIDEO_PATH}")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
