import os
import random
import cv2
import yaml
from pymongo import MongoClient
from ultralytics import YOLO

MONGO_URL = os.getenv("MONGO_DETAILS", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db = client["digital_twin"]
tasks_collection = db["tasks"]
results_collection = db["crack_results"]

def generate_calibration_data(model_type="road", max_frames=300):
    print(f"📊 Đang tạo calibration dataset cho loại mô hình: {model_type}")
    
    # 1. Tìm các task đã xử lý thành công
    tasks = list(tasks_collection.find({
        "modelType": model_type,
        "processingStatus": "xử lý xong"
    }).limit(10))
    
    video_files = []
    if tasks:
        video_files = [task["sourceFilePath"] for task in tasks if task.get("sourceFilePath") and os.path.exists(task["sourceFilePath"])]
        
    # Nếu không tìm thấy video từ task thành công, quét trực tiếp trong thư mục sources
    if not video_files:
        sources_dir = "./sources" if os.path.exists("./sources") else "/data/file/sources"
        print(f"⚠️ Không tìm thấy task thành công hoặc file video tương ứng trong DB. Quét trực tiếp file video trong {sources_dir}...")
        if os.path.exists(sources_dir):
            for root, dirs, files in os.walk(sources_dir):
                for file in files:
                    if file.lower().endswith(('.mp4', '.avi', '.mov', '.mkv')):
                        video_files.append(os.path.join(root, file))
                        
    if not video_files:
        print("❌ Không tìm thấy video nào để trích xuất calibration.")
        return False
        
    print(f"Tìm thấy {len(video_files)} video để trích xuất.")
    
    # Tạo thư mục đầu ra
    output_dir = f"weights/calibration_{model_type}/images"
    os.makedirs(output_dir, exist_ok=True)
    
    # Xóa ảnh cũ nếu có
    for f in os.listdir(output_dir):
        if f.lower().endswith('.jpg'):
            try:
                os.remove(os.path.join(output_dir, f))
            except Exception:
                pass

    extracted_count = 0
    frames_per_video = max(10, max_frames // len(video_files))
    
    for video_path in video_files:
        if extracted_count >= max_frames:
            break
            
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            continue
            
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            continue
            
        # Chọn ngẫu nhiên các khung hình
        frame_indices = sorted(random.sample(range(total_frames), min(total_frames, frames_per_video)))
        
        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
                
            img_name = f"{model_type}_calib_{extracted_count:05d}.jpg"
            img_path = os.path.join(output_dir, img_name)
            cv2.imwrite(img_path, frame)
            extracted_count += 1
            
            if extracted_count >= max_frames:
                break
                
        cap.release()
        
    print(f"✅ Đã trích xuất {extracted_count} ảnh vào {output_dir}")
    
    # 2. Load model để lấy class names và tạo file yaml
    pt_model_path = f"weights/crack_{model_type}.pt"
    if os.path.exists(pt_model_path):
        try:
            model = YOLO(pt_model_path)
            class_names = model.names
            
            yaml_data = {
                "path": f"/app/weights/calibration_{model_type}",  # absolute path in docker container
                "train": "images",
                "val": "images",
                "names": class_names
            }
            
            yaml_path = f"weights/calibration_{model_type}.yaml"
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(yaml_data, f, default_flow_style=False)
            print(f"✅ Đã sinh file cấu hình {yaml_path}")
            return True
        except Exception as e:
            print(f"❌ Lỗi khi load model hoặc ghi YAML: {e}")
            return False
    else:
        print(f"⚠️ Không tìm thấy file model gốc {pt_model_path} để đọc class names.")
        return False

if __name__ == "__main__":
    generate_calibration_data("road", max_frames=300)
    generate_calibration_data("bridge", max_frames=300)
