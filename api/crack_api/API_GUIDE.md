# HƯỚNG DẪN SỬ DỤNG VÀ TRIỂN KHAI CRACK DETECTION API V2

Tài liệu này hướng dẫn chi tiết cách triển khai, cấu hình hệ thống và sử dụng các API của **Hệ thống Nhận diện Khuyết tật Cầu/Đường (V2)**. Hệ thống tích hợp mô hình YOLO phát hiện hư hỏng, mô hình SegFormer phân tách làn đường, thuật toán theo dõi ByteTrack, công cụ tự động hiệu chuẩn kích thước thực tế theo TCVN 41:2019 và công nghệ phân tích bằng Vision LLM (Qwen2.5-VL / Gemma).

---

## 1. Sơ đồ Kiến trúc & Luồng Dữ liệu

Hệ thống hoạt động theo mô hình container hóa phân tán:

```mermaid
flowchart TD
    Client[Client / Web App] <-->|HTTP / WebSocket| API[FastAPI AI Service Container]
    API <-->|Đọc/Ghi dữ liệu| MongoDB[(MongoDB Container)]
    API -->|Quét & Theo dõi| YOLO[Mô hình YOLO + ByteTrack + GMC]
    API -->|Phân tách mặt đường| Seg[SegFormer Road Segmentation]
    API -->|Đo đạc kích thước thực| GSD[GSD Calibrator TCVN 41:2019]
    API -->|Phân tích chuyên sâu| VLM[Ollama / Vision LLM Server]
    API -->|Báo cáo kỹ thuật| Chatbot[Chatbot RAGFlow Middleware]

    subgraph Host_Storage [Lưu trữ trên Máy chủ Vật lý]
        ./weights[(Thư mục weights/)] <-->|Mount| API
        ./sources[(Thư mục sources/)] <-->|Mount| API
        ./mongo_data[(Thư mục mongo_data/)] <-->|Mount| MongoDB
    end
```

---

## 2. Hướng dẫn Triển khai Hệ thống

Hệ thống có thể chạy trên cả hệ điều hành **Windows** và **Ubuntu/Linux**. Bản đóng gói hỗ trợ cả phần cứng có GPU NVIDIA (khuyên dùng để đạt hiệu năng tối ưu) và CPU-only (tự động fallback sang mô hình ONNX chạy trên CPU).

### 2.1. Chuẩn bị Thư mục & Trọng số mô hình (Weights)
Trước khi khởi động Docker, bạn cần đặt các file trọng số AI vào đúng vị trí để hệ thống mount vào container:

1. Tạo cấu trúc thư mục dự án:
   ```bash
   v2/
   ├── weights/
   ├── sources/
   ├── mongo_data/
   └── docker-compose.yml
   ```
2. Đặt các tệp tin trọng số mô hình PyTorch (`.pt`) tương ứng vào thư mục `weights/`:
   - `weights/crack_road.pt` (Mô hình phát hiện khuyết tật mặt đường)
   - `weights/crack_bridge.pt` (Mô hình phát hiện khuyết tật cấu kiện cầu)
   - *Lưu ý*: Lần đầu tiên khởi động, hệ thống sẽ tự động chuyển đổi các file `.pt` này sang định dạng `.engine` (nếu chạy GPU) hoặc định dạng `.onnx` (nếu chạy CPU) để tăng tốc độ suy luận.

### 2.2. Hướng dẫn cấu hình GPU và CPU trong `docker-compose.yml`

Tệp `docker-compose.yml` đã được tối ưu hóa để chạy đa nền tảng. 

#### Môi trường chạy GPU (NVIDIA GPU - Khuyên dùng)
- Đảm bảo máy chủ đã cài đặt **NVIDIA Driver** và **NVIDIA Container Toolkit**.
- Giữ nguyên cấu hình mặc định của `docker-compose.yml` (khối `deploy` được mở để cấp quyền truy cập GPU cho container).

