from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from openpyxl import load_workbook


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".xlsx"}
MIME_BY_EXTENSION = {
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pdf': 'application/pdf',
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
DECLARED_MIME_BY_EXTENSION = {
    '.docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/octet-stream'},
    '.pdf': {'application/pdf', 'application/octet-stream'},
    ".jpg": {"image/jpeg", "image/jpg"},
    ".jpeg": {"image/jpeg", "image/jpg"},
    ".png": {"image/png"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    },
}


class DocumentValidationError(ValueError):
    pass


def _validate_signature(extension: str, content: bytes) -> None:
    if extension in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise DocumentValidationError("JPG 文件头校验失败")
    if extension == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise DocumentValidationError("PNG 文件头校验失败")
    if extension == ".xlsx" and not content.startswith(b"PK"):
        raise DocumentValidationError("XLSX 文件头校验失败")


def inspect_and_store_document(
    *,
    batch_id: str,
    file_name: str,
    content: bytes,
    upload_root: Path,
    max_file_bytes: int,
    declared_mime: str | None = None,
    document_role: str = "MATERIAL",
    meeting_attachment: bool = False,
) -> dict[str, object]:
    safe_name = Path(file_name).name
    extension = Path(safe_name).suffix.lower()
    allowed = ALLOWED_EXTENSIONS | {'.docx','.pdf'} if meeting_attachment else ALLOWED_EXTENSIONS
    if extension not in allowed:
        raise DocumentValidationError("仅支持 JPG、PNG 和 XLSX")
    normalized_role = document_role.strip().upper()
    if normalized_role not in {"MATERIAL", "EVIDENCE"}:
        raise DocumentValidationError("文件用途无效")
    if normalized_role == "EVIDENCE" and not meeting_attachment and extension not in {".jpg", ".jpeg", ".png"}:
        raise DocumentValidationError("证明附件仅支持 JPG、PNG")
    normalized_mime = (declared_mime or "").split(";", 1)[0].strip().lower()
    if normalized_mime and normalized_mime not in DECLARED_MIME_BY_EXTENSION[extension]:
        raise DocumentValidationError("文件扩展名与 MIME 类型不一致")
    if not content:
        raise DocumentValidationError("文件不能为空")
    if len(content) > max_file_bytes:
        raise DocumentValidationError("单文件不能超过 10MB")
    _validate_signature(extension, content)
    if extension == '.pdf' and (not content.startswith(b'%PDF-') or b'%%EOF' not in content[-4096:]):
        raise DocumentValidationError('PDF 文件格式不正确')
    if extension == '.docx':
        from zipfile import ZipFile
        from xml.etree import ElementTree as ET
        try:
            with ZipFile(BytesIO(content)) as archive:
                if archive.getinfo('word/document.xml').file_size > 20 * 1024 * 1024:
                    raise ValueError('文档过大')
                root = ET.fromstring(archive.read('word/document.xml'))
                if root.tag != '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document':
                    raise ValueError('文档格式错误')
                if '[Content_Types].xml' not in archive.namelist():
                    raise ValueError('缺少文档类型')
        except Exception as exc:
            raise DocumentValidationError('Word 文件损坏或不是 DOCX 格式') from exc

    pages = 1
    if extension == ".xlsx":
        try:
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=False)
            pages = len(workbook.sheetnames)
            workbook.close()
        except Exception as exc:
            raise DocumentValidationError("XLSX 文件损坏或格式不正确") from exc

    digest = sha256(content).hexdigest()
    document_id = str(uuid4())
    batch_dir = (upload_root / batch_id).resolve()
    root = upload_root.resolve()
    if root not in batch_dir.parents:
        raise DocumentValidationError("上传目录无效")
    batch_dir.mkdir(parents=True, exist_ok=True)
    storage_path = batch_dir / f"{document_id}{extension}"
    storage_path.write_bytes(content)
    return {
        "file_name": safe_name,
        "mime": MIME_BY_EXTENSION[extension],
        "size_bytes": len(content),
        "sha256": digest,
        "storage_path": str(storage_path),
        "pages": pages,
        "document_role": normalized_role,
    }
