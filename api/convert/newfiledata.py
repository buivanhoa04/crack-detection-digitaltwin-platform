import os
import shutil
from pathlib import Path

def merge_and_filter_images(source_folder, destination_folder):
    # Tạo thư mục đích nếu chưa tồn tại
    os.makedirs(destination_folder, exist_ok=True)

    # Các định dạng ảnh cần xử lý (bạn có thể thêm nếu cần)
    valid_extensions = {'.jpg', '.jpeg', '.png', '.bmp'}
    
    count_copied = 0

    # Duyệt qua tất cả các thư mục và thư mục con
    for root, dirs, files in os.walk(source_folder):
        for file in files:
            # Tách tên file và đuôi mở rộng
            name, ext = os.path.splitext(file)
            
            # Kiểm tra xem file có phải là ảnh không
            if ext.lower() in valid_extensions:
                # Chỉ lấy những file có tên kết thúc bằng '00'
                if name.endswith('00'):
                    source_path = os.path.join(root, file)
                    dest_path = os.path.join(destination_folder, file)

                    # Xử lý trường hợp trùng tên file (nếu 2 thư mục con có ảnh tên giống hệt nhau)
                    counter = 1
                    while os.path.exists(dest_path):
                        new_name = f"{name}_{counter}{ext}"
                        dest_path = os.path.join(destination_folder, new_name)
                        counter += 1

                    # Copy file sang thư mục tổng hợp
                    shutil.copy2(source_path, dest_path)
                    count_copied += 1
                    print(f"Đã copy: {file}")

    print("-" * 30)
    print(f"Hoàn thành! Đã gộp thành công {count_copied} ảnh gốc.")

# --- ĐIỀN ĐƯỜNG DẪN CỦA BẠN VÀO ĐÂY ---
# Thư mục cha chứa nhiều thư mục con
SOURCE_DIR = r"D:\data\CR"

# Thư mục mới để chứa tất cả ảnh gốc đã được lọc
DEST_DIR = r"D:\data\CR2" 

merge_and_filter_images(SOURCE_DIR, DEST_DIR)