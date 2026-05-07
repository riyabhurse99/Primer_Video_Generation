import boto3
from modules.storage.base import BaseStorage
from config.settings import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET, AWS_REGION
from utils.logger import get_logger

logger = get_logger(__name__)


class S3Storage(BaseStorage):

    def __init__(self):
        self.client = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION
        )
        self.bucket = AWS_S3_BUCKET

    def save(self, file_path: str, destination: str) -> str:
        logger.info(f"Uploading to S3: s3://{self.bucket}/{destination}")
        self.client.upload_file(
            file_path,
            self.bucket,
            destination,
            ExtraArgs={"ContentType": "video/mp4"}
        )
        url = f"https://{self.bucket}.s3.{AWS_REGION}.amazonaws.com/{destination}"
        logger.info(f"Uploaded: {url}")
        return url
