import os
import cv2
import random
from pathlib import Path
from tqdm import tqdm

def check_overlap(box1, box2):
    x1_1, y1_1, x2_1, y2_1 = box1
    x1_2, y1_2, x2_2, y2_2 = box2
    inter_x1 = max(x1_1, x1_2)
    inter_y1 = max(y1_1, y1_2)
    inter_x2 = min(x2_1, x2_2)
    inter_y2 = min(y2_1, y2_2)
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    return (inter_w * inter_h) > 0

def crop_damaged_square(img, bbox, padding_ratio=0.15):
    h, w, _ = img.shape
    x1, y1, x2, y2 = bbox
    box_w = x2 - x1
    box_h = y2 - y1
    pad_w = int(box_w * padding_ratio)
    pad_h = int(box_h * padding_ratio)
    cx = x1 + box_w // 2
    cy = y1 + box_h // 2
    max_side = max(box_w + 2 * pad_w, box_h + 2 * pad_h)
    
    sq_x1 = max(0, cx - max_side // 2)
    sq_y1 = max(0, cy - max_side // 2)
    sq_x2 = min(w, sq_x1 + max_side)
    sq_y2 = min(h, sq_y1 + max_side)
    return img[sq_y1:sq_y2, sq_x1:sq_x2], max_side

def get_random_undamaged_patch(img, gt_boxes, size, max_attempts=100):
    h, w, _ = img.shape
    if h <= size or w <= size:
        return None
    for _ in range(max_attempts):
        rx1 = random.randint(0, w - size)
        ry1 = random.randint(0, h - size)
        rx2 = rx1 + size
        ry2 = ry1 + size
        candidate = [rx1, ry1, rx2, ry2]
        
        has_overlap = False
        for gt in gt_boxes:
            if check_overlap(candidate, gt):
                has_overlap = True
                break
        if not has_overlap:
            return img[ry1:ry2, rx1:rx2]
    return None

def main():
    # --- ĐƯỜNG DẪN CẤU HÌNH ---
    # Thư mục chứa dataset YOLO (Có train/valid/test chứa images và labels)
    SRC_DETECTION_DATA = r"C:\Users\buiva\Downloads\Crack.v5i.yolo26"
    # Thư mục đầu ra cho bộ phân loại mới sạch sẽ
    DEST_CLASSIFICATION_DATA = r"C:\Users\buiva\Downloads\Super_Dataset_Cls_Perfect"
    
    src_root = Path(SRC_DETECTION_DATA)
    dest_root = Path(DEST_CLASSIFICATION_DATA)
    
    splits = ["train", "valid", "test"]
    img_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    random.seed(42)
    
    for split in splits:
        img_dir = src_root / split / "images"
        lbl_dir = src_root / split / "labels"
        
        if not img_dir.exists():
            continue
            
        damaged_out = dest_root / split / "damaged"
        undamaged_out = dest_root / split / "undamaged"
        damaged_out.mkdir(parents=True, exist_ok=True)
        undamaged_out.mkdir(parents=True, exist_ok=True)
        
        print(f"🔄 Đang xử lý tập: {split}...")
        img_files = list(img_dir.glob("*"))
        
        for img_path in tqdm(img_files):
            if img_path.suffix.lower() not in img_extensions:
                continue
            lbl_path = lbl_dir / f"{img_path.stem}.txt"
            if not lbl_path.exists():
                continue
                
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            h, w, _ = img.shape
            
            # Đọc nhãn tọa độ lỗi
            gt_boxes = []
            with open(lbl_path, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) < 5:
                        continue
                    cx, cy, bw, bh = map(float, parts[1:5])
                    x1 = int((cx - bw / 2) * w)
                    y1 = int((cy - bh / 2) * h)
                    x2 = int((cx + bw / 2) * w)
                    y2 = int((cy + bh / 2) * h)
                    gt_boxes.append([x1, y1, x2, y2])
            
            # Thực hiện cắt
            for idx, bbox in enumerate(gt_boxes):
                # 1. Cắt vùng lỗi (Damaged)
                damaged_patch, size = crop_damaged_square(img, bbox, padding_ratio=0.15)
                if damaged_patch.size > 0:
                    cv2.imwrite(str(damaged_out / f"{img_path.stem}_box{idx}.jpg"), damaged_patch)
                    
                    # 2. Cắt vùng sạch đối chứng (Undamaged) trên chính ảnh đó
                    undamaged_patch = get_random_undamaged_patch(img, gt_boxes, size)
                    if undamaged_patch is not None and undamaged_patch.size > 0:
                        cv2.imwrite(str(undamaged_out / f"{img_path.stem}_bg{idx}.jpg"), undamaged_patch)

    print("\n✨ Hoàn tất tạo bộ dữ liệu phân loại nhị phân không lệch thuộc tính!")

if __name__ == "__main__":
    main()