#### Môi trường chạy CPU (Không có card đồ họa NVIDIA)
Nếu deploy trên server không có GPU (ví dụ: các VPS thường hoặc máy cá nhân chỉ chạy CPU):
- Mở file `docker-compose.yml`.
- Tìm đến dòng `deploy:` của dịch vụ `ai-service` và **comment out (thêm dấu `#`)** toàn bộ khối cấu hình này:
  ```yaml
  # deploy:
  #   resources:
  #     reservations:
  #       devices:
  #         - driver: nvidia
  #           count: all
  #           capabilities: [ gpu ]
  ```

### 2.3. Lệnh build Image và khởi động dịch vụ
Tại thư mục chứa dự án `v2/`, thực hiện các lệnh sau:

1. **Build Docker Image**:
   ```bash
   docker build -t buihoa04/crack_api_ai_v3:latest .
   ```
2. **Push Docker Image lên Docker Hub (Nếu cần)**:
   ```bash
   docker login
   docker push buihoa04/crack_api_ai_v3:latest
   ```
3. **Khởi động hệ thống bằng Docker Compose**:
   ```bash
   docker compose up -d
   ```
   Lệnh này sẽ tự động tải cơ sở dữ liệu MongoDB và khởi chạy AI Service tại cổng `8000`.

---

## 3. Cấu hình biến môi trường (`docker-compose.yml`)

Dưới đây là các tham số cấu hình chính trong `docker-compose.yml` của dịch vụ `ai-service`:

| Biến môi trường | Giá trị mặc định | Giải thích |
| :--- | :--- | :--- |
| `API_TOKEN` | `secure_token_CrackAPI_12345678@@` | Token bảo mật dùng để gọi API. |
| `ALLOWED_IPS` | `""` | Danh sách IP được phép truy cập không cần token (ngăn cách bằng dấu phẩy). |
| `MONGO_DETAILS` | `mongodb://mongodb:27017/` | Chuỗi kết nối đến cơ sở dữ liệu MongoDB trong mạng Docker nội bộ. |
| `CHATBOT_ENABLED` | `false` | Bật (`true`) hoặc Tắt (`false`) tự động đẩy báo cáo sang Chatbot RAGFlow. |
| `CHATBOT_MIDDLEWARE_URL` | `http://host.docker.internal:8088` | Địa chỉ URL của Chatbot RAGFlow Middleware để tự động đẩy báo cáo kỹ thuật. |
| `CHATBOT_API_TOKEN` | `secure_token_RAGFlow_12345678@@` | Token xác thực với Chatbot Middleware. |
| `OLLAMA_BASE_URL` | `http://192.168.103.222:18001` | Địa chỉ máy chủ chạy Ollama để phân tích ảnh bằng Vision LLM. |
| `OLLAMA_MODEL` | `Qwen/Qwen2.5-VL-7B-Instruct-AWQ` | Model Vision LLM sử dụng trên Ollama. |
| `VISION_ENABLED` | `false` | Bật (`true`) hoặc Tắt (`false`) phân tích ảnh tự động bằng LLM. |

---

## 4. Cơ chế Xác thực API

Hệ thống bảo mật API thông qua kiểm tra địa chỉ IP vật lý và Token. Để thực hiện các yêu cầu API thành công, bạn cần chọn một trong các phương thức xác thực sau:

1. **HTTP Header (Khuyên dùng)**:
   - Header tên: `Authorization` với giá trị `Bearer <API_TOKEN>`
   - Hoặc Header tên: `X-API-Token` với giá trị `<API_TOKEN>`
2. **Query Parameter**:
   - Thêm tham số `?token=<API_TOKEN>` vào cuối đường dẫn URL gọi API.

*Lưu ý: Các endpoint tài liệu hệ thống `/docs`, `/openapi.json` và thư mục tệp tĩnh `/files/` được cấu hình public công khai.*

---

## 5. Danh sách các API Endpoints chi tiết

### 5.1. Nhận diện vết nứt trên Ảnh tĩnh (`POST /api/v1/detect/image`)
Sử dụng để phân tích ảnh tĩnh đơn lẻ. Hỗ trợ cơ chế tự động chia nhỏ ảnh (Tiling) đối với các bức ảnh chụp từ Drone có độ phân giải lớn (4K - 8K) nhằm giữ độ sắc nét tối đa của vết nứt khi đưa qua mô hình AI.

