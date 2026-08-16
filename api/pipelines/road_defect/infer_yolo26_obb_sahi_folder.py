import os
import cv2
import math
import time
import glob
import argparse
import numpy as np
import pandas as pd
import onnxruntime as ort
from tqdm import tqdm

# ================================================================
# CẤU HÌNH MẶC ĐỊNH & THAM SỐ TỐI ƯU
# ================================================================
MODEL_PATH = r"D:\API\yolo26_train_pipeline\output_obb_sota\yolo26m_cbam_obb_sota\weights\best.onnx"
DEFAULT_INPUT_DIR = r"D:\data\CR\F0001"
DEFAULT_OUTPUT_DIR = r"D:\data\CR\F0001_output_fast"

# Chế độ chạy: False = Direct Inference (Nhanh gấp 150 lần), True = SAHI Slicing
USE_SAHI = False

# Tham số lọc chất lượng (Tối ưu chống nhiễu & box siêu nhỏ)
CONF_THRESHOLD = 0.35     # Ngưỡng tự tin tối thiểu (Nâng từ 0.25 lên 0.35 để loại bỏ nhiễu)
NMS_THRESHOLD = 0.40      # Ngưỡng đè lấp NMS hộp xoay
MIN_BOX_SIZE = 15.0       # Kích thước box tối thiểu (loại bỏ box nhỏ li ti < 15px)

# Tham số SAHI (Dùng khi USE_SAHI = True)
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
    0: (0, 0, 255),      # Đỏ (Nứt cá sấu)
    1: (0, 255, 255),    # Vàng (Vết nứt)
    2: (0, 165, 255)     # Cam (Ổ gà / Bóc tách)
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

        # Tính toán 4 đỉnh của hình chữ nhật xoay (Rotated Polygon Corners)
        self.points = self._compute_corners()

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
# ENGINE SUY LUẬN ONNX MODEL (YOLO26 OBB GPU)
# ================================================================
class YOLO26OBBONNXEngine:
    def __init__(self, model_path):
        print(f"[ONNX] Dang khoi tao phien lam viec ONNXRuntime: {model_path}")
        
        try:
            self.session = ort.InferenceSession(model_path, providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
            active_p = self.session.get_providers()
            print(f"[ONNX SUCCESS] Active Providers: {active_p}")
            if 'CUDAExecutionProvider' in active_p:
                print("[ONNX GPU] Engine dang TANG TOC TRUC TIEP TREN GPU CUDA (RTX GPU)!")
            else:
                print("[ONNX CPU] Dang chay tren CPU.")
        except Exception as e:
            print(f"[ONNX Warning] EP Error: {e}. Fallback CPU...")
            self.session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])

        self.input_name = self.session.get_inputs()[0].name
        self.input_shape = self.session.get_inputs()[0].shape  # [1, 3, 640, 640]
        self.img_h, self.img_w = self.input_shape[2], self.input_shape[3]

    def predict_tile(self, tile_bgr, conf_thres=CONF_THRESHOLD):
        """Chạy inference trên 1 tile BGR 640x640."""
        th, tw = tile_bgr.shape[:2]
        
        # Nếu tile nhỏ hơn 640x640, tiến hành pad xám 114
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
            if cx > tw or cy > th:
                continue
            # Lọc box quá nhỏ nhỏ hơn ngưỡng MIN_BOX_SIZE
            if w < MIN_BOX_SIZE or h < MIN_BOX_SIZE:
                continue

            det = OBBDetection(cx, cy, w, h, angles[i], max_scores[i], class_ids[i])
            detections.append(det)

        return detections

    def predict_direct(self, img_bgr, conf_thres=CONF_THRESHOLD, nms_thres=NMS_THRESHOLD):
        """
        [DIRECT INFERENCE ENGINE - NHANH GẤP 150 LẦN]
        Resize/Letterbox ảnh đầu vào trực tiếp về 640x640, suy luận 1 lần duy nhất,
        sau đó quy đổi ngược lại tọa độ pixel ảnh gốc chuẩn xác!
        """
        h_orig, w_orig = img_bgr.shape[:2]
        
        # 1. Tính toán tỉ lệ scale giữ nguyên aspect ratio
        scale = min(self.img_w / w_orig, self.img_h / h_orig)
        nw, nh = int(w_orig * scale), int(h_orig * scale)

        # 2. Resize và đệm viền Letterbox 640x640
        resized = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        padded = np.full((self.img_h, self.img_w, 3), 114, dtype=np.uint8)
        padded[:nh, :nw] = resized

        # 3. Chạy ONNX inference 1 lần trên ảnh padded
        raw_dets = self.predict_tile(padded, conf_thres=conf_thres)
        if not raw_dets:
            return []

        # 4. NMS Hộp Xoay
        r_boxes = [((d.cx, d.cy), (d.w, d.h), d.angle_deg) for d in raw_dets]
        scores = [d.score for d in raw_dets]
        indices = cv2.dnn.NMSBoxesRotated(r_boxes, scores, score_threshold=conf_thres, nms_threshold=nms_thres)

        if len(indices) == 0:
            return []

        indices = indices.flatten()
        filtered_dets = [raw_dets[i] for i in indices]

        # 5. Rescale tọa độ ngược lại kích thước ảnh gốc
        final_dets = []
        for det in filtered_dets:
            orig_cx = det.cx / scale
            orig_cy = det.cy / scale
            orig_w = det.w / scale
            orig_h = det.h / scale

            # Giới hạn tọa độ trong phạm vi ảnh gốc
            if orig_cx > w_orig or orig_cy > h_orig:
                continue

            orig_det = OBBDetection(orig_cx, orig_cy, orig_w, orig_h, det.angle_rad, det.score, det.class_id)
            final_dets.append(orig_det)

        return final_dets

