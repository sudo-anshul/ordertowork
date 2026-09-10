from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from ordertowork.config import get_settings
from ordertowork.db import get_db, new_id
from ordertowork.models.jobs import Attachment
from ordertowork.services.auth import Actor, get_actor, require_business_actor, require_membership
from ordertowork.services.files import (
    download_url,
    local_path,
    put_file,
    remove_file,
    validate_upload,
)
from ordertowork.services.orders import get_order
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/orders/{order_id}/files", tags=["private files"]
)


def metadata(item: Attachment) -> dict:
    return {
        name: getattr(item, name)
        for name in ("id", "filename", "content_type", "size_bytes", "sha256", "created_at")
    }


@router.get("")
def list_files(
    workspace_id: str,
    order_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    require_membership(db, actor, workspace_id, roles=("owner",))
    get_order(db, workspace_id, order_id)
    rows = db.scalars(
        select(Attachment)
        .where(Attachment.workspace_id == workspace_id, Attachment.order_id == order_id)
        .order_by(Attachment.created_at)
    )
    return {"files": [metadata(row) for row in rows]}


@router.post("", status_code=201)
def upload(
    workspace_id: str,
    order_id: str,
    file: UploadFile,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    require_business_actor(actor)
    require_membership(db, actor, workspace_id, roles=("owner",))
    get_order(db, workspace_id, order_id)
    content = file.file.read(get_settings().max_upload_bytes + 1)
    content_type = file.content_type or "application/octet-stream"
    name = validate_upload(file.filename or "attachment", content_type, content)
    identifier = new_id()
    key = f"attachments/{workspace_id}/{order_id}/{identifier}"
    digest = put_file(key, content, content_type)
    record = Attachment(
        id=identifier,
        workspace_id=workspace_id,
        order_id=order_id,
        filename=name,
        content_type=content_type,
        size_bytes=len(content),
        sha256=digest,
        storage_key=key,
    )
    db.add(record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        remove_file(key)
        raise
    return metadata(record)


@router.get("/{attachment_id}/download")
def download(
    workspace_id: str,
    order_id: str,
    attachment_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    require_membership(db, actor, workspace_id, roles=("owner",))
    get_order(db, workspace_id, order_id)
    record = db.scalar(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.workspace_id == workspace_id,
            Attachment.order_id == order_id,
        )
    )
    if not record:
        raise HTTPException(404, detail={"code": "not_found", "message": "File not found."})
    if get_settings().storage_mode == "s3":
        return RedirectResponse(download_url(record.storage_key), status_code=303)
    path = local_path(record.storage_key)
    if not path.is_file():
        raise HTTPException(404, detail={"code": "not_found", "message": "File is unavailable."})
    return FileResponse(path, filename=record.filename, media_type="application/octet-stream")
