"""Exercise S3's request and signing contracts without AWS or real credentials."""

import hashlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest
from botocore.stub import Stubber
from fastapi import HTTPException
from ordertowork.services import files as file_service


@pytest.fixture
def s3_storage(monkeypatch):
    settings = SimpleNamespace(
        storage_mode="s3", s3_bucket="ordertowork-offline-test", aws_region="us-east-1"
    )
    monkeypatch.setattr(file_service, "get_settings", lambda: settings)
    # An explicit isolated session bypasses local profiles, refresh and metadata.
    session = boto3.Session(
        aws_access_key_id="offline-example-access-key",
        aws_secret_access_key="offline-example-secret",
        aws_session_token="offline-example-session-token",
    )
    monkeypatch.setattr(file_service.boto3, "client", session.client)
    return settings


def test_s3_upload_and_cleanup_match_private_bucket_contract(s3_storage, monkeypatch):
    client = file_service.s3_client()
    monkeypatch.setattr(file_service, "s3_client", lambda: client)
    content = b"%PDF-1.4\noffline attachment fixture"
    key = "attachments/workspace/order/random-file-id"
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {"ETag": '"fixture-etag"'},
            {
                "Bucket": s3_storage.s3_bucket,
                "Key": key,
                "Body": content,
                "ContentType": "application/pdf",
                "ServerSideEncryption": "AES256",
                "ContentDisposition": "attachment",
            },
        )
        stub.add_response("delete_object", {}, {"Bucket": s3_storage.s3_bucket, "Key": key})
        assert (
            file_service.put_file(key, content, "application/pdf")
            == hashlib.sha256(content).hexdigest()
        )
        file_service.remove_file(key)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("region", ["us-east-1", "ap-south-1"])
def test_s3_download_presigns_sigv4_with_temporary_credentials(s3_storage, region):
    s3_storage.aws_region = region
    url = file_service.download_url("attachments/workspace/order/random-file-id")
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.path.endswith("/attachments/workspace/order/random-file-id")
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["60"]
    assert query["X-Amz-Security-Token"] == ["offline-example-session-token"]
    assert f"/{region}/s3/aws4_request" in query["X-Amz-Credential"][0]
    assert query["response-content-disposition"] == ["attachment"]
    assert "Signature" not in query  # us-east-1 must not fall back to legacy SigV2.


def test_s3_unconfigured_bucket_fails_before_sdk_credentials(s3_storage, monkeypatch):
    s3_storage.s3_bucket = ""

    def unexpected_client(*args, **kwargs):
        raise AssertionError("Must not resolve credentials for unconfigured storage")

    monkeypatch.setattr(file_service.boto3, "client", unexpected_client)
    with pytest.raises(HTTPException) as error:
        file_service.put_file("attachments/fixture", b"fixture", "application/pdf")
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "storage_unavailable"
