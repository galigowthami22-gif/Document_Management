import logging
import math
import os
import uuid
from pathlib import Path
from typing import Annotated
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from core.database import get_db
from core.cache import get_cached_json, invalidate_document_lists, set_cached_json
from core.config import settings
from dependencies.auth_dependency import (
    get_current_user,
    has_role,
    require_admin,
    require_upload_role,
)
from models.document_model import Document, DocumentShare
from models.user_model import User, UserRole
from schemas.document_schema import DocumentResponse, DocumentListResponse
from services.permission_service import (
    get_accessible_document,
    require_download_permission,
)
from services.upload_notification import notify_document_uploaded

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])

ALLOWED_TYPES = {
    ".pdf": {"application/pdf"},
    ".txt": {"text/plain"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"}}
CHUNK_SIZE = 1024 * 1024

def validate_file_header(extension: str, header: bytes) -> bool:
    """Perform basic file-signature validation."""
    if extension == ".pdf":
        return header.startswith(b"%PDF-")
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".docx":
        return header.startswith(b"PK\x03\x04")
    if extension == ".txt":
        try:
            header.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    return False

async def save_upload(upload: UploadFile) -> dict:
    original_name = Path(upload.filename or "").name
    if (
        not original_name
        or original_name in {".", ".."}
        or len(original_name) > 255
        or "\x00" in original_name):
        raise HTTPException(status_code=400, detail="Invalid filename")
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="File extension is not allowed")
    if upload.content_type not in ALLOWED_TYPES[extension]:
        raise HTTPException(status_code=415, detail="File content type does not match the extension")
    storage_dir = Path(settings.UPLOAD_DIR).resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    final_path = storage_dir / stored_name
    temp_path = storage_dir / f".{uuid.uuid4().hex}.tmp"
    size = 0
    header = b""
    try:
        with temp_path.open("xb") as output:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="File exceeds the 10 MB limit")
                if len(header) < 16:
                    header += chunk[:16 - len(header)]
                output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Empty files are not allowed")
        if not validate_file_header(extension, header):
            raise HTTPException(status_code=415, detail="File content does not match the supported format")
        os.replace(temp_path, final_path)
        return {
            "original_filename": original_name,
            "stored_filename": stored_name,
            "content_type": upload.content_type,
            "file_size": size,
            "storage_path": str(final_path)}
    except Exception:
        temp_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

def remove_stored_files(paths: list[str]) -> None:
    """Best-effort cleanup after a database failure."""
    for raw_path in paths:
        try:
            path = Path(raw_path).resolve()
            root = Path(settings.UPLOAD_DIR).resolve()

            if path.parent == root:
                path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to clean up uploaded file")

def owned_document(db: Session, document_id: int, user_id: int) -> Document:
    document = db.scalar(select(Document).where(Document.id == document_id, Document.owner_id == user_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document

@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    description=(
        "Upload one PDF, TXT, PNG, JPG/JPEG, or DOCX file as "
        "multipart/form-data in the `file` field."
    ),
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: Annotated[
        UploadFile,
        File(
            description=(
                "Select a PDF, TXT, PNG, JPG/JPEG, or DOCX file to upload."
            )
        ),
    ],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_upload_role)]):
    saved = await save_upload(file)
    document = Document(**saved, owner_id=current_user.id)

    try:
        db.add(document)
        db.commit()
        db.refresh(document)
        invalidate_document_lists()
        background_tasks.add_task(
            notify_document_uploaded,
            document.id,
            current_user.id,
            document.original_filename,
        )
        return document
    except Exception:
        db.rollback()
        remove_stored_files([saved["storage_path"]])
        raise HTTPException(status_code=500, detail="Unable to save document metadata")

