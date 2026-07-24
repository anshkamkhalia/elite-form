import os
import uuid
import boto3
from botocore.client import Config
from dotenv import load_dotenv

load_dotenv()

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.getenv("R2_BUCKET")

client = boto3.client(
    "s3",
    endpoint_url=os.environ["R2_ENDPOINT"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    region_name="auto",
    config=Config(signature_version="s3v4"),
)

BUCKET = os.environ["R2_BUCKET"]


def upload_video(local_path: str, folder: str) -> str:
    key = f"{folder}/{uuid.uuid4()}.mp4"

    client.upload_file(local_path, BUCKET, key)

    return key


def presigned_video_url(key: str, expires_seconds: int = 60 * 60 * 24) -> str:
    """Signed GET URL for a stored video. R2's S3 endpoint rejects unsigned
    requests, so plain `base_url + key` links can't play in a browser unless
    the bucket has public access (pub-*.r2.dev / custom domain). Presigning
    makes playback work regardless."""
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": key},
        ExpiresIn=expires_seconds,
    )