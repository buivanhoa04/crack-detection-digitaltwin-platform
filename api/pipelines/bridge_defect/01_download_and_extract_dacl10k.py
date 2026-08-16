import os
import sys
import zipfile
import urllib.request
from pathlib import Path
from tqdm import tqdm

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).parent
RAW_DIR = BASE_DIR / "dacl10k_raw"
ZIP_PATH = BASE_DIR / "dacl10k_v2_devphase.zip"
DOWNLOAD_URL = "https://dacl10k.s3.eu-central-1.amazonaws.com/dacl10k-challenge/dacl10k_v2_devphase.zip"

class DownloadProgressBar(tqdm):
    def update_to(self, b=1, bsize=1, tsize=None):
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)

def download_and_extract():
    print(f"\n{'='*75}")
    print("  01. DOWNLOADING AND EXTRACTING DACL10K OFFICIAL DATASET (WACV 2024)")
    print(f"{'='*75}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if not ZIP_PATH.exists() or ZIP_PATH.stat().st_size < 4000000000:
        print(f">> Bắt đầu tải file zip dacl10k (4.75 GB) từ Amazon S3...")
        with DownloadProgressBar(unit='B', unit_scale=True, miniters=1, desc="Downloading dacl10k_v2_devphase.zip") as t:
            urllib.request.urlretrieve(DOWNLOAD_URL, filename=ZIP_PATH, reporthook=t.update_to)
        print("✅ Tải xong file zip thành công!")
    else:
        print(f"✅ File zip dacl10k đã tồn tại sẵn tại {ZIP_PATH} ({ZIP_PATH.stat().st_size / (1024**3):.2f} GB)")

    print("\n>> Bắt đầu giải nén bộ dữ liệu dacl10k...")
    with zipfile.ZipFile(ZIP_PATH, 'r') as zip_ref:
        zip_ref.extractall(RAW_DIR)
    print(f"✅ Giải nén thành công vào thư mục {RAW_DIR}!")

if __name__ == "__main__":
    download_and_extract()
