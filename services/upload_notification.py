import logging

logger = logging.getLogger(__name__)


def notify_document_uploaded(
    document_id: int,
    owner_id: int,
    original_filename: str,
) -> None:
    logger.info(
        "Document upload notification: document_id=%s owner_id=%s filename=%s",
        document_id,
        owner_id,
        original_filename,
    )
