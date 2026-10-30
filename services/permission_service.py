from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from models.document_model import Document, DocumentShare
from dependencies.auth_dependency import has_role
from models.user_model import User, UserRole

def get_accessible_document(db: Session, document_id: int, user: User) -> tuple[Document, DocumentShare | None]:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if has_role(user, UserRole.ADMIN):
        return document, None
    if document.owner_id == user.id:
        return document, None
    share = db.scalar(select(DocumentShare).where(DocumentShare.document_id == document_id, DocumentShare.shared_with_user_id == user.id, DocumentShare.can_view.is_(True)))
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document, share

def require_download_permission(user: User, share: DocumentShare | None) -> None:
    if has_role(user, UserRole.ADMIN):
        return
    if share is None:
        return
    if not share.can_download:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Download permission has not been granted")