import os
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
os.environ["CUDA_LAUNCH_BLOCKING"] = "0"

import gc
import time
import csv
import warnings
from pathlib import Path
import torch
from ultralytics import YOLO

warnings.filterwarnings("ignore")

# Cấu hình tối ưu Tensor Core trên GPU RTX/Tesla
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
torch.backends.cudnn.benchmark = True

# ================================================================
# CẤU HÌNH ĐƯỜNG DẪN (KAGGLE / LOCAL)
# ================================================================
IS_KAGGLE = False  # Đổi thành True nếu chạy trên Kaggle

if IS_KAGGLE:
    DATASET_DIR = "/kaggle/input/super-dataset-cls"
    PROJECT_DIR = "/kaggle/working/road_crack_yolo26_cls"
else:
    DATASET_DIR = r"C:\Users\buiva\Downloads\Super_Dataset_Cls"
    PROJECT_DIR = r"D:\API\road_crack_yolo26_cls"

MODEL_NAME = "yolo26m-cls.pt"  # Sử dụng model phân loại Medium của YOLO26

# ================================================================
# THIẾT LẬP HYPERPARAMETERS SOTA (YOLO26-CLS)
# ================================================================
TRAIN_ARGS = dict(
    data=DATASET_DIR,
    epochs=100,               # Đủ để mô hình hội tụ tốt
    imgsz=640,                # Giữ nguyên 640px để không bỏ lỡ các vết nứt siêu mảnh
    batch=32,                 # Batch size tối ưu (hạ xuống 16 nếu tràn VRAM GPU)
    patience=15,              # Early stopping nếu val loss không giảm sau 15 epochs
    workers=4,
    optimizer="AdamW",        # SOTA Optimizer cho các bài toán phân loại
    lr0=0.0005,               # Tốc độ học ban đầu
    lrf=0.01,                 # Tỷ lệ giảm LR cuối cùng
    weight_decay=0.0005,      # Phạt trọng số để tránh overfitting
    warmup_epochs=3.0,        # Khởi động ổn định gradient
    
    # Kỹ thuật chống nhiễu gán nhãn
    label_smoothing=0.1,      # Cực kỳ cần thiết cho bài toán phân loại nứt đường
    
    # Augmentations chuyên biệt cho ảnh UAV (Xoay tự do & Lật)
    degrees=180.0,            # Cho phép xoay 360 độ ngẫu nhiên
    scale=0.5,                # Thu phóng ngẫu nhiên
    fliplr=0.5,               # Lật ngang
    flipud=0.5,               # Lật dọc
    erasing=0.2,              # Random Erasing chống Overfitting
    mixup=0.0,                # Tắt Mixup để tránh làm mờ đặc trưng vết nứt mảnh
    
    # Cấu hình tính toán
    amp=True,                 # Kích hoạt Mixed Precision tiết kiệm VRAM và tăng tốc
    cache=False,              # Tắt cache tránh tràn RAM hệ thống
    save=True,
    val=True,
    plots=True,
)

# ================================================================
# CÁC HÀM BỔ TRỢ & BẢN VÁ LỖI
# ================================================================
def clean_mem():
    """Giải phóng tài nguyên RAM/VRAM"""
    gc.collect()
    torch.cuda.empty_cache()
    if torch.cuda.is_available():
        torch.cuda.ipc_collect()
        torch.cuda.synchronize()
    time.sleep(2)

def custom_csv_logger(csv_path):
    """Lưu trữ lịch sử train ra file CSV để theo dõi cục bộ"""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    header = ["epoch", "train/loss", "val/loss", "metrics/accuracy_top1", "lr"]
    if not csv_path.exists():
        with open(csv_path, "w", newline="") as f:
            csv.writer(f).writerow(header)

    def cb(trainer):
        try:
            metrics = trainer.metrics
            epoch = trainer.epoch + 1
            train_loss = trainer.loss.item() if hasattr(trainer, 'loss') else 0
            val_loss = metrics.get("val/loss", 0)
            acc = metrics.get("metrics/accuracy_top1", 0)
            lr = trainer.optimizer.param_groups[0]["lr"]
            
            with open(csv_path, "a", newline="") as f:
                csv.writer(f).writerow([epoch, round(train_loss, 5), round(val_loss, 5), round(acc, 5), round(lr, 8)])
        except Exception:
            pass
    return cb

# --- MONKEY PATCH CHỐNG LỖI NaN/Inf TRONG EMA KHI LƯU FLOAT16 ---
from ultralytics.engine.trainer import BaseTrainer
original_save_model = BaseTrainer.save_model

def patched_save_model(self):
    if hasattr(self, 'ema') and self.ema is not None:
        for p in self.ema.ema.parameters():
            p.data.clamp_(-65000.0, 65000.0)
    if hasattr(self, 'model') and self.model is not None:
        for p in self.model.parameters():
            p.data.clamp_(-65000.0, 65000.0)
    original_save_model(self)

BaseTrainer.save_model = patched_save_model

# ================================================================
# CHƯƠNG TRÌNH HUẤN LUYỆN CHÍNH
# ================================================================
def main():
    print("=" * 60)
    print("🚀 PIPELINE HUẤN LUYỆN YOLOV26-CLASSIFICATION SOTA")
    print(f"📂 Dataset: {DATASET_DIR}")
    print("=" * 60)
    
    clean_mem()
    
    # Khởi tạo mô hình YOLO26m-cls
    model = YOLO(MODEL_NAME)
    
    csv_path = Path(PROJECT_DIR) / "yolo26_cls_training_log.csv"
    model.add_callback("on_fit_epoch_end", custom_csv_logger(csv_path))
    
    # Thiết lập đa GPU nếu khả dụng
    num_gpus = torch.cuda.device_count() if torch.cuda.is_available() else 0
    train_device = ",".join(str(i) for i in range(num_gpus)) if num_gpus > 1 else "0"
    print(f">> Thiết bị: GPU {train_device}" if num_gpus > 0 else ">> Thiết bị: CPU")

    # Huấn luyện
    model.train(
        project=PROJECT_DIR,
        name="yolo26_binary_cls",
        device=train_device,
        exist_ok=True,
        **TRAIN_ARGS
    )
    
    # Export mô hình tốt nhất sang ONNX FP16
    best_weights = Path(PROJECT_DIR) / "yolo26_binary_cls" / "weights" / "best.pt"
    if best_weights.exists():
        print(f"\n🔥 Checkpoint tốt nhất tại: {best_weights}")
        print("📦 Đang tiến hành export sang ONNX FP16 để tối ưu nhúng...")
        clean_mem()
        best_model = YOLO(str(best_weights))
        best_model.export(format="onnx", imgsz=TRAIN_ARGS['imgsz'], simplify=True, half=True)
        print("🎉 Xuất mô hình ONNX thành công!")

if __name__ == "__main__":
    # Kết nối tài khoản WandB cá nhân của bạn
    WANDB_API_KEY = "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"
    os.environ["WANDB_API_KEY"] = WANDB_API_KEY
    os.environ["WANDB_MODE"] = "online"
    os.environ["WANDB_PROJECT"] = "road_crack_yolo26_cls"
    os.environ["WANDB_ENTITY"] = "buivanhoa261004-a"
    
    try:
        import wandb
        wandb.login(key=WANDB_API_KEY)
        from ultralytics import settings
        settings.update({'wandb': True})
    except ImportError:
        pass
        
    main()