- **Request Body**:
  ```json
  {
    "FilePath": "/data/file/sources/2026/06/03/road/sample_image.jpg",
    "RequestId": "task_image_unique_001",
    "ModelType": "road"
  }
  ```
  *(Lưu ý: `FilePath` phải là đường dẫn tuyệt đối nằm bên trong thư mục mount `/data/file/sources` của container)*

- **Response thành công (HTTP 200)**:
  ```json
  {
    "status": true,
    "data": {
      "processingStatus": "xử lý xong",
      "datas": [
        {
          "sourceFilePath": "/data/file/sources/2026/06/03/road/sample_image.jpg",
          "images": [
            {
              "frame_index": 0,
              "timestamp": "00:00",
              "frameFilePath": "/files/2026/06/03/road/sample_image.jpg",
              "detections": [
                {
                  "track_id": 1,
                  "class": "road_pothole",
                  "confidence": 0.892,
                  "bbox": [120.5, 450.2, 340.8, 680.1],
                  "polygon": [[120.5, 450.2], [340.8, 450.2], [340.8, 680.1], [120.5, 680.1]],
                  "analysis": {
                    "observed_object": "Mặt đường bê tông nhựa nóng",
                    "defect_code_mapping": "[RD-PTH-006] Ổ gà mặt đường",
                    "current_status_details": "Nhận diện tự động từ AI: Phát hiện khuyết tật Ổ gà mặt đường với độ tin cậy 89.2%...",
                    "technical_analysis": {
                      "tcvn_references": [
                        "Theo tiêu chuẩn áp dụng TCVN 14182:2024...",
                        "Theo phân tích kỹ thuật: Ổ gà làm giảm cường độ và gây đọng nước mặt đường."
                      ]
                    },
                    "conclusion_and_repair_plan": "Hư hỏng mức độ trung bình. Phân nhóm bảo trì đề xuất: Bảo dưỡng thường xuyên.",
                    "recommended_actions": [
                      "Cắt vuông khu vực hư hỏng trước khi vá",
                      "Vá nóng bằng bê tông nhựa đầm chặt"
                    ]
                  }
                }
              ]
            }
          ]
        }
      ],
      "ErrorCode": null
    }
  }
  ```

---

### 5.2. Nhận diện vết nứt trên Video ngoại tuyến (`POST /api/v1/detect/video`)
Gửi yêu cầu phân tích video dài. Hệ thống sẽ tạo một tiến trình chạy ngầm (Background Task), thực hiện phân tích từng frame hình kết hợp bộ lọc chuyển động camera (Global Motion Compensation) và bộ theo dõi đối tượng (ByteTrack). Hệ thống tự động lưu trữ các khung hình rõ nét nhất chứa vết nứt mà không chèn khung vẽ đè lên hình để tối ưu hóa việc phân tích của các kỹ sư.

- **Request Body**:
  ```json
  {
    "FilePath": "/data/file/sources/2026/06/03/road/inspect_video.mp4",
    "RequestId": "task_video_unique_999",
    "ModelType": "road"
  }
  ```

- **Response thành công (HTTP 200)**:
  ```json
  {
    "status": true
  }
  ```
  *(API trả về ngay lập tức để Client không bị treo kết nối. Tiến trình xử lý sẽ chạy ẩn trong nền)*

---

### 5.3. Tra cứu trạng thái xử lý tác vụ (`GET /api/v1/status/{request_id}`)
Truy vấn trạng thái hiện tại của tác vụ xử lý video hoặc ảnh dựa trên `RequestId`.

- **Response thành công (Khi đang xử lý)**:
  ```json
  {
    "status": true,
    "data": {
      "processingStatus": "đang xử lý",
      "progress": "45.23%",
      "current_frame": 452,
      "total_frames": 1000,
      "sourceFilePath": "/data/file/sources/2026/06/03/road/inspect_video.mp4"
    }
  }
  ```

