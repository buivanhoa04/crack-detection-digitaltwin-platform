FROM buihoa04/crack_api_ai_v3:latest
# Thiết lập thư mục làm việc
WORKDIR /app

# Cài đặt ffmpeg cho transcode video
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg g++ && rm -rf /var/lib/apt/lists/*

# Copy file requirements và cài đặt các thư viện cần thiết
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ code vào image
COPY *.py .
COPY *.json .

# Cấu hình môi trường tải model qua mirror không bị chặn ở Việt Nam
ENV HF_ENDPOINT=https://hf-mirror.com

# Tạo các thư mục làm việc (Trọng số weights được mount từ Volume gắn ngoài ./weights:/app/weights)
RUN mkdir -p /app/weights /data/file/sources

# Mở cổng 8000 cho API
EXPOSE 8000

# Lệnh chạy server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
