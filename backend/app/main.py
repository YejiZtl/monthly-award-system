from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import Repository
from app.domain.state_machine import BatchStatus
from app.schemas import (
    AdminLoginRequest,
    BatchCreate,
    CalculateRequest,
    ConfirmCalculationRequest,
    ExportRequest,
    FieldPatch,
    ReturnBatchRequest,
    SystemSettingsPatch,
    RemarkPatch,
    MonthlyRemarkPatch,
    FinalAmountPatch,
    MeetingApprovalRequest,
    MeetingCellsPatch,
    SupplementalRowPatch,
    ReviewNotePatch,
    MeetingReturnRequest,
)
from app.security import (
    AuthenticationError,
    issue_admin_token,
    issue_download_token,
    verify_admin_password,
    verify_admin_token,
    verify_download_token,
    verify_reviewer_token,
)
from app.services import (
    DocumentValidationError,
    calculate_rule,
    confirmed_calculation_inputs,
    create_export_workbook,
    inspect_and_store_document,
    recalculate_project_budget_fields,
    recalculate_project_settlement_fields,
    validate_confirmed_fields,
)
from app.services.extraction_processor import ExtractionProcessor
from app.services.standard_templates import STANDARDS, FIELD_LABELS, calculate_standard, extract_standard
from app.services.summary_review import remark_key, template_rows, evidence_html
from app.services.meeting_review import meeting_rows


settings.ensure_directories()
repository = Repository(settings.database_path)
processor = ExtractionProcessor(repository, settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    settings.validate_runtime()
    repository.initialize()
    yield


app = FastAPI(
    title="员工月奖自动核算系统 API",
    version="0.7.8",
    description="业务表单上传、字段核对、初审、复审、会议终审与 Excel 导出",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:4173", "http://localhost:4173", "http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Idempotency-Key", "Authorization", "X-Reviewer-Token", "X-Submitter-Key"],
)

from app.services.submission_access import install_submission_access
install_submission_access(app, lambda: repository, lambda: settings)


def request_id() -> str:
    return str(uuid4())


def response_with_id(payload: dict[str, Any], current_request_id: str | None = None) -> dict[str, Any]:
    return {"request_id": current_request_id or request_id(), **payload}


def require_batch(batch_id: str) -> dict[str, Any]:
    batch = repository.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


def require_calculation(calculation_id: str) -> dict[str, Any]:
    calculation = repository.get_calculation(calculation_id)
    if not calculation:
        raise HTTPException(status_code=404, detail="计算记录不存在")
    return calculation


def reviewer_actor(x_reviewer_token: Annotated[str | None, Header(alias="X-Reviewer-Token")] = None) -> str:
    try:
        return verify_reviewer_token(x_reviewer_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def admin_actor(authorization: Annotated[str | None, Header()] = None) -> str:
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    try:
        return verify_admin_token(token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def public_documents(batch_id: str) -> list[dict[str, Any]]:
    items = []
    for document in repository.list_documents(batch_id):
        document.pop("storage_path", None)
        document["preview_url"] = f"/api/v1/documents/{document['id']}/content"
        document['download_url'] = document['preview_url'] + '?download=true'
        if Path(document['file_name']).suffix.lower() == '.xlsx':
            document['workbook_preview_url'] = f"/api/v1/documents/{document['id']}/preview"
        items.append(document)
    return items


def batch_remark(batch: dict[str, Any]) -> dict[str, Any]:
    saved = repository.get_summary_remark(batch["month"], remark_key(batch))
    if saved is not None:
        return saved
    if batch["form_type"] in {"STD_PROJECT", "STD_SERVICE"}:
        legacy = repository.get_summary_remark(batch["month"], "projects:all")
        if legacy is not None:
            return {"text": legacy["text"], "revision": 0}
    row = STANDARDS.get(batch["form_type"], {}).get("summary_row") or 20
    defaults = template_rows(settings.standard_template_path) if settings.standard_template_path.exists() else {}
    return {"text": defaults.get(row, {}).get("text", ""), "revision": 0}


@app.get("/api/v1/templates/{code}/download")
def download_standard_template(code: str):
    if code not in STANDARDS or not settings.standard_template_path.is_file():
        raise HTTPException(status_code=404, detail="模板不存在")
    from urllib.parse import quote
    from app.services.blank_templates import blank_template
    content = blank_template(settings.standard_template_path, code)
    name = STANDARDS[code]['sheet'] + '_空白模板.xlsx'
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename*=UTF-8''"+quote(name), "Cache-Control": "no-store"})


@app.get("/api/v1/batches/{batch_id}/evidence", response_class=HTMLResponse)
def public_evidence(batch_id: str):
    batch = require_batch(batch_id)
    body = evidence_html(batch, repository.list_fields(batch_id), repository.list_documents(batch_id), FIELD_LABELS, STANDARDS.get(batch["form_type"], {}).get("sheet", ""))
    return HTMLResponse(body, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'self'"})


@app.patch("/api/v1/batches/{batch_id}/remark")
def update_batch_remark(batch_id: str, payload: RemarkPatch, actor: str = Depends(reviewer_actor)):
    batch = require_batch(batch_id)
    try:
        value = repository.save_summary_remark(batch["month"], remark_key(batch), payload.text, payload.revision, actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"remark": value}


def monthly_remark_items(month: str):
    import re
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(status_code=422, detail="月份格式应为 YYYY-MM")
    defaults = template_rows(settings.standard_template_path)
    items = [{"key": f"fixed:{row}", **defaults[row], "revision": 0} for row in range(3,20)]
    items.append({"key": "projects:all", "label": "立项项目统一备注（本月所有1表、2表项目）", "text": defaults[20]["text"], "revision": 0})
    for calc in repository.list_confirmed_calculations(month):
        batch = repository.get_batch(calc['batch_id'])
        if batch['form_type'] in {'STD_PROJECT','STD_SERVICE'}:
            items.append({'key':f"batch:{batch['id']}",'label':batch['batch_no'],**batch_remark(batch)})
    for item in items:
        saved = repository.get_summary_remark(month, item["key"])
        if saved is not None:
            item.update(saved)
    return items


@app.get("/api/v1/months/{month}/remarks")
def monthly_remarks(month: str, _: str = Depends(reviewer_actor)):
    return {"items": monthly_remark_items(month)}


@app.patch("/api/v1/months/{month}/remarks")
def update_monthly_remark(month: str, payload: MonthlyRemarkPatch, actor: str = Depends(reviewer_actor)):
    if payload.key not in {item["key"] for item in monthly_remark_items(month)}:
        raise HTTPException(status_code=404, detail="汇总条目不存在")
    try:
        value = repository.save_summary_remark(month, payload.key, payload.text, payload.revision, actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"remark": value}


def remove_managed_file(path_value: str, root: Path) -> bool:
    """Remove a stored artifact only when it resolves inside its managed root."""
    try:
        resolved_root = root.resolve()
        resolved_path = Path(path_value).resolve()
        if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
            return False
        if resolved_path.is_file():
            resolved_path.unlink()
        return True
    except OSError:
        return False


@app.get("/health")
def health() -> dict[str, Any]:
    recognition_status = processor.ocr.public_status()
    return {
        "status": "ok",
        "version": app.version,
        "rule_version": settings.rule_version,
        "image_recognition": recognition_status["state"],
        "image_recognition_error": recognition_status["last_error_code"],
        "image_provider": "local_ocr",
        "image_model": settings.ocr_engine if settings.ocr_enabled else None,
        "prompt_version": None,
        "recognition_limits": {
            "image_max_edge": settings.ocr_image_max_edge,
            "confidence_threshold": settings.ocr_confidence_threshold,
        },
        "worker_mode": "inline" if settings.inline_worker else "separate",
        "reviewer_auth": "required" if settings.reviewer_token else "development_bypass",
        "admin_auth": "configured" if settings.admin_password else "unconfigured",
    }


@app.post('/api/v1/session/employee')
def employee_session():
    from fastapi.responses import JSONResponse
    response = JSONResponse({'mode':'employee'})
    response.delete_cookie('monthly_award_material_reader', path='/api/v1')
    return response


@app.get("/api/v1/form-types")
def form_types() -> dict[str, Any]:
    return {
        "rule_version": "RV-2026.09",
        "limits": {"max_file_mb": 10, "max_batch_files": 30, "confidence_threshold": "0.90"},
        "field_labels": FIELD_LABELS,
        "items": [{**meta, "code": code, "rule_code": code, "accepted": ["XLSX"], "template_url": f"/api/v1/templates/{code}/download"} for code, meta in STANDARDS.items()],
    }


@app.get('/api/v1/evidence-templates/download')
def download_evidence_templates():
    path = settings.standard_template_path.parent.parent / '证明材料模板.zip'
    if not path.is_file():
        raise HTTPException(status_code=404, detail='证明材料模板尚未安装')
    return FileResponse(path, media_type='application/zip', filename='证明材料模板.zip')


@app.post("/api/v1/batches", status_code=201)
def create_batch(
    payload: BatchCreate,
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    owner = request.state.submission_owner
    if not owner and not request.state.is_reviewer:
        raise HTTPException(status_code=401, detail='请允许本机浏览器保存数据后再提交')
    scope = 'create_batch:' + owner if owner else 'create_batch'
    if idempotency_key and (previous := repository.get_idempotent_response(idempotency_key, scope)):
        return previous
    batch = repository.create_batch(payload.model_dump(mode="json"))
    if owner:
        repository.bind_batch_owner(batch['id'], owner)
    current_request_id = request_id()
    repository.append_audit(actor="anonymous", action="BATCH_CREATED", object_type="batch", object_id=batch["id"], batch_id=batch["id"], after=batch, request_id=current_request_id)
    response = response_with_id({"batch": batch}, current_request_id)
    if idempotency_key:
        repository.save_idempotent_response(idempotency_key, scope, response)
    return response


@app.get("/api/v1/batches")
def list_batches(request: Request, month: str | None = None) -> dict[str, Any]:
    items = [item for item in repository.list_batches(month) if not item.get('excluded')]
    if not request.state.is_reviewer:
        owner = request.state.submission_owner
        if not owner:
            raise HTTPException(status_code=401, detail='请在原申报电脑的浏览器打开，或验证审核身份')
        items = [item for item in items if owner and repository.owns_batch(item['id'], owner)]
    for batch in items:
        batch['return_notice'] = repository.employee_return_notice(batch)
        batch['project_name'] = next((f['value_normalized'] for f in repository.list_fields(batch['id']) if f['path'] == 'project.name'), '')
        calculation = repository.get_latest_calculation_for_batch(batch["id"])
        batch["calculation"] = (
            {
                "id": calculation["id"],
                "status": calculation["status"],
                "rounded_total": calculation["rounded_total"],
                "confirmed_total": calculation["confirmed_total"],
                "created_at": calculation["created_at"],
            }
            if calculation
            else None
        )
    return {"items": items}


@app.get("/api/v1/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    batch = require_batch(batch_id)
    batch['project_name'] = next((f['value_normalized'] for f in repository.list_fields(batch_id) if f['path'] == 'project.name'), '')
    return {"batch": batch, "documents": public_documents(batch_id)}


@app.get("/api/v1/batches/{batch_id}/review")
def get_batch_review(batch_id: str, _: str = Depends(reviewer_actor)) -> dict[str, Any]:
    """Return the real source material and frozen calculation used during review."""
    return {
        "batch": require_batch(batch_id),
        "documents": public_documents(batch_id),
        "fields": repository.list_fields(batch_id),
        "calculation": repository.get_latest_calculation_for_batch(batch_id),
        "audit": repository.list_audit(batch_id),
        "remark": batch_remark(require_batch(batch_id)),
        "initial_review": repository.get_initial_review(batch_id),
        "review_notes": repository.review_notes(batch_id),
    }


@app.patch('/api/v1/batches/{batch_id}/review-note')
def save_review_note(batch_id: str, payload: ReviewNotePatch, actor: str = Depends(reviewer_actor)):
    require_batch(batch_id)
    try:
        return {'note':repository.save_review_note(batch_id,payload.stage,payload.text,payload.revision,payload.calculation_id,actor)}
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/api/v1/batches/{batch_id}/documents", status_code=201)
def upload_documents(
    batch_id: str,
    files: Annotated[list[UploadFile] | None, File()] = None,
    evidence_files: Annotated[list[UploadFile] | None, File()] = None,
) -> dict[str, Any]:
    batch = require_batch(batch_id)
    if batch["status"] not in {BatchStatus.DRAFT, BatchStatus.UPLOADED, BatchStatus.WAITING_FOR_EVIDENCE, BatchStatus.FAILED}:
        raise HTTPException(status_code=409, detail="当前批次状态不允许继续上传")
    material_uploads = files or []
    evidence_uploads = evidence_files or []
    uploads = [(upload, "MATERIAL") for upload in material_uploads] + [
        (upload, "EVIDENCE") for upload in evidence_uploads
    ]
    if not uploads:
        raise HTTPException(status_code=422, detail="请选择申报材料或证明附件")
    existing_materials = [
        item for item in repository.list_documents(batch_id)
        if item.get("document_role", "MATERIAL") == "MATERIAL"
    ]
    if not material_uploads and not existing_materials:
        raise HTTPException(status_code=422, detail="至少上传一份 Excel 文件或 Excel 截图")
    if batch["form_type"] in STANDARDS:
        if len(material_uploads) > 1:
            raise HTTPException(status_code=422, detail="每次标准申报须且只能有一份主表；其他材料请作为证明附件提交")
        if any(Path(upload.filename or "").suffix.lower() != ".xlsx" for upload in material_uploads):
            raise HTTPException(status_code=422, detail="标准模板请上传 XLSX，截图请放入证明附件")
    if len(uploads) > settings.max_batch_files:
        raise HTTPException(status_code=422, detail="单批次最多上传30个文件")
    staged: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    try:
        for upload, document_role in uploads:
            content = upload.file.read(settings.max_file_bytes + 1)
            try:
                document_data = inspect_and_store_document(
                    batch_id=batch_id,
                    file_name=upload.filename or "unnamed",
                    content=content,
                    upload_root=settings.upload_root,
                    max_file_bytes=settings.max_file_bytes,
                    declared_mime=upload.content_type,
                    document_role=document_role,
                )
            except DocumentValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            digest = str(document_data["sha256"])
            if digest in seen_hashes:
                Path(str(document_data['storage_path'])).unlink(missing_ok=True)
                raise HTTPException(status_code=409, detail=f'同次上传中有重复文件：{upload.filename}')
            seen_hashes.add(digest)
            duplicate = repository.find_document_by_hash(batch_id, digest)
            if duplicate:
                Path(str(document_data["storage_path"])).unlink(missing_ok=True)
                if duplicate['document_role'] != document_role:
                    raise HTTPException(status_code=409, detail='同一文件不能同时作为主表和证明')
                continue
            staged.append(document_data)
            if document_role == "MATERIAL" and batch["form_type"] in STANDARDS:
                try:
                    extract_standard({**document_data, "id": "upload-check"}, batch["form_type"])
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            saved = repository.add_documents(batch_id, staged)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        for document in staged:
            Path(str(document["storage_path"])).unlink(missing_ok=True)
        raise
    saved_paths = {item['storage_path'] for item in saved}
    for item in staged:
        if item['storage_path'] not in saved_paths:
            remove_managed_file(str(item['storage_path']), settings.upload_root)
    current_request_id = request_id()
    repository.append_audit(
        actor="anonymous", action="DOCUMENTS_UPLOADED", object_type="batch", object_id=batch_id,
        batch_id=batch_id,
        after={"materials": len(material_uploads), "evidence": len(evidence_uploads)},
        request_id=current_request_id,
    )
    return response_with_id({"documents": public_documents(batch_id)}, current_request_id)


@app.post('/api/v1/templates/detect')
def detect_template(file: Annotated[UploadFile, File()]):
    from io import BytesIO
    from app.services.standard_templates import detect_standard_template
    if Path(file.filename or '').suffix.lower() != '.xlsx':
        raise HTTPException(status_code=422, detail='主表请选择 XLSX 文件')
    content = file.file.read(settings.max_file_bytes + 1)
    if len(content) > settings.max_file_bytes:
        raise HTTPException(status_code=422, detail='单文件不能超过10MB')
    try:
        code = detect_standard_template(BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else 'XLSX 文件损坏或格式不正确') from exc
    return {key: STANDARDS[code][key] for key in ('code', 'category', 'label')}


@app.post('/api/v1/batches/{batch_id}/withdraw')
def withdraw_batch(batch_id: str):
    before = require_batch(batch_id)
    try:
        repository.reopen_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    batch = require_batch(batch_id)
    repository.append_audit(actor='anonymous', action='BATCH_WITHDRAWN', object_type='batch', object_id=batch_id, batch_id=batch_id, before=before, after=batch)
    return response_with_id({'batch': batch})


@app.post('/api/v1/batches/{batch_id}/reset-upload')
def reset_upload(batch_id: str):
    before = require_batch(batch_id)
    try:
        paths = repository.reopen_batch(batch_id, reset_upload=True)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    for path in paths:
        remove_managed_file(path, settings.upload_root)
    batch = require_batch(batch_id)
    repository.append_audit(actor='anonymous', action='UPLOAD_RESET', object_type='batch', object_id=batch_id, batch_id=batch_id, before=before, after=batch)
    return response_with_id({'batch': batch})


@app.post('/api/v1/batches/{batch_id}/revisions', status_code=201)
def create_batch_revision(batch_id: str):
    require_batch(batch_id)
    try:
        batch = repository.create_revision(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.append_audit(actor='anonymous', action='REVISION_OPENED', object_type='batch', object_id=batch['id'], batch_id=batch['id'], after={'supersedes_batch_id':batch_id})
    return response_with_id({'batch': batch})


@app.get('/api/v1/documents/{document_id}/preview', response_class=HTMLResponse)
def preview_workbook(document_id: str):
    from app.services.workbook_preview import render_document_preview
    with repository.connection() as connection:
        row = connection.execute('SELECT * FROM source_documents WHERE id=?', (document_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='原件不存在')
    document = dict(row)
    if not Path(document['storage_path']).is_file():
        raise HTTPException(status_code=410, detail='原件文件已丢失')
    body = render_document_preview(document, repository.list_documents(document['batch_id']))
    return HTMLResponse(body, headers={'Cache-Control':'no-store', 'X-Robots-Tag':'noindex, nofollow', 'Content-Security-Policy':"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"})


@app.get("/api/v1/documents/{document_id}/content")
def document_content(
    document_id: str,
    download: Annotated[bool, Query()] = False,
) -> FileResponse:
    with repository.connection() as connection:
        row = connection.execute("SELECT * FROM source_documents WHERE id=?", (document_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="原件不存在")
    document = dict(row)
    path = Path(document["storage_path"])
    if not path.exists():
        raise HTTPException(status_code=410, detail="原件文件已丢失")
    return FileResponse(
        path,
        media_type=document["mime"],
        filename=document["file_name"],
        content_disposition_type="attachment" if download else "inline",
    )


@app.post("/api/v1/batches/{batch_id}/extract", status_code=202)
def extract_batch(
    batch_id: str,
    background_tasks: BackgroundTasks,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    scope = f"extract:{batch_id}"
    if idempotency_key and (previous := repository.get_idempotent_response(idempotency_key, scope)):
        return previous
    batch = require_batch(batch_id)
    documents = [
        document for document in repository.list_documents(batch_id)
        if document.get("document_role", "MATERIAL") == "MATERIAL"
    ]
    if not documents:
        raise HTTPException(status_code=422, detail="请先上传 Excel 文件或 Excel 截图")
    if batch["status"] == BatchStatus.EXTRACTING:
        raise HTTPException(status_code=409, detail="识别任务正在执行")
    if batch["status"] not in {BatchStatus.UPLOADED, BatchStatus.REVIEW_REQUIRED, BatchStatus.FAILED}:
        raise HTTPException(status_code=409, detail="当前批次状态不允许启动识别")
    repository.set_batch_status(batch_id, BatchStatus.EXTRACTING, strict=False)
    has_image = any(Path(document["storage_path"]).suffix.lower() in {".jpg", ".jpeg", ".png"} for document in documents)
    provider = "LOCAL_OCR" if has_image and processor.ocr.enabled else ("MANUAL_FALLBACK" if has_image else "LOCAL_XLSX")
    job = repository.create_extraction_job(batch_id, provider, settings.ocr_engine if has_image else "openpyxl", "OCR-LOCAL")
    if settings.inline_worker:
        background_tasks.add_task(processor.process_job, job["id"])
    response = response_with_id({"job": job})
    if idempotency_key:
        repository.save_idempotent_response(idempotency_key, scope, response)
    return response


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = repository.get_extraction_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="识别任务不存在")
    payload: dict[str, Any] = {"job": job}
    if job["status"] in {"SUCCEEDED", "MANUAL_REQUIRED"}:
        batch = require_batch(job["batch_id"])
        payload.update({"revision": batch["revision"], "fields": repository.list_fields(job["batch_id"])})
    return payload


@app.get("/api/v1/batches/{batch_id}/fields")
def get_fields(batch_id: str) -> dict[str, Any]:
    batch = require_batch(batch_id)
    return {"revision": batch["revision"], "fields": repository.list_fields(batch_id)}


@app.patch("/api/v1/batches/{batch_id}/fields")
def patch_fields(batch_id: str, payload: FieldPatch) -> dict[str, Any]:
    batch = require_batch(batch_id)
    if batch["status"] != BatchStatus.REVIEW_REQUIRED:
        raise HTTPException(status_code=409, detail="只有待字段核对的批次可以修改字段")
    before = repository.list_fields(batch_id)
    if batch['form_type'] in STANDARDS and {f['path'] for f in before} != {f.path for f in payload.fields}:
        raise HTTPException(status_code=422, detail='字段结构与上传表格不一致，请重新识别后核对')
    try:
        submitted_fields = [item.model_dump(mode="json") for item in payload.fields]
        if batch["form_type"] == "PROJECT_BUDGET":
            submitted_fields = recalculate_project_budget_fields(submitted_fields)
        elif batch["form_type"] == "PROJECT_SETTLEMENT":
            submitted_fields = recalculate_project_settlement_fields(submitted_fields)
        revision = repository.replace_fields(batch_id, submitted_fields, payload.revision)
    except RuntimeError as exc:
        if str(exc) == "REVISION_CONFLICT":
            raise HTTPException(status_code=409, detail="字段已被其他操作更新，请刷新后重试") from exc
        raise
    after = repository.list_fields(batch_id)
    current_request_id = request_id()
    repository.append_audit(actor="anonymous", action="FIELDS_CONFIRMED", object_type="batch", object_id=batch_id, batch_id=batch_id, before=before, after=after, request_id=current_request_id)
    return response_with_id({"revision": revision, "fields": after}, current_request_id)


@app.post("/api/v1/batches/{batch_id}/validate")
def validate_batch(batch_id: str) -> dict[str, Any]:
    batch = require_batch(batch_id)
    if batch["status"] != BatchStatus.REVIEW_REQUIRED:
        raise HTTPException(status_code=409, detail="当前批次状态不允许执行字段校验")
    threshold = repository.get_setting("confidence_threshold", "0.90")
    validation = validate_confirmed_fields(repository.list_fields(batch_id), batch["form_type"], threshold=__import__("decimal").Decimal(str(threshold)))
    target = BatchStatus.REVIEW_REQUIRED if validation["errors"] else BatchStatus.READY_TO_CALCULATE
    if batch["status"] != target:
        repository.set_batch_status(batch_id, target, strict=False)
    return response_with_id({"valid": not validation["errors"], **validation, "status": target})


@app.post("/api/v1/batches/{batch_id}/calculate")
def calculate_batch(batch_id: str, payload: CalculateRequest) -> dict[str, Any]:
    batch = require_batch(batch_id)
    if batch["status"] != BatchStatus.READY_TO_CALCULATE:
        raise HTTPException(status_code=409, detail="字段或证据尚未完成核对")
    try:
        if batch["form_type"] in STANDARDS:
            if payload.rule_code != batch["form_type"]:
                raise ValueError("当前表格只能使用对应的标准核算规则")
            fields = repository.list_fields(batch_id)
            result_data = calculate_standard(batch["form_type"], fields)
            inputs = {field["path"]: field.get("value_normalized") for field in fields}
        else:
            rule_code, inputs = confirmed_calculation_inputs(
                batch["form_type"], repository.list_fields(batch_id), payload.rule_code,
            )
            result_data = calculate_rule(rule_code, inputs).to_dict()
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    calculation = repository.save_calculation(batch_id, inputs, result_data, "RV-2026.09" if batch["form_type"] in STANDARDS else settings.rule_version)
    target = BatchStatus.MANUAL_APPROVAL_REQUIRED if result_data["status"] == "MANUAL_APPROVAL_REQUIRED" else BatchStatus.CALCULATED
    repository.set_batch_status(batch_id, target, strict=False)
    current_request_id = request_id()
    repository.append_audit(actor="system", action="CALCULATION_CREATED", object_type="calculation", object_id=calculation["id"], batch_id=batch_id, after=calculation, request_id=current_request_id)
    return response_with_id({"calculation": calculation}, current_request_id)


@app.get("/api/v1/calculations/{calculation_id}")
def get_calculation(calculation_id: str, _: str = Depends(reviewer_actor)) -> dict[str, Any]:
    return {"calculation": require_calculation(calculation_id)}


@app.post("/api/v1/calculations/{calculation_id}/initial-review")
def initial_review(calculation_id: str, payload: ConfirmCalculationRequest, actor: str = Depends(reviewer_actor)):
    require_calculation(calculation_id)
    if payload.override_total is not None and not payload.reason:
        raise HTTPException(status_code=422, detail='调整金额必须填写原因')
    try:
        result = repository.initial_review(calculation_id, str(payload.override_total) if payload.override_total is not None else None, payload.reason, actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'initial_review':result}


@app.post("/api/v1/calculations/{calculation_id}/confirm")
def confirm_calculation(calculation_id: str, payload: ConfirmCalculationRequest, actor: str = Depends(reviewer_actor)) -> dict[str, Any]:
    before = require_calculation(calculation_id)
    if before["status"] not in {"CALCULATED", "MANUAL_APPROVAL_REQUIRED"}:
        raise HTTPException(status_code=409, detail="当前计算状态不允许确认")
    current_batch = require_batch(before["batch_id"])
    try:
        calculation = repository.confirm_calculation(
            calculation_id, actor,
            str(payload.override_total) if payload.override_total is not None else None,
            payload.reason,
            payload.replace_batch_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    current_request_id = request_id()
    repository.append_audit(
        actor=actor,
        action="CALCULATION_CONFIRMED",
        object_type="calculation",
        object_id=calculation_id,
        batch_id=calculation["batch_id"],
        before=before,
        after={"calculation": calculation, "reason": payload.reason, "replaced_batch_id": payload.replace_batch_id},
        request_id=current_request_id,
    )
    return response_with_id({"calculation": calculation}, current_request_id)


@app.post("/api/v1/batches/{batch_id}/return")
def return_batch(batch_id: str, payload: ReturnBatchRequest, actor: str = Depends(reviewer_actor)) -> dict[str, Any]:
    before = require_batch(batch_id)
    if before["status"] not in {BatchStatus.CALCULATED, BatchStatus.MANUAL_APPROVAL_REQUIRED, BatchStatus.INITIAL_REVIEWED}:
        raise HTTPException(status_code=409, detail="当前批次状态不允许退回")
    try:
        if before['status'] == BatchStatus.INITIAL_REVIEWED:
            repository.return_to_initial(batch_id)
        else:
            repository.reopen_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    batch = require_batch(batch_id)
    current_request_id = request_id()
    repository.append_audit(actor=actor, action="BATCH_RETURNED", object_type="batch", object_id=batch_id, batch_id=batch_id, before=before, after={"batch": batch, "reason": payload.reason}, request_id=current_request_id)
    return response_with_id({"batch": batch}, current_request_id)


@app.get("/api/v1/batches/{batch_id}/audit")
def batch_audit(batch_id: str, _: str = Depends(reviewer_actor)) -> dict[str, Any]:
    require_batch(batch_id)
    return {"items": repository.list_audit(batch_id)}


@app.get("/api/v1/months/{month}/final-review")
def final_review(month: str, _: str = Depends(reviewer_actor)):
    snapshot = repository.final_review_snapshot(month)
    # Re-read remarks between revision checks so concurrent edits cannot be silently accepted.
    snapshot['remarks'] = monthly_remark_items(month)
    snapshot['rows'] = meeting_rows(repository, settings.standard_template_path, month, snapshot, snapshot['remarks'])
    if snapshot['revision'] != repository.final_review_snapshot(month)['revision']:
        raise HTTPException(status_code=409, detail="终审内容已变化，请刷新")
    return snapshot


@app.post("/api/v1/months/{month}/approve")
def approve_meeting(month: str, payload: MeetingApprovalRequest, actor: str = Depends(reviewer_actor)):
    try:
        return repository.approve_meeting(month, payload.revision, actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/v1/months/{month}/meeting-cells")
def update_meeting_cells(month: str, payload: MeetingCellsPatch, actor: str = Depends(reviewer_actor)):
    values = {key:str(getattr(payload,key)) if getattr(payload,key) is not None else None for key in ('total','other','weight','actual')}
    try:
        return repository.save_meeting_cells(month,payload.key,values,payload.reason,payload.revision,actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete('/api/v1/months/{month}/rows')
def remove_month_row(month: str, key: str, revision: str, actor: str = Depends(reviewer_actor)):
    try:
        return repository.exclude_row(month,key,revision,actor)
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post('/api/v1/months/{month}/return-to-secondary')
def return_meeting_row(month: str, payload: MeetingReturnRequest, actor: str = Depends(reviewer_actor)):
    try:
        batch = repository.return_to_secondary(month, payload.key, payload.revision, payload.reason, actor)
        return {'batch':batch}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def save_supplement(month, row_id, values, files, revision, actor):
    import re
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):
        raise HTTPException(status_code=422,detail='结算月份格式错误')
    staged=[]
    try:
        if len(files)>settings.max_batch_files:
            raise HTTPException(status_code=422,detail='单行最多上传30个附件')
        for upload in files:
            staged.append(inspect_and_store_document(batch_id='supplemental-'+str(uuid4()),
                file_name=upload.filename or 'unnamed',content=upload.file.read(settings.max_file_bytes+1),
                upload_root=settings.upload_root,max_file_bytes=settings.max_file_bytes,
                declared_mime=upload.content_type,document_role='EVIDENCE',meeting_attachment=True))
        return repository.save_supplemental_row(month,row_id,values,staged,revision,actor)
    except Exception as exc:
        for document in staged:
            remove_managed_file(document['storage_path'],settings.upload_root)
        if isinstance(exc,DocumentValidationError):
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        if isinstance(exc,ValueError):
            raise HTTPException(status_code=409,detail=str(exc)) from exc
        raise


@app.post('/api/v1/months/{month}/supplemental-rows',status_code=201)
def create_supplemental_row(month: str, revision: Annotated[str, Form()], label: Annotated[str, Form()],
    other: Annotated[str, Form()], remark: Annotated[str, Form()]='',
    files: list[UploadFile] = File(default=[]), actor: str=Depends(reviewer_actor)):
    from pydantic import ValidationError
    try:
        payload=SupplementalRowPatch(revision=revision,label=label,other=other,remark=remark)
    except ValidationError as exc:
        raise HTTPException(status_code=422,detail='增补行名称、金额或版本无效') from exc
    return save_supplement(month,None,{'label':payload.label,'other':format(payload.other.quantize(__import__('decimal').Decimal('.01')),'f'),'remark':payload.remark},files,revision,actor)


@app.patch('/api/v1/months/{month}/supplemental-rows/{row_id}')
def update_supplemental_row(month: str,row_id: str,payload: SupplementalRowPatch,actor: str=Depends(reviewer_actor)):
    return save_supplement(month,row_id,{'label':payload.label,'other':format(payload.other.quantize(__import__('decimal').Decimal('.01')),'f'),'remark':payload.remark},[],payload.revision,actor)


@app.post('/api/v1/months/{month}/supplemental-rows/{row_id}/attachments')
def upload_supplemental_attachments(month: str,row_id: str,revision: Annotated[str,Form()],
    files: list[UploadFile]=File(default=[]),actor: str=Depends(reviewer_actor)):
    if not files:
        raise HTTPException(status_code=422,detail='请选择附件')
    return save_supplement(month,row_id,None,files,revision,actor)


@app.delete('/api/v1/months/{month}/supplemental-rows/{row_id}')
def delete_supplemental_row(month: str,row_id: str,revision: str,actor: str=Depends(reviewer_actor)):
    try:
        import json
        stored = repository.supplemental_row(month,row_id)
        result = repository.save_supplemental_row(month,row_id,None,[],revision,actor,delete=True)
        for document in json.loads(stored['attachments_json']) if stored else []:
            remove_managed_file(document['storage_path'], settings.upload_root)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get('/api/v1/months/{month}/supplemental-rows/{row_id}/attachments/{attachment_id}')
def supplemental_content(month: str,row_id: str,attachment_id: str,request: Request,share: str | None=None):
    supplemental_read_access(month,row_id,request,share)
    document=repository.supplemental_attachment(month,row_id,attachment_id)
    if document is None:
        raise HTTPException(status_code=404,detail='附件不存在')
    if not Path(document['storage_path']).is_file():
        raise HTTPException(status_code=410,detail='附件文件已丢失')
    return FileResponse(document['storage_path'],media_type=document['mime'],filename=document['file_name'],content_disposition_type='inline')


@app.get('/api/v1/months/{month}/supplemental-rows/{row_id}/evidence',response_class=HTMLResponse)
def supplemental_evidence(month: str,row_id: str,request: Request,share: str | None=None):
    supplemental_read_access(month,row_id,request,share)
    from html import escape
    row=repository.supplemental_row(month,row_id)
    if row is None:
        raise HTTPException(status_code=404,detail='增补行不存在')
    import json
    row['attachments'] = [dict(d,content_url=f"/api/v1/months/{month}/supplemental-rows/{row_id}/attachments/{d['id']}") for d in json.loads(row['attachments_json'])]
    return '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><h1>'+escape(row['label'])+'</h1><p>'+escape(row['remark'])+'</p>'+''.join('<p><a href="'+escape(d['content_url']+('?share='+share if share else ''),quote=True)+'">'+escape(d['file_name'])+'</a></p>' for d in row['attachments'])


def supplemental_read_access(month, row_id, request, share):
    if share:
        from app.security import verify_supplemental_share
        try:
            verify_supplemental_share(month,row_id,share)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401,detail=str(exc)) from exc
    else:
        reviewer_actor(request.headers.get('X-Reviewer-Token'))


@app.patch("/api/v1/calculations/{calculation_id}/final-amount")
def revise_final_amount(calculation_id: str, payload: FinalAmountPatch, actor: str = Depends(reviewer_actor)):
    require_calculation(calculation_id)
    try:
        calculation = repository.revise_final_amount(calculation_id, str(payload.total), payload.reason, payload.revision, actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'calculation': calculation}


@app.post("/api/v1/calculations/{calculation_id}/exports", status_code=201)
def create_export(
    calculation_id: str,
    payload: ExportRequest,
    request: Request,
    actor: str = Depends(reviewer_actor),
) -> dict[str, Any]:
    calculation = require_calculation(calculation_id)
    batch = require_batch(calculation["batch_id"])
    snapshot = repository.final_review_snapshot(batch['month'])
    removed_anchor = not snapshot['items'] and snapshot['export_anchor'] == calculation_id and calculation['status'] == 'EXCLUDED'
    if (calculation.get("status") != "CONFIRMED" and not removed_anchor) or batch.get("superseded_by_batch_id"):
        raise HTTPException(status_code=409, detail="只有已确认的计算结果可以正式导出")
    if not payload.final_review_revision or snapshot['revision'] != payload.final_review_revision:
        raise HTTPException(status_code=409, detail="请进入终审池核对最新汇总，终审通过后再归档")
    if not snapshot['approved']:
        raise HTTPException(status_code=409, detail="请先在终审页面确认会议结果，再导出归档")
    from app.security import supplemental_share_token
    monthly_items: list[dict[str, Any]] = []
    for confirmed_calculation in repository.list_confirmed_calculations(batch["month"]):
        confirmed_batch = require_batch(confirmed_calculation["batch_id"])
        documents = repository.list_documents(confirmed_batch["id"])
        materials = [
            document for document in documents
            if document.get("document_role", "MATERIAL") == "MATERIAL"
        ]
        primary_document = next(
            (document for document in materials if Path(str(document.get("file_name") or "")).suffix.lower() == ".xlsx"),
            materials[0] if materials else None,
        )
        download_url = ""
        if primary_document:
            base_url = str(request.base_url).rstrip("/")
            download_url = (
                f"{base_url}/api/v1/documents/{primary_document['id']}/content?download=true"
            )
        monthly_items.append({
            "calculation": confirmed_calculation,
            "batch": confirmed_batch,
            "fields": repository.list_fields(confirmed_batch["id"]),
            "documents": documents,
            "audit_events": repository.list_audit(confirmed_batch["id"]),
            "download_url": download_url,
            "evidence_url": str(request.base_url).rstrip("/") + f"/api/v1/batches/{confirmed_batch['id']}/evidence",
            "remark": batch_remark(confirmed_batch)["text"],
            "fixed_remarks": {row: repository.get_summary_remark(batch["month"], f"fixed:{row}") for row in range(3,20)},
            "meeting_cells": snapshot.get('cells',{}),
            "cell_reasons": snapshot.get('cell_reasons',{}),
            "excluded_keys": snapshot.get('excluded_keys',[]),
            "supplemental_rows": [dict(row,evidence_url=str(request.base_url).rstrip('/')+f"/api/v1/months/{batch['month']}/supplemental-rows/{row['id']}/evidence?share={supplemental_share_token(batch['month'],row['id'])}" if row['attachments'] else '') for row in snapshot.get('supplemental_rows',[])],
        })
    standard_export = removed_anchor or any(item['batch']['form_type'] in STANDARDS for item in monthly_items)
    summary_context = {
        'fixed_remarks': {row:repository.get_summary_remark(batch['month'],f'fixed:{row}') for row in range(3,20)},
        'meeting_cells':snapshot['cells'], 'cell_reasons':snapshot['cell_reasons'], 'excluded_keys':snapshot['excluded_keys'],
        'supplemental_rows':[dict(row,evidence_url=str(request.base_url).rstrip('/')+f"/api/v1/months/{batch['month']}/supplemental-rows/{row['id']}/evidence?share={supplemental_share_token(batch['month'],row['id'])}" if row['attachments'] else '') for row in snapshot['supplemental_rows']],
    } if standard_export else None
    # Freeze originals into the same archive as the reviewed workbook. URLs in
    # the archived copy are relative paths and require no server or credentials.
    archive_supplements = []
    import json
    for row in snapshot['supplemental_rows']:
        stored = repository.supplemental_row(batch['month'], row['id'])
        archive_supplements.append(dict(row, attachments=json.loads(stored['attachments_json'])))
    artifact_data = None
    try:
        artifact_data = create_export_workbook(
            calculation=calculation,
            batch=batch,
            fields=repository.list_fields(batch["id"]),
            mode=payload.mode,
            export_root=settings.export_root,
            template_version="TMPL-2026.10" if standard_export else settings.template_version,
            template_path=settings.standard_template_path if standard_export else (settings.template_path if settings.template_path.exists() else None),
            documents=repository.list_documents(batch["id"]),
            audit_events=repository.list_audit(batch["id"]),
            monthly_items=monthly_items,
            summary_context=summary_context,
        )
        from app.services.archive_export import build_archive
        build_archive(artifact_data['file_path'], monthly_items, archive_supplements)
    except (ValueError, OSError) as exc:
        if artifact_data:
            remove_managed_file(artifact_data['file_path'], settings.export_root)
            remove_managed_file(str(Path(artifact_data['file_path']).with_suffix('.zip')), settings.export_root)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        artifact = repository.add_export(calculation_id, artifact_data, payload.final_review_revision)
    except ValueError as exc:
        remove_managed_file(artifact_data['file_path'], settings.export_root)
        remove_managed_file(str(Path(artifact_data['file_path']).with_suffix('.zip')), settings.export_root)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    token = issue_download_token(artifact["id"])
    artifact["download_url"] = f"/api/v1/exports/{artifact['id']}/download?token={token}"
    artifact['archive_url'] = f"/api/v1/exports/{artifact['id']}/archive?token={token}"
    artifact['workbook_url'] = artifact['download_url']
    artifact['download_url'] = artifact['archive_url']
    artifact['archive_name'] = Path(artifact_data['file_path']).with_suffix('.zip').name
    artifact.pop("file_path", None)
    current_request_id = request_id()
    repository.append_audit(actor=actor, action="EXPORT_CREATED", object_type="export", object_id=artifact["id"], batch_id=batch["id"], after=artifact, request_id=current_request_id)
    repository.append_audit(actor=actor, action="FINAL_REVIEW_APPROVED", object_type="export", object_id=artifact["id"], batch_id=batch["id"], after={'month':batch['month'],'revision':payload.final_review_revision,'mode':payload.mode}, request_id=current_request_id)
    return response_with_id({"export": artifact}, current_request_id)


@app.get("/api/v1/exports")
def list_exports(_: str = Depends(reviewer_actor)) -> dict[str, Any]:
    items = repository.list_exports()
    for artifact in items:
        token = issue_download_token(artifact["id"])
        artifact["download_url"] = f"/api/v1/exports/{artifact['id']}/download?token={token}"
        artifact['workbook_url'] = artifact['download_url']
        if Path(artifact['file_path']).with_suffix('.zip').is_file():
            artifact['archive_url'] = f"/api/v1/exports/{artifact['id']}/archive?token={token}"
            artifact['download_url'] = artifact['archive_url']
            artifact['archive_name'] = Path(artifact['file_path']).with_suffix('.zip').name
        artifact.pop("file_path", None)
    return {"items": items}


@app.get("/api/v1/exports/{export_id}/download")
def download_export(export_id: str, token: Annotated[str | None, Query()] = None) -> FileResponse:
    try:
        verify_download_token(export_id, token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    artifact = repository.get_export(export_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="导出记录不存在")
    path = Path(artifact["file_path"])
    if not path.exists():
        raise HTTPException(status_code=410, detail="导出文件已丢失")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=path.name)


@app.delete('/api/v1/exports/{export_id}')
def delete_export(export_id: str, actor: str = Depends(reviewer_actor)):
    artifact = repository.delete_export(export_id, actor)
    if not artifact:
        raise HTTPException(status_code=404, detail='导出记录不存在')
    remove_managed_file(artifact['file_path'], settings.export_root)
    remove_managed_file(str(Path(artifact['file_path']).with_suffix('.zip')), settings.export_root)
    return {'deleted':export_id}


@app.get('/api/v1/exports/{export_id}/archive')
def download_archive(export_id: str, token: str | None = None):
    try:
        verify_download_token(export_id, token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    artifact = repository.get_export(export_id)
    if not artifact:
        raise HTTPException(status_code=404, detail='导出记录不存在')
    path = Path(artifact['file_path']).with_suffix('.zip')
    if not path.is_file():
        raise HTTPException(status_code=410, detail='该历史导出没有离线归档包，请重新导出')
    return FileResponse(path, media_type='application/zip', filename=path.name)


@app.post("/api/v1/admin/login")
def admin_login(payload: AdminLoginRequest) -> dict[str, Any]:
    try:
        verify_admin_password(payload.password)
        token = issue_admin_token()
    except AuthenticationError as exc:
        status_code = 503 if "尚未初始化" in str(exc) else 401
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {"token": token, "expires_in": 8 * 60 * 60}


@app.get("/api/v1/admin/settings")
def get_settings(_: str = Depends(admin_actor)) -> dict[str, Any]:
    defaults = {
        "application_start": None,
        "application_end": None,
        "confidence_threshold": "0.90",
        "retention_months": settings.retention_months,
    }
    return {key: repository.get_setting(key, default) for key, default in defaults.items()}


@app.put("/api/v1/admin/settings")
def update_settings(payload: SystemSettingsPatch, actor: str = Depends(admin_actor)) -> dict[str, Any]:
    updated = []
    for key, value in payload.model_dump(exclude_none=True, mode="json").items():
        updated.append(repository.set_setting(key, value, actor))
    current_request_id = request_id()
    repository.append_audit(actor=actor, action="SETTINGS_UPDATED", object_type="system_settings", after=updated, request_id=current_request_id)
    return response_with_id({"items": updated}, current_request_id)


@app.get("/api/v1/admin/batches")
def admin_list_batches(_: str = Depends(admin_actor)) -> dict[str, Any]:
    items = repository.list_batches()
    for batch in items:
        calculation = repository.get_latest_calculation_for_batch(batch["id"])
        batch["calculation"] = (
            {
                "id": calculation["id"],
                "status": calculation["status"],
                "rounded_total": calculation["rounded_total"],
                "confirmed_total": calculation["confirmed_total"],
            }
            if calculation
            else None
        )
        batch["document_count"] = repository.count_documents(batch["id"])
    return {"items": items}


@app.delete("/api/v1/admin/batches/{batch_id}")
def admin_delete_batch(batch_id: str, actor: str = Depends(admin_actor)) -> dict[str, Any]:
    try:
        deleted = repository.delete_batch(batch_id)
    except RuntimeError as exc:
        if str(exc) == "BATCH_BUSY":
            raise HTTPException(status_code=409, detail="批次正在识别处理中，请完成后再删除") from exc
        raise
    if not deleted:
        raise HTTPException(status_code=404, detail="批次不存在")

    deleted_files = 0
    cleanup_failed = 0
    for path_value in deleted["document_paths"]:
        if remove_managed_file(path_value, settings.upload_root):
            deleted_files += 1
        else:
            cleanup_failed += 1
    for path_value in deleted["export_paths"]:
        remove_managed_file(str(Path(path_value).with_suffix('.zip')), settings.export_root)
        if remove_managed_file(path_value, settings.export_root):
            deleted_files += 1
        else:
            cleanup_failed += 1

    batch_dir = (settings.upload_root / batch_id).resolve()
    upload_root = settings.upload_root.resolve()
    if upload_root in batch_dir.parents:
        try:
            batch_dir.rmdir()
        except OSError:
            pass

    current_request_id = request_id()
    batch = deleted["batch"]
    summary = {
        "batch_id": batch_id,
        "batch_no": batch["batch_no"],
        "document_count": deleted["document_count"],
        "calculation_count": deleted["calculation_count"],
        "export_count": deleted["export_count"],
        "deleted_files": deleted_files,
        "cleanup_failed": cleanup_failed,
    }
    repository.append_audit(
        actor=actor,
        action="BATCH_DELETED",
        object_type="batch",
        object_id=batch_id,
        before={
            "batch_id": batch_id,
            "batch_no": batch["batch_no"],
            "month": batch["month"],
            "status": batch["status"],
        },
        after=summary,
        request_id=current_request_id,
    )
    return response_with_id({"deleted": summary}, current_request_id)


# Nginx serves these assets in Docker. When the application runs natively on
# Windows, exposing them from FastAPI keeps the UI and API on one private IP
# and one port without changing the container deployment.
frontend_preview_root = Path(__file__).resolve().parents[2] / "frontend" / "preview"
frontend_app_root = Path(__file__).resolve().parents[2] / "frontend" / "app"
if frontend_preview_root.is_dir():
    @app.get("/", include_in_schema=False)
    def preview_redirect() -> RedirectResponse:
        return RedirectResponse(url="/preview/")

    app.mount("/preview", StaticFiles(directory=frontend_preview_root, html=True), name="preview")
if frontend_app_root.is_dir():
    app.mount("/app", StaticFiles(directory=frontend_app_root), name="frontend-app")