- **Response thành công (Khi đã hoàn thành)**:
  ```json
  {
    "status": true,
    "data": {
      "processingStatus": "xử lý xong",
      "trackingDataUrl": "/files/2026/06/03/road/inspect_video/tracking_data.json",
      "datas": [
        {
          "sourceFilePath": "/data/file/sources/2026/06/03/road/inspect_video.mp4",
          "images": [
            {
              "frame_index": 120,
              "timestamp": "00:04",
              "frameFilePath": "/files/2026/06/03/road/inspect_video/track_1.jpeg",
              "detections": [
                {
                  "track_id": 1,
                  "class": "road_alligator_crack",
                  "confidence": 0.815,
                  "bbox": [512.4, 200.1, 720.5, 410.8]
                }
              ]
            }
          ]
        }
      ]
    }
  }
  ```

---

### 5.4. Tự động hiệu chuẩn kích thước vết nứt GSD (`POST /api/v1/calibrate`)
Áp dụng tiêu chuẩn đo lường **TCVN 41:2019 (Báo hiệu đường bộ — Vạch kẻ đường)** để chuyển đổi kích thước khuyết tật từ Pixel sang kích thước thực tế (Chiều rộng thực: mm, Diện tích thực: m²). 

Nguyên lý hoạt động: AI tự động đo bề rộng pixel của vạch kẻ phân làn (có kích thước chuẩn quy định là 150mm hoặc 200mm) -> Tính toán chỉ số GSD (Ground Sample Distance - mm/pixel) -> Quy đổi trực tiếp cho vết nứt.

- **Request Body**:
  ```json
  {
    "image_width": 1920,
    "image_height": 1080,
    "manual_gsd": 0.0, 
    "detections": [
      {
        "class_name": "lane_line_solid",
        "confidence": 0.88,
        "bbox": [1400.0, 100.0, 1450.0, 800.0],
        "mask_points": [[1400, 100], [1450, 100], [1450, 800], [1400, 800]]
      },
      {
        "class_name": "road_transverse_crack",
        "confidence": 0.82,
        "bbox": [600.0, 300.0, 750.0, 320.0],
        "mask_points": [[600, 300], [750, 300], [750, 320], [600, 320]]
      }
    ]
  }
  ```
  *(Nếu có thông số GSD nhập tay từ dữ liệu EXIF của Drone, truyền giá trị vào `manual_gsd`. Nếu truyền `0.0`, hệ thống sẽ tự động đo đạc dựa trên vạch kẻ đường `lane_line_solid`)*

- **Response thành công**:
  ```json
  {
    "status": true,
    "data": {
      "is_calibrated": true,
      "gsd_mm_per_pixel": 3.0,
      "calibration_source": "lane_line_solid",
      "calibration_source_name": "Vạch liền phân làn",
      "calibration_confidence": 0.88,
      "damages": [
        {
          "class_name": "road_transverse_crack",
          "confidence": 0.82,
          "bbox": [600.0, 300.0, 750.0, 320.0],
          "pixel_width": 20.0,
          "pixel_area": 3000.0,
          "real_width_mm": 60.0,
          "real_area_m2": 0.027
        }
      ],
      "references_found": [
        {
          "class_name": "lane_line_solid",
          "standard_name": "Vạch liền phân làn",
          "standard_width_mm": 150.0,
          "confidence": 0.88
        }
      ]
    }
  }
  ```

---

### 5.5. Phân tích vết nứt chuyên sâu bằng Vision LLM (`POST /api/v1/analyze/snapshot`)
Gửi ảnh snapshot của một vết nứt cụ thể lên mô hình AI đa phương thức (Vision LLM) để đánh giá trực quan, phân tích nguyên nhân kết cấu và kiến nghị giải pháp sửa chữa chuyên sâu. 

- **Request Body**:
  ```json
  {
    "task_id": "task_video_unique_999",
    "track_id": 1,
    "force_reanalyze": false
  }
  ```

