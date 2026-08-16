import os
import shutil
import random
from pathlib import Path
from tqdm import tqdm

def create_image_level_cls_dataset(yolo_dataset_dir, clean_images_dir, dest_dir, train_ratio=0.70, val_ratio=0.15, test_ratio=0.15):
    yolo_root = Path(yolo_dataset_dir)
    clean_root = Path(clean_images_dir)
    dest_root = Path(dest_dir)
    
    img_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    random.seed(42)  # Đảm bảo tính nhất quán mỗi lần chia
    
    # --- BƯỚC 1: XỬ LÝ LỚP CÓ HƯ HỎNG (DAMAGED) ---
    # Chỉ đơn giản là copy ảnh từ các thư mục train/valid/test images của YOLO hiện tại sang
    splits = ["train", "valid", "test"]
    for split in splits:
        src_img_dir = yolo_root / split / "images"
        dest_damaged_dir = dest_root / split / "damaged"
        dest_damaged_dir.mkdir(parents=True, exist_ok=True)
        
        if src_img_dir.exists():
            print(f"📦 Đang copy ảnh có lỗi sang tập [{split}/damaged]...")
            img_files = [p for p in src_img_dir.glob("*") if p.suffix.lower() in img_extensions]
            for img_path in tqdm(img_files):
                shutil.copy2(img_path, dest_damaged_dir / img_path.name)
        else:
            print(f"⚠️ Không tìm thấy thư mục: {src_img_dir}")

    # --- BƯỚC 2: XỬ LÝ LỚP KHÔNG HƯ HỎNG (UNDAMAGED) ---
    # Quét toàn bộ kho ảnh sạch của bạn
    print("\n🔍 Đang quét kho ảnh sạch...")
    all_clean_files = [p for p in clean_root.rglob("*") if p.is_file() and p.suffix.lower() in img_extensions]
    total_clean = len(all_clean_files)
    print(f"📊 Tìm thấy {total_clean} ảnh sạch.")
    
    if total_clean == 0:
        print("❌ Không tìm thấy ảnh sạch nào trong thư mục chỉ định!")
        return

    # Trộn ngẫu nhiên
    random.shuffle(all_clean_files)
    
    # Tính toán số lượng ảnh sạch phân bổ cho từng tập
    num_train = int(total_clean * train_ratio)
    num_val = int(total_clean * val_ratio)
    num_test = total_clean - num_train - num_val
    
    clean_splits = {
        "train": all_clean_files[:num_train],
        "valid": all_clean_files[num_train:num_train + num_val],
        "test": all_clean_files[num_train + num_val:]
    }
    
    # Copy ảnh sạch vào thư mục tương ứng
    for split, files in clean_splits.items():
        dest_undamaged_dir = dest_root / split / "undamaged"
        dest_undamaged_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"🚀 Đang chia và copy {len(files)} ảnh sạch sang tập [{split}/undamaged]...")
        for img_path in tqdm(files):
            # Tránh trùng tên nếu kho ảnh sạch có trùng tên với ảnh có sẵn
            dest_file_path = dest_undamaged_dir / img_path.name
            if dest_file_path.exists():
                dest_file_path = dest_undamaged_dir / f"clean_{img_path.name}"
            shutil.copy2(img_path, dest_file_path)

    # --- BƯỚC 3: THỐNG KÊ LẠI BỘ DỮ LIỆU MỚI ---
    print("\n================ THỐNG KÊ DATASET PHÂN LOẠI NHỊ PHÂN MỚI ================")
    for split in splits:
        damaged_count = len(list((dest_root / split / "damaged").glob("*")))
        undamaged_count = len(list((dest_root / split / "undamaged").glob("*")))
        print(f"📂 Tập [{split}]:")
        print(f"   ├─ Có hư hỏng (damaged)  : {damaged_count} ảnh")
        print(f"   └─ Không hư hỏng (undamaged): {undamaged_count} ảnh")
    print("=========================================================================")

if __name__ == "__main__":
    # Thay đổi đường dẫn theo cấu hình máy của bạn
    YOLO_DATASET = r"C:\Users\buiva\Downloads\Crack.v5i.yolo26" # Folder chứa train/valid/test của YOLO
    CLEAN_IMAGES_DIR = r"D:\data\CR2"           # Thư mục chứa kho ảnh sạch của bạn
    DEST_CLASSIFICATION = r"D:\data\Super_Dataset_Cls"  # Thư mục chứa kết quả đầu ra
    
    create_image_level_cls_dataset(
        yolo_dataset_dir=YOLO_DATASET,
        clean_images_dir=CLEAN_IMAGES_DIR,
        dest_dir=DEST_CLASSIFICATION,
        train_ratio=0.70,
        val_ratio=0.15,
        test_ratio=0.15
    )
    print("✨ Hoàn tất chuẩn bị dữ liệu phân loại nhị phân ở cấp độ ảnh gốc!")