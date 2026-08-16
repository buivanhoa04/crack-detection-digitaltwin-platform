import os
import sys
import math
import torch
import torch.nn as nn
import warnings
from pathlib import Path
from ultralytics import YOLO

# Cấu hình UTF-8 cho Windows / Linux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

warnings.filterwarnings("ignore")

# ================================================================
# ĐỊNH NGHĨA ATTENTION MODULE CBAM & REGISTRATION MONKEY PATCH
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

# Đăng ký CBAM vào tất cả các module của Ultralytics và __main__ để unpickle không bị lỗi AttributeError
import ultralytics.nn.tasks as tasks
import ultralytics.nn.modules as modules

setattr(tasks, 'CBAM', CBAM)
setattr(modules, 'CBAM', CBAM)
setattr(tasks.nn, 'CBAM', CBAM)
setattr(sys.modules['__main__'], 'CBAM', CBAM)


# ================================================================
# ĐƯỜNG DẪN CẤU HÌNH
# ================================================================
BASE_DIR = Path(__file__).parent.resolve()

# Kiểm tra các đường dẫn có thể chứa file best.pt
POSSIBLE_WEIGHT_PATHS = [
    BASE_DIR / "output_obb_sota" / "yolo26m_cbam_obb_sota" / "weights" / "best.pt",
    BASE_DIR / "output_obb_sota" / "weights" / "best.pt",
    BASE_DIR / "output_obb_sota" / "best.pt"
]

WEIGHTS_PATH = None
for p in POSSIBLE_WEIGHT_PATHS:
    if p.exists():
        WEIGHTS_PATH = p
        break

DATASET_YAML = BASE_DIR / "Dataset_ALL_Sliced" / "data.yaml"
OUTPUT_EVAL_DIR = BASE_DIR / "output_obb_sota" / "test_evaluation_results"

def evaluate_on_test_set():
    print("=================================================================")
    print("  ĐÁNH GIÁ MÔ HÌNH TRÊN TẬP TEST ĐỘC LẬP (BENCHMARK SOTA)")
    print("=================================================================")
    
    if WEIGHTS_PATH is None or not WEIGHTS_PATH.exists():
        print(f"[!] Không tìm thấy file trọng số best.pt trong output_obb_sota!")
        print("    Vui lòng kiểm tra lại đường dẫn chứa file best.pt")
        return

    print(f"  [+] Trọng số thử nghiệm: {WEIGHTS_PATH}")
    print(f"  [+] Tập dữ liệu Test:     {DATASET_YAML}")
    print(f"  [+] Kết quả sẽ lưu tại: {OUTPUT_EVAL_DIR}")
    
    # Khoi tao mo hinh
    model = YOLO(str(WEIGHTS_PATH))
    
    print("\n>> Đang chạy đánh giá trên tập Test độc lập (Có kích hoạt TTA Test-Time Augmentation)...")
    
    # Đánh giá trên tập TEST với TTA (augment=True) và vẽ đầy đủ biểu đồ (plots=True)
    results = model.val(
        data=str(DATASET_YAML),
        split="test",            # Ép buộc chạy trên tập Test độc lập
        batch=4,
        imgsz=640,
        device=0,
        augment=True,            # KÍCH HOẠT TTA (Test-Time Augmentation) để tối đa mAP
        plots=True,              # VẼ ĐẦY ĐỦ 100% BIỂU ĐỒ (PR, F1, P, R, Confusion Matrix)
        project=str(OUTPUT_EVAL_DIR.parent),
        name=OUTPUT_EVAL_DIR.name,
        exist_ok=True
    )

    print("\n=================================================================")
    print("  KẾT QUẢ ĐÁNH GIÁ BÁO CÁO (SUMMARY METRICS ON TEST SET)")
    print("=================================================================")
    
    try:
        # In bảng tổng hợp
        print(f"  - mAP50 (Toàn bộ lớp):      {results.box.map50 * 100:.2f}%")
        print(f"  - mAP50-95 (Toàn bộ lớp):   {results.box.map * 100:.2f}%")
        print(f"  - Precision (Trung bình):   {results.box.mp * 100:.2f}%")
        print(f"  - Recall (Trung bình):      {results.box.mr * 100:.2f}%")
        
        print("\n  [+] Bảng chỉ số chi tiết từng lớp hư hỏng:")
        print("  ---------------------------------------------------------------")
        print("   Lớp hư hỏng                 |  Precision  |   Recall   |  mAP50  | mAP50-95 ")
        print("  ---------------------------------------------------------------")
        
        names = results.names
        for i, c in enumerate(results.box.p):
            class_name = names.get(i, f"Class {i}")
            p = results.box.p[i] * 100
            r = results.box.r[i] * 100
            ap50 = results.box.ap50[i] * 100
            ap = results.box.ap[i] * 100
            print(f"   {class_name:<27} |   {p:6.2f}%   |  {r:6.2f}%  | {ap50:6.2f}% |  {ap:6.2f}% ")
        print("  ---------------------------------------------------------------")
        
        print(f"\n[THÀNH CÔNG] Đã xuất toàn bộ biểu đồ (PR Curve, F1 Curve, Confusion Matrix) vào:")
        print(f"             {OUTPUT_EVAL_DIR}")
        
    except Exception as e:
        print(f"[!] Không thể in chi tiết: {e}")

if __name__ == "__main__":
    evaluate_on_test_set()