- **Response thành công**:
  ```json
  {
    "status": true,
    "source": "llm",
    "data": {
      "observed_object": "Mặt đường bê tông nhựa nóng",
      "defect_code_mapping": "[RD-ALC-001] Nứt da cá sấu",
      "current_status_details": "Vết nứt da cá sấu liên kết chặt chẽ dạng vảy trên diện rộng khoảng 1.5m2, các góc cạnh mảng bê tông nhựa có hiện tượng vỡ vụn nhẹ...",
      "technical_analysis": {
        "tcvn_references": [
          "Đối chiếu TCVN 14182:2024 và 22TCN 211-06...",
          "Cơ chế hư hỏng: Mỏi vật liệu do tải trọng trục bánh xe lặp lại nhiều lần kết hợp nền móng yếu."
        ]
      },
      "conclusion_and_repair_plan": "Mức độ hư hỏng: Trung bình đến Nghiêm trọng. Đề xuất cào bóc gia cố nền móng trước khi thảm lại mặt đường.",
      "recommendations_to_contractor": [
        "Đào bóc lớp bê tông nhựa bị mỏi nứt",
        "Lu lèn chặt móng đường đạt K >= 0.95"
      ]
    }
  }
  ```

---

### 5.6. Phân tích thời gian thực qua WebSocket Stream (`WS /api/v1/ws/stream`)
Dành cho các luồng xử lý trực tiếp (Real-time Stream) từ camera RTSP hoặc video đang chạy. API này sử dụng kết nối song công WebSocket giúp đẩy dữ liệu phát hiện khuyết tật liên tục lên Client với độ trễ cực thấp.

- **Cách gọi**:
  Client thiết lập kết nối WebSocket tới:
  `ws://localhost:8000/api/v1/ws/stream?file_path=rtsp://admin:12345@192.168.1.100:554/stream1&model_type=road&request_id=stream_live_001&token=secure_token_CrackAPI_12345678@@`

- **Dữ liệu trả về liên tục dưới dạng JSON**:
  ```json
  {
    "status": true,
    "data": {
      "processingStatus": "đang stream",
      "datas": [
        {
          "sourceFilePath": "rtsp://...",
          "images": [
            {
              "frame_index": 1052,
              "timestamp": "10:14:22",
              "frameFilePath": "/files/streams/stream_live_001/track_4.jpeg",
              "detections": [
                {
                  "track_id": 4,
                  "class": "road_transverse_crack",
                  "confidence": 0.852,
                  "bbox": [800.5, 400.2, 950.4, 420.1]
                }
              ]
            }
          ]
        }
      ]
    },
    "ErrorCode": null
  }
  ```

---

## 6. Sơ đồ dữ liệu MongoDB (Database Schemas)

Hệ thống lưu trữ toàn bộ dữ liệu trạng thái và kết quả phân tích trong MongoDB:

1. **Collection `tasks`**: Quản lý thông tin chung của các tác vụ khảo sát.
   - Trường chính: `_id` (Request ID), `processingStatus` (chờ xử lý / đang xử lý / xử lý xong / lỗi), `sourceFilePath`, `trackingDataUrl`.
2. **Collection `crack_results`**: Lưu trữ các frame hình snapshot đại diện và danh sách khuyết tật được phát hiện trong tác vụ.
   - Trường chính: `task_id`, `track_id`, `frame_index`, `timestamp`, `frameFilePath`, `detections` (mảng chứa tọa độ bbox, class, confidence).
3. **Collection `analysis_reports`**: Lưu các báo cáo phân tích chuyên sâu của từng vết nứt được tổng hợp từ Vision LLM.
   - Trường chính: `_id` (`task_id` + `track_id`), `defect_name`, `severity`, `real_width_mm`, `real_area_m2`, `conclusion_and_repair_plan`.
4. **Collection `defect_catalog`**: Thư viện chứa danh mục 12 loại khuyết tật tiêu chuẩn (Cầu và Đường) theo tiêu chuẩn Việt Nam để đối chiếu.

---

## 7. Chi tiết danh mục khuyết tật (Defect Catalog) — Kho tri thức Prompt cho LLM

### 7.1. Vai trò của Defect Catalog: Kho tri thức Prompt cho Vision LLM
Trong hệ thống AI Crack Detection V2, `defect_catalog` không chỉ đơn thuần là một bảng tra cứu dữ liệu tĩnh trong database, mà đóng vai trò cốt lõi như là **Kho tri thức Prompt (Prompt Knowledge Base)** trực tiếp điều hướng hành vi phân tích của **Vision LLM (Qwen2.5-VL)**. 

