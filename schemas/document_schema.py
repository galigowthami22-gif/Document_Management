from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

class DocumentResponse(BaseModel):
    id: int
    original_filename: str
    content_type: str
    file_size: int
    owner_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

class DocumentShareCreate(BaseModel):
    recipient_user_id: int = Field(gt=0)
    can_view: bool = True
    can_download: bool = False

class DocumentShareResponse(BaseModel):
    id: int
    document_id: int
    shared_by_user_id: int
    shared_with_user_id: int
    can_view: bool
    can_download: bool

    model_config = ConfigDict(from_attributes=True)