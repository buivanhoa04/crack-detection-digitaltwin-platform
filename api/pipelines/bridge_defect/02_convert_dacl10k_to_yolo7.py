import os
import sys
import json
import shutil
from pathlib import Path
from tqdm import tqdm
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).parent

# ================================================================
# ⚙️ CẤU HÌNH ĐƯỜNG DẪN THƯ MỤC GỐC DỮ LIỆU DACL10K TRÊN SERVER
# ================================================================
RAW_DIR = Path(r"C:\Users\User\Downloads\dacl10k-DatasetNinja")

YOLO_RAW_DIR = BASE_DIR / "dacl10k_yolo_raw"

# ================================================================
# BẢNG ÁNH XẠ CHUẨN KHOA HỌC TỪ DACL10K META.JSON SANG 7 LỚP NATIVE
# ================================================================
DACL10K_TO_7CLASS = {
    "weathering": 0,                   # Biological_Growth / Phong hóa (0)
    "rust": 1,                         # Corrosion / Rỉ sét (1)
    "crack": 2,                        # Crack / Vết nứt (2)
    "alligator crack": 2,              # Crack (2)
    "efflorescence": 3,                # Efflorescence_Leaching / Vôi hóa (3)
    "exposed rebars": 4,               # Exposed Rebar / Cốt thép lộ (4)
    "spalling": 5,                     # Spalling / Bong tróc bê tông (5)
    "cavity": 5,                       # Spalling (5)
    "rockpocket": 5,                   # Spalling (5)
    "hollowareas": 5,                  # Spalling (5)
    "restformwork": 5,                 # Spalling (5)
    "wetspot": 6,                      # Staining_Infiltration / Thấm ẩm (6)
    "washouts/concrete corrosion": 6   # Staining_Infiltration (6)
}

CLASS_NAMES_7 = [
    "Biological_Growth",
    "Corrosion",
    "Crack",
    "Efflorescence_Leaching",
    "Exposed Rebar",
    "Spalling",
    "Staining_Infiltration"
]

def parse_json_annotation(json_path, img_w, img_h):
    """Hỗ trợ tự động cả 2 định dạng: LabelMe JSON và Supervisely / DatasetNinja JSON"""
    yolo_lines = []
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if "objects" in data:
            for obj in data["objects"]:
                label = obj.get("classTitle", "").strip().lower()
                if label not in DACL10K_TO_7CLASS:
                    continue

                cid = DACL10K_TO_7CLASS[label]
                points_data = obj.get("points", {})
                pts = points_data.get("exterior", [])
                if len(pts) < 3:
                    continue

                normalized_pts = []
                for pt in pts:
                    x = max(0.0, min(1.0, float(pt[0]) / img_w))
                    y = max(0.0, min(1.0, float(pt[1]) / img_h))
                    normalized_pts.extend([f"{x:.6f}", f"{y:.6f}"])

                line_str = f"{cid} " + " ".join(normalized_pts)
                yolo_lines.append(line_str)

        elif "shapes" in data:
            for shape in data["shapes"]:
                label = shape.get("label", "").strip().lower()
                if label not in DACL10K_TO_7CLASS:
                    continue

                cid = DACL10K_TO_7CLASS[label]
                pts = shape.get("points", [])
                if len(pts) < 3:
                    continue

                normalized_pts = []
                for pt in pts:
                    x = max(0.0, min(1.0, float(pt[0]) / img_w))
                    y = max(0.0, min(1.0, float(pt[1]) / img_h))
                    normalized_pts.extend([f"{x:.6f}", f"{y:.6f}"])

                line_str = f"{cid} " + " ".join(normalized_pts)
                yolo_lines.append(line_str)

    except Exception:
        pass

    return yolo_lines

def process_split(split_name):
    print(f"\n>> Đang xử lý và chuyển đổi tập: [{split_name.upper()}]...")

    split_dir = RAW_DIR / split_name
    if not split_dir.exists():
        candidates = [d for d in RAW_DIR.iterdir() if d.is_dir() and split_name in d.name.lower()]
        split_dir = candidates[0] if candidates else None

    if not split_dir or not split_dir.exists():
        print(f"⚠️ Bỏ qua split [{split_name}] (Không tìm thấy thư mục trong {RAW_DIR})")
        return

    img_dir = split_dir / "img"
    ann_dir = split_dir / "ann"

    if not img_dir.exists():
        img_dir = split_dir
    if not ann_dir.exists():
        ann_dir = split_dir

    img_files = list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpeg"))
    print(f"   • Tìm thấy {len(img_files)} ảnh gốc trong {img_dir}")

    target_img_dir = YOLO_RAW_DIR / "images" / split_name
    target_lbl_dir = YOLO_RAW_DIR / "labels" / split_name
    target_img_dir.mkdir(parents=True, exist_ok=True)
    target_lbl_dir.mkdir(parents=True, exist_ok=True)

    converted_count = 0
    stats_counter = Counter()

    import cv2
    for img_path in tqdm(img_files, desc=f"Converting {split_name}"):
        json_path = ann_dir / f"{img_path.name}.json"
        if not json_path.exists():
            json_path = ann_dir / f"{img_path.stem}.json"
        if not json_path.exists():
            continue

        img = cv2.imread(str(img_path))
        if img is None:
            continue

        img_h, img_w = img.shape[:2]
        lines = parse_json_annotation(json_path, img_w, img_h)

        dst_img = target_img_dir / img_path.name
        dst_lbl = target_lbl_dir / f"{img_path.stem}.txt"

        shutil.copy(img_path, dst_img)
        with open(dst_lbl, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines) + "\n" if lines else "")

        converted_count += 1
        for l in lines:
            cid = int(l.split()[0])
            stats_counter[cid] += 1

    print(f"   ✅ Đã chuyển đổi {converted_count} file sang chuẩn YOLO Polygon!")
    print(f"   • Thống kê số lượng nhãn 7 lớp: {dict(stats_counter)}")

def run_conversion():
    print(f"\n{'='*75}")
    print("  02. CONVERTING DACL10K (DATASET NINJA / SUPERVISELY) TO YOLO 7-CLASS")
    print(f"{'='*75}")

    for split in ["train", "val", "test"]:
        process_split(split)

    yaml_path = YOLO_RAW_DIR / "data.yaml"
    yaml_content = f"""path: {YOLO_RAW_DIR.as_posix()}
train: images/train
val: images/val
test: images/test

nc: 7
names: {CLASS_NAMES_7}
"""
    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write(yaml_content)
    print(f"\n✅ Đã tạo file data.yaml tại {yaml_path}")

if __name__ == "__main__":
    run_conversion()
