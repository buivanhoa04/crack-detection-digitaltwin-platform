import os
import sys
import cv2
import yaml
import random
import shutil
import numpy as np
from pathlib import Path
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

# Cấu hình encoding UTF-8 để chạy mượt trên cả Windows và Linux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

# ================================================================
# 1. CẤU HÌNH PIPELINE SLICING OBB
# ================================================================
# Thư mục chứa dữ liệu sau khi chia train/val/test (Tự động nhận diện)
SRC_DIR = Path(__file__).parent / "Dataset_ALL_Split"

# Thư mục đích lưu dataset OBB sau khi cắt patch
DEST_DIR = Path(__file__).parent / "Dataset_ALL_Sliced"

# Kích thước patch (mảnh cắt) chuẩn cho YOLO26 OBB
PATCH_SIZE = 640

# Overlap (phần chồng chập) giữa các patch kề nhau (pixel) - ~20%
OVERLAP_PX = 128

# Ngưỡng diện tích phần giao của BBox với patch (so với diện tích BBox gốc)
# Nếu phần giao quá nhỏ (< 30%), loại bỏ đối tượng khỏi patch đó
MIN_BBOX_AREA_RATIO = 0.30

# Tỷ lệ ảnh nền trống (background patch - chứa lề đường, nắp cống, rác, đường sạch) giữ lại
# [CẢI TIẾN 50/50]: Đặt 1.0 để giữ lại tỷ lệ 50% ảnh hư hỏng / 50% ảnh nền sạch (1:1)
# Giúp mô hình triệt tiêu hoàn toàn lỗi nhận diện nhầm lề đường, nắp cống, rác thành ổ gà
BG_KEEP_RATIO = 1.00

# Số worker xử lý đa luồng I/O
NUM_WORKERS = 8

# Các tập dữ liệu cần xử lý (train, val và test sau khi chia dữ liệu)
# Val và Test: KHÔNG áp dụng lọc ảnh nền theo tỉ lệ (giữ tất cả) để đánh giá khách quan
SPLITS = ["train", "val", "test"]

# ================================================================
# 2. CÁC HÀM XỬ LÝ NHÃN XOAY OBB (ORIENTED BOUNDING BOX)
# ================================================================

def parse_yolo_obb_labels(label_path):
    """
    Đọc file nhãn YOLO OBB: class_id x1 y1 x2 y2 x3 y3 x4 y4 (tọa độ normalized [0,1])
    """
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


def o_bbox_to_pixels(coords, img_w, img_h):
    """Chuyển tọa độ OBB normalized [0, 1] sang pixel thực tế trên ảnh gốc."""
    px = [c * img_w for c in coords[0::2]]
    py = [c * img_h for c in coords[1::2]]
    return px, py


def generate_patch_coords(img_h, img_w, patch_size, overlap_px):
    """
    Tạo lưới tọa độ patch (x1, y1, x2, y2) bao phủ toàn bộ ảnh gốc.
    """
    stride = patch_size - overlap_px
    if stride <= 0:
        stride = patch_size // 2
    
    patches = []
    
    # Nếu ảnh nhỏ hơn kích thước patch chuẩn -> Trả về 1 patch bao phủ toàn bộ (sẽ letterbox sau)
    if img_h <= patch_size and img_w <= patch_size:
        patches.append((0, 0, img_w, img_h))
        return patches
        
    for y in range(0, img_h, stride):
        for x in range(0, img_w, stride):
            x2 = min(x + patch_size, img_w)
            y2 = min(y + patch_size, img_h)
            x1 = max(0, x2 - patch_size)
            y1 = max(0, y2 - patch_size)
            patches.append((x1, y1, x2, y2))
            
    # Loại bỏ các tọa độ patch trùng lặp và sắp xếp lại
    patches = list(set(patches))
    patches.sort(key=lambda p: (p[1], p[0]))
    return patches


def letterbox_pad(img, target_size, pad_color=(114, 114, 114)):
    """
    Đệm viền xám (letterbox) góc trên-trái (top-left) cho ảnh có kích thước nhỏ hơn target_size.
    Không co giãn ảnh để giữ nguyên chất lượng và tỉ lệ vết nứt.
    """
    h, w = img.shape[:2]
    if h >= target_size and w >= target_size:
        return img[:target_size, :target_size], 0, 0
        
    canvas = np.full((target_size, target_size, 3), pad_color, dtype=np.uint8)
    paste_h = min(h, target_size)
    paste_w = min(w, target_size)
    canvas[:paste_h, :paste_w] = img[:paste_h, :paste_w]
    return canvas, 0, 0


# ================================================================
# 3. LUỒNG XỬ LÝ CHÍNH CHO TỪNG ẢNH
# ================================================================

