from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from core.cache import invalidate_document_lists
from core.database import get_db
from dependencies.auth_dependency import get_current_user, has_role
from models.document_model import Document, DocumentShare
from models.user_model import User, UserRole
from schemas.document_schema import DocumentShareCreate, DocumentShareResponse

router = APIRouter(prefix="/api/v1/documents", tags=["Document Sharing"])

@router.post("/{document_id}/shares", response_model=DocumentShareResponse, status_code=status.HTTP_201_CREATED)
def share_document(document_id: int, payload: DocumentShareCreate, db: Annotated[Session, Depends(get_db)], current_user: Annotated[User, Depends(get_current_user)]):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    if (not has_role(current_user, UserRole.ADMIN)
        and document.owner_id != current_user.id):
        raise HTTPException(404, "Document not found")
    if payload.recipient_user_id == document.owner_id:
        raise HTTPException(status_code=400, detail="A document cannot be shared with its owner")
    recipient = db.get(User, payload.recipient_user_id)
    if recipient is None or not recipient.is_active:
        raise HTTPException(status_code=404, detail="Recipient not found")
    if not payload.can_view and payload.can_download:
        raise HTTPException(status_code=422, detail="Download permission requires view permission")
    existing = db.scalar(select(DocumentShare).where(DocumentShare.document_id == document_id, DocumentShare.shared_with_user_id == payload.recipient_user_id))
    if existing:
        raise HTTPException(status_code=409, detail="Document is already shared with this user")
    share = DocumentShare(
        document_id=document_id,
        shared_by_user_id=current_user.id,
        shared_with_user_id=payload.recipient_user_id,
        can_view=payload.can_view,
        can_download=payload.can_download)
    try:
        db.add(share)
        db.commit()
        db.refresh(share)
        invalidate_document_lists()
        return share
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Document is already shared with this user")

@router.get("/{document_id}/shares", response_model=list[DocumentShareResponse])
def list_document_shares(document_id: int, db: Annotated[Session, Depends(get_db)], current_user: Annotated[User, Depends(get_current_user)]):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    if (not has_role(current_user, UserRole.ADMIN)
        and document.owner_id != current_user.id):
        raise HTTPException(404, "Document not found")
    return list(db.scalars(select(DocumentShare).where(DocumentShare.document_id == document_id)).all())

@router.delete("/{document_id}/shares/{recipient_user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_document_share(document_id: int, recipient_user_id: int, db: Annotated[Session, Depends(get_db)], current_user: Annotated[User, Depends(get_current_user)]):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    if (not has_role(current_user, UserRole.ADMIN)
        and document.owner_id != current_user.id):
        raise HTTPException(404, "Document not found")
    share = db.scalar(select(DocumentShare).where(DocumentShare.document_id == document_id, DocumentShare.shared_with_user_id == recipient_user_id))
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")
    db.delete(share)
    db.commit()
    invalidate_document_lists()
    return None