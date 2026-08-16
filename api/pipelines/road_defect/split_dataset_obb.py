import os
import sys
import random
import shutil
from pathlib import Path
from tqdm import tqdm
import yaml

# Đảm bảo in UTF-8 không lỗi trên Windows/Linux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

# ================================================================
# CẤU HÌNH ĐƯỜNG DẪN CHIA DỮ LIỆU
# ================================================================
# Thư mục gốc chứa ảnh đang nằm hoàn toàn trong thư mục 'train' (Tự động nhận diện)
SRC_DIR = Path(__file__).parent / "Dataset_ALL"

# Thư mục mới chứa dữ liệu sau khi chia tỉ lệ Train/Val/Test
DEST_DIR = Path(__file__).parent / "Dataset_ALL_Split"

# Tỉ lệ phân chia SOTA: 70% Train / 15% Val / 15% Test
# - Val: Dùng để monitor loss + early stopping trong quá trình train
# - Test: Tập MÙ hoàn toàn, chỉ dùng để đánh giá mô hình cuối cùng (không bao giờ chạm vào khi train)
# Với ~3174 ảnh:
#   Train: ~2221 ảnh | Val: ~476 ảnh | Test: ~477 ảnh
TRAIN_RATIO = 0.70
VAL_RATIO   = 0.15
# TEST_RATIO  = 1.0 - TRAIN_RATIO - VAL_RATIO = 0.15 (phần còn lại)

# Seed cố định để tái tạo kết quả chia tập (reproducibility)
RANDOM_SEED = 42


