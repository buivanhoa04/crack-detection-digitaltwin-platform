import os
import sys
import torch
import torch.nn as nn
from pathlib import Path
import ultralytics
import ultralytics.nn.tasks
import ultralytics.nn.modules

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

# 🛡️ FIX CUDNN STREAM & ALLOCATOR MEMORY FRAGMENTATION
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
torch.backends.cudnn.benchmark = True
torch.backends.cudnn.deterministic = False

# ================================================================
# DYNAMICALLY REGISTER CUSTOM CBAM LAYER IN ULTRALYTICS
# ================================================================
class ChannelAttention(nn.Module):
    def __init__(self, channels, reduction=16):
        super().__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.max_pool = nn.AdaptiveMaxPool2d(1)
        reduced = max(1, channels // reduction)
        self.fc = nn.Sequential(
            nn.Conv2d(channels, reduced, 1, bias=False),
            nn.ReLU(inplace=True),
            nn.Conv2d(reduced, channels, 1, bias=False)
        )
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avg_out = self.fc(self.avg_pool(x))
        max_out = self.fc(self.max_pool(x))
        return self.sigmoid(avg_out + max_out) * x

class SpatialAttention(nn.Module):
    def __init__(self, kernel_size=7):
        super().__init__()
        self.conv = nn.Conv2d(2, 1, kernel_size=kernel_size, padding=kernel_size // 2, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avg_out = torch.mean(x, dim=1, keepdim=True)
        max_out, _ = torch.max(x, dim=1, keepdim=True)
        out = torch.cat([avg_out, max_out], dim=1)
        return self.sigmoid(self.conv(out)) * x

class CBAM(nn.Module):
    def __init__(self, c1, *args, reduction=16, kernel_size=7):
        super().__init__()
        # c1 luôn là số channels THỰC TẾ của tensor x truyền vào do parse_model tự động tính theo scale s
        self.ca = ChannelAttention(c1, reduction=reduction)
        self.sa = SpatialAttention(kernel_size=kernel_size)

    def forward(self, x):
        return self.sa(self.ca(x))

# Register CBAM in Ultralytics modules dictionary
setattr(ultralytics.nn.tasks, 'CBAM', CBAM)
setattr(ultralytics.nn.modules, 'CBAM', CBAM)
setattr(sys.modules['ultralytics.nn.modules'], 'CBAM', CBAM)
setattr(sys.modules['ultralytics.nn.tasks'], 'CBAM', CBAM)
if hasattr(ultralytics.nn.tasks, 'parse_model'):
    sys.modules['ultralytics.nn.tasks'].__dict__['CBAM'] = CBAM

BASE_DIR = Path(__file__).parent
DATASET_YAML = BASE_DIR / "dacl10k_Dataset_Sliced" / "data.yaml"
PROJECT_DIR = BASE_DIR / "output_dacl10k_segment_sota"

def write_yolo26s_yaml(yaml_path):
    content = """# YOLO26 Small Segment CBAM SOTA Config (nc: 7)
nc: 7
scales:
  s: [0.33, 0.50, 1024]

backbone:
  - [-1, 1, Conv, [64, 3, 2]]
  - [-1, 1, Conv, [128, 3, 2]]
  - [-1, 2, C3k2, [128, True, 0.25]]
  - [-1, 1, Conv, [256, 3, 2]]
  - [-1, 2, C3k2, [256, True, 0.25]]
  - [-1, 1, Conv, [512, 3, 2]]
  - [-1, 2, C3k2, [512, True]]
  - [-1, 1, Conv, [1024, 3, 2]]
  - [-1, 2, C3k2, [1024, True]]
  - [-1, 1, SPPF, [1024, 5]]
  - [-1, 2, C2PSA, [1024]]

head:
  - [-1, 1, CBAM, [1024]]
  - [-1, 1, nn.Upsample, [None, 2, "nearest"]]
  - [[-1, 6], 1, Concat, [1]]
  - [-1, 2, C3k2, [512, True]]
  - [-1, 1, CBAM, [512]]
  - [-1, 1, nn.Upsample, [None, 2, "nearest"]]
  - [[-1, 4], 1, Concat, [1]]
  - [-1, 2, C3k2, [256, True]]
  - [-1, 1, CBAM, [256]]
  - [-1, 1, Conv, [256, 3, 2]]
  - [[-1, 15], 1, Concat, [1]]
  - [-1, 2, C3k2, [512, True]]
  - [-1, 1, Conv, [512, 3, 2]]
  - [[-1, 11], 1, Concat, [1]]
  - [-1, 2, C3k2, [1024, True]]
  - [[18, 22, 25], 1, Segment, [nc, 32, 256]]
"""
    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write(content)

def run_training():
    print(f"\n{'='*75}")
    print("  05. TRAIN ULTRA-FAST YOLO26s-SEG ON DACL10K DATASET (nc: 7)")
    print(f"{'='*75}")

    if not DATASET_YAML.exists():
        print(f"❌ Không tìm thấy file data.yaml tại {DATASET_YAML}")
        return

    target_cfg_path = BASE_DIR / "yolo26s_bridge_sota_seg.yaml"
    write_yolo26s_yaml(target_cfg_path)

    # 🧹 DỌN DẸP TRAIN.CACHE NẾU CÓ ĐỂ RESCAN CHUẨN XÁC TẬP TRAIN ĐÃ CẮT TỈA
    labels_train_dir = BASE_DIR / "dacl10k_Dataset_Sliced" / "labels"
    train_cache = labels_train_dir / "train.cache"
    if train_cache.exists():
        try:
            train_cache.unlink()
            print(">> Đã xóa train.cache cũ để Ultralytics tự động quét lại tập train mới!")
        except Exception:
            pass

    device = 0 if torch.cuda.is_available() else "cpu"
    print(f">> Khởi tạo mô hình YOLO26s-Seg CBAM trên thiết bị: {device}")
    
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        print(f"   • GPU Target: {gpu_name} ({vram:.2f} GB VRAM)")

    from ultralytics import YOLO
    
    # 🎯 LOAD CHUẨN "yolo26s-seg.pt" (BẢN TỐC ĐỘ SIÊU NHẸ 11M PARAMS)
    print(">> Đang nạp duy nhất file weights pretrained chuẩn: yolo26s-seg.pt...")
    model = YOLO(str(target_cfg_path)).load("yolo26s-seg.pt")
    print("   ✅ Đã nạp thành công bộ trọng số pretrained: yolo26s-seg.pt")

    train_args = {
        "data":          str(DATASET_YAML),
        "project":       str(PROJECT_DIR),
        "name":          "yolo26s_dacl10k_cbam_segment_fast_sota",
        "task":          "segment",
        "device":        device,
        "epochs":        100,       # Tối đa 100 Epochs
        "imgsz":         640,
        "batch":         16,        # Batch=16 chạy cực mát 4.5 GB VRAM!
        "workers":       4,         # Workers=4 cực kỳ ổn định
        "cache":         False,
        "deterministic": False,
        "optimizer":     "AdamW",
        "lr0":           0.001,
        "lrf":           0.01,
        "cos_lr":        True,
        "warmup_epochs": 3.0,
        "warmup_momentum": 0.8,
        "momentum":      0.937,
        "weight_decay":  0.0005,
        "patience":      30,        # Early Stopping tự ngắt khi bão hòa mAP
        "amp":           True,

        # Loss Weights
        "cls":           1.5,
        "box":           7.5,

        # Data Augmentations
        "degrees":       30.0,
        "translate":     0.1,
        "scale":         0.5,
        "fliplr":        0.5,
        "flipud":        0.5,
        "hsv_h":         0.015,
        "hsv_s":         0.7,
        "hsv_v":         0.4,
        "mosaic":        0.8,
        "mixup":         0.15,
        "copy_paste":    0.3,
        "close_mosaic":   15,
        "save":          True,
        "save_period":   10,
        "plots":         True
    }

    print("\n🚀 Bắt đầu quá trình huấn luyện dacl10k YOLO26s-Seg CBAM (Ultra Fast Speed ~15 mins/Epoch)...")
    results = model.train(**train_args)
    print(f"\n✅ HUẤN LUYỆN HOÀN TẤT! Weights lưu tại: {PROJECT_DIR / 'yolo26s_dacl10k_cbam_segment_fast_sota' / 'weights' / 'best.pt'}")

if __name__ == "__main__":
    run_training()
