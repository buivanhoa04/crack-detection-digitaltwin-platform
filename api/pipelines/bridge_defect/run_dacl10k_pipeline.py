import os
import sys
import subprocess
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).parent
RAW_DIR = BASE_DIR / "dacl10k_raw"

def run_step(script_name, step_title):
    print(f"\n\n{'='*80}")
    print(f"  ▶️ [{step_title}]")
    print(f"{'='*80}\n")
    script_path = BASE_DIR / script_name
    if not script_path.exists():
        print(f"❌ Không tìm thấy script {script_name}")
        sys.exit(1)

    result = subprocess.run([sys.executable, str(script_path)])
    if result.returncode != 0:
        print(f"\n❌ Lỗi tại bước [{script_name}]. Mã lỗi: {result.returncode}")
        sys.exit(result.returncode)

def main():
    print(f"\n{'='*80}")
    print("  MASTER PIPELINE RUNNER - DACL10K BRIDGE DEFECT INSTANCE SEGMENTATION (nc: 7)")
    print("  (Official German WACV 2024 SOTA Benchmark -> YOLO26m-Seg CBAM Model)")
    print(f"{'='*80}")

    # Step 1: Skip download if dacl10k_raw exists on server
    if not RAW_DIR.exists() or not any(RAW_DIR.iterdir()):
        if (BASE_DIR / "01_download_and_extract_dacl10k.py").exists():
            run_step("01_download_and_extract_dacl10k.py", "Step 1: Download and Extract dacl10k Official Dataset")
    else:
        print(f"\n✅ Đã phát hiện bộ dữ liệu dacl10k_raw có sẵn tại {RAW_DIR}. Tự động bỏ qua bước tải xuống!")

    # Step 2: Convert LabelMe JSON to 7-class YOLO Polygon
    run_step("02_convert_dacl10k_to_yolo7.py", "Step 2: Convert LabelMe JSON to Native 7-Class YOLO Format")

    # Step 3: Patch Slicing 640x640 with Overlap
    run_step("03_slice_dacl10k_polygon_seg.py", "Step 3: Patch Slicing 640x640 Polygon with 20% Overlap")

    # Step 4: Perfect 7-Class Hybrid Balancing
    run_step("04_oversample_dacl10k_hybrid_balancing.py", "Step 4: Perfect 7-Class Hybrid Data Balancing (Ratio 1.15 : 1)")

    # Step 5: Train YOLO26m CBAM Segment Model
    run_step("05_train_dacl10k_yolo26_seg.py", "Step 5: Train YOLO26m-Seg CBAM Model on GPU")

    print(f"\n{'='*80}")
    print("🎉 TOÀN BỘ PIPELINE HUẤN LUYỆN DACL10K 7 LỚP ĐÃ HOÀN THÀNH XUẤT SẮC!")
    print(f"{'='*80}\n")

if __name__ == "__main__":
    main()
