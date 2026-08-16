import boto3
from botocore.config import Config
from app.config import settings

class MinioStorage:
    def __init__(self):
        self.endpoint = settings.MINIO_ENDPOINT
        self.access_key = settings.MINIO_ACCESS_KEY
        self.secret_key = settings.MINIO_SECRET_KEY
        self.bucket_name = settings.MINIO_BUCKET
        
        # S3 Client Configuration with Resilience Timeouts
        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            # v8.0: Resilience timeouts for high-latency network
            config=Config(
                signature_version="s3v4",
                connect_timeout=2, 
                read_timeout=3,
                retries={'max_attempts': 1}
            ),
            region_name="us-east-1",
        )
        
        # Ensure bucket exists (caught and handled gracefully to prevent startup crashes)
        try:
            self.ensure_bucket()
        except Exception as e:
            print(f"[MINIO WARNING] Failed to verify or create bucket '{self.bucket_name}' on startup: {e}")

    def ensure_bucket(self):
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            print(f"[MINIO] Bucket '{self.bucket_name}' verified successfully.")
        except Exception as head_err:
            # If bucket doesn't exist or is not accessible, attempt to create it
            print(f"[MINIO] Bucket '{self.bucket_name}' check failed. Attempting to create...")
            try:
                self.client.create_bucket(Bucket=self.bucket_name)
                print(f"[MINIO] Bucket '{self.bucket_name}' created successfully.")
            except Exception as create_err:
                print(f"[MINIO ERROR] Failed to create bucket '{self.bucket_name}': {create_err}")
                raise create_err

    def generate_presigned_url(self, object_name, expiration=3600):
        """
        Generate a presigned URL to upload a file directly to MinIO.
        """
        try:
            response = self.client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket_name, "Key": object_name},
                ExpiresIn=expiration,
            )
            return response
        except Exception as e:
            print(f"[MINIO ERROR] Failed to generate presigned URL: {e}")
            return None

    def get_download_url(self, object_name, expiration=3600):
        """
        Generate a presigned URL for downloading/viewing a file.
        """
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": object_name},
                ExpiresIn=expiration,
            )
        except Exception as e:
            print(f"[MINIO ERROR] Failed to generate download URL: {e}")
            return None

    def upload_file(self, local_path, object_name):
        """
        Upload a local file to MinIO.
        """
        try:
            self.client.upload_file(local_path, self.bucket_name, object_name)
            print(f"[MINIO] Uploaded {local_path} to {object_name}")
            return True
        except Exception as e:
            print(f"[MINIO ERROR] Upload failed: {e}")
            return False

storage_service = MinioStorage()
