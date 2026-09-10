"""Private, bounded attachment storage. Uploaded files are not executable content."""

import hashlib
import re
from pathlib import Path

import boto3
from fastapi import HTTPException
from ordertowork.config import get_settings


def validate_upload(filename: str, content_type: str, content: bytes) -> str:
    allowed = {
        "image/png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": lambda b: b.startswith(b"\xff\xd8\xff"),
        "application/pdf": lambda b: b.startswith(b"%PDF-"),
    }
    if not content or len(content) > get_settings().max_upload_bytes:
        raise HTTPException(
            413,
            detail={"code": "file_size", "message": "File is empty or exceeds the upload limit."},
        )
    if content_type not in allowed or not allowed[content_type](content):
        raise HTTPException(
            415,
            detail={
                "code": "file_type",
                "message": "Use a PNG, JPEG or PDF file with matching content.",
            },
        )
    name = re.sub(r"[\x00-\x1f\x7f]", "", Path(filename.replace("\\", "/")).name)[:255]
    return name or "attachment"


def local_path(key: str) -> Path:
    root = (get_settings().data_dir / "files").resolve()
    candidate = (root / key).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Invalid storage key")
    return candidate


def put_file(key: str, content: bytes, content_type: str) -> str:
    settings = get_settings()
    if settings.storage_mode == "s3":
        boto3.client("s3", region_name=settings.aws_region).put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            ServerSideEncryption="AES256",
            ContentDisposition="attachment",
        )
    else:
        path = local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Random new keys plus exclusive creation prevent replacement of approved bytes.
        with path.open("xb") as handle:
            handle.write(content)
    return hashlib.sha256(content).hexdigest()


def remove_file(key: str):
    settings = get_settings()
    if settings.storage_mode == "s3":
        boto3.client("s3", region_name=settings.aws_region).delete_object(
            Bucket=settings.s3_bucket, Key=key
        )
    else:
        local_path(key).unlink(missing_ok=True)


def download_url(key: str) -> str:
    settings = get_settings()
    return boto3.client("s3", region_name=settings.aws_region).generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.s3_bucket,
            "Key": key,
            "ResponseContentDisposition": "attachment",
        },
        ExpiresIn=60,
    )
