FROM python:3.10-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Cài đặt ffmpeg, libGL cho OpenCV và docker.io để gọi DooD
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    curl \
    docker.io \
    && rm -rf /var/lib/apt/lists/*
    
# Cài đặt thư viện Python
COPY requirements_api.txt .
RUN pip install --no-cache-dir -r requirements_api.txt

# Copy source code
COPY main.py /app/main.py
COPY tracking_4d.py /app/tracking_4d.py
COPY static /app/static


EXPOSE 8090

# Khởi động Uvicorn trên port 8090
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