@router.post("/upload-multiple", response_model=list[DocumentResponse], status_code=201)
async def upload_multiple_documents(
    background_tasks: BackgroundTasks,
    files: Annotated[
        list[UploadFile],
        File(json_schema_extra={"items": {"type": "string", "format": "binary"}}),
    ],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_upload_role)],
):
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    if len(files) > settings.MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=413, detail="A maximum of 5 files is allowed per request")
    saved_files = []
    documents = []

    try:
        for upload in files:
            saved = await save_upload(upload)
            saved_files.append(saved)
            documents.append(Document(**saved, owner_id=current_user.id))
        db.add_all(documents)
        db.commit()
        for document in documents:
            db.refresh(document)
            background_tasks.add_task(
                notify_document_uploaded,
                document.id,
                current_user.id,
                document.original_filename,
            )
        invalidate_document_lists()
        return documents

    except Exception as exc:
        db.rollback()
        remove_stored_files([item["storage_path"] for item in saved_files])
        if isinstance(exc, HTTPException):
            raise
        logger.exception("Multiple document upload failed")
        raise HTTPException(status_code=500, detail="Unable to save uploaded documents")
    finally:
        for upload in files:
            if not upload.file.closed:
                await upload.close()

@router.get("", response_model=DocumentListResponse)
def list_documents(db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    filename: str | None = None, content_type: str | None = None,
    sort_by: Annotated[str,
        Query(pattern="^(created_at|original_filename|file_size)$")] = "created_at", sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc"):
    cache_key = (
        f"documents:list:{current_user.id}:{has_role(current_user, UserRole.ADMIN)}:"
        f"{page}:{page_size}:{filename or ''}:{content_type or ''}:{sort_by}:{sort_order}"
    )
    cached = get_cached_json(cache_key)
    if cached is not None:
        return cached
    if has_role(current_user, UserRole.ADMIN):
        conditions = []
    else:
        visible_shares = select(DocumentShare.document_id).where(
            DocumentShare.shared_with_user_id == current_user.id,
            DocumentShare.can_view.is_(True),
        )
        conditions = [
            or_(
                Document.owner_id == current_user.id,
                Document.id.in_(visible_shares),
            )
        ]
    if filename:
        escaped = (
            filename
            .replace("!", "!!")
            .replace("%", "!%")
            .replace("_", "!_"))
        conditions.append(
            Document.original_filename.ilike(
                f"%{escaped}%",
                escape="!"))
    if content_type:
        conditions.append(Document.content_type == content_type)
    total = db.scalar(select(func.count()).select_from(Document).where(*conditions)) or 0
    sort_column = getattr(Document, sort_by)
    ordering = (
        sort_column.asc()
        if sort_order == "asc"
        else sort_column.desc())
    statement = (
        select(Document)
        .where(*conditions)
        .order_by(ordering, Document.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size))
    items = list(db.scalars(statement).all())
    response = DocumentListResponse(
        items=items, total=total,
        page=page, page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0)
    set_cached_json(
        cache_key,
        response.model_dump(mode="json"),
        settings.CACHE_TTL_SECONDS,
    )
    return response

@router.get("/admin-only")
def admin_endpoint(current_user: Annotated[User, Depends(require_admin)]):
    return {"message": "Admin access granted"}

@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: int, db: Annotated[Session, Depends(get_db)], current_user: Annotated[User, Depends(get_current_user)]):
    document, _ = get_accessible_document(db, document_id, current_user)
    return document

@router.get("/{document_id}/download")
def download_document(document_id: int, db: Annotated[Session, Depends(get_db)], current_user: Annotated[User, Depends(get_current_user)]):
    document, share = get_accessible_document(db, document_id, current_user)
    require_download_permission(current_user, share)
    storage_dir = Path(settings.UPLOAD_DIR).resolve()
    path = Path(document.storage_path).resolve()
    if path.parent != storage_dir or not path.is_file():
        raise HTTPException(status_code=404, detail="Stored file not found")
    return FileResponse(
        path=path,
        media_type="application/octet-stream",
        filename=Path(document.original_filename).name,
        content_disposition_type="attachment")

@router.delete(
    "/{document_id}",
    status_code=status.HTTP_200_OK,
    response_model=dict[str, str],
)
def delete_document(
    document_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]):
    document = db.get(Document, document_id)
    if document is None or (
        document.owner_id != current_user.id
        and not has_role(current_user, UserRole.ADMIN)
    ):
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(document.storage_path).resolve()
    storage_dir = Path(settings.UPLOAD_DIR).resolve()

    if path.parent != storage_dir:
        raise HTTPException(status_code=500, detail="Invalid stored file path")
    db.delete(document)
    db.commit()
    invalidate_document_lists()

    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Document deleted from database but file cleanup failed")
    return {"message": "Document deleted successfully"}
