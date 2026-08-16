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
# CUDA & PYTORCH PERFORMANCE TUNING
# ================================================================
# Tăng tốc phép nhân ma trận trên Ampere GPU (như RTX 3090)
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
torch.backends.cudnn.benchmark = True

# Chống phân mảnh VRAM và ngăn ngừa lỗi CUDA Out Of Memory (OOM)
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

# ================================================================
# ĐỊNH NGHĨA ATTENTION MODULE CBAM (SOTA ATTENTION FOR ROAD DEFECTS)
# ================================================================
class ChannelAttention(nn.Module):
    def __init__(self, channels, reduction=16):
        super().__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.max_pool = nn.AdaptiveMaxPool2d(1)
        
        # [SOTA SAFE] Sử dụng max(1, channels // reduction) đề phòng số kênh nhỏ hơn 16
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
    """
    Convolutional Block Attention Module: Tập trung đặc trưng vào vết nứt (Spatial)
    và loại bỏ thông tin nền không liên quan như bóng cây/đá cát (Channel).
    """
    def __init__(self, channels, reduction=16, kernel_size=7):
        super().__init__()
        self.channel_attention = ChannelAttention(channels, reduction)
        self.spatial_attention = SpatialAttention(kernel_size)

    def forward(self, x):
        x = self.channel_attention(x)
        x = self.spatial_attention(x)
        return x

# ================================================================
# MONKEY PATCH: ĐĂNG KÝ ĐỘNG VÀ TỰ ĐỘNG SCALE KÊNH CHO CBAM
# ================================================================
import ultralytics.nn.tasks as tasks
import ultralytics.nn.modules as modules

original_parse_model = tasks.parse_model

def make_divisible(x, divisor):
    return int(math.ceil(x / divisor) * divisor) if x > 0 else 0

def pre_scale_cbam_args(d, width_factor):
    """
    Tính toán kênh đầu ra của các layer trước để gán số kênh thực tế (đã scale theo width_factor)
    cho đối số của CBAM trước khi Ultralytics khởi tạo mô hình.
    """
    ch = [3]
    layers = d['backbone'] + d['head']
    
    for i, layer in enumerate(layers):
        f, n, m, args = layer
        
        # Xác định kênh đầu vào c1
        if isinstance(f, int):
            f_idx = f if f >= 0 else i + f + 1
            c1 = ch[f_idx]
        elif isinstance(f, list):
            c1 = [ch[x if x >= 0 else i + x + 1] for x in f]
        else:
            c1 = None
            
        # Xác định kênh đầu ra c2
        if m in ('Conv', 'ConvTranspose', 'LightConv', 'DWConv'):
            c2 = int(args[0] * width_factor)
            c2 = make_divisible(c2, 8)
        elif m in ('C3k2', 'C2PSA', 'SPPF', 'Bottleneck', 'C2f', 'C3'):
            c2 = int(args[0] * width_factor)
            c2 = make_divisible(c2, 8)
        elif m == 'Concat':
            c2 = sum(c1)
        elif m == 'CBAM':
            # Gán đối số của CBAM bằng kênh đầu vào c1
            args[0] = c1
            c2 = c1
        elif m in ('Detect', 'OBB', 'Segment', 'Pose'):
            c2 = None
        else:
            # Fallback mặc định
            if len(args) > 0 and isinstance(args[0], int) and args[0] > 10:
                c2 = int(args[0] * width_factor)
                c2 = make_divisible(c2, 8)
            else:
                c2 = c1 if isinstance(c1, int) else c1[0]
                
        ch.append(c2)


def patched_parse_model(d, ch, verbose=True):
    """
    Hook đè vào parse_model của Ultralytics để đăng ký CBAM và tự động scale kênh.
    """
    setattr(tasks, 'CBAM', CBAM)
    setattr(modules, 'CBAM', CBAM)
    setattr(tasks.nn, 'CBAM', CBAM)
    
    width_factor = d.get('width_multiple', 1.0)
    pre_scale_cbam_args(d, width_factor)
    return original_parse_model(d, ch, verbose)

tasks.parse_model = patched_parse_model

# ================================================================
# 3. CẤU HÌNH ĐƯỜNG DẪN DỰ ÁN (CÓ THỂ ĐIỀU CHỈNH TRÊN SERVER)
# ================================================================
# Đường dẫn file yaml dataset OBB đã cắt patch trên server (Tự động nhận diện tuyệt đối theo thư mục chạy)
DATASET_YAML = str(Path(__file__).parent / "Dataset_ALL_Sliced" / "data.yaml")
# Thư mục lưu kết quả train OBB SOTA (Tự động lưu cùng thư mục)
PROJECT_DIR = str(Path(__file__).parent / "output_obb_sota")