Bản chất của các mô hình Vision LLM là có khả năng hiểu hình ảnh rất tốt, nhưng lại thiếu kiến thức chuyên sâu về tiêu chuẩn kỹ thuật xây dựng đặc thù của Việt Nam (TCVN) và dễ bị hiện tượng "ảo tưởng" (hallucination) khi đưa ra các giải pháp sửa chữa. Để giải quyết vấn đề này, hệ thống áp dụng kỹ thuật **RAG (Retrieval-Augmented Generation) mức độ Prompt**. Khi mô hình YOLO phát hiện ra một khuyết tật (ví dụ: `road_alligator_crack` - Nứt da cá sấu), hệ thống sẽ truy vấn cấu trúc dữ liệu tương ứng trong `defect_catalog` và "bơm" trực tiếp các tiêu chuẩn TCVN liên quan, tiêu chí phân cấp hư hỏng, cùng các hướng dẫn khắc phục đã được định nghĩa chuẩn xác vào ngữ cảnh của Prompt gửi lên LLM. 

Cách tiếp cận này biến `defect_catalog` thành một "cuốn sách giáo khoa" thu nhỏ, ép LLM phải suy luận và trả ra kết quả đánh giá kỹ thuật đồng bộ với luật pháp và tiêu chuẩn xây dựng tại Việt Nam.

---

### 7.2. Cấu trúc dữ liệu chi tiết của 1 danh mục khuyết tật (Catalog Schema)
Mỗi bản ghi trong collection `defect_catalog` tuân thủ cấu trúc JSON chi tiết dưới đây:

| Trường dữ liệu | Kiểu dữ liệu | Vai trò trong Prompt của LLM & Hệ thống | Ví dụ |
| :--- | :--- | :--- | :--- |
| `class_name` | `string` | Tên lớp nhãn của YOLO (được dùng làm Khóa chính `_id` để đối chiếu). | `road_pothole` |
| `defect_code` | `string` | Mã ký hiệu khuyết tật theo chuyên ngành quản lý đường bộ. | `RD-PTH-006` |
| `defect_name` | `string` | Tên khuyết tật bằng tiếng Việt để hiển thị lên báo cáo. | `Ổ gà mặt đường` |
| `model_type` | `string` | Phân nhóm mô hình (áo đường nhựa hoặc kết cấu cầu bê tông). | `road` hoặc `bridge` |
| `tcvn_codes` | `array[string]` | Các văn bản tiêu chuẩn kỹ thuật Việt Nam áp dụng làm căn cứ pháp lý. | `["TCVN 14182:2024", "Thông tư 41/2024/TT-BGTVT"]` |
| `severity_criteria`| `object` | Tiêu chí phân cấp Nhẹ/Trung bình/Nặng để LLM đối chiếu với ảnh chụp thực tế. | `{ "low": "Đường kính < 30cm...", "high": "Đường kính > 60cm..." }` |
| `repair_guidelines`| `object` | Định hướng cho LLM đề xuất phương án khắc phục tạm thời & lâu dài đạt chuẩn. | `{ "temporary": "Vá nguội...", "permanent": "Vá nóng..." }` |
| `prompt_template`  | `string` | Khuôn mẫu định hướng (System Prompt con) hướng dẫn LLM tập trung phân tích vùng ảnh khuyết tật. | `"Hình ảnh ổ gà mặt đường nhựa. Phân tích đường kính..."` |
| `analysis`         | `object` | Mô tả cơ chế phá hủy vật liệu và nguyên nhân cốt lõi nạp vào tri thức của LLM. | `{ "description": "...", "causes": [...], "technical_detail": "..." }` |
| `tcvn_references`  | `array[string]` | Các trích dẫn tài liệu tham khảo chi tiết đi kèm báo cáo. | `["TCVN 8819:2011 - Mặt đường bê tông nhựa nóng"]` |
| `recommendations`   | `array[string]` | Các khuyến nghị bắt buộc đối với đơn vị thi công mà LLM sẽ trích xuất. | `["Vá nóng bằng bê tông nhựa đầm chặt", "Xử lý thoát nước"]` |
| `severity_levels`  | `array[object]` | Cấu hình tự động ánh xạ độ tin cậy AI sang mức độ nghiêm trọng (Nhẹ/Vừa/Nặng). | `[{"level": "low", "confidence_range": [0.55, 0.74], "label": "Nhẹ"}]` |