# ================================================================
# BỘ CẮT TILE SAHI (SLICED AIDED HYPER INFERENCE 640x640)
# ================================================================
class SAHISlicingEngine:
    def __init__(self, onnx_engine, tile_size=TILE_SIZE, overlap_ratio=OVERLAP_RATIO):
        self.engine = onnx_engine
        self.tile_w, self.tile_h = tile_size
        self.overlap_ratio = overlap_ratio

    def _generate_slices(self, img_w, img_h):
        """Tạo lưới tọa độ cắt tile 640x640 lấp chồng."""
        stride_w = int(self.tile_w * (1.0 - self.overlap_ratio))
        stride_h = int(self.tile_h * (1.0 - self.overlap_ratio))

        x_coords = list(range(0, img_w - self.tile_w + 1, stride_w))
        if len(x_coords) == 0 or x_coords[-1] + self.tile_w < img_w:
            x_coords.append(max(0, img_w - self.tile_w))

        y_coords = list(range(0, img_h - self.tile_h + 1, stride_h))
        if len(y_coords) == 0 or y_coords[-1] + self.tile_h < img_h:
            y_coords.append(max(0, img_h - self.tile_h))

        slices = []
        for y in y_coords:
            for x in x_coords:
                x_end = min(x + self.tile_w, img_w)
                y_end = min(y + self.tile_h, img_h)
                slices.append((x, y, x_end, y_end))
        return slices

    def predict_image(self, img_bgr, conf_thres=CONF_THRESHOLD, nms_thres=NMS_THRESHOLD):
        """Chạy SAHI trên toàn bộ bức ảnh kích thước bất kỳ."""
        img_h, img_w = img_bgr.shape[:2]

        if img_w <= self.tile_w and img_h <= self.tile_h:
            dets = self.engine.predict_tile(img_bgr, conf_thres=conf_thres)
            if not dets:
                return []
            r_boxes = [((d.cx, d.cy), (d.w, d.h), d.angle_deg) for d in dets]
            scores = [d.score for d in dets]
            indices = cv2.dnn.NMSBoxesRotated(r_boxes, scores, score_threshold=conf_thres, nms_threshold=nms_thres)
            if len(indices) > 0:
                indices = indices.flatten()
                return [dets[i] for i in indices]
            return []

        slices = self._generate_slices(img_w, img_h)
        all_detections = []

        for (x1, y1, x2, y2) in slices:
            tile = img_bgr[y1:y2, x1:x2]
            tile_dets = self.engine.predict_tile(tile, conf_thres=conf_thres)

            for det in tile_dets:
                global_cx = det.cx + x1
                global_cy = det.cy + y1
                global_det = OBBDetection(global_cx, global_cy, det.w, det.h, det.angle_rad, det.score, det.class_id)
                all_detections.append(global_det)

        if not all_detections:
            return []

        # NMS Hộp Xoay Toàn Cục
        r_boxes = [((d.cx, d.cy), (d.w, d.h), d.angle_deg) for d in all_detections]
        scores = [d.score for d in all_detections]
        indices = cv2.dnn.NMSBoxesRotated(r_boxes, scores, score_threshold=conf_thres, nms_threshold=nms_thres)

        filtered_detections = []
        if len(indices) > 0:
            indices = indices.flatten()
            for idx in indices:
                filtered_detections.append(all_detections[idx])

        return filtered_detections

