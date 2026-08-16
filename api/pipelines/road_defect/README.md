# HƯỚNG DẪN HUẤN LUYỆN YOLO26 OBB CBAM SOTA TRÊN SERVER

Thư mục này chứa đầy đủ công cụ tiền xử lý dữ liệu và huấn luyện mô hình **YOLO26 Oriented Bounding Box (OBB)** kết hợp cơ chế chú ý **CBAM (Convolutional Block Attention Module)** giúp tăng cường độ chính xác phát hiện vết nứt dẹt và loại bỏ bóng nhiễu.

---

## 📂 Danh sách các file trong thư mục:
1. **`yolo26_sota_obb.yaml`**: File cấu hình cấu trúc mạng YOLO26 OBB tích hợp attention CBAM ở backbone và neck.
2. **`slice_dataset_obb.py`**: Script cắt ảnh trượt (slicing) 640x640 chuyên dụng cho dữ liệu nhãn xoay OBB, kèm lọc 15% ảnh nền trống.
3. **`train_yolo26_obb_sota.py`**: Script huấn luyện chính trên server, chứa kỹ thuật **Monkey Patch** để Ultralytics nhận diện và dựng mô hình CBAM tự động mà không cần sửa mã nguồn thư viện hệ thống.

---

## 🛠️ Quy trình triển khai trên Server:

### Bước 1: Nén và Tải thư mục lên Server
Nén thư mục `yolo26_train_pipeline` lại và sử dụng SCP hoặc SFTP để tải lên server của bạn.
*Ví dụ dùng Terminal:*
```bash
scp -r yolo26_train_pipeline.zip tnadmin@<IP_SERVER>:/home/tnadmin/Yolo/
```

### Bước 2: Giải nén trên Server
Đăng nhập SSH vào server, di chuyển tới thư mục chứa file zip và tiến hành giải nén:
```bash
cd /home/tnadmin/Yolo/
unzip yolo26_train_pipeline.zip
cd yolo26_train_pipeline
```

### Bước 2.5: Chia tập dữ liệu (Train / Val Split)
1. Mở file `split_dataset_obb.py` và điều chỉnh các đường dẫn ở mục cấu hình ở đầu file (nếu cần):
   * `SRC_DIR`: Đường dẫn tới thư mục chứa dataset OBB gốc ban đầu (đang nằm hoàn toàn trong thư mục `train`).
   * `DEST_DIR`: Thư mục mới bạn muốn lưu trữ dataset sau khi chia tỉ lệ Train (80%) và Val (20%).
2. Chạy lệnh chia dữ liệu:
   ```bash
   python split_dataset_obb.py
   ```

### Bước 3: Chạy cắt ảnh trượt (OBB Slicing)
1. Mở file `slice_dataset_obb.py` và điều chỉnh các đường dẫn ở đầu file:
   * `SRC_DIR`: Đường dẫn trỏ tới thư mục kết quả của **Bước 2.5** (`DEST_DIR` - chứa dữ liệu đã chia train/val).
   * `DEST_DIR`: Đường dẫn nơi bạn muốn lưu dataset sau khi cắt patch 640x640.
2. Chạy lệnh cắt ảnh:
   ```bash
   python slice_dataset_obb.py
   ```

### Bước 3.5: Chạy cân bằng lớp (Oversampling)
1. Mở file `oversample_obb.py` và điều chỉnh đường dẫn `DATASET_DIR` ở đầu file trỏ tới thư mục chứa kết quả đã cắt ở **Bước 3** (`DEST_DIR`).
2. Chạy lệnh để nhân bản mẫu hiếm, cân bằng lớp:
   ```bash
   python oversample_obb.py
   ```

### Bước 4: Chạy huấn luyện (Training)
1. Mở file `train_yolo26_obb_sota.py` và cập nhật các đường dẫn ở đầu file:
   * `DATASET_YAML`: Đường dẫn tới file `data.yaml` nằm bên trong thư mục dataset đã cắt patch ở **Bước 3** (ví dụ `/home/tnadmin/Yolo/Dataset_OBB_Sliced/data.yaml`).
   * `PROJECT_DIR`: Thư mục bạn muốn lưu trữ kết quả huấn luyện (pt, csv, plots...).
2. Khởi động tiến trình train:
   ```bash
   python train_yolo26_obb_sota.py
   ```

---

## 💡 Các kỹ thuật tối ưu SOTA được tích hợp sẵn:
* **Progressive Augmentations & Warmup:** Tự động kích hoạt các phương pháp xoay OBB (`degrees=180.0`), lật dọc/ngang, Mosaic/Mixup/CopyPaste và tự động ngắt mosaic ở 10 epoch cuối nhằm ổn định phân phối góc xoay bBox.
* **BatchNorm & Gradient Stability:** Thiết lập cấu hình optimizer AdamW kết hợp Cosine Learning Rate Decay chuẩn xác.
* **Auto-Export TensorRT FP16:** Sau khi hoàn thành huấn luyện, script sẽ tự động chuyển đổi file trọng số tốt nhất (`best.pt`) sang TensorRT Engine (`.engine`) ở chế độ Half (FP16) để tích hợp trực tiếp vào FastAPI phục vụ nhận diện thời gian thực siêu tốc.
