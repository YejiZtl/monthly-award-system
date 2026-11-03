from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import Settings
from app.db.repository import Repository
from app.domain.state_machine import BatchStatus
from app.integrations import OCRExtractionClient, OCRRecognitionUnavailable
from app.services.extraction_service import extract_document


class ExtractionProcessor:
    def __init__(self, repository: Repository, config: Settings):
        self.repository = repository
        self.config = config
        self.ocr = OCRExtractionClient(config)

    def process_job(self, job_id: str | None = None) -> dict[str, Any] | None:
        job = self.repository.claim_extraction_job(job_id)
        if not job:
            return None
        batch_id = job["batch_id"]
        try:
            batch = self.repository.get_batch(batch_id)
            if not batch:
                raise KeyError("BATCH_NOT_FOUND")
            documents = [
                document for document in self.repository.list_documents(batch_id)
                if document.get("document_role", "MATERIAL") == "MATERIAL"
            ]
            if not documents:
                raise ValueError("NO_DOCUMENTS")
            self.repository.update_extraction_job(job["id"], progress=15)
            extracted: dict[str, dict[str, Any]] = {}
            manual_required = False
            recognition_messages: list[str] = []
            for index, document in enumerate(documents, start=1):
                path = Path(document["storage_path"])
                if path.suffix.lower() == ".xlsx":
                    fields = extract_document(document, batch["form_type"])
                else:
                    try:
                        recognition = self.ocr.extract_image(path, document["id"], batch["form_type"])
                        fields = recognition.fields
                        manual_required = manual_required or recognition.manual_required
                        recognition_messages.extend(recognition.warnings)
                    except OCRRecognitionUnavailable as exc:
                        fields = extract_document(document, batch["form_type"])
                        manual_required = True
                        recognition_messages.append(f"{document['file_name']}：{exc.message}（{exc.code}）")
                for field in fields:
                    current = extracted.get(field["path"])
                    if not current or float(field.get("confidence", 0)) > float(current.get("confidence", 0)):
                        extracted[field["path"]] = field
                progress = 15 + int(index / len(documents) * 65)
                self.repository.update_extraction_job(job["id"], progress=progress)

            current_batch = self.repository.get_batch(batch_id) or batch
            revision = self.repository.replace_fields(batch_id, list(extracted.values()), expected_revision=int(current_batch["revision"]))
            self.repository.set_batch_status(batch_id, BatchStatus.REVIEW_REQUIRED, strict=False)
            final_batch = self.repository.get_batch(batch_id) or {}
            final_status = "MANUAL_REQUIRED" if manual_required else "SUCCEEDED"
            recognition_message = "；".join(dict.fromkeys(recognition_messages))[:500] or None
            result = self.repository.update_extraction_job(
                job["id"], status=final_status, progress=100,
                error_code="OCR_MANUAL_INPUT" if manual_required else None,
                error_message=recognition_message if manual_required else None,
                result_revision=int(final_batch.get("revision", revision)),
            )
            self.repository.append_audit(
                actor="system", action="EXTRACTION_COMPLETED", object_type="extraction_job",
                object_id=job["id"], batch_id=batch_id,
                after={
                    "status": final_status,
                    "field_count": len(extracted),
                    "recognition_messages": recognition_messages,
                },
            )
            return result
        except Exception as exc:
            self.repository.set_batch_status(batch_id, BatchStatus.FAILED, strict=False)
            return self.repository.update_extraction_job(
                job["id"], status="FAILED", progress=100,
                error_code=type(exc).__name__, error_message=str(exc)[:500],
            )