# ================================================================
# BỘ VẼ GIAO DIỆN VÀ LƯU ẢNH
# ================================================================
def draw_and_save_annotation(img_bgr, detections, save_path):
    annotated = img_bgr.copy()
    class_counts = {0: 0, 1: 0, 2: 0}

    for det in detections:
        class_counts[det.class_id] += 1
        color = CLASS_COLORS.get(det.class_id, (0, 255, 0))
        pts = det.points.reshape((-1, 1, 2))

        # 1. Vẽ đa giác bọc xoay OBB
        cv2.polylines(annotated, [pts], isClosed=True, color=color, thickness=2, lineType=cv2.LINE_AA)
        
        # 2. Vẽ điểm đầu hướng
        cv2.circle(annotated, tuple(pts[0][0]), 4, (255, 255, 255), -1, cv2.LINE_AA)

        # 3. Thẻ nhãn chữ
        label = f"{det.class_name} {det.score:.2f}"
        txt_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        txt_w, txt_h = txt_size
        rx, ry = pts[0][0][0], pts[0][0][1] - 8
        
        cv2.rectangle(annotated, (rx, ry - txt_h - 4), (rx + txt_w + 6, ry + 4), color, -1)
        cv2.putText(annotated, label, (rx + 3, ry - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

    # Dashboard trên góc ảnh
    overlay = annotated.copy()
    cv2.rectangle(overlay, (20, 20), (380, 130), (15, 15, 15), -1)
    cv2.addWeighted(overlay, 0.75, annotated, 0.25, 0, annotated)
    cv2.rectangle(annotated, (20, 20), (380, 130), (0, 255, 255), 1, cv2.LINE_AA)

    cv2.putText(annotated, "KET QUA NHAN DIEN HU HONG MAT DUONG", (30, 42),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)

    y_offset = 65
    for cid, cname in CLASS_NAMES.items():
        ccolor = CLASS_COLORS[cid]
        cnt = class_counts[cid]
        cv2.rectangle(annotated, (30, y_offset - 10), (42, y_offset + 2), ccolor, -1)
        cv2.putText(annotated, f"{cname}: {cnt}", (50, y_offset),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        y_offset += 20

    cv2.imwrite(save_path, annotated)

# ================================================================
# CHƯƠNG TRÌNH CHÍNH (FOLDER BATCH INFERENCE TỐI ƯU)
# ================================================================
def process_folder(input_dir, output_dir, model_path=MODEL_PATH, use_sahi=USE_SAHI):
    mode_str = "SAHI 640 Slicing" if use_sahi else "DIRECT INFERENCE (SIEU NHANH 150X)"
    print("=" * 70)
    print(f"  CHUONG TRINH NGHIEN BATCH ANH TRONG FOLDER - CHE DO: {mode_str}")
    print("=" * 70)
    print(f"  Thu muc anh dau vao: {input_dir}")
    print(f"  Thu muc anh dau ra:  {output_dir}")
    print(f"  Model ONNX:          {model_path}")
    print(f"  Confidence Threshold:{CONF_THRESHOLD}")
    print("=" * 70 + "\n")

    if not os.path.exists(input_dir):
        raise FileNotFoundError(f"Khong tim thay thu muc anh dau vao: {input_dir}")

    os.makedirs(output_dir, exist_ok=True)

    # 1. Lấy danh sách tất cả các ảnh trong folder
    valid_exts = ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp", "*.tif", "*.tiff")
    img_paths = []
    for ext in valid_exts:
        img_paths.extend(glob.glob(os.path.join(input_dir, ext)))
        img_paths.extend(glob.glob(os.path.join(input_dir, ext.upper())))

    img_paths = sorted(list(set(img_paths)))
    total_imgs = len(img_paths)
    print(f"[FOLDER] Tim thay tong cong {total_imgs} file anh trong thu muc.")

    if total_imgs == 0:
        print("[WARNING] Khong tim thay anh hop le nao trong thu muc!")
        return

    # 2. Khởi tạo Engine Suy Luận ONNX
    onnx_engine = YOLO26OBBONNXEngine(model_path)
    sahi_engine = SAHISlicingEngine(onnx_engine, tile_size=TILE_SIZE, overlap_ratio=OVERLAP_RATIO) if use_sahi else None

    # 3. Tiến hành suy luận batch
    t_start = time.time()
    results_records = []
    total_defects_found = 0

    print("\n[PROCESSING] Dang bat dau nghien batch anh...")
    pbar = tqdm(total=total_imgs, desc="Processing Images", unit="img")

    for img_path in img_paths:
        img_name = os.path.basename(img_path)
        img_bgr = cv2.imread(img_path)
        if img_bgr is None:
            pbar.update(1)
            continue

        # Chọn phương thức suy luận
        if use_sahi:
            detections = sahi_engine.predict_image(img_bgr, conf_thres=CONF_THRESHOLD, nms_thres=NMS_THRESHOLD)
        else:
            detections = onnx_engine.predict_direct(img_bgr, conf_thres=CONF_THRESHOLD, nms_thres=NMS_THRESHOLD)

        total_defects_found += len(detections)

        # Lưu ảnh kết quả đã vẽ OBB
        save_path = os.path.join(output_dir, img_name)
        draw_and_save_annotation(img_bgr, detections, save_path)

        # Ghi nhận kết quả ra bảng thống kê
        for det in detections:
            results_records.append({
                "image_name": img_name,
                "class_id": det.class_id,
                "class_name": det.class_name,
                "score": round(det.score, 4),
                "cx": round(det.cx, 2),
                "cy": round(det.cy, 2),
                "w": round(det.w, 2),
                "h": round(det.h, 2),
                "angle_deg": round(det.angle_deg, 2)
            })

        pbar.update(1)

    pbar.close()
    total_time = time.time() - t_start
    avg_speed = total_time / total_imgs if total_imgs > 0 else 0

    # 4. Xuất CSV kết quả tổng hợp
    csv_path = os.path.join(output_dir, "defects_summary.csv")
    if results_records:
        df = pd.DataFrame(results_records)
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 70)
    print(f"  HOAN THANH TIEN TRINH NGHIEN FOLDER ANH! ({mode_str})")
    print("=" * 70)
    print(f"  Tong so anh da xu ly:       {total_imgs} anh")
    print(f"  Tong thoi gian thuc hien:   {total_time:.2f} s ({avg_speed:.3f} s/anh)")
    print(f"  Tong so hu hong phat hien:  {total_defects_found} loi")
    print(f"  Thu muc anh xuat ket qua:   {output_dir}")
    print(f"  File CSV bao cao chi tiet:  {csv_path}")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YOLO26 OBB High-Speed Folder Inference Engine")
    parser.add_argument("--dir", type=str, default=DEFAULT_INPUT_DIR, help="Duong dan thu muc anh dau vao")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT_DIR, help="Duong dan thu muc luu anh dau ra")
    parser.add_argument("--model", type=str, default=MODEL_PATH, help="Duong dan file model ONNX")
    parser.add_argument("--sahi", action="store_true", help="Bat mode SAHI slying (default la Direct Inference sieu nhanh)")
    args = parser.parse_args()

    use_sahi_flag = args.sahi or USE_SAHI
    process_folder(args.dir, args.output, args.model, use_sahi=use_sahi_flag)
