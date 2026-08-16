import os
import sys
import cv2
from tqdm import tqdm
import torch
from ultralytics import YOLO

# ================================================================
# CẤU HÌNH ĐƯỜNG DẪN
# ================================================================
# Đường dẫn tới file model best.pt của bạn
MODEL_PATH = r"D:\Drone\Weights\best_class.pt"

# Đường dẫn tới file Video đầu vào cần phân tích (ví dụ: .mp4, .avi)
INPUT_VIDEO_PATH = r"D:\Drone\100Ftask Road Inspec 1080p.mp4"

# Đường dẫn để lưu file Video kết quả đầu ra
OUTPUT_VIDEO_PATH = r"D:\Drone\result_classified.mp4"

# Thư mục để lưu ảnh chụp màn hình (snapshot) khi có lỗi
SNAPSHOTS_DIR = r"D:\Drone\Snapshots"

# ================================================================
# CHƯƠNG TRÌNH PHÂN TÍCH VIDEO CHUYÊN SÂU
# ================================================================
def main():
    # 1. Kiểm tra và tải mô hình
    if not os.path.exists(MODEL_PATH):
        print(f"❌ Không tìm thấy mô hình tại: {MODEL_PATH}")
        sys.exit()
    if not os.path.exists(INPUT_VIDEO_PATH):
        print(f"❌ Không tìm thấy file video đầu vào tại: {INPUT_VIDEO_PATH}")
        sys.exit()

    # Tạo thư mục lưu ảnh chụp nếu chưa tồn tại
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

    print("🧠 Đang tải mô hình YOLO26 Classification...")
    model = YOLO(MODEL_PATH)
    print("✅ Tải mô hình thành công!")

    # 2. Đọc Video đầu vào
    cap = cv2.VideoCapture(INPUT_VIDEO_PATH)
    if not cap.isOpened():
        print("❌ Lỗi: Không thể mở file video đầu vào.")
        sys.exit()

    # Lấy các thông số của video gốc
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    print(f"📊 Thông số Video: {width}x{height} | {fps} FPS | Tổng số {total_frames} khung hình.")

    # 3. Cấu hình đối tượng ghi Video đầu ra (Output Video Writer)
    # Sử dụng codec mp4v thông dụng
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(OUTPUT_VIDEO_PATH, fourcc, fps, (width, height))

    print("🚀 Bắt đầu phân tích video...")
    
    last_snapshot_idx = -30  # Khởi tạo thời điểm chụp ảnh gần nhất để tránh trùng lặp liên tục
    
    # Sử dụng tqdm để vẽ thanh tiến trình chạy trực quan
    for frame_idx in tqdm(range(total_frames), desc="Đang phân tích khung hình"):
        success, frame = cap.read()
        if not success:
            break
            
        # Cắt lấy vùng trung tâm hình vuông (ví dụ 1080x1080) để loại bỏ cỏ 2 bên lề và tránh méo hình khi resize
        h_f, w_f, _ = frame.shape
        if w_f > h_f:
            start_x = (w_f - h_f) // 2
            crop_frame = frame[0:h_f, start_x:start_x+h_f]
        else:
            crop_frame = frame
            
        # 4. Dự đoán trên khung hình trung tâm đã crop
        device = '0' if torch.cuda.is_available() else 'cpu'
        results = model.predict(source=crop_frame, imgsz=640, device=device, verbose=False)
        
        for result in results:
            probs = result.probs
            top1_idx = probs.top1
            top1_conf = probs.top1conf.item()
            class_name = result.names[top1_idx]
            
            # Cấu hình màu sắc nhãn (Đỏ cho lỗi, Xanh lá cho sạch)
            color = (0, 0, 255) if class_name == "damaged" else (0, 255, 0)
            label = f"{class_name.upper()}: {top1_conf:.2%}"
            
            # Vẽ chữ cảnh báo lên góc trái trên cùng của khung hình
            # (Bạn có thể tùy chỉnh font chữ và kích cỡ cho phù hợp độ phân giải video)
            cv2.putText(
                frame, 
                label, 
                (50, 100), 
                cv2.FONT_HERSHEY_SIMPLEX, 
                2.0,       # Cỡ chữ (tăng lên nếu video 4K/1080p)
                color, 
                4          # Độ dày nét chữ
            )
            
            # Chụp ảnh lưu lại nếu phát hiện hư hỏng (chỉ chụp khi độ tin cậy >= 90% và cách ảnh trước ít nhất 30 frames)
            if class_name == "damaged" and top1_conf >= 0.90 and (frame_idx - last_snapshot_idx) >= 30:
                last_snapshot_idx = frame_idx
                # Định dạng tên file: damage_94.52_frame_123.jpg (thêm frame index để tránh trùng lặp ghi đè)
                snapshot_name = f"damage_{top1_conf * 100:.2f}_frame_{frame_idx}.jpg"
                snapshot_path = os.path.join(SNAPSHOTS_DIR, snapshot_name)
                cv2.imwrite(snapshot_path, frame)
            
        # Ghi khung hình đã xử lý vào video kết quả
        out.write(frame)

    # 5. Giải phóng tài nguyên
    cap.release()
    out.release()
    cv2.destroyAllWindows()
    
    print(f"\n🎉 HOÀN TẤT PHÂN TÍCH VIDEO!")
    print(f"💾 Video kết quả được lưu tại: {OUTPUT_VIDEO_PATH}")

if __name__ == "__main__":
    main()