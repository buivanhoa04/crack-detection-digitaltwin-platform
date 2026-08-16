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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).parent

# Đường dẫn dữ liệu đã chia split sẵn của người dùng
SRC_DIR = Path(r"F:\bridge_defect_pipeline\data")
if not SRC_DIR.exists():
    SRC_DIR = BASE_DIR / "Dataset_Split"

DEST_DIR = BASE_DIR / "Dataset_Sliced"

PATCH_SIZE = 640
OVERLAP_PX = 128
BG_KEEP_RATIO = 0.50
NUM_WORKERS = 8
SPLITS = ["train", "val", "test"]

# ================================================================
# BẢNG LỌC VÀ ÁNH XẠ CHUẨN XÁC NATIVE 7 LỚP DẦM CẦU CỐT LÕI (0..6)
# ================================================================
RAW_TO_7CLASS_MAP = {
    0: 0,   # Biological_Growth -> 0
    2: 1,   # Corrosion -> 1
    3: 2,   # Crack -> 2
    4: 3,   # Efflorescence_Leaching -> 3
    6: 4,   # Exposed Rebar -> 4
    9: 5,   # Spalling -> 5
    10: 6   # Staining_Infiltration -> 6
}

PURE_7CLASSES_NAMES = {
    0: "Biological_Growth",
    1: "Corrosion",
    2: "Crack",
    3: "Efflorescence_Leaching",
    4: "Exposed Rebar",
    5: "Spalling",
    6: "Staining_Infiltration"
}

def parse_polygon_label(label_path):
    labels = []
    if not label_path.exists():
        return labels
    with open(label_path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 7:
                raw_cls_id = int(parts[0])
                # Lọc bỏ 4 lớp không thuộc 7 lớp dầm cầu cốt lõi
                if raw_cls_id in RAW_TO_7CLASS_MAP:
                    mapped_cls_id = RAW_TO_7CLASS_MAP[raw_cls_id]
                    coords = [float(x) for x in parts[1:]]
                    labels.append((mapped_cls_id, coords))
    return labels

def process_single_image(img_path, lbl_path, dest_img_dir, dest_lbl_dir, is_train=True):
    img = cv2.imread(str(img_path))
    if img is None:
        return 0, 0, Counter()
    
    h, w, _ = img.shape
    poly_labels = parse_polygon_label(lbl_path)
    
    stride = PATCH_SIZE - OVERLAP_PX
    x_steps = max(1, int(np.ceil((w - OVERLAP_PX) / stride)))
    y_steps = max(1, int(np.ceil((h - OVERLAP_PX) / stride)))

    bg_patches = []
    fg_patches = []
    stats = Counter()

    for i in range(y_steps):
        for j in range(x_steps):
            x1 = j * stride
            y1 = i * stride
            if x1 + PATCH_SIZE > w:
                x1 = max(0, w - PATCH_SIZE)
            if y1 + PATCH_SIZE > h:
                y1 = max(0, h - PATCH_SIZE)

            x2 = min(x1 + PATCH_SIZE, w)
            y2 = min(y1 + PATCH_SIZE, h)

            patch = img[y1:y2, x1:x2]
            ph, pw, _ = patch.shape

            if ph < PATCH_SIZE or pw < PATCH_SIZE:
                padded = np.zeros((PATCH_SIZE, PATCH_SIZE, 3), dtype=np.uint8)
                padded[0:ph, 0:pw] = patch
                patch = padded

            patch_polys = []
            for cls_id, coords in poly_labels:
                pts = np.array(coords).reshape(-1, 2)
                pts[:, 0] *= w
                pts[:, 1] *= h

                pts[:, 0] -= x1
                pts[:, 1] -= y1

                inside_pts = (pts[:, 0] >= 0) & (pts[:, 0] <= PATCH_SIZE) & \
                             (pts[:, 1] >= 0) & (pts[:, 1] <= PATCH_SIZE)
                             
                if np.sum(inside_pts) >= 3:
                    pts[:, 0] = np.clip(pts[:, 0], 0, PATCH_SIZE)
                    pts[:, 1] = np.clip(pts[:, 1], 0, PATCH_SIZE)

                    pts_norm = pts / PATCH_SIZE
                    coords_str = " ".join([f"{pt[0]:.6f} {pt[1]:.6f}" for pt in pts_norm])
                    patch_polys.append(f"{cls_id} {coords_str}")
                    stats[cls_id] += 1

            patch_name = f"{img_path.stem}_p{x1}_{y1}"
            if patch_polys:
                fg_patches.append((patch_name, patch, patch_polys))
            else:
                bg_patches.append((patch_name, patch, []))

    if is_train and bg_patches:
        n_bg_keep = int(len(bg_patches) * BG_KEEP_RATIO)
        bg_patches = random.sample(bg_patches, n_bg_keep)

    saved_count = 0
    for patch_name, p_img, p_labels in (fg_patches + bg_patches):
        out_img_path = dest_img_dir / f"{patch_name}.jpg"
        out_lbl_path = dest_lbl_dir / f"{patch_name}.txt"
        cv2.imwrite(str(out_img_path), p_img)

        with open(out_lbl_path, "w", encoding="utf-8") as f:
            if p_labels:
                f.write("\n".join(p_labels) + "\n")
        saved_count += 1

    return saved_count, len(fg_patches), stats

def run_slicing():
    print(f"\n{'='*65}")
    print("  01. SLICING DATASET POLYGON - 7 PURE BRIDGE CLASSES (nc: 7)")
    print(f"{'='*65}")
    print(f">> Nguồn dữ liệu gốc: {SRC_DIR}")

    for split in SPLITS:
        src_img_dir = SRC_DIR / "images" / split
        src_lbl_dir = SRC_DIR / "labels" / split

        if not src_img_dir.exists():
            src_img_dir = SRC_DIR / split / "images"
            src_lbl_dir = SRC_DIR / split / "labels"

        dest_img_dir = DEST_DIR / "images" / split
        dest_lbl_dir = DEST_DIR / "labels" / split

        dest_img_dir.mkdir(parents=True, exist_ok=True)
        dest_lbl_dir.mkdir(parents=True, exist_ok=True)

        if not src_img_dir.exists():
            print(f"⚠️ Bỏ qua split {split} vì không tìm thấy {src_img_dir}")
            continue

        img_files = list(src_img_dir.glob("*.*"))
        if not img_files:
            continue

        print(f"\n>> Đang cắt patch cho tập: [{split.upper()}] ({len(img_files)} ảnh gốc)...")
        is_train = (split == "train")

        total_saved = 0
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            futures = [executor.submit(process_single_image, f, src_lbl_dir / f"{f.stem}.txt", dest_img_dir, dest_lbl_dir, is_train) for f in img_files]
            for future in tqdm(as_completed(futures), total=len(futures), desc=f"Slicing {split}"):
                saved, fg, stats = future.result()
                total_saved += saved

        print(f"   ✅ Đã tạo {total_saved} patches cho tập {split}.")

    yaml_content = {
        'path': str(DEST_DIR.absolute()).replace("\\", "/"),
        'train': 'images/train',
        'val': 'images/val',
        'test': 'images/test',
        'nc': 7,
        'names': PURE_7CLASSES_NAMES
    }
    with open(DEST_DIR / "data.yaml", "w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f, sort_keys=False)

    print(f"\n✅ CẮT PATCH POLYGON HOÀN TẤT (nc: 7)! Dataset Sliced lưu tại: {DEST_DIR}")

if __name__ == "__main__":
    run_slicing()