---

### 7.3. Danh sách 12 loại khuyết tật tiêu chuẩn (Mặc định trong hệ thống)
Hệ thống được thiết lập sẵn (seed tự động từ tệp `seed_catalog.json` khi khởi động database trống) 12 loại khuyết tật phổ biến tại Việt Nam:

#### Nhóm khuyết tật áo đường (`model_type: "road"`)
1. **Nứt da cá sấu (`road_alligator_crack` - RD-ALC-001)**: Nứt mỏi kết cấu thành mạng dày đặc do nền yếu, thoát nước kém. Tham chiếu *TCVN 14182:2024, TCVN 8819:2011*.
2. **Nứt ngang mặt đường (`road_transverse_crack` - RD-TRC-002)**: Vết nứt vuông góc trục đường do co ngót nhiệt hoặc nứt phản ảnh từ lớp dưới. Tham chiếu *TCVN 14182:2024*.
3. **Nứt dọc mặt đường (`road_longitudinal_crack` - RD-LGC-003)**: Nứt song song trục đường do mối nối thi công kém hoặc lún nền không đều. Tham chiếu *TCVN 14182:2024*.
4. **Vá mặt đường (`road_patched` - RD-PTC-004)**: Khu vực vá cũ bị lồi lõm, sụt lún hoặc rạn nứt mép nối cần xử lý lại. Tham chiếu *TCVN 14182:2024, TCVN 8865:2011*.
5. **Nứt chéo mặt đường (`road_oblique_crack` - RD-OBC-005)**: Nứt xiên chéo liên quan đến cung trượt taluy hoặc nền đường đắp mất ổn định. Tham chiếu *TCVN 9436:2012*.
6. **Ổ gà mặt đường (`road_pothole` - RD-PTH-006)**: Hố lõm sụt bề mặt gây xóc nẩy mạnh và mất an toàn giao thông nghiêm trọng. Tham chiếu *TCVN 14182:2024, Thông tư 41/2024/TT-BGTVT*.

#### Nhóm khuyết tật kết cấu cầu (`model_type: "bridge"`)
1. **Vết nứt cầu (`bridge_cracks` - BR-CRK-001)**: Nứt kết cấu dầm, mố, trụ cầu bê tông do co ngót, biến dạng nhiệt hoặc quá tải. Tham chiếu *TCVN 12885:2020, TCVN 9343:2012*.
2. **Lộ cốt thép cầu (`bridge_exposed_rebar` - BR-RBR-002)**: Bong tróc lớp bảo vệ làm lộ cốt thép chịu lực chính gây rỉ sét ăn mòn nguy hiểm. Tham chiếu *TCVN 9346:2012, TCVN 12885:2020*.
3. **Bề mặt rỗ tổ ong cầu (`bridge_honeycomb_surface` - BR-HCB-003)**: Lỗ rỗng sâu trong cấu kiện bê tông do đầm nén thiếu sót khi đổ. Tham chiếu *TCVN 4453:1995*.
4. **Gỉ sét cầu (`bridge_rust` - BR-RST-004)**: Ăn mòn hóa học kết cấu thép làm giảm tiết diện chịu lực chính. Tham chiếu *TCVN 9346:2012, TCVN 5572:2012*.
5. **Thấm nước cầu (`bridge_seepage` - BR-SPG-005)**: Nước thấm qua khe co giãn hoặc nứt kết cấu kèm theo kết tủa vôi hóa (thạch nhũ). Tham chiếu *TCVN 9065:2012*.
6. **Bong tróc bê tông cầu (`bridge_spalling` - BR-SPL-006)**: Bê tông bảo vệ nứt vỡ thành mảng lớn rụng khỏi cấu kiện chịu lực. Tham chiếu *TCVN 9343:2012*.

