import os
import sys
import math
import cv2
import json
import torch
import torch.nn as nn
import numpy as np
from pathlib import Path
from ultralytics import YOLO
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

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

PURE_7CLASSES_NAMES = {
    0: "Biological_Growth",
    1: "Corrosion",
    2: "Crack",
    3: "Efflorescence_Leaching",
    4: "Exposed Rebar",
    5: "Spalling",
    6: "Staining_Infiltration"
}

BASE_DIR = Path(__file__).parent
BEST_WEIGHTS = BASE_DIR / "output_bridge_segment_sota" / "yolo26m_bridge_cbam_segment_sota" / "weights" / "best.pt"
DATA_YAML = BASE_DIR / "Dataset_Sliced" / "data.yaml"

def evaluate_script_06_master():
    print(f"\n{'='*75}")
    print("  06. MASTER EVALUATION SCRIPT: NATIVE 7 PURE BRIDGE CLASSES (nc: 7)")
    print(f"{'='*75}")

    if not BEST_WEIGHTS.exists():
        print(f"❌ Chưa tìm thấy file weights best.pt tại: {BEST_WEIGHTS}")
        return

    model = YOLO(str(BEST_WEIGHTS), task="segment")
    eval_out_path = BASE_DIR / "output_eval_test" / "eval_7classes_pure_bridge_structural"

    metrics = model.val(
        data=str(DATA_YAML),
        split="test",
        project=str(BASE_DIR / "output_eval_test"),
        name="eval_7classes_pure_bridge_structural",
        batch=8,
        imgsz=640,
        device=0,
        plots=True,
        save_json=True
    )

    try:
        cm_matrix = metrics.confusion_matrix.matrix
        np.save(eval_out_path / "confusion_matrix.npy", cm_matrix)
        np.savetxt(eval_out_path / "confusion_matrix.csv", cm_matrix, delimiter=",", fmt="%.4f")
        print(f"   ✅ Đã lưu ma trận gốc: confusion_matrix.npy và confusion_matrix.csv")
    except Exception as e:
        print(f"⚠️ Warning saving confusion matrix npy/csv: {e}")

    print(f"\n✅ [BƯỚC 1 HOÀN TẤT] Kết quả Metrics NATIVE Ultralytics 7 Lớp [0..6]:")
    print(f"   • Mask Precision (7 Classes): {metrics.seg.p.mean():.4f}")
    print(f"   • Mask Recall    (7 Classes): {metrics.seg.r.mean():.4f}")
    print(f"   • Mask mAP50     (7 Classes): {metrics.seg.map50:.4f}")
    print(f"   • Mask mAP50-95  (7 Classes): {metrics.seg.map:.4f}")

    print(f"\n{'='*75}")
    print(f"✅ TỔNG HỢP ĐÁNH GIÁ 7 LỚP HƯ HỎNG KẾT CẤU NATIVE HOÀN TẤT!")
    print(f"📊 DANH SÁCH TỆP LƯU TRONG THƯ MỤC: {eval_out_path}")
    print(f"{'='*75}\n")

if __name__ == "__main__":
    evaluate_script_06_master()
