import os
import shutil
import sys
from pathlib import Path
from tqdm import tqdm
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import yaml

# Đảm bảo in UTF-8 không lỗi trên Windows/Linux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

# Đường dẫn thư mục dataset đã cắt trượt trên server/local (Tự động nhận diện)
DATASET_DIR = Path(__file__).parent / "Dataset_ALL_Sliced"

def count_labels_task(lbl_path):
    """Đếm số lượng đối tượng của từng lớp trong file nhãn."""
    c = Counter()
    try:
        with open(lbl_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) > 0:
                    c[int(parts[0])] += 1
    except Exception:
        pass
    return lbl_path, c


def run_oversampling():
    print("="*65)
    print(" BAT DAU PIPELINE CAN BANG LOP TU DONG (DYNAMIC OVERSAMPLING OBB)")
    print("="*65)
    print("  [QUAN TRONG] Chi chay oversample tren tap TRAIN!")
    print("  [QUAN TRONG] Khong bao gio oversample tren Val va Test!\n")

    # Cau truc YOLO chuan: images/train va labels/train
    img_dir = DATASET_DIR / "images" / "train"
    lbl_dir = DATASET_DIR / "labels" / "train"

    if not img_dir.exists() or not lbl_dir.exists():
        print(f"[!] Khong tim thay thu muc train tai: {DATASET_DIR}")
        print("    Kiem tra lai cau truc: images/train va labels/train")
        return

    # Lấy danh sách file nhãn txt hiện tại trong tập train
    lbl_files = list(lbl_dir.glob("*.txt"))
    if not lbl_files:
        print("⚠️ Thu muc nhan rong hoac khong chua file .txt nao.")
        return

    print("[+] Buoc 1: Quet song song va phan tich cac nhan hien co...")
    lbl_to_counts = {}
    lbl_to_classes = {}
    total_class_counts = Counter()
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(tqdm(executor.map(count_labels_task, lbl_files), total=len(lbl_files), desc="Quet nhan"))
        for path, c in results:
            lbl_to_counts[path] = c
            lbl_to_classes[path] = set(c.keys())
            total_class_counts.update(c)

    # Tự động tính toán luật nhân bản động để cân bằng hoàn hảo theo lớp đa số
    if not total_class_counts:
        print("⚠️ Khong tim thay doi tuong nhan nao de thuc hien can bang.")
        return
        
    majority_class = total_class_counts.most_common(1)[0][0]
    majority_count = total_class_counts[majority_class]
    
    print(f"  - Lop da so: Class [{majority_class}] voi {majority_count} instances.")
    
    OVERSAMPLE_RULES = {}
    for cid, count in total_class_counts.items():
        if cid == majority_class or count == 0:
            OVERSAMPLE_RULES[cid] = 1
        else:
            mult = int(round(majority_count / count))
            OVERSAMPLE_RULES[cid] = max(1, min(5, mult)) # Gioi han multiplier tu 1 den 5 (bao thu de tranh overfit)
            
    print("  - Luat nhan ban tu dong (Dynamic Oversample Rules):")
    for cid, mult in OVERSAMPLE_RULES.items():
        print(f"    * Class [{cid}]: x{mult} lan (So luong hien tai: {total_class_counts[cid]} instances)")

    copy_tasks = []
    img_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".JPG", ".PNG"}

    # Lập lịch nhân bản
    for lbl_path in lbl_files:
        classes_in_file = lbl_to_classes.get(lbl_path, set())
        if not classes_in_file:
            continue

        # Tìm ảnh tương ứng với file nhãn
        img_path = None
        for ext in img_extensions:
            potential_img = img_dir / f"{lbl_path.stem}{ext}"
            if potential_img.exists():
                img_path = potential_img
                break

        if img_path is None:
            continue

        # Tìm hệ số nhân bản lớn nhất trong các class có trong file nhãn này
        max_multiplier = 1
        for cls_id in classes_in_file:
            if cls_id in OVERSAMPLE_RULES:
                max_multiplier = max(max_multiplier, OVERSAMPLE_RULES[cls_id])

        # Tạo danh sách các tác vụ copy
        if max_multiplier > 1:
            for copy_idx in range(1, max_multiplier):
                new_stem = f"{lbl_path.stem}_os_{copy_idx}"
                new_img_path = img_dir / f"{new_stem}{img_path.suffix}"
                new_lbl_path = lbl_dir / f"{new_stem}.txt"
                copy_tasks.append((img_path, new_img_path, lbl_path, new_lbl_path))

    if not copy_tasks:
        print("🎉 Khong co mau cua lop hiem nao can nhan ban!")
        return

    print(f"\n[+] Buoc 2: Tien hanh nhan ban {len(copy_tasks)} cap file song song...")
    
    def execute_copy_task(task):
        src_img, dest_img, src_lbl, dest_lbl = task
        try:
            shutil.copy2(str(src_img), str(dest_img))
            shutil.copy2(str(src_lbl), str(dest_lbl))
            return True
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        success_list = list(tqdm(executor.map(execute_copy_task, copy_tasks), total=len(copy_tasks), desc="Nhân bản file"))

    success_count = sum(1 for s in success_list if s)
    print(f"  - Đã nhân bản thành công {success_count}/{len(copy_tasks)} file nhãn và ảnh lên đĩa.")

    # Thống kê kết quả sau khi oversample
    print("\n[+] Bước 3: Thống kê lại phân bố dữ liệu nhãn xoay sau khi cân bằng...")
    new_lbl_files = list(lbl_dir.glob("*.txt"))
    counter = Counter()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(tqdm(executor.map(count_labels_task, new_lbl_files), total=len(new_lbl_files), desc="Đang tính toán"))
        for lbl_path, c in results:
            counter.update(c)

    class_names = {0: "nut_ca_sau", 1: "nut", 2: "o_ga/bong_bat"}
    
    # Đọc tên class từ data.yaml nếu có
    yaml_path = DATASET_DIR / "data.yaml"
    if yaml_path.exists():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                names = cfg.get("names", {})
                if isinstance(names, dict):
                    class_names.update({int(k): v for k, v in names.items()})
        except Exception:
            pass

    sep = "=" * 65
    print(f"\n{sep}")
    print(" PHAN BO LOP MOI SAU KHI CAN BANG (OVERSAMPLED):")
    print(sep)
    total_instances = sum(counter.values())
    for cid in sorted(class_names.keys()):
        name = class_names[cid]
        count = counter.get(cid, 0)
        pct = (count / total_instances * 100) if total_instances > 0 else 0
        print(f"  - [{cid}] {name:<20}: {count:<6} instances ({pct:.1f}%)")
    print("-" * 65)
    print(f" Tong so luong nhan toan bo sau can bang: {total_instances} instances")
    print("=" * 65 + "\n")
    print("[NHAN XET]")
    print("  * Tap train da duoc can bang, mo hinh se hoc deu ca 3 lop.")
    print("  * Tap Val va Test giu nguyen de danh gia khach quan.")


if __name__ == "__main__":
    run_oversampling()
