import os
import sys
import torch
import torch.nn as nn
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

# ================================================================
# ĐĂNG KÝ MODULE CBAM ATTENTION BẮT BUỘC ĐỂ LOAD BEST.PT
# ================================================================
class ChannelAttention(nn.Module):
    def __init__(self, channels, reduction=16):
        super().__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.max_pool = nn.AdaptiveMaxPool2d(1)
        reduction_channels = max(1, channels // reduction)
        self.fc = nn.Sequential(
            nn.Conv2d(channels, reduction_channels, 1, bias=False),
            nn.ReLU(),
            nn.Conv2d(reduction_channels, channels, 1, bias=False)
        )
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avg_out = self.fc(self.avg_pool(x))
        max_out = self.fc(self.max_pool(x))
        out = avg_out + max_out
        return self.sigmoid(out) * x


class SpatialAttention(nn.Module):
    def __init__(self, kernel_size=7):
        super().__init__()
        self.conv = nn.Conv2d(2, 1, kernel_size, padding=kernel_size//2, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avg_out = torch.mean(x, dim=1, keepdim=True)
        max_out, _ = torch.max(x, dim=1, keepdim=True)
        out = torch.cat([avg_out, max_out], dim=1)
        out = self.conv(out)
        return self.sigmoid(out) * x


class CBAM(nn.Module):
    def __init__(self, channels, reduction=16, kernel_size=7):
        super().__init__()
        self.channel_attention = ChannelAttention(channels, reduction)
        self.spatial_attention = SpatialAttention(kernel_size)

    def forward(self, x):
        x = self.channel_attention(x)
        x = self.spatial_attention(x)
        return x

import ultralytics.nn.tasks as tasks
import ultralytics.nn.modules as modules

setattr(tasks, 'CBAM', CBAM)
setattr(modules, 'CBAM', CBAM)
setattr(tasks.nn, 'CBAM', CBAM)
sys.modules['__main__'].CBAM = CBAM

# ================================================================
# CHỦẦN THƯ VIỆN CHÍNH THỨC SAHI (OFFICIAL SAHI LIBRARY INTEGRATION)
# ================================================================
try:
    from sahi import AutoDetectionModel
    from sahi.predict import get_sliced_prediction
    from sahi.utils.cv import read_image
    print("✅ Đã kết nối thành công Thư viện chính thức SAHI (sahi framework)!")
except ImportError:
    print("⚠️ Đang tự động cài đặt thư viện chính thức SAHI (pip install sahi)...")
    os.system("pip install sahi -q")
    from sahi import AutoDetectionModel
    from sahi.predict import get_sliced_prediction
    from sahi.utils.cv import read_image

BASE_DIR = Path(__file__).parent
BEST_WEIGHTS = BASE_DIR / "output_bridge_segment_sota" / "yolo26m_bridge_cbam_segment_sota-2" / "weights" / "best.pt"
if not BEST_WEIGHTS.exists():
    BEST_WEIGHTS = BASE_DIR / "output_bridge_segment_sota" / "yolo26m_bridge_cbam_segment_sota" / "weights" / "best.pt"

ROBOFLOW_CANONICAL_NAMES = {
    0: "Biological_Growth",
    1: "Control Point",
    2: "Corrosion",
    3: "Crack",
    4: "Efflorescence_Leaching",
    5: "Expansion Joint",
    6: "Exposed Rebar",
    7: "Guardrail Damaged",
    8: "Pothole Asphalt",
    9: "Spalling",
    10: "Staining_Infiltration",
}

def run_official_sahi_inference(image_path, output_dir, conf_threshold=0.25):
    """
    Chạy suy luận ĐÚNG CHUẨN THƯ VIỆN CHÍNH THỨC SAHI (sahi framework)
    trên ảnh kiểm định cầu 4K/8K siêu nét.
    """
    print(f"\n🚀 [OFFICIAL SAHI ENGINE] Running Slicing Inference on: {image_path}")

    if not BEST_WEIGHTS.exists():
        print(f"❌ Không tìm thấy file trọng số best.pt tại: {BEST_WEIGHTS}")
        return

    # 1. Khởi tạo Official SAHI AutoDetectionModel cho Ultralytics YOLO26-Seg
    detection_model = AutoDetectionModel.from_pretrained(
        model_type='yolov8',  # Ultralytics architecture wrapper
        model_path=str(BEST_WEIGHTS),
        confidence_threshold=conf_threshold,
        device='cuda:0' if torch.cuda.is_available() else 'cpu',
        category_mapping={
            str(class_id): class_name
            for class_id, class_name in ROBOFLOW_CANONICAL_NAMES.items()
        },
    )

    # 2. Đọc ảnh gốc bằng thư viện SAHI
    image = read_image(str(image_path))

    # 3. Chạy hàm get_sliced_prediction CHUẨN THƯ VIỆN CHÍNH THỨC SAHI
    result = get_sliced_prediction(
        image,
        detection_model,
        slice_height=640,
        slice_width=640,
        overlap_height_ratio=0.2,
        overlap_width_ratio=0.2,
        perform_standard_pred=True # Kết hợp cả Full Image prediction và Slice prediction
    )

    # 4. Xuất ảnh dự đoán chính thức từ SAHI
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / f"OFFICIAL_SAHI_{Path(image_path).name}"
    result.export_visuals(export_dir=str(output_dir), file_name=f"OFFICIAL_SAHI_{Path(image_path).stem}")

    print(f"✅ HỢP NHẤT SAHI NATIVE HOÀN TẤT!")
    print(f"   • Số lượng vùng phát hiện SAHI: {len(result.object_prediction_list)}")
    print(f"   • Ảnh xuất chuẩn SAHI lưu tại: {output_dir}\n")

if __name__ == "__main__":
    print(f"{'='*70}")
    print("  07. OFFICIAL SAHI FRAMEWORK INFERENCE ENGINE")
    print(f"{'='*70}")
    
    test_img_dir = BASE_DIR / "Dataset_Sliced" / "images" / "test"
    sample_images = list(test_img_dir.glob("*.jpg")) + list(test_img_dir.glob("*.png"))

    if sample_images:
        sample_img = sample_images[0]
        out_dir = BASE_DIR / "output_official_sahi_predictions"
        run_official_sahi_inference(
            image_path=sample_img,
            output_dir=out_dir,
            conf_threshold=0.25
        )
    else:
        print(f"⚠️ Chưa tìm thấy ảnh mẫu trong {test_img_dir}")
