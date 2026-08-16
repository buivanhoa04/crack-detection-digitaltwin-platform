import os
import cv2
import sys
import numpy as np
from pathlib import Path
from tqdm import tqdm
from shapely.geometry import Polygon, MultiPolygon

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).parent
YOLO_RAW_DIR = BASE_DIR / "dacl10k_yolo_raw"
SLICED_DIR = BASE_DIR / "dacl10k_Dataset_Sliced"

PATCH_SIZE = 640
OVERLAP = 128
STRIDE = PATCH_SIZE - OVERLAP
MIN_AREA_PX = 25

CLASS_NAMES_7 = [
    "Biological_Growth",
    "Corrosion",
    "Crack",
    "Efflorescence_Leaching",
    "Exposed Rebar",
    "Spalling",
    "Staining_Infiltration"
]

def slice_single_image(img_path, lbl_path, out_img_dir, out_lbl_dir, split_name):
    img = cv2.imread(str(img_path))
    if img is None:
        return 0, 0

    h, w = img.shape[:2]

    labels = []
    if lbl_path.exists():
        with open(lbl_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 7:
                    cid = int(parts[0])
                    pts = np.array([float(x) for x in parts[1:]], dtype=np.float32).reshape(-1, 2)
                    pts[:, 0] *= w
                    pts[:, 1] *= h
                    labels.append((cid, pts))

    patch_count = 0
    fg_patch_count = 0

    x_steps = list(range(0, max(1, w - PATCH_SIZE + 1), STRIDE))
    if x_steps[-1] + PATCH_SIZE < w:
        x_steps.append(w - PATCH_SIZE)

    y_steps = list(range(0, max(1, h - PATCH_SIZE + 1), STRIDE))
    if y_steps[-1] + PATCH_SIZE < h:
        y_steps.append(h - PATCH_SIZE)

    patch_idx = 0
    for y in y_steps:
        for x in x_steps:
            x2 = min(x + PATCH_SIZE, w)
            y2 = min(y + PATCH_SIZE, h)
            
            patch_img = img[y:y2, x:x2]
            patch_h, patch_w = patch_img.shape[:2]

            if patch_h < PATCH_SIZE or patch_w < PATCH_SIZE:
                patch_img = cv2.copyMakeBorder(
                    patch_img, 0, PATCH_SIZE - patch_h, 0, PATCH_SIZE - patch_w,
                    cv2.BORDER_CONSTANT, value=(114, 114, 114)
                )

            patch_poly_box = Polygon([(x, y), (x2, y), (x2, y2), (x, y2)])
            patch_lines = []

            for cid, pts in labels:
                if len(pts) < 3:
                    continue
                try:
                    poly = Polygon(pts)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if not poly.intersects(patch_poly_box):
                        continue

                    inter = poly.intersection(patch_poly_box)
                    if inter.is_empty:
                        continue

                    geoms = [inter] if isinstance(inter, Polygon) else (inter.geoms if isinstance(inter, MultiPolygon) else [])
                    for g in geoms:
                        if g.area < MIN_AREA_PX:
                            continue
                        g_pts = np.array(g.exterior.coords, dtype=np.float32)
                        g_pts[:, 0] = (g_pts[:, 0] - x) / PATCH_SIZE
                        g_pts[:, 1] = (g_pts[:, 1] - y) / PATCH_SIZE
                        g_pts = np.clip(g_pts, 0.0, 1.0)

                        pts_str = " ".join([f"{px:.6f} {py:.6f}" for px, py in g_pts[:-1]])
                        patch_lines.append(f"{cid} {pts_str}")
                except Exception:
                    pass

            # 🛠️ CHỈ NÊU LƯU PATCH CHỨA KHUYẾT TẬT TRONG LÚC CẮT (HOẶC NHẬN LỌC PHÔNG NỀN CHỌN LỌC)
            # Tối ưu siêu tốc: loại bỏ các patch bê tông trống vô nghĩa để đẩy tốc độ train lên 15-20 phút/Epoch!
            if split_name == "train" and len(patch_lines) == 0:
                # Giữ xác suất 5% phông nền trống trong lúc cắt
                if np.random.rand() > 0.05:
                    continue

            stem = f"{img_path.stem}_p{patch_idx}_{x}_{y}"
            out_img_path = out_img_dir / f"{stem}.jpg"
            out_lbl_path = out_lbl_dir / f"{stem}.txt"

            cv2.imwrite(str(out_img_path), patch_img)
            with open(out_lbl_path, "w", encoding="utf-8") as f:
                f.write("\n".join(patch_lines) + "\n" if patch_lines else "")

            patch_count += 1
            if len(patch_lines) > 0:
                fg_patch_count += 1

            patch_idx += 1

    return patch_count, fg_patch_count

def run_slicing():
    print(f"\n{'='*75}")
    print("  03. SLICING DACL10K HIGH-RES IMAGES INTO 640x640 OVERLAP PATCHES")
    print(f"{'='*75}")

    for split in ["train", "val"]:
        img_dir = YOLO_RAW_DIR / "images" / split
        lbl_dir = YOLO_RAW_DIR / "labels" / split

        if not img_dir.exists():
            img_dir = YOLO_RAW_DIR / "images" / "validation"
            lbl_dir = YOLO_RAW_DIR / "labels" / "validation"

        if not img_dir.exists():
            print(f"⚠️ Bỏ qua split {split} (Không tìm thấy thư mục tại {YOLO_RAW_DIR})")
            continue

        out_img_dir = SLICED_DIR / "images" / split
        out_lbl_dir = SLICED_DIR / "labels" / split
        out_img_dir.mkdir(parents=True, exist_ok=True)
        out_lbl_dir.mkdir(parents=True, exist_ok=True)

        img_files = list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.png"))
        print(f"\n>> Đang cắt patch cho tập: [{split.upper()}] ({len(img_files)} ảnh gốc)...")

        total_patches = 0
        total_fg_patches = 0
        for img_path in tqdm(img_files, desc=f"Slicing {split}"):
            lbl_path = lbl_dir / f"{img_path.stem}.txt"
            p_cnt, fg_cnt = slice_single_image(img_path, lbl_path, out_img_dir, out_lbl_dir, split)
            total_patches += p_cnt
            total_fg_patches += fg_cnt

        print(f"   ✅ Đã tạo {total_patches} patches ({total_fg_patches} chứa nhãn khuyết tật) cho tập {split}.")

    yaml_path = SLICED_DIR / "data.yaml"
    yaml_content = f"""path: {SLICED_DIR.as_posix()}
train: images/train
val: images/val
test: images/val

nc: 7
names: {CLASS_NAMES_7}
"""
    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write(yaml_content)
    print(f"\n✅ Đã tạo data.yaml chuẩn tại {yaml_path}")

if __name__ == "__main__":
    run_slicing()
