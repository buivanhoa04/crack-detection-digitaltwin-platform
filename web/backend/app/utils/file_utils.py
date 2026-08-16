"""
File Utilities — Quản lý dọn dẹp thư mục vật lý tự động cho Digital Twin.
"""
import os
import shutil
import logging
from app.config import settings

logger = logging.getLogger("digitaltwin.file_utils")


def cleanup_empty_parent_directories(target_path: str, root_boundary: str = None):
    """
    Tự động quét ngược và xóa các thư mục cha rỗng (ví dụ: thư mục model_type, thư mục ngày DD, 
    thư mục tháng MM, thư mục năm YYYY) sau khi một tác vụ (task) bị xóa khỏi đĩa.
    
    Dừng lại ngay lập tức nếu:
    1. Thư mục cha vẫn còn chứa các task con khác hoặc file dữ liệu khác.
    2. Đạt tới ranh giới root_boundary (ví dụ: LOCAL_SOURCES_DIR hoặc UPLOAD_DIR).
    3. Đạt tới thư mục gốc ổ đĩa hệ thống.
    """
    if not target_path:
        return

    # Mặc định root_boundary lấy từ LOCAL_SOURCES_DIR trong settings
    if not root_boundary:
        root_boundary = getattr(settings, "LOCAL_SOURCES_DIR", r"D:\crack_api\sources")

    # Xác định thư mục bắt đầu quét ngược
    if not os.path.exists(target_path):
        current_dir = os.path.dirname(target_path)
    elif os.path.isdir(target_path):
        current_dir = os.path.dirname(target_path)
    else:
        current_dir = os.path.dirname(target_path)

    abs_boundary = os.path.abspath(root_boundary) if root_boundary else None

    while current_dir:
        abs_current = os.path.abspath(current_dir)

        # 1. Kiểm tra ranh giới Root Boundary
        if abs_boundary and (abs_current == abs_boundary or not abs_current.startswith(abs_boundary)):
            break

        # 2. Tránh xóa thư mục gốc ổ đĩa (C:\, D:\, /)
        if abs_current == os.path.dirname(abs_current):
            break

        if not os.path.exists(abs_current):
            current_dir = os.path.dirname(abs_current)
            continue

        try:
            # Lọc các file/folder hợp lệ (bỏ qua file hệ thống ẩn như .DS_Store, Thumbs.db)
            items = [i for i in os.listdir(abs_current) if not i.startswith('.') and i.lower() != 'thumbs.db']
            
            if len(items) == 0:
                # Thư mục hoàn toàn rỗng -> Xóa thư mục cha rỗng này
                # Dọn nốt các file ẩn còn sót nếu có
                for hidden in os.listdir(abs_current):
                    hidden_path = os.path.join(abs_current, hidden)
                    if os.path.isfile(hidden_path):
                        try:
                            os.remove(hidden_path)
                        except Exception:
                            pass
                os.rmdir(abs_current)
                logger.info(f"🧹 [Auto Cleanup] Deleted empty parent directory: {abs_current}")
                print(f"🧹 [Auto Cleanup] Deleted empty parent directory: {abs_current}")
                # Tiến tục quét tiếp thư mục cấp cha cao hơn nữa
                current_dir = os.path.dirname(abs_current)
            else:
                # Thư mục vẫn còn chứa task khác hoặc dữ liệu khác -> DỪNG LẠI NGAY
                logger.info(f"📁 [Auto Cleanup] Retained parent directory (contains {len(items)} items): {abs_current}")
                print(f"📁 [Auto Cleanup] Retained parent directory (contains {len(items)} items): {abs_current}")
                break
        except Exception as e:
            logger.warning(f"⚠️ [Auto Cleanup Warning] Could not remove folder {abs_current}: {e}")
            print(f"⚠️ [Auto Cleanup Warning] Could not remove folder {abs_current}: {e}")
            break