def process_single_image(args):
    """
    Cắt một ảnh gốc và file nhãn OBB tương ứng thành nhiều patch nhỏ.
    """
    img_path, lbl_path, dest_img_dir, dest_lbl_dir, patch_size, overlap_px = args
    
    stats = {"num_patches": 0, "num_fg": 0, "num_bg": 0, "class_counts": Counter()}
    
    img = cv2.imread(str(img_path))
    if img is None:
        return stats

    # [CẢI TIẾN ĐỒNG BỘ TONE MÀU]: Chuyển toàn bộ ảnh sang 3-channel Grayscale (màu xám nhựa đường)
    # Giúp loại bỏ sự lệch màu giữa 1k ảnh CVAT (ảnh màu RGB) và các ảnh còn lại (ảnh đen trắng)
    if len(img.shape) == 3 and img.shape[2] == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        
    img_h, img_w = img.shape[:2]
    
    # Đọc nhãn OBB gốc
    original_labels = parse_yolo_obb_labels(lbl_path) if lbl_path else []
    
    # Chuyển đổi nhãn sang pixel trên ảnh gốc
    pixel_labels = []
    for cls_id, coords in original_labels:
        px, py = o_bbox_to_pixels(coords, img_w, img_h)
        pixel_labels.append((cls_id, px, py))
        
    # Tạo lưới patch
    patches = generate_patch_coords(img_h, img_w, patch_size, overlap_px)
    
    temp_patches_data = []
    
    for idx, (px1, py1, px2, py2) in enumerate(patches):
        patch_w = px2 - px1
        patch_h = py2 - py1
        
        # Cắt ảnh
        patch_img = img[py1:py2, px1:px2]
        
        patch_labels = []
        for cls_id, px, py in pixel_labels:
            # Lấy bounding box tối giản của OBB
            bx1, by1 = min(px), min(py)
            bx2, by2 = max(px), max(py)
            
            # Kiểm tra xem BBox tối giản của OBB có giao với patch không
            ix1 = max(bx1, px1)
            iy1 = max(by1, py1)
            ix2 = min(bx2, px2)
            iy2 = min(by2, py2)
            
            if ix1 >= ix2 or iy1 >= iy2:
                continue  # Không giao
                
            # Đánh giá tỷ lệ giao diện tích
            original_area = (bx2 - bx1) * (by2 - by1)
            clipped_area = (ix2 - ix1) * (iy2 - iy1)
            
            if original_area <= 0 or (clipped_area / original_area) < MIN_BBOX_AREA_RATIO:
                continue  # Tỉ lệ giao quá nhỏ
                
            # Cắt (clip) tọa độ 4 góc của OBB vào biên patch và chuyển sang hệ tọa độ cục bộ (local)
            local_px = []
            local_py = []
            for x, y in zip(px, py):
                cx = min(max(x, px1), px2) - px1
                cy = min(max(y, py1), py2) - py1
                local_px.append(cx)
                local_py.append(cy)
                
            # Chuẩn hóa về [0, 1] theo kích thước patch thực tế trước khi đệm viền
            norm_coords = []
            for lx, ly in zip(local_px, local_py):
                norm_coords.append(lx / patch_w)
                norm_coords.append(ly / patch_h)
                
            patch_labels.append((cls_id, norm_coords))
            
        is_fg = len(patch_labels) > 0
        
        # Đệm viền xám nếu patch nhỏ hơn kích thước chuẩn 640x640 (thường ở rìa ảnh gốc)
        if patch_w < patch_size or patch_h < patch_size:
            patch_img, offset_x, offset_y = letterbox_pad(patch_img, patch_size)
            
            # Cân chỉnh lại tọa độ normalized OBB theo canvas đệm viền góc trên-trái
            adjusted_labels = []
            for cls_id, norm_coords in patch_labels:
                adj_coords = []
                for i in range(4):
                    nx = (norm_coords[2*i] * patch_w + offset_x) / patch_size
                    ny = (norm_coords[2*i+1] * patch_h + offset_y) / patch_size
                    adj_coords.append(min(max(nx, 0.0), 1.0))
                    adj_coords.append(min(max(ny, 0.0), 1.0))
                adjusted_labels.append((cls_id, adj_coords))
            patch_labels = adjusted_labels
            
        temp_patches_data.append((patch_img, patch_labels, is_fg, (px1, py1, px2, py2)))

    # Phân loại và lọc ảnh nền (Background) ngẫu nhiên theo tỉ lệ
    fg_patches = [p for p in temp_patches_data if p[2]]
    bg_patches = [p for p in temp_patches_data if not p[2]]

    # [SOTA FIX] Lấy ngẫu nhiên thay vì lấy tuần tự đầu danh sách (tránh bias không gian)
    # Số lượng ảnh nền giữ lại tối đa dựa trên ảnh chứa đối tượng
    max_bg_to_keep = max(1, int(len(fg_patches) * BG_KEEP_RATIO))
    keep_bg_patches = random.sample(bg_patches, min(max_bg_to_keep, len(bg_patches)))

    final_patches = fg_patches + keep_bg_patches
    
    # Ghi file ảnh và file nhãn patch ra ổ đĩa
    img_name_stem = img_path.stem
    for p_idx, (p_img, p_lbls, p_fg, coords) in enumerate(final_patches):
        p_name = f"{img_name_stem}_p{p_idx}"
        
        # Ghi ảnh
        cv2.imwrite(str(dest_img_dir / f"{p_name}.jpg"), p_img)
        
        # Ghi nhãn
        lbl_file_path = dest_lbl_dir / f"{p_name}.txt"
        if p_lbls:
            with open(lbl_file_path, "w", encoding="utf-8") as lf:
                for cls_id, norm_coords in p_lbls:
                    coords_str = " ".join(f"{c:.6f}" for c in norm_coords)
                    lf.write(f"{cls_id} {coords_str}\n")
                    stats["class_counts"][cls_id] += 1
        else:
            # Tạo file trống cho ảnh background
            with open(lbl_file_path, "w", encoding="utf-8") as lf:
                pass
                
        stats["num_patches"] += 1
        if p_fg:
            stats["num_fg"] += 1
        else:
            stats["num_bg"] += 1
            
    return stats


