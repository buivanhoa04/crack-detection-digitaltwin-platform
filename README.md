# 🏗️ Drone-based Crack Detection & 3D Digital Twin Inspection Platform

Hệ thống ứng dụng AI thị giác máy tính kết hợp tái tạo không gian 3D (Digital Twin) phục vụ công tác kiểm định, phát hiện hư hỏng và quản lý chất lượng kết cấu hạ tầng giao thông (Mặt đường bộ TCVN 8866/8859 & Cấu kiện cầu bê tông DACL10k).

---

## 🌟 Tính Năng Nổi Bật (Key Features)

- 🔍 **AI Vision SOTA Detection**: 
  - Mô hình **YOLO + CBAM Attention + Oriented Bounding Boxes (OBB)** chuyên dụng cho đường bộ.
  - Phân đoạn khuyết tật kết cấu cầu bê tông với **YOLO Segmentation + SAHI (Slicing Aided Hyper Inference)** phát hiện vết nứt siêu nhỏ ở ảnh độ phân giải cao (4K/8K).
  - Tích hợp **BoT-SORT / ByteTrack** theo dõi chuyển động camera drone và loại trừ trùng lặp thời gian thực.
  - Tối ưu tăng tốc phần cứng với **NVIDIA TensorRT FP16 / INT8 Dynamic Batching**.
- 🏢 **3D Digital Twin Reconstruction**:
  - Tái tạo không gian 3D cấu kiện công trình từ chuỗi ảnh/video drone qua pipeline **COLMAP / Meshroom**.
  - Trực quan hóa tương tác 3D trên Web qua **Three.js** kết hợp gắn thẻ vị trí khuyết tật trong không gian 3D.
- 📊 **Hệ Thống Báo Cáo & Khảo Sát Tự Động (TCVN)**:
  - Tự động phân loại mức độ nghiêm trọng, diện tích hư hại theo tiêu chuẩn quốc gia **TCVN**.
  - Tích hợp **Direct VLM (Vision Language Model)** thẩm định hiện trạng hư hỏng chuyên nghiệp.
- 💻 **Modern Web Interface**:
  - Giao diện Next.js 14, Tailwind CSS, Lucide icons, Dark/Light Mode.
  - Bản đồ GIS tích hợp định vị sự cố (Interactive Map), Telemetry HUD đo đạc thông số drone thời gian thực.

---

## 📁 Cấu Trúc Dự Án (Project Architecture)

`
crack-digitaltwin-platform/
├── api/                            # Khối AI & Phân Tích Dữ Liệu
│   ├── crack_api/                  # Core FastAPI AI Service (YOLO, SAHI, Tracking, Celery, MongoDB/Redis)
│   ├── meshroom_3d/                # Dịch vụ 3D Reconstruction (Meshroom / COLMAP)
│   ├── chatbot_middleware/         # Middleware kết nối RAG / AI Chatbot
│   ├── pipelines/                  # Pipeline huấn luyện mô hình
│   │   ├── bridge_defect/          # Huấn luyện khuyết tật cầu (DACL10k)
│   │   └── road_defect/            # Huấn luyện nứt mặt đường bộ
│   └── convert/                    # Tiện ích chuyển đổi dữ liệu & nhãn
│
├── web/                            # Khối Giao Diện & Điều Hành Web
│   ├── frontend/                   # Next.js 14 Web UI (3D Viewer, Telemetry HUD, Map, Defect Catalog)
│   ├── backend/                    # FastAPI Master Backend (Quản lý đợt khảo sát, sự cố, media auth)
│   └── nginx/                      # Cấu hình Reverse Proxy Nginx
│
├── docker-compose.yml              # File điều phối Docker Compose toàn hệ thống
├── .env.example                    # File mẫu cấu hình biến môi trường
└── .gitignore                      # Quy tắc loại trừ file dữ liệu lớn & bảo mật
`

---

## 🚀 Hướng Dẫn Cài Đặt & Chạy Hệ Thống

### 1. Yêu Cầu Môi Trường (Prerequisites)
- **Hệ điều hành**: Windows 10/11 (WSL2) hoặc Linux (Ubuntu 22.04 LTS).
- **Phần cứng**: NVIDIA GPU (Khuyên dùng VRAM >= 6GB, hỗ trợ CUDA 12.x / TensorRT).
- **Phần mềm**: Docker & Docker Compose, Node.js >= 18, Python >= 3.10.

### 2. Thiết Lập Biến Môi Trường
`ash
cp .env.example .env
# Chỉnh sửa file .env với thông tin token và cấu hình GPU phù hợp
`

### 3. Khởi Chạy Bằng Docker Compose
`ash
# Khởi động toàn bộ dịch vụ (Web Frontend, Backend, AI Engine, MongoDB, Redis)
docker compose up -d --build
`

### 4. Truy Cập Hệ Thống
- **Web Frontend**: http://localhost:3000
- **Backend API Docs**: http://localhost:8000/docs
- **Crack AI API Docs**: http://localhost:8000/docs (hoặc cổng cấu hình)

---

## 📄 License & Authors
- Developed for Smart Civil Infrastructure & Bridge/Road Inspection.
