import os
import gc
import time
import math
import torch
import torch.nn as nn
from pathlib import Path
from ultralytics import YOLO
import warnings
warnings.filterwarnings("ignore")

# ================================================================
# CUDA & PYTORCH PERFORMANCE TUNING FOR AMPERE GPU (RTX 3060 12GB)
# ================================================================
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
torch.backends.cudnn.benchmark = True
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

# ================================================================
# DEFINITION OF CBAM ATTENTION MODULE FOR BRIDGE DEFECT SEGMENTATION
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

original_parse_model = tasks.parse_model

def pre_scale_cbam_args(d, width_factor):
    ch = [3]
    layers = d['backbone'] + d['head']
    for i, layer in enumerate(layers):
        f, n, m, args = layer
        if isinstance(f, int):
            f_idx = f if f >= 0 else i + f + 1
            c1 = ch[f_idx]
        elif isinstance(f, list):
            c1 = [ch[x if x >= 0 else i + x + 1] for x in f]
        else:
            c1 = None

        if m in ('Conv', 'ConvTranspose', 'LightConv', 'DWConv'):
            c2 = int(args[0] * width_factor)
        elif m in ('C3k2', 'C2PSA', 'SPPF', 'Bottleneck', 'C2f', 'C3'):
            c2 = int(args[0] * width_factor)
        elif m == 'Concat':
            c2 = sum(c1)
        elif m == 'CBAM':
            args[0] = c1
            c2 = c1
        elif m in ('Detect', 'OBB', 'Segment', 'Pose'):
            c2 = None
        else:
            c2 = c1 if isinstance(c1, int) else c1[0]
        ch.append(c2)

def patched_parse_model(d, ch, verbose=True):
    setattr(tasks, 'CBAM', CBAM)
    setattr(modules, 'CBAM', CBAM)
    setattr(tasks.nn, 'CBAM', CBAM)
    width_factor = d.get('width_multiple', 1.0)
    pre_scale_cbam_args(d, width_factor)
    return original_parse_model(d, ch, verbose)

tasks.parse_model = patched_parse_model

BASE_DIR = Path(__file__).parent
DATASET_YAML = str(BASE_DIR / "Dataset_Sliced" / "data.yaml")
PROJECT_DIR = str(BASE_DIR / "output_bridge_segment_sota")
MODEL_CONFIG = str(BASE_DIR / "yolo26_bridge_sota_seg.yaml")
PRETRAINED_WEIGHTS = "yolo26m-seg.pt"

parent_yolo26 = BASE_DIR.parent / "yolo26_train_pipeline" / "yolo26m-seg.pt"
if parent_yolo26.exists():
    PRETRAINED_WEIGHTS = str(parent_yolo26)

def train_bridge_seg_sota():
    print(f"\n{'='*65}")
    print("  BAT DAU HUAN LUYEN ULTRA-FAST YOLO26-SEG 7 PURE CLASSES (nc: 7)")
    print(f"{'='*65}")
    print(f"  Pretrained Weights:  YOLO26m-SEG ({PRETRAINED_WEIGHTS})")
    print(f"  GPU Target:          NVIDIA RTX 3060 12GB (AMP=True, Batch=12, Workers=6)")
    print(f"  Task:                INSTANCE SEGMENTATION (nc: 7)")
    print(f"  Architecture Config: {MODEL_CONFIG}")
    print(f"  Output Directory:    {PROJECT_DIR}")

    model = YOLO(MODEL_CONFIG, task="segment").load(PRETRAINED_WEIGHTS)

    train_args = {
        "data":        DATASET_YAML,
        "project":     PROJECT_DIR,
        "name":        "yolo26m_bridge_cbam_segment_sota",
        "task":        "segment",
        "device":      0,
        "epochs":      100,
        "imgsz":       640,
        "batch":       12,
        "workers":     6,
        "optimizer":   "AdamW",
        "lr0":         0.001,
        "lrf":         0.01,
        "cos_lr":      True,
        "warmup_epochs": 3.0,
        "warmup_momentum": 0.8,
        "momentum":    0.937,
        "weight_decay": 0.0005,
        "patience":    30,
        "amp":         True,

        "cls":         1.5,
        "box":         7.5,

        "degrees":     30.0,
        "translate":   0.1,
        "scale":       0.5,
        "fliplr":      0.5,
        "flipud":      0.5,
        "hsv_h":       0.015,
        "hsv_s":       0.7,
        "hsv_v":       0.4,
        "mosaic":      0.8,
        "mixup":       0.15,
        "copy_paste":  0.3,
        "close_mosaic": 20,
        "save":        True,
        "save_period": 10,
        "plots":       True
    }

    print("\n🚀 Bắt đầu quá trình Train Ultra-Fast YOLO26 7 Pure Bridge Classes SOTA...")
    results = model.train(**train_args)
    print("\n✅ HUẤN LUYỆN HOÀN TẤT! Best weights lưu tại: output_bridge_segment_sota/yolo26m_bridge_cbam_segment_sota/weights/best.pt")

if __name__ == "__main__":
    train_bridge_seg_sota()