def run_slicing_pipeline():
    print("="*60)
    print("🚀 BẮT ĐẦU PIPELINE CẮT ẢNH TRƯỢT XOAY (OBB SLICING)")
    print("="*60)
    
    if not SRC_DIR.exists():
        print(f"❌ Không tìm thấy thư mục gốc: {SRC_DIR}")
        return
        
    for split in SPLITS:
        src_img_dir = SRC_DIR / "images" / split
        src_lbl_dir = SRC_DIR / "labels" / split
        
        if not src_img_dir.exists():
            print(f"⚠️ Bỏ qua split '{split}' do không có thư mục images.")
            continue
            
        dest_img_dir = DEST_DIR / "images" / split
        dest_lbl_dir = DEST_DIR / "labels" / split
        dest_img_dir.mkdir(parents=True, exist_ok=True)
        dest_lbl_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"\n[+] Đang chuẩn bị cắt dữ liệu tập: '{split}'")
        
        # Lấy danh sách ảnh
        image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".JPG", ".PNG"}
        img_files = [f for f in src_img_dir.iterdir() if f.is_file() and f.suffix in image_extensions]
        
        tasks = []
        for img_file in img_files:
            lbl_file = src_lbl_dir / f"{img_file.stem}.txt"
            tasks.append((img_file, lbl_file if lbl_file.exists() else None, dest_img_dir, dest_lbl_dir, PATCH_SIZE, OVERLAP_PX))
            
        print(f"  - Số lượng ảnh gốc: {len(img_files)}")
        
        total_patches = 0
        total_fg = 0
        total_bg = 0
        global_class_counts = Counter()
        
        # Chạy song song đa luồng I/O tăng tốc tối đa
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            futures = {executor.submit(process_single_image, task): task for task in tasks}
            
            for future in tqdm(as_completed(futures), total=len(futures), desc=f"Slicing {split}"):
                try:
                    res = future.result()
                    total_patches += res["num_patches"]
                    total_fg += res["num_fg"]
                    total_bg += res["num_bg"]
                    global_class_counts.update(res["class_counts"])
                except Exception as e:
                    print(f"  ⚠️ Lỗi khi xử lý 1 file ảnh: {e}")
                    
        # In kết quả thống kê sau khi cắt patch
        print(f"\n📊 KẾT QUẢ CẮT NATIVE PATCH CHO TẬP '{split}':")
        print(f"  - Tổng số patch tạo ra:       {total_patches}")
        print(f"  - Số patch chứa nhãn (FG):    {total_fg}")
        print(f"  - Số patch nền trống (BG):    {total_bg}")
        print("  - Thống kê box các lớp nhãn xoay (OBB):")
        class_names = {0: "nut_ca_sau", 1: "nut", 2: "o_ga/bong_bat"}
        for cid in sorted(class_names.keys()):
            name = class_names[cid]
            count = global_class_counts.get(cid, 0)
            print(f"    * [{cid}] {name:<20}: {count:<6} boxes")
            
    # Tạo/Copy file data.yaml cho thư mục đích mới
    src_yaml = SRC_DIR / "data.yaml"
    dest_yaml = DEST_DIR / "data.yaml"
    if src_yaml.exists():
        try:
            with open(src_yaml, "r", encoding="utf-8") as sf:
                data = yaml.safe_load(sf)
            data["path"] = str(DEST_DIR.absolute()).replace("\\", "/")
            with open(dest_yaml, "w", encoding="utf-8") as df:
                yaml.safe_dump(data, df, sort_keys=False)
            print("\n✅ Đã đồng bộ file data.yaml cho tập dữ liệu đã cắt mới.")
        except Exception as e:
            print(f"\n⚠️ Lỗi đồng bộ data.yaml: {e}")
            
    print("\n" + "="*60)
    print(f"🎉 PIPELINE SLICING HOÀN THÀNH XUẤT SẮC!")
    print(f"Đường dẫn kết quả: {DEST_DIR}")
    print("="*60)


if __name__ == "__main__":
    run_slicing_pipeline()