---

### 7.4. Các API quản lý Danh mục (CRUD Catalog)
Hệ thống cung cấp sẵn các Endpoint quản lý để bạn có thể thêm mới, cập nhật hoặc xóa các danh mục khuyết tật trong database MongoDB khi huấn luyện thêm các model AI mới hoặc cập nhật tiêu chuẩn TCVN:

#### 1. Lấy toàn bộ danh sách khuyết tật (`GET /api/v1/defect-catalog`)
- **Query Parameter (Tùy chọn)**: `model_type` (`road` hoặc `bridge`) để lọc.
- **Ví dụ gọi**: `GET /api/v1/defect-catalog?model_type=road`
- **Phản hồi**:
  ```json
  {
    "status": true,
    "total": 6,
    "data": [
      {
        "class_name": "road_pothole",
        "defect_code": "RD-PTH-006",
        "defect_name": "Ổ gà mặt đường",
        "model_type": "road",
        "tcvn_codes": ["TCVN 14182:2024"],
        ...
      }
    ]
  }
  ```

#### 2. Lấy chi tiết 1 loại khuyết tật (`GET /api/v1/defect-catalog/{class_name}`)
- **Ví dụ gọi**: `GET /api/v1/defect-catalog/road_pothole`

#### 3. Thêm mới / Cập nhật 1 danh mục (`POST /api/v1/defect-catalog`)
- **Request Body**:
  ```json
  {
    "class_name": "road_rutting",
    "defect_code": "RD-RUT-007",
    "defect_name": "Vệt hằn lún bánh xe",
    "model_type": "road",
    "tcvn_codes": ["TCVN 8819:2011"],
    "severity_criteria": {
      "low": "Độ sâu hằn lún < 10mm",
      "medium": "Độ sâu hằn lún từ 10mm đến 25mm",
      "high": "Độ sâu hằn lún > 25mm, mặt đường bị đùn trồi hai bên vệt bánh"
    },
    "repair_guidelines": {
      "temporary": "Bào phẳng cục bộ phần trồi để đảm bảo an toàn tạm thời",
      "permanent": "Phay cào bóc lớp bê tông nhựa cũ và tiến hành thảm lại bằng bê tông nhựa mới có mô-đun đàn hồi cao"
    },
    "prompt_template": "Hình ảnh vệt hằn lún bánh xe trên mặt đường nhựa. Đánh giá độ sâu lún và mức độ đùn nhựa theo {tcvn_codes}.",
    "analysis": {
      "description": "Biến dạng vĩnh cửu tích lũy dọc theo vệt bánh xe của phương tiện vận tải.",
      "causes": ["Nhiệt độ mặt đường cao liên tục", "Lưu lượng xe tải trọng nặng lớn", "Đầm nén mặt đường chưa đủ"],
      "technical_detail": "Lún vệt bánh xe làm giảm độ an toàn do đọng nước gây trơn trượt (hydroplaning)."
    },
    "tcvn_references": ["TCVN 8819:2011"],
    "recommendations": ["Cào bóc tái sinh nguội hoặc thảm lại nhựa mới chống hằn lún"],
    "severity_levels": [
      {"level": "low", "confidence_range": [0.55, 0.74], "label": "Nhẹ - Lún nông"},
      {"level": "medium", "confidence_range": [0.75, 0.89], "label": "Trung bình - Lún rõ rệt"},
      {"level": "high", "confidence_range": [0.90, 1.00], "label": "Nghiêm trọng - Lún sâu đùn trồi"}
    ]
  }
  ```

#### 4. Thêm hàng loạt danh mục (`POST /api/v1/defect-catalog/bulk`)
- **Request Body**:
  ```json
  {
    "items": [
      { "class_name": "road_rutting", ... },
      { "class_name": "road_bleeding", ... }
    ]
  }
  ```

#### 5. Xóa 1 danh mục khuyết tật (`DELETE /api/v1/defect-catalog/{class_name}`)
- **Ví dụ gọi**: `DELETE /api/v1/defect-catalog/road_rutting`
---