def run_splitting():
    print("="*65)
    print(" BẮT ĐẦU CHIA DATASET: TRAIN (70%) / VAL (15%) / TEST (15%)")
    print("="*65)
    print("  [SOTA] Chia ảnh gốc trước khi cắt patch để tránh data leakage!")
    print("  [SOTA] Test set MU hoàn toan - khong dung trong bat ky buoc train nao.\n")

    src_img_dir = SRC_DIR / "images" / "train"
    src_lbl_dir = SRC_DIR / "labels" / "train"

    if not src_img_dir.exists() or not src_lbl_dir.exists():
        print(f"[!] Khong tim thay thu muc goc train tai: {SRC_DIR}")
        print("    Dam bao thu muc goc co chua 'images/train' va 'labels/train'.")
        return

    # Tạo các thư mục đích mới (train, val, test)
    for split in ["train", "val", "test"]:
        (DEST_DIR / "images" / split).mkdir(parents=True, exist_ok=True)
        (DEST_DIR / "labels" / split).mkdir(parents=True, exist_ok=True)

    image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".JPG", ".PNG"}

    labeled_pairs   = []  # (img_path, lbl_path) có nhãn
    unlabeled_pairs = []  # (img_path, lbl_path) nền trống (background)

    print("[+] Buoc 1: Quet va phan loai anh...")
    for img_file in sorted(src_img_dir.iterdir()):  # sort để xáo trộn có thứ tự nhất quán
        if img_file.is_file() and img_file.suffix in image_extensions:
            lbl_file = src_lbl_dir / f"{img_file.stem}.txt"

            is_labeled = False
            if lbl_file.exists():
                with open(lbl_file, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]
                if lines:
                    is_labeled = True

            if is_labeled:
                labeled_pairs.append((img_file, lbl_file))
            else:
                unlabeled_pairs.append((img_file, lbl_file if lbl_file.exists() else None))

    print(f"  - Anh co nhan:    {len(labeled_pairs)}")
    print(f"  - Anh nen (BG):   {len(unlabeled_pairs)}")
    print(f"  - Tong:           {len(labeled_pairs) + len(unlabeled_pairs)}")

    # Xáo trộn ngẫu nhiên với seed cố định (đảm bảo tái tạo)
    random.seed(RANDOM_SEED)
    random.shuffle(labeled_pairs)
    random.shuffle(unlabeled_pairs)

    def three_way_split(pairs, train_r, val_r):
        n = len(pairs)
        n_train = int(n * train_r)
        n_val   = int(n * val_r)
        train_p = pairs[:n_train]
        val_p   = pairs[n_train:n_train + n_val]
        test_p  = pairs[n_train + n_val:]
        return train_p, val_p, test_p

    tr_labeled, va_labeled, te_labeled     = three_way_split(labeled_pairs,   TRAIN_RATIO, VAL_RATIO)
    tr_unlabeled, va_unlabeled, te_unlabeled = three_way_split(unlabeled_pairs, TRAIN_RATIO, VAL_RATIO)

    train_set = tr_labeled + tr_unlabeled
    val_set   = va_labeled + va_unlabeled
    test_set  = te_labeled + te_unlabeled

    # Xáo trộn thêm lần nữa để trộn đều labeled và background
    random.shuffle(train_set)
    random.shuffle(val_set)
    random.shuffle(test_set)

    print(f"\n[+] Buoc 2: Ket qua phan chia TRAIN / VAL / TEST:")
    print(f"  - TRAIN: {len(train_set):>4} anh  (Co nhan: {len(tr_labeled):>4}, Nen: {len(tr_unlabeled):>3})")
    print(f"  - VAL:   {len(val_set):>4} anh  (Co nhan: {len(va_labeled):>4}, Nen: {len(va_unlabeled):>3})")
    print(f"  - TEST:  {len(test_set):>4} anh  (Co nhan: {len(te_labeled):>4}, Nen: {len(te_unlabeled):>3})")
    print(f"  - Tong:  {len(train_set)+len(val_set)+len(test_set)} anh")

    def copy_split_data(dataset_split, split_name):
        dest_img_path = DEST_DIR / "images" / split_name
        dest_lbl_path = DEST_DIR / "labels" / split_name

        for img_path, lbl_path in tqdm(dataset_split, desc=f"  Copy {split_name:>5}"):
            shutil.copy2(str(img_path), str(dest_img_path / img_path.name))
            if lbl_path and lbl_path.exists():
                shutil.copy2(str(lbl_path), str(dest_lbl_path / lbl_path.name))
            else:
                # Tạo file nhãn trống cho ảnh background
                (dest_lbl_path / f"{img_path.stem}.txt").touch()

    print("\n[+] Buoc 3: Sao chep file sang thu muc dich...")
    copy_split_data(train_set, "train")
    copy_split_data(val_set,   "val")
    copy_split_data(test_set,  "test")

    # Tạo/Đồng bộ file data.yaml
    src_yaml  = SRC_DIR / "data.yaml"
    dest_yaml = DEST_DIR / "data.yaml"
    if src_yaml.exists():
        try:
            with open(src_yaml, "r", encoding="utf-8") as sf:
                data = yaml.safe_load(sf)
            data["path"]  = str(DEST_DIR.absolute()).replace("\\", "/")
            data["train"] = "images/train"
            data["val"]   = "images/val"
            data["test"]  = "images/test"
            with open(dest_yaml, "w", encoding="utf-8") as df:
                yaml.safe_dump(data, df, sort_keys=False)
            print("\n[OK] Da cap nhat data.yaml voi duong dan train/val/test moi.")
        except Exception as e:
            print(f"\n[!] Loi dong bo data.yaml: {e}")

    print("\n" + "="*65)
    print(f" HOAN THANH! Ket qua tai: {DEST_DIR}")
    print("="*65)
    print("\n[GHI CHU QUAN TRONG]")
    print("  * Tap TEST (images/test, labels/test) la tap MU hoan toan.")
    print("  * Khong bao gio dung tap TEST de dieu chinh hyperparameter.")
    print("  * Chi dung TEST 1 LAN duy nhat vao cuoi cung de bao cao ket qua.")


if __name__ == "__main__":
    run_splitting()
