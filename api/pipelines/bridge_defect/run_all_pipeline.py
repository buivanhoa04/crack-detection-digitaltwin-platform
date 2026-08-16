import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
REQ_FILE = BASE_DIR / "requirements.txt"

def check_and_install_dependencies():
    print(">> Đang kiểm tra các thư viện phụ thuộc (Dependencies Check)...")
    required_packages = ["ultralytics", "cv2", "numpy", "yaml", "tqdm", "shapely"]
    missing = []
    
    for pkg in required_packages:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
            
    if missing and REQ_FILE.exists():
        print(f"⚠️ Phát hiện thiếu {len(missing)} thư viện: {missing}")
        print(">> Đang tự động cài đặt từ requirements.txt...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(REQ_FILE)], check=True)
        print("✅ Cài đặt hoàn tất!\n")
    else:
        print("✅ Tất cả thư viện phụ thuộc đã được cài đặt đầy đủ!\n")

if __name__ == "__main__":
    check_and_install_dependencies()

    # Bắt đầu trực tiếp từ Cắt Patch dữ liệu F:\bridge_defect_pipeline\data (Đã chia split sẵn)
    scripts = [
        ("Step 1: Patch Slicing 640x640 Polygon with Overlap từ F:\\bridge_defect_pipeline\\data", BASE_DIR / "03_slice_dataset_polygon_seg.py"),
        ("Step 2: Dynamic Oversampling Tail Classes (RFS)", BASE_DIR / "04_oversample_polygon_seg.py"),
        ("Step 3: Train YOLO26 Instance Segmentation CBAM SOTA Model trên RTX 3060 12GB", BASE_DIR / "05_train_bridge_seg_sota.py")
    ]

    print("="*65)
    print("  MASTER PIPELINE RUNNER - BRIDGE DEFECT POLYGON INSTANCE SEGMENTATION")
    print("  (Data Source: F:\\bridge_defect_pipeline\\data -> Model YOLO26 Segment)")
    print("="*65)

    for title, script_path in scripts:
        print(f"\n▶️ [{title}]...")
        if not script_path.exists():
            print(f"❌ Script not found: {script_path}")
            sys.exit(1)
            
        res = subprocess.run([sys.executable, str(script_path)])
        if res.returncode != 0:
            print(f"❌ Step failed with code {res.returncode}. Stopping pipeline.")
            sys.exit(res.returncode)

    print("\n🎉 HOÀN THÀNH TOÀN BỘ PIPELINE SOTA PHÂN ĐOẠN POLYGON KHUYẾT TẬT CẦU!")