# File cấu hình model OBB tích hợp attention CBAM (Tự động lấy đường dẫn tuyệt đối cùng thư mục với script)
MODEL_CONFIG = str(Path(__file__).parent / "yolo26_sota_obb.yaml")

# Trọng số pretrained ban đầu để fine-tune (YOLO26 Medium OBB)
PRETRAINED_WEIGHTS = "yolo26m-obb.pt"

# ================================================================
# 4. TIỆN ÍCH DỌN DẸP BỘ NHỚ
# ================================================================
def clean_mem():
    gc.collect()
    torch.cuda.empty_cache()
    if torch.cuda.is_available():
        torch.cuda.ipc_collect()
        torch.cuda.synchronize()
    time.sleep(3)

# ================================================================
# 5. TIẾN TRÌNH HUẤN LUYỆN CHÍNH
# ================================================================
def train_yolo26_obb_sota():
    print(f"\n{'='*65}")
    print(f"  BAT DAU HUAN LUYEN YOLO26m-OBB CBAM SOTA")
    print(f"{'='*65}")
    print(f"  Kien truc tuy chinh:  {MODEL_CONFIG}")
    print(f"  Trong so Pretrained:  {PRETRAINED_WEIGHTS}")
    print(f"  Dataset Config:       {DATASET_YAML}")
    print(f"  Thu muc ket qua:      {PROJECT_DIR}")
    # ================================================================
    # [SOTA AUTO-FIX] TỰ ĐỘNG HIỆU CHỈNH ĐƯỜNG DẪN TRONG DATA.YAML
    # ================================================================
    if Path(DATASET_YAML).exists():
        try:
            import yaml
            with open(DATASET_YAML, "r", encoding="utf-8") as f:
                data_cfg = yaml.safe_load(f)
            
            # Lấy đường dẫn thư mục chứa data.yaml làm path tuyệt đối
            current_abs_path = str(Path(DATASET_YAML).parent.absolute()).replace("\\", "/")
            if data_cfg.get("path") != current_abs_path:
                print(f">> [PATH AUTO-FIX] Phat hien sai lech duong dan trong data.yaml.")
                print(f"   Cu:  {data_cfg.get('path')}")
                print(f"   Moi: {current_abs_path}")
                data_cfg["path"] = current_abs_path
                with open(DATASET_YAML, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data_cfg, f, sort_keys=False)
                print("   >> Da tu dong cap nhat data.yaml thanh cong!")
        except Exception as e:
            print(f">> [PATH WARNING] Khong the tu dong sua duong dan data.yaml: {e}")

    print("\n" + "="*65 + "\n")

    # Khởi tạo mô hình OBB kết hợp CBAM
    # YOLO(MODEL_CONFIG) đọc yaml kiến trúc, CBAM được tiêm qua monkey patch ở trên.
    # .load(PRETRAINED_WEIGHTS) kế thừa weights của các Conv tương thích từ DOTA/COCO.
    print(">> Dang khoi tao mo hinh va tai trong so pretrained...")
    model = YOLO(MODEL_CONFIG, task="obb").load(PRETRAINED_WEIGHTS)

    try:
        from ultralytics import settings
        # Bật cấu hình tích hợp WandB chính thức của Ultralytics (tương thích 100% v8.4+)
        settings.update({"wandb": True})
        print(">> [WANDB SUCCESS] Da bat cau hinh dong bo tu dong WandB qua Ultralytics settings!")
    except Exception as e:
        print(f">> [WANDB WARNING] Khong the cau hinh WandB: {e}")

    # ================================================================
    # SIÊU THAM SỐ HUẤN LUYỆN OBB SOTA ĐẦY ĐỦ
    # ================================================================
    train_args = {
        "data":    DATASET_YAML,
        "project": PROJECT_DIR,
        "name":    "yolo26m_cbam_obb_sota",
        "task":    "obb",   # Ép buộc tác vụ nhận diện hộp xoay OBB
        "device":  0,       # GPU 0

        # ── Training Schedule ─────────────────────────────────────────
        "epochs": 100,      # [TỐI ƯU SOTA]: Rút ngắn xuống 100 epoch vì bắt đầu từ best.pt
        "imgsz":  640,      # Đồng bộ với kích thước patch slicing
        "batch":  4,        # [FIX OOM]: Giảm batch=4 vì dùng FP32 tốn VRAM hơn, an toàn cho GPU đang chạy nhiều luồng khác.
        # ── Optimizer & LR Scheduling ─────────────────────────────────
        "optimizer":        "AdamW",
        "lr0":              0.001,    # [SOTA] LR chuẩn cho AdamW khi fine-tune từ pretrained weights
        "lrf":              0.01,     # Cosine decay: LR giảm dần theo đường cong Cosine từ 0.001 -> 0.00001
        "cos_lr":           True,     # Bật Cosine Annealing scheduler
        "warmup_epochs":    3.0,      # Warmup 3 epoch (~10.000 steps ở batch 8)
        "warmup_momentum":  0.8,      # Momentum tăng dần từ 0.8 → momentum
        "momentum":         0.937,    # AdamW beta1
        "weight_decay":     0.0005,   # L2 regularization

        # ── [SOTA] Early Stopping ────────────────────────────────────
        # Tự động dừng nếu mAP50-OBB trên val không cải thiện sau 35 epoch
        "patience": 35,

        # ── Hardware & Optimization ──────────────────────────────────
        "amp":             False,  # [FIX SOTA]: Tắt Mixed Precision (amp=False) dùng chuẩn FP32. Các hàm Loss của OBB và CBAM rất dễ bị tràn số (Gradient Explosion) khi dùng FP16, dẫn đến lỗi NaN/Inf.
        "cache":           False,  # Đặt False để không tràn RAM/VRAM
        "workers":         2,      # 2 DataLoader workers nhẹ nhàng cho CPU/RAM
        "multi_scale":     False,  # Tắt Multi-Scale chống phân mảnh VRAM

        # ── [SOTA] Color & Brightness Augmentation ──────────────────
        # Cực quan trọng: ảnh đường thi công có ánh sáng thay đổi theo giờ/thời tiết
        "hsv_h": 0.015,    # Biến thiên Hue (màu sắc) ±1.5%
        "hsv_s": 0.70,     # Biến thiên Saturation (bão hòa) ±70%
        "hsv_v": 0.40,     # Biến thiên Value (độ sáng) ±40%

        # ── [SOTA] Geometric Augmentation đặc thù OBB ───────────────
        "degrees":     180.0,   # Xoay 0–180° → BẮT BUỘC để học góc xoay vết nứt
        "translate":   0.10,    # Dịch ảnh ±10% → tăng khả năng nhận diện rìa ảnh
        "scale":       0.50,    # Zoom in/out ±50% → học vết nứt ở các tỷ lệ khác nhau
        "shear":       2.0,     # Biến dạng xiên ±2° → giả lập góc chụp nghiêng
        "perspective": 0.0005,  # Perspective warp → giả lập ảnh drone góc thấp
        "fliplr":      0.50,    # Lật ngang 50%
        "flipud":      0.50,    # Lật dọc 50%

        # ── [SOTA] Complex Augmentation ─────────────────────────────
        "mosaic":       1.0,    # Mosaic 4 ảnh → học context đa dạng
        "mixup":        0.05,   # [FIXED] Giảm Mixup: trộn ảnh OBB gây mâu thuẫn góc xoay
        "copy_paste":   0.0,    # [FIXED] Tắt: đã có 500 ảnh PACP, bật thêm gây artifact chồng
        "close_mosaic": 15,     # Tắt mosaic 15 epoch cuối → fine-tune góc xoay



        # ── Loss Weights (điều chỉnh cân bằng localization ↔ classification) ─
        "box": 7.5,   # [FIXED] Về mặc định 7.5: không cần đẩy quá mạnh box
        "cls": 1.5,   # [FIXED] Tăng gấp 3 lần: buộc model phân biệt ổ gà↔nứt↔nứt_CS tốt hơn

        # ── Evaluation & Checkpointing ───────────────────────────────
        "save":        True,
        "save_period": 10,    # Lưu checkpoint mỗi 10 epoch (an toàn hơn)
        "val":         True,
        "plots":       True,
        "exist_ok":    True,
    }

    print("\n>> Bat dau huan luyen mo hinh...")
    model.train(**train_args)

    best_weights = Path(PROJECT_DIR) / "yolo26m_cbam_obb_sota" / "weights" / "best.pt"
    print(f"\n[OK] Qua trinh train ket thuc! File weights tot nhat tai: {best_weights}")

    # Giải phóng bộ nhớ GPU trước khi export
    del model
    clean_mem()

    # Tự động export sang TensorRT FP16 để infer realtime trên API
    if best_weights.exists():
        print("\n>> Dang xuat mo hinh sang TensorRT FP16 Engine...")
        try:
            export_model = YOLO(str(best_weights))
            export_model.export(format="engine", half=True, imgsz=640, device=0)
            print("  [OK] Xuat TensorRT Engine thanh cong!")
        except Exception as e:
            print(f"  [!] Loi xuat TensorRT (co the thieu CUDA/TRT lib tren server): {e}")

if __name__ == "__main__":
    train_yolo26_obb_sota()