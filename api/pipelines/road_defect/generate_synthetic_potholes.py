import os
import sys
import cv2
import math
import random
import numpy as np
from pathlib import Path
from tqdm import tqdm
import argparse

# Cấu hình encoding UTF-8 để chạy ổn định trên mọi hệ điều hành
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')

# ================================================================
# 1. HÀM HỖ TRỢ TRÍCH XUẤT VÀ BIẾN ĐỔI HÌNH HỌC OBB
# ================================================================

def parse_yolo_obb_labels(label_path):
    """Đọc file nhãn YOLO OBB: class_id x1 y1 x2 y2 x3 y3 x4 y4"""
    labels = []
    if not label_path.exists():
        return labels
    with open(label_path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 9:
                cls_id = int(parts[0])
                coords = [float(x) for x in parts[1:9]]
                labels.append((cls_id, coords))
    return labels


def crop_pothole_with_padding(img, corners, padding_factor=1.4):
    """
    Cắt đối tượng ổ gà kèm theo khoảng đệm để tránh bị mất góc khi xoay/scale.
    Trả về crop patch, mặt nạ nhãn polygon mask và tọa độ các góc tương đối.
    """
    img_h, img_w = img.shape[:2]
    px = [c * img_w for c in corners[0::2]]
    py = [c * img_h for c in corners[1::2]]
    
    xmin, xmax = int(min(px)), int(max(px))
    ymin, ymax = int(min(py)), int(max(py))
    
    # Giới hạn tọa độ trong ảnh
    xmin = max(0, xmin)
    xmax = min(img_w, xmax)
    ymin = max(0, ymin)
    ymax = min(img_h, ymax)
    
    cw = xmax - xmin
    ch = ymax - ymin
    
    if cw < 5 or ch < 5:
        return None, None, None
        
    # Tính đường chéo để đệm xoay an toàn
    diag = int(math.ceil(math.sqrt(cw**2 + ch**2) * padding_factor))
    pad_x = (diag - cw) // 2
    pad_y = (diag - ch) // 2
    
    p_xmin = max(0, xmin - pad_x)
    p_xmax = min(img_w, xmax + pad_x)
    p_ymin = max(0, ymin - pad_y)
    p_ymax = min(img_h, ymax + pad_y)
    
    crop = img[p_ymin:p_ymax, p_xmin:p_xmax].copy()
    if crop.shape[0] == 0 or crop.shape[1] == 0:
        return None, None, None
        
    # Chuyển đổi tọa độ các góc tương đối so với ảnh đã đệm
    rel_corners = []
    for i in range(4):
        cx = corners[2 * i] * img_w - p_xmin
        cy = corners[2 * i + 1] * img_h - p_ymin
        rel_corners.extend([cx, cy])
        
    # Tạo mặt nạ polygon
    mask = np.zeros(crop.shape[:2], dtype=np.uint8)
    poly = np.array(rel_corners, dtype=np.int32).reshape((-1, 1, 2))
    cv2.fillPoly(mask, [poly], 255)
    
    return crop, mask, rel_corners


def transform_obb_corners(rel_corners, pw, ph, cx, cy, angle, scale):
    """Biến đổi tọa độ góc OBB dựa trên ma trận xoay & scale M, dịch chuyển về tâm đích (cx, cy)"""
    M = cv2.getRotationMatrix2D((pw / 2, ph / 2), angle, scale)
    new_corners = []
    for i in range(4):
        rx = rel_corners[2 * i]
        ry = rel_corners[2 * i + 1]
        pt = np.array([rx, ry, 1.0])
        new_pt = M.dot(pt)
        # Tọa độ thực trên ảnh đích
        tx = new_pt[0] + cx - (pw / 2)
        ty = new_pt[1] + cy - (ph / 2)
        new_corners.extend([tx, ty])
    return new_corners


def check_bbox_intersection(box_a, box_b):
    """Kiểm tra xem hai hộp giới hạn (AABB) có giao nhau hay không để tránh dán đè"""
    return not (box_a[2] < box_b[0] or box_a[0] > box_b[2] or box_a[3] < box_b[1] or box_a[1] > box_b[3])

# ================================================================
# 2. TIẾN TRÌNH SINH ẢNH CHÍNH (PACP)
# ================================================================

def run_pothole_synthesis(split_dir, class_id=2, num_aug=2000):
    images_dir = Path(split_dir) / "images" / "train"
    labels_dir = Path(split_dir) / "labels" / "train"
    
    if not images_dir.exists() or not labels_dir.exists():
        print(f">> [ERROR] Khong tim thay thu muc: {images_dir} hoac {labels_dir}")
        return
        
    print(">> Dang quet thu muc va trich xuat cac mau o ga...")
    pothole_database = []
    
    # Tìm kiếm các định dạng ảnh phổ biến
    image_paths = []
    for ext in ("*.jpg", "*.jpeg", "*.png"):
        image_paths.extend(images_dir.glob(ext))
        
    for img_path in tqdm(image_paths, desc="Trich xuat o ga"):
        lbl_path = labels_dir / f"{img_path.stem}.txt"
        if not lbl_path.exists():
            continue
            
        labels = parse_yolo_obb_labels(lbl_path)
        pothole_labels = [lbl for lbl in labels if lbl[0] == class_id]
        
        if not pothole_labels:
            continue
            
        img = cv2.imread(str(img_path))
        if img is None:
            continue
            
        for _, coords in pothole_labels:
            crop, mask, rel_corners = crop_pothole_with_padding(img, coords)
            if crop is not None:
                pothole_database.append({
                    "crop": crop,
                    "mask": mask,
                    "rel_corners": rel_corners,
                    "aspect_ratio": crop.shape[1] / crop.shape[0]
                })
                
    num_potholes = len(pothole_database)
    print(f">> Da trich xuat duoc: {num_potholes} mau o ga duy nhat.")
    if num_potholes == 0:
        print(">> [WARNING] Khong tim thay bat ky o ga nao trong tap Train. Dung tien trinh.")
        return
        
    print(f">> Tien hanh sinh them {num_aug} anh datuong bang PACP (Copy-Paste thong minh)...")
    
    # Lựa chọn ngẫu nhiên các ảnh đích để dán
    augmented_count = 0
    pbar = tqdm(total=num_aug, desc="Sinh anh moi")
    
    while augmented_count < num_aug:
        # Chuyển đổi chọn ngẫu nhiên ảnh đích trong tập train
        tgt_img_path = random.choice(image_paths)
        tgt_lbl_path = labels_dir / f"{tgt_img_path.stem}.txt"
        
        tgt_img = cv2.imread(str(tgt_img_path))
        if tgt_img is None:
            continue
            
        tgt_h, tgt_w = tgt_img.shape[:2]
        
        # Đọc các nhãn hiện có trên ảnh đích
        tgt_labels = []
        if tgt_lbl_path.exists():
            tgt_labels = parse_yolo_obb_labels(tgt_lbl_path)
            
        # Lấy AABB của các nhãn hiện tại để tránh dán chồng chập
        existing_boxes = []
        for cls, coords in tgt_labels:
            xs = [c * tgt_w for c in coords[0::2]]
            ys = [c * tgt_h for c in coords[1::2]]
            existing_boxes.append([min(xs), min(ys), max(xs), max(ys)])
            
        # Chọn ngẫu nhiên số lượng ổ gà cần dán vào ảnh mới (từ 1 đến 3 ổ gà)
        num_pastes = random.randint(1, 3)
        new_labels = [lbl for lbl in tgt_labels] # Clone danh sách nhãn hiện tại
        aug_img = tgt_img.copy()
        
        success_paste = 0
        for _ in range(num_pastes):
            pothole = random.choice(pothole_database)
            crop = pothole["crop"]
            mask = pothole["mask"]
            rel_corners = pothole["rel_corners"]
            
            pw, ph = crop.shape[1], crop.shape[0]
            
            # Thử tìm vị trí hợp lệ trong tối đa 5 lần
            for _ in range(5):
                # PACP: Chọn tâm dán trong vùng mặt đường (40% - 92% chiều cao ảnh)
                cy = random.randint(int(0.40 * tgt_h), int(0.92 * tgt_h))
                cx = random.randint(int(0.08 * tgt_w), int(0.92 * tgt_w))
                
                # Tính toán phối cảnh xa gần: vật càng ở dưới (cy lớn) -> kích thước càng to
                # Horizon ở 0.40 * tgt_h (scale=0.15), foreground ở 0.92 * tgt_h (scale=1.0)
                norm_y = (cy - int(0.40 * tgt_h)) / (int(0.52 * tgt_h))
                norm_y = max(0.0, min(1.0, norm_y))
                perspective_scale = 0.15 + 0.85 * norm_y
                
                # Thêm chút biến thiên kích thước ngẫu nhiên ±15%
                scale = perspective_scale * random.uniform(0.85, 1.15)
                # Xoay hướng ngẫu nhiên để tăng tính đa dạng góc
                angle = random.uniform(0, 360)
                
                # Tính toán kích thước hộp bao mới sau biến đổi hình học
                new_w = int(pw * scale)
                new_h = int(ph * scale)
                
                # Kiểm tra xem có nằm ngoài biên ảnh đích hay không
                if (cx - new_w // 2 < 0 or cx + new_w // 2 >= tgt_w or
                    cy - new_h // 2 < 0 or cy + new_h // 2 >= tgt_h):
                    continue
                    
                paste_box = [cx - new_w // 2, cy - new_h // 2, cx + new_w // 2, cy + new_h // 2]
                
                # Kiểm tra giao nhau với các hộp hiện có
                intersect = False
                for box in existing_boxes:
                    if check_bbox_intersection(paste_box, box):
                        intersect = True
                        break
                        
                if intersect:
                    continue
                    
                # Tiến hành biến đổi xoay & scale trên ảnh crop và mặt nạ mask
                M = cv2.getRotationMatrix2D((pw / 2, ph / 2), angle, scale)
                rot_crop = cv2.warpAffine(crop, M, (pw, ph), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                rot_mask = cv2.warpAffine(mask, M, (pw, ph), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                
                # Hòa trộn biên ảnh SOTA bằng cv2.seamlessClone (Mixed Clone để trộn texture nền)
                try:
                    aug_img = cv2.seamlessClone(rot_crop, aug_img, rot_mask, (cx, cy), cv2.MIXED_CLONE)
                except Exception as e:
                    # Fallback nếu seamless clone lỗi
                    # Bbox mask
                    mask_indices = np.where(rot_mask > 0)
                    if len(mask_indices[0]) > 0:
                        y_indices = mask_indices[0] + cy - (ph // 2)
                        x_indices = mask_indices[1] + cx - (pw // 2)
                        # Kẹp tọa độ biên
                        y_indices = np.clip(y_indices, 0, tgt_h - 1)
                        x_indices = np.clip(x_indices, 0, tgt_w - 1)
                        aug_img[y_indices, x_indices] = rot_crop[mask_indices[0], mask_indices[1]]
                
                # Cập nhật tọa độ xoay OBB mới cho ảnh đích
                new_coords_px = transform_obb_corners(rel_corners, pw, ph, cx, cy, angle, scale)
                
                # Normalize tọa độ về [0, 1] cho nhãn YOLO OBB
                new_coords_norm = []
                for i in range(4):
                    nx = max(0.0, min(1.0, new_coords_px[2 * i] / tgt_w))
                    ny = max(0.0, min(1.0, new_coords_px[2 * i + 1] / tgt_h))
                    new_coords_norm.extend([nx, ny])
                    
                new_labels.append((class_id, new_coords_norm))
                existing_boxes.append(paste_box)
                success_paste += 1
                break # Thành công dán 1 ổ gà, thoát khỏi thử lại vị trí
                
        # Chỉ lưu ảnh nếu dán thành công ít nhất 1 ổ gà mới
        if success_paste > 0:
            out_img_name = f"{tgt_img_path.stem}_pothole_aug_{augmented_count}.jpg"
            out_lbl_name = f"{tgt_img_path.stem}_pothole_aug_{augmented_count}.txt"
            
            out_img_path = images_dir / out_img_name
            out_lbl_path = labels_dir / out_lbl_name
            
            # Ghi ảnh mới
            cv2.imwrite(str(out_img_path), aug_img)
            
            # Ghi nhãn mới
            with open(out_lbl_path, "w", encoding="utf-8") as f:
                for cls, coords in new_labels:
                    coords_str = " ".join([f"{c:.6f}" for c in coords])
                    f.write(f"{cls} {coords_str}\n")
                    
            augmented_count += 1
            pbar.update(1)
            
    pbar.close()
    print(f">> [HOAN THANH] Da sinh thanh cong {augmented_count} anh augmented moi gan nhan o ga.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PACP - Perspective-Aware Copy-Paste generator for Road defects OBB")
    parser.add_argument("--split_dir", type=str, default=str(Path(__file__).parent / "Dataset_ALL_Split"),
                        help="Duong dan den thu muc Dataset_ALL_Split")
    parser.add_argument("--class_id", type=int, default=2, help="Class ID cua o ga (mac dinh = 2)")
    parser.add_argument("--num_aug", type=int, default=500, help="So luong anh can sinh them (mac dinh = 500)")
    
    args = parser.parse_args()
    
    run_pothole_synthesis(args.split_dir, args.class_id, args.num_aug)
