from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.domain.state_machine import BatchStatus, transition


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS batch_owners (
    batch_id TEXT PRIMARY KEY REFERENCES settlement_batches(id) ON DELETE CASCADE,
    credential_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_notes (
    batch_id TEXT NOT NULL REFERENCES settlement_batches(id) ON DELETE CASCADE,
    stage TEXT NOT NULL, text TEXT NOT NULL, revision INTEGER NOT NULL,
    calculation_id TEXT NOT NULL, actor TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(batch_id,stage)
);
CREATE TABLE IF NOT EXISTS excluded_rows (
    month TEXT NOT NULL, row_key TEXT NOT NULL, batch_id TEXT,
    actor TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(month,row_key)
);
CREATE TABLE IF NOT EXISTS archived_supplemental_rows (
    id TEXT PRIMARY KEY, month TEXT NOT NULL, row_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS initial_reviews (
    calculation_id TEXT PRIMARY KEY REFERENCES calculation_runs(id) ON DELETE CASCADE,
    total_fen INTEGER NOT NULL, reason TEXT, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meeting_approvals (
    month TEXT PRIMARY KEY, revision TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS supplemental_rows (
    id TEXT PRIMARY KEY, month TEXT NOT NULL, label TEXT NOT NULL,
    other TEXT NOT NULL, remark TEXT NOT NULL, attachments_json TEXT NOT NULL,
    actor TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meeting_cells (
    month TEXT NOT NULL, row_key TEXT NOT NULL, values_json TEXT NOT NULL,
    reason TEXT NOT NULL, actor TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(month,row_key)
);
CREATE TABLE IF NOT EXISTS summary_remarks (
    month TEXT NOT NULL,
    row_key TEXT NOT NULL,
    text TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(month, row_key)
);
CREATE TABLE IF NOT EXISTS settlement_batches (
    id TEXT PRIMARY KEY,
    batch_no TEXT NOT NULL UNIQUE,
    month TEXT NOT NULL,
    project_owner TEXT,
    business_category TEXT NOT NULL,
    form_type TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT 'anonymous',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retention_until TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_batches_month_status ON settlement_batches(month, status);

CREATE TABLE IF NOT EXISTS source_documents (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES settlement_batches(id),
    file_name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    pages INTEGER,
    document_role TEXT NOT NULL DEFAULT 'MATERIAL',
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, sha256)
);

CREATE TABLE IF NOT EXISTS extracted_fields (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES settlement_batches(id),
    path TEXT NOT NULL,
    value_raw TEXT,
    value_normalized TEXT,
    confidence TEXT NOT NULL,
    source_document_id TEXT REFERENCES source_documents(id),
    page_no INTEGER,
    bounding_box TEXT,
    confirmed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(batch_id, path)
);

CREATE TABLE IF NOT EXISTS extraction_jobs (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES settlement_batches(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    result_revision INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_status_created ON extraction_jobs(status, created_at);

CREATE TABLE IF NOT EXISTS calculation_runs (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES settlement_batches(id),
    rule_code TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    status TEXT NOT NULL,
    inputs_snapshot TEXT NOT NULL,
    raw_total_fen INTEGER NOT NULL,
    rounded_total_fen INTEGER NOT NULL,
    confirmed_total_fen INTEGER,
    confirmed_by TEXT,
    confirmed_at TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calculation_lines (
    id TEXT PRIMARY KEY,
    calculation_id TEXT NOT NULL REFERENCES calculation_runs(id),
    ordinal INTEGER NOT NULL,
    formula_code TEXT NOT NULL,
    label TEXT NOT NULL,
    expression TEXT NOT NULL,
    operands TEXT NOT NULL,
    raw_result TEXT NOT NULL,
    rounded_result TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_overrides (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES settlement_batches(id),
    calculation_id TEXT REFERENCES calculation_runs(id),
    field TEXT NOT NULL,
    before_value TEXT,
    after_value TEXT,
    reason TEXT NOT NULL,
    approver TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS export_artifacts (
    id TEXT PRIMARY KEY,
    calculation_id TEXT NOT NULL REFERENCES calculation_runs(id),
    mode TEXT NOT NULL,
    template_version TEXT NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    report_month TEXT,
    report_total_fen INTEGER,
    project_count INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT,
    before_json TEXT,
    after_json TEXT,
    request_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_batch_created ON audit_events(batch_id, created_at);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def yuan_to_fen(value: str | Decimal | int) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fen_to_yuan(value: int | None) -> str | None:
    if value is None:
        return None
    return str((Decimal(value) / 100).quantize(Decimal("0.01")))


class Repository:
    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(calculation_runs)").fetchall()
            }
            if "warnings_json" not in columns:
                connection.execute(
                    "ALTER TABLE calculation_runs ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "metadata_json" not in columns:
                connection.execute(
                    "ALTER TABLE calculation_runs ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
                )
            document_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(source_documents)").fetchall()
            }
            if "document_role" not in document_columns:
                connection.execute(
                    "ALTER TABLE source_documents ADD COLUMN document_role TEXT NOT NULL DEFAULT 'MATERIAL'"
                )
            export_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(export_artifacts)").fetchall()
            }
            if "report_month" not in export_columns:
                connection.execute("ALTER TABLE export_artifacts ADD COLUMN report_month TEXT")
            if "report_total_fen" not in export_columns:
                connection.execute("ALTER TABLE export_artifacts ADD COLUMN report_total_fen INTEGER")
            if "project_count" not in export_columns:
                connection.execute("ALTER TABLE export_artifacts ADD COLUMN project_count INTEGER")
            batch_columns = {row['name'] for row in connection.execute('PRAGMA table_info(settlement_batches)')}
            for column in ('supersedes_batch_id', 'superseded_by_batch_id'):
                if column not in batch_columns:
                    connection.execute(f'ALTER TABLE settlement_batches ADD COLUMN {column} TEXT')
            connection.execute("PRAGMA optimize")

    def create_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        batch_id = str(uuid4())
        compact_month = payload["month"].replace("-", "")
        batch_no = f"BAT-{compact_month}-{batch_id[:6].upper()}"
        retention_year = int(payload["month"][:4]) + 2
        retention_until = f"{retention_year}-{payload['month'][5:]}-28T23:59:59+00:00"
        values = {
            "id": batch_id,
            "batch_no": batch_no,
            "month": payload["month"],
            "project_owner": payload.get("project_owner"),
            "business_category": str(payload["business_category"]),
            "form_type": str(payload["form_type"]),
            "status": BatchStatus.DRAFT.value,
            "revision": 0,
            "created_at": now,
            "updated_at": now,
            "retention_until": retention_until,
        }
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO settlement_batches
                (id,batch_no,month,project_owner,business_category,form_type,status,revision,created_at,updated_at,retention_until)
                VALUES (:id,:batch_no,:month,:project_owner,:business_category,:form_type,:status,:revision,:created_at,:updated_at,:retention_until)""",
                values,
            )
        return values

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return row_to_dict(connection.execute("SELECT * FROM settlement_batches WHERE id=?", (batch_id,)).fetchone())

    def reopen_batch(self, batch_id: str, *, reset_upload: bool = False) -> list[str]:
        """Invalidate stale review snapshots atomically before editing an unapproved batch."""
        paths = []
        allowed = ({'DRAFT', 'UPLOADED', 'REVIEW_REQUIRED', 'WAITING_FOR_EVIDENCE', 'FAILED'}
                   if reset_upload else {'CALCULATED', 'MANUAL_APPROVAL_REQUIRED', 'READY_TO_CALCULATE'})
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            batch = connection.execute('SELECT * FROM settlement_batches WHERE id=?', (batch_id,)).fetchone()
            if not batch or batch['status'] not in allowed:
                raise ValueError('当前状态不允许修改；进入终审后申报材料已锁定')
            if connection.execute("SELECT 1 FROM extraction_jobs WHERE batch_id=? AND status IN ('PENDING','RUNNING')", (batch_id,)).fetchone():
                raise ValueError('识别任务尚未结束，请稍后再试')
            connection.execute("UPDATE calculation_runs SET status='WITHDRAWN' WHERE batch_id=? AND status IN ('CALCULATED','MANUAL_APPROVAL_REQUIRED')", (batch_id,))
            if reset_upload:
                paths = [row['storage_path'] for row in connection.execute('SELECT storage_path FROM source_documents WHERE batch_id=?', (batch_id,))]
                connection.execute('DELETE FROM extracted_fields WHERE batch_id=?', (batch_id,))
                connection.execute('DELETE FROM extraction_jobs WHERE batch_id=?', (batch_id,))
                connection.execute('DELETE FROM source_documents WHERE batch_id=?', (batch_id,))
                connection.execute('DELETE FROM idempotency_keys WHERE scope=?', (f'extract:{batch_id}',))
            connection.execute('UPDATE settlement_batches SET status=?,revision=revision+1,updated_at=? WHERE id=?',
                               ('DRAFT' if reset_upload else 'REVIEW_REQUIRED', utc_now(), batch_id))
        return paths

    def create_revision(self, batch_id: str) -> dict[str, Any]:
        raise ValueError('进入终审后申报材料已锁定，不能替换或重新提交')

    def bind_batch_owner(self, batch_id, credential_hash):
        with self.connection() as connection:
            connection.execute('INSERT INTO batch_owners VALUES (?,?) ON CONFLICT(batch_id) DO UPDATE SET credential_hash=excluded.credential_hash', (batch_id, credential_hash))

    def owns_batch(self, batch_id, credential_hash):
        with self.connection() as connection:
            return bool(connection.execute('SELECT 1 FROM batch_owners WHERE batch_id=? AND credential_hash=?',
                                           (batch_id, credential_hash)).fetchone())

    def get_summary_remark(self, month: str, key: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT text,revision FROM summary_remarks WHERE month=? AND row_key=?", (month, key)).fetchone()
            result = dict(row) if row else None
            if result and key == 'fixed:14':
                result['text'] = result['text'].replace('；旧版含毛利时按超额累进核算。', '。').replace('旧版含毛利时按超额累进核算。', '')
            return result

    def save_summary_remark(self, month: str, key: str, text: str, revision: int, actor: str) -> dict[str, Any]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT text,revision FROM summary_remarks WHERE month=? AND row_key=?", (month,key)).fetchone()
            if (row["revision"] if row else 0) != revision:
                raise ValueError("备注已被其他操作更新，请刷新后重试")
            after = {"text": text, "revision": revision + 1}
            connection.execute("INSERT INTO summary_remarks VALUES (?,?,?,?,?,?) ON CONFLICT(month,row_key) DO UPDATE SET text=excluded.text,revision=excluded.revision,updated_by=excluded.updated_by,updated_at=excluded.updated_at", (month,key,text,revision+1,actor,utc_now()))
            connection.execute("INSERT INTO audit_events (id,batch_id,actor,action,object_type,object_id,before_json,after_json,request_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (str(uuid4()),None,actor,"SUMMARY_REMARK_UPDATED","summary_remark",month+":"+key,json.dumps(dict(row) if row else None,ensure_ascii=False),json.dumps(after,ensure_ascii=False),None,utc_now()))
            return after

    def list_batches(self, month: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT *, (EXISTS(SELECT 1 FROM excluded_rows e WHERE e.batch_id=settlement_batches.id) OR (SELECT status FROM calculation_runs c WHERE c.batch_id=settlement_batches.id ORDER BY created_at DESC,id DESC LIMIT 1)='EXCLUDED') AS excluded FROM settlement_batches"
        params: tuple[Any, ...] = ()
        if month:
            query += " WHERE month=?"
            params = (month,)
        query += " ORDER BY updated_at DESC,created_at DESC,id DESC"
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def employee_return_notice(self, batch):
        if batch['status'] not in {'DRAFT', 'UPLOADED', 'EXTRACTING', 'REVIEW_REQUIRED',
                                   'WAITING_FOR_EVIDENCE', 'READY_TO_CALCULATE', 'FAILED'}:
            return None
        with self.connection() as connection:
            event = connection.execute('''SELECT action,after_json,created_at FROM audit_events
                WHERE batch_id=? AND action IN ('BATCH_RETURNED','CALCULATION_CREATED','BATCH_WITHDRAWN')
                ORDER BY created_at DESC,rowid DESC LIMIT 1''', (batch['id'],)).fetchone()
        if event and event['action'] == 'BATCH_RETURNED':
            after = json.loads(event['after_json'] or '{}')
            if after.get('batch', {}).get('status') == 'REVIEW_REQUIRED':
                return {'reason': after.get('reason', ''), 'returned_at': event['created_at']}
        return None

    def set_batch_status(self, batch_id: str, target: BatchStatus, *, strict: bool = True) -> dict[str, Any]:
        batch = self.get_batch(batch_id)
        if not batch:
            raise KeyError(batch_id)
        if strict:
            transition(batch["status"], target)
        with self.connection() as connection:
            connection.execute(
                "UPDATE settlement_batches SET status=?,revision=revision+1,updated_at=? WHERE id=?",
                (target.value, utc_now(), batch_id),
            )
        return self.get_batch(batch_id) or {}

    def delete_batch(self, batch_id: str) -> dict[str, Any] | None:
        """Delete one batch and all operational records in one transaction.

        Audit rows are detached from the deleted batch instead of being erased so
        an administrator action remains traceable without keeping the batch alive.
        Physical files are returned to the service layer for guarded cleanup.
        """
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch_row = connection.execute(
                "SELECT * FROM settlement_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if not batch_row:
                return None
            active_job = connection.execute(
                """SELECT 1 FROM extraction_jobs
                WHERE batch_id=? AND status IN ('PENDING','RUNNING') LIMIT 1""",
                (batch_id,),
            ).fetchone()
            if active_job:
                raise RuntimeError("BATCH_BUSY")
            if batch_row['supersedes_batch_id'] or batch_row['superseded_by_batch_id'] or connection.execute('SELECT 1 FROM settlement_batches WHERE supersedes_batch_id=?', (batch_id,)).fetchone():
                raise RuntimeError('BATCH_BUSY')

            documents = [
                dict(row)
                for row in connection.execute(
                    "SELECT id,storage_path FROM source_documents WHERE batch_id=?",
                    (batch_id,),
                ).fetchall()
            ]
            exports = [
                dict(row)
                for row in connection.execute(
                    """SELECT export_artifacts.id,export_artifacts.file_path
                    FROM export_artifacts
                    JOIN calculation_runs ON calculation_runs.id=export_artifacts.calculation_id
                    WHERE calculation_runs.batch_id=?""",
                    (batch_id,),
                ).fetchall()
            ]
            calculation_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM calculation_runs WHERE batch_id=?",
                    (batch_id,),
                ).fetchone()["count"]
            )

            connection.execute(
                """DELETE FROM calculation_lines WHERE calculation_id IN
                (SELECT id FROM calculation_runs WHERE batch_id=?)""",
                (batch_id,),
            )
            connection.execute(
                """DELETE FROM export_artifacts WHERE calculation_id IN
                (SELECT id FROM calculation_runs WHERE batch_id=?)""",
                (batch_id,),
            )
            connection.execute("DELETE FROM manual_overrides WHERE batch_id=?", (batch_id,))
            connection.execute("DELETE FROM calculation_runs WHERE batch_id=?", (batch_id,))
            connection.execute("DELETE FROM extracted_fields WHERE batch_id=?", (batch_id,))
            connection.execute("DELETE FROM extraction_jobs WHERE batch_id=?", (batch_id,))
            connection.execute("DELETE FROM source_documents WHERE batch_id=?", (batch_id,))
            connection.execute(
                "UPDATE audit_events SET batch_id=NULL WHERE batch_id=?", (batch_id,)
            )
            connection.execute(
                "DELETE FROM idempotency_keys WHERE scope=? OR response_json LIKE ?",
                (f"extract:{batch_id}", f"%{batch_id}%"),
            )
            connection.execute("DELETE FROM settlement_batches WHERE id=?", (batch_id,))

        return {
            "batch": dict(batch_row),
            "document_paths": [item["storage_path"] for item in documents],
            "export_paths": [item["file_path"] for item in exports],
            "document_count": len(documents),
            "calculation_count": calculation_count,
            "export_count": len(exports),
        }

    def count_documents(self, batch_id: str) -> int:
        with self.connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM source_documents WHERE batch_id=?", (batch_id,)).fetchone()
            return int(row["count"])

    def find_document_by_hash(self, batch_id: str, sha256: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return row_to_dict(connection.execute("SELECT * FROM source_documents WHERE batch_id=? AND sha256=?", (batch_id, sha256)).fetchone())

    def add_document(self, batch_id: str, document: dict[str, Any]) -> dict[str, Any]:
        values = {"id": str(uuid4()), "batch_id": batch_id, "created_at": utc_now(), **document}
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO source_documents
                (id,batch_id,file_name,mime,size_bytes,sha256,storage_path,pages,document_role,created_at)
                VALUES (:id,:batch_id,:file_name,:mime,:size_bytes,:sha256,:storage_path,:pages,:document_role,:created_at)""",
                values,
            )
        return values

    def add_documents(self, batch_id: str, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        values_list = [
            {"id": str(uuid4()), "batch_id": batch_id, "created_at": utc_now(), **document}
            for document in documents
        ]
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            batch = connection.execute('SELECT * FROM settlement_batches WHERE id=?', (batch_id,)).fetchone()
            if not batch or batch['status'] not in {'DRAFT', 'UPLOADED', 'WAITING_FOR_EVIDENCE', 'FAILED'}:
                raise ValueError('当前批次状态已变化，请刷新后重试')
            existing = [dict(row) for row in connection.execute('SELECT * FROM source_documents WHERE batch_id=?', (batch_id,))]
            hashes = {row['sha256']: row['document_role'] for row in existing}
            unique = []
            for item in values_list:
                if item['sha256'] in hashes:
                    if hashes[item['sha256']] != item['document_role']:
                        raise ValueError('同一文件不能同时作为主表和证明')
                    continue
                hashes[item['sha256']] = item['document_role']
                unique.append(item)
            values_list = unique
            combined = existing + values_list
            if len(combined) > 30:
                raise ValueError('单批次最多上传30个文件')
            materials = [item for item in combined if item['document_role'] == 'MATERIAL']
            if not materials or (batch['form_type'].startswith('STD_') and len(materials) != 1):
                raise ValueError('每次标准申报须且只能有一份主表；修改主表请先返回修改上传文件')
            connection.executemany(
                """INSERT INTO source_documents
                (id,batch_id,file_name,mime,size_bytes,sha256,storage_path,pages,document_role,created_at)
                VALUES (:id,:batch_id,:file_name,:mime,:size_bytes,:sha256,:storage_path,:pages,:document_role,:created_at)""",
                values_list,
            )
            connection.execute("UPDATE settlement_batches SET status='UPLOADED',revision=revision+1,updated_at=? WHERE id=?", (utc_now(),batch_id))
        return values_list

    def list_documents(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM source_documents WHERE batch_id=? ORDER BY created_at", (batch_id,)).fetchall()]

    def create_extraction_job(self, batch_id: str, provider: str, model: str, prompt_version: str) -> dict[str, Any]:
        now = utc_now()
        values = {
            "id": str(uuid4()), "batch_id": batch_id, "provider": provider, "model": model,
            "prompt_version": prompt_version, "status": "PENDING", "progress": 0, "attempts": 0,
            "error_code": None, "error_message": None, "result_revision": None,
            "created_at": now, "updated_at": now,
        }
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO extraction_jobs
                (id,batch_id,provider,model,prompt_version,status,progress,attempts,error_code,error_message,result_revision,created_at,updated_at)
                VALUES (:id,:batch_id,:provider,:model,:prompt_version,:status,:progress,:attempts,:error_code,:error_message,:result_revision,:created_at,:updated_at)""",
                values,
            )
        return values

    def get_extraction_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return row_to_dict(connection.execute("SELECT * FROM extraction_jobs WHERE id=?", (job_id,)).fetchone())

    def claim_extraction_job(self, job_id: str | None = None) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if job_id:
                row = connection.execute("SELECT * FROM extraction_jobs WHERE id=? AND status='PENDING'", (job_id,)).fetchone()
            else:
                row = connection.execute("SELECT * FROM extraction_jobs WHERE status='PENDING' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE extraction_jobs SET status='RUNNING',progress=5,attempts=attempts+1,updated_at=? WHERE id=? AND status='PENDING'",
                (utc_now(), row["id"]),
            )
        return self.get_extraction_job(row["id"])

    def update_extraction_job(self, job_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"status", "progress", "error_code", "error_message", "result_revision"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            return self.get_extraction_job(job_id) or {}
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connection() as connection:
            connection.execute(f"UPDATE extraction_jobs SET {assignments} WHERE id=?", (*values.values(), job_id))
        return self.get_extraction_job(job_id) or {}

    def replace_fields(self, batch_id: str, fields: list[dict[str, Any]], expected_revision: int) -> int:
        batch = self.get_batch(batch_id)
        if not batch:
            raise KeyError(batch_id)
        if int(batch["revision"]) != expected_revision:
            raise RuntimeError("REVISION_CONFLICT")
        now = utc_now()
        with self.connection() as connection:
            # This is a snapshot replacement, so fields no longer produced by a
            # form-specific extractor must not survive a re-recognition run.
            connection.execute("DELETE FROM extracted_fields WHERE batch_id=?", (batch_id,))
            for item in fields:
                values = {
                    "id": str(uuid4()),
                    "batch_id": batch_id,
                    "path": item["path"],
                    "value_raw": item.get("value_raw"),
                    "value_normalized": json.dumps(item.get("value_normalized"), ensure_ascii=False),
                    "confidence": str(item.get("confidence", 0)),
                    "source_document_id": item.get("source_document_id"),
                    "page_no": item.get("page_no"),
                    "bounding_box": json.dumps(item.get("bounding_box"), ensure_ascii=False),
                    "confirmed": 1 if item.get("confirmed") else 0,
                    "updated_at": now,
                }
                connection.execute(
                    """INSERT INTO extracted_fields
                    (id,batch_id,path,value_raw,value_normalized,confidence,source_document_id,page_no,bounding_box,confirmed,updated_at)
                    VALUES (:id,:batch_id,:path,:value_raw,:value_normalized,:confidence,:source_document_id,:page_no,:bounding_box,:confirmed,:updated_at)
                    ON CONFLICT(batch_id,path) DO UPDATE SET
                    value_raw=excluded.value_raw,value_normalized=excluded.value_normalized,confidence=excluded.confidence,
                    source_document_id=excluded.source_document_id,page_no=excluded.page_no,bounding_box=excluded.bounding_box,
                    confirmed=excluded.confirmed,updated_at=excluded.updated_at""",
                    values,
                )
            cursor = connection.execute(
                "UPDATE settlement_batches SET revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                (now, batch_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("REVISION_CONFLICT")
        return expected_revision + 1

    def list_fields(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM extracted_fields WHERE batch_id=? ORDER BY path", (batch_id,)).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["value_normalized"] = json.loads(item["value_normalized"])
            item["bounding_box"] = json.loads(item["bounding_box"]) if item["bounding_box"] else None
            item["confirmed"] = bool(item["confirmed"])
            output.append(item)
        return output

    def save_calculation(self, batch_id: str, inputs: dict[str, Any], result: dict[str, Any], rule_version: str) -> dict[str, Any]:
        calculation_id = str(uuid4())
        created_at = utc_now()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO calculation_runs
                (id,batch_id,rule_code,rule_version,status,inputs_snapshot,raw_total_fen,rounded_total_fen,warnings_json,metadata_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    calculation_id,
                    batch_id,
                    result["rule_code"],
                    rule_version,
                    result["status"],
                    json.dumps(inputs, ensure_ascii=False, default=str),
                    yuan_to_fen(result["raw_total"]),
                    yuan_to_fen(result["rounded_total"]),
                    json.dumps(result.get("warnings") or [], ensure_ascii=False, default=str),
                    json.dumps(result.get("metadata") or {}, ensure_ascii=False, default=str),
                    created_at,
                ),
            )
            for index, line in enumerate(result["lines"]):
                connection.execute(
                    """INSERT INTO calculation_lines
                    (id,calculation_id,ordinal,formula_code,label,expression,operands,raw_result,rounded_result)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (str(uuid4()),calculation_id,index,line["formula_code"],line["label"],line["expression"],json.dumps(line["operands"],ensure_ascii=False),line["raw_result"],line["rounded_result"]),
                )
        return self.get_calculation(calculation_id) or {}

    def get_calculation(self, calculation_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            run = row_to_dict(connection.execute("SELECT * FROM calculation_runs WHERE id=?", (calculation_id,)).fetchone())
            if not run:
                return None
            lines = [dict(row) for row in connection.execute("SELECT * FROM calculation_lines WHERE calculation_id=? ORDER BY ordinal", (calculation_id,)).fetchall()]
        run["inputs_snapshot"] = json.loads(run["inputs_snapshot"])
        run["warnings"] = json.loads(run.pop("warnings_json") or "[]")
        run["metadata"] = json.loads(run.pop("metadata_json") or "{}")
        run["raw_total"] = fen_to_yuan(run.pop("raw_total_fen"))
        run["rounded_total"] = fen_to_yuan(run.pop("rounded_total_fen"))
        run["confirmed_total"] = fen_to_yuan(run.pop("confirmed_total_fen"))
        for line in lines:
            line["operands"] = json.loads(line["operands"])
        run["lines"] = lines
        return run

    def get_latest_calculation_for_batch(self, batch_id: str) -> dict[str, Any] | None:
        """Return the newest calculation snapshot for a batch, if one exists."""
        with self.connection() as connection:
            row = connection.execute(
                "SELECT id FROM calculation_runs WHERE batch_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                (batch_id,),
            ).fetchone()
        return self.get_calculation(row["id"]) if row else None

    def confirm_calculation(self, calculation_id: str, reviewer: str, override_total: str | None, reason: str | None, replace_batch_id: str | None = None) -> dict[str, Any]:
        calculation = self.get_calculation(calculation_id)
        if not calculation:
            raise KeyError(calculation_id)
        confirmed_total = override_total or calculation["rounded_total"]
        now = utc_now()
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            run = connection.execute('SELECT * FROM calculation_runs WHERE id=?', (calculation_id,)).fetchone()
            batch = connection.execute('SELECT * FROM settlement_batches WHERE id=?', (calculation['batch_id'],)).fetchone()
            latest = connection.execute('SELECT id FROM calculation_runs WHERE batch_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (batch['id'],)).fetchone()
            if batch['status'] != 'INITIAL_REVIEWED':
                raise ValueError('请先完成初审，再进入复审确认')
            initial = connection.execute('SELECT * FROM initial_reviews WHERE calculation_id=?', (calculation_id,)).fetchone()
            if not initial:
                raise ValueError('缺少初审记录，请刷新')
            confirmed_total = override_total if override_total is not None else fen_to_yuan(initial['total_fen'])
            if (batch['status'] != 'INITIAL_REVIEWED'
                    or run['status'] not in {'CALCULATED', 'MANUAL_APPROVAL_REQUIRED'} or latest['id'] != calculation_id):
                raise ValueError('该申报已撤回或更新，请刷新审核页面')
            if replace_batch_id or batch['supersedes_batch_id']:
                raise ValueError('原申报已进入终审，不能替换材料')
            parent = batch['supersedes_batch_id']
            replacement_matched = False
            if replace_batch_id and parent and replace_batch_id != parent:
                raise ValueError('修订申报不能替换其他原申报')
            if parent:
                original = connection.execute('SELECT * FROM settlement_batches WHERE id=?', (parent,)).fetchone()
                if not original or original['superseded_by_batch_id'] or original['status'] not in {'CONFIRMED', 'EXPORTED'}:
                    raise ValueError('原申报已被修订，请刷新后重试')
            if batch['form_type'].startswith('STD_'):
                project_types = {'STD_PROJECT', 'STD_SERVICE'}
                name_row = connection.execute("SELECT value_normalized FROM extracted_fields WHERE batch_id=? AND path='project.name'", (batch['id'],)).fetchone()
                name = str(json.loads(name_row['value_normalized']) or '').strip() if name_row else ''
                others = connection.execute("""SELECT DISTINCT b.id,b.form_type,f.value_normalized AS project_name
                    FROM settlement_batches b JOIN calculation_runs c ON c.batch_id=b.id
                    LEFT JOIN extracted_fields f ON f.batch_id=b.id AND f.path='project.name'
                    WHERE b.month=? AND c.status='CONFIRMED' AND b.superseded_by_batch_id IS NULL
                    AND NOT EXISTS(SELECT 1 FROM excluded_rows e WHERE e.batch_id=b.id)""", (batch['month'],)).fetchall()
                for other in others:
                    if other['id'] in {batch['id'], parent}:
                        continue
                    fixed_duplicate = batch['form_type'] not in project_types and other['form_type'] == batch['form_type']
                    other_name = str(json.loads(other['project_name']) or '').strip() if other['project_name'] else ''
                    project_duplicate = batch['form_type'] in project_types and other['form_type'] in project_types and name and name == other_name
                    if fixed_duplicate or project_duplicate:
                        if other['id'] != replace_batch_id or parent:
                            raise ValueError('本月该部门或同名项目已进入终审，材料已锁定，不能重复申报')
                        parent = other['id']
                        replacement_matched = True
            if replace_batch_id and not replacement_matched and replace_batch_id != batch['supersedes_batch_id']:
                raise ValueError('待替换申报已变化或与当前申报不匹配，请刷新')
            if replacement_matched:
                connection.execute('UPDATE settlement_batches SET supersedes_batch_id=? WHERE id=?', (parent,batch['id']))
            connection.execute(
                "UPDATE calculation_runs SET status='CONFIRMED',confirmed_total_fen=?,confirmed_by=?,confirmed_at=? WHERE id=?",
                (yuan_to_fen(confirmed_total), reviewer, now, calculation_id),
            )
            if override_total is not None:
                connection.execute(
                    """INSERT INTO manual_overrides
                    (id,batch_id,calculation_id,field,before_value,after_value,reason,approver,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (str(uuid4()),calculation["batch_id"],calculation_id,"rounded_total",calculation["rounded_total"],override_total,reason or "",reviewer,now),
                )
            connection.execute("UPDATE settlement_batches SET status='CONFIRMED',revision=revision+1,updated_at=? WHERE id=?", (now,batch['id']))
            from app.services.summary_review import FIXED_ROWS
            if batch['form_type'] in FIXED_ROWS:
                connection.execute('DELETE FROM excluded_rows WHERE month=? AND row_key=?',(batch['month'],f"fixed:{FIXED_ROWS[batch['form_type']]}"))
            if parent:
                connection.execute('UPDATE settlement_batches SET superseded_by_batch_id=?,updated_at=? WHERE id=?', (batch['id'],now,parent))
        return self.get_calculation(calculation_id) or {}

    def add_export(self, calculation_id: str, artifact: dict[str, Any], final_review_revision: str | None = None) -> dict[str, Any]:
        report_total = artifact.get("report_total")
        values = {
            "id": str(uuid4()),
            "calculation_id": calculation_id,
            "created_at": utc_now(),
            **artifact,
            "report_total_fen": yuan_to_fen(report_total) if report_total is not None else None,
        }
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if final_review_revision is not None:
                current = self._final_review_snapshot(connection, artifact['report_month'])
                if current['revision'] != final_review_revision:
                    raise ValueError('终审内容已变化，请刷新终审池并重新核对')
                if not current['approved']:
                    raise ValueError('请先在终审页面确认会议结果，再导出归档')
                allowed = {item['calculation_id'] for item in current['items']} or {current['export_anchor']}
                if calculation_id not in allowed:
                    raise ValueError('该申报已被替换，请刷新终审池')
            connection.execute(
                """INSERT INTO export_artifacts
                (id,calculation_id,mode,template_version,file_path,sha256,report_month,report_total_fen,project_count,created_at)
                VALUES (:id,:calculation_id,:mode,:template_version,:file_path,:sha256,:report_month,:report_total_fen,:project_count,:created_at)""",
                values,
            )
            if final_review_revision is not None:
                connection.execute("""UPDATE settlement_batches SET status='EXPORTED',updated_at=?
                    WHERE id IN (SELECT batch_id FROM calculation_runs WHERE status='CONFIRMED')
                    AND month=? AND superseded_by_batch_id IS NULL""", (utc_now(),artifact['report_month']))
        values["month"] = values.get("report_month")
        values["confirmed_total"] = fen_to_yuan(values.get("report_total_fen"))
        return values

    def _final_review_snapshot(self, connection, month):
        rows = [dict(row) for row in connection.execute("""
            SELECT c.id AS calculation_id,c.batch_id,c.confirmed_total_fen,c.confirmed_by,c.confirmed_at,
                c.inputs_snapshot,c.metadata_json,c.rule_version,b.batch_no,b.form_type,b.project_owner,
                (SELECT reason FROM manual_overrides m WHERE m.calculation_id=c.id AND m.field='confirmed_total' ORDER BY m.created_at DESC LIMIT 1) AS final_reason
            FROM calculation_runs c JOIN settlement_batches b ON b.id=c.batch_id
            WHERE b.month=? AND c.status='CONFIRMED' AND b.superseded_by_batch_id IS NULL
            AND NOT EXISTS(SELECT 1 FROM excluded_rows e WHERE e.batch_id=b.id)
            ORDER BY c.id""", (month,))]
        sources = []
        items = []
        for row in rows:
            fields = [dict(f) for f in connection.execute('SELECT * FROM extracted_fields WHERE batch_id=? ORDER BY path', (row['batch_id'],))]
            documents = [dict(d) for d in connection.execute('SELECT id,sha256,document_role FROM source_documents WHERE batch_id=? ORDER BY id', (row['batch_id'],))]
            sources.append({'row':row, 'fields':fields, 'documents':documents})
            items.append({key:row[key] for key in ('calculation_id','batch_id','batch_no','form_type','project_owner')} |
                         {'total':fen_to_yuan(row['confirmed_total_fen']), 'final_reason':row['final_reason'] or '',
                          'components':json.loads(row['metadata_json']).get('summary_components',{}),
                          'project_name':next((json.loads(f['value_normalized']) for f in fields if f['path']=='project.name'), '')})
        remarks = [dict(row) for row in connection.execute('SELECT * FROM summary_remarks WHERE month=? ORDER BY row_key', (month,))]
        cells = [dict(row) for row in connection.execute('SELECT * FROM meeting_cells WHERE month=? ORDER BY row_key', (month,))]
        # Ignore project supplements belonging to a replaced batch; fixed rows retain monthly decisions.
        supplemental = [dict(r) for r in connection.execute('SELECT * FROM supplemental_rows WHERE month=? ORDER BY created_at,id',(month,))]
        valid_keys = {f'batch:{item["batch_id"]}' for item in items} | {f'fixed:{n}' for n in range(3,20)} | {'supplement:'+r['id'] for r in supplemental}
        cells = [row for row in cells if row['row_key'] in valid_keys]
        cell_values = {row['row_key']:json.loads(row['values_json']) for row in cells}
        cell_reasons = {row['row_key']:row['reason'] for row in cells}
        exclusions = [dict(r) for r in connection.execute('SELECT * FROM excluded_rows WHERE month=? ORDER BY row_key',(month,))]
        excluded_keys = {r['row_key'] for r in exclusions}
        from app.services.summary_review import DEFAULT_OTHER, DEFAULT_RULE_NOTES, FIXED_ROWS, effective_amounts
        effective = {f'fixed:{n}':effective_amounts(f'fixed:{n}',overrides=cell_values.get(f'fixed:{n}')) for n in range(3,20)}
        for item in items:
            key = f"fixed:{FIXED_ROWS[item['form_type']]}" if item['form_type'] in FIXED_ROWS else f"batch:{item['batch_id']}"
            effective[key] = effective_amounts(key,item['form_type'],item['total'],item['components'],cell_values.get(key))
        effective = {k:v for k,v in effective.items() if k not in excluded_keys}
        digest = sha256(json.dumps([month,sources,remarks,cells,supplemental,exclusions,DEFAULT_OTHER,DEFAULT_RULE_NOTES],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        for row in supplemental:
            extra = cell_values.get('supplement:'+row['id'], {})
            row['other'] = extra.get('other') if extra.get('other') is not None else row['other']
            row['total'] = extra.get('total') if extra.get('total') is not None else row['other']
            row['actual'] = extra.get('actual') if extra.get('actual') is not None else row['total']
            row['actual_manual'] = extra.get('actual') is not None
            row['reason'] = cell_reasons.get('supplement:'+row['id'], '')
            row['attachments'] = [{k:v for k,v in d.items() if k != 'storage_path'} for d in json.loads(row.pop('attachments_json'))]
            for attachment in row['attachments']:
                attachment['content_url'] = f"/api/v1/months/{month}/supplemental-rows/{row['id']}/attachments/{attachment['id']}"
        report_total = sum((Decimal(v['total']) for v in effective.values()), Decimal(0)) + sum((Decimal(r['total']) for r in supplemental),Decimal(0))
        actual_total = sum((Decimal(cell_values.get(k,{}).get('actual') if cell_values.get(k,{}).get('actual') is not None else v['total']) for k,v in effective.items()), Decimal(0)) + sum((Decimal(r['actual']) for r in supplemental), Decimal(0))
        approval = connection.execute('SELECT * FROM meeting_approvals WHERE month=?', (month,)).fetchone()
        anchor = items[0]['calculation_id'] if items else None
        if not anchor:
            removed = connection.execute("SELECT c.id FROM calculation_runs c JOIN settlement_batches b ON b.id=c.batch_id WHERE b.month=? AND c.status='EXCLUDED' AND b.form_type LIKE 'STD_%' ORDER BY c.created_at DESC,c.id DESC LIMIT 1",(month,)).fetchone()
            anchor = removed['id'] if removed else None
        return {'month':month, 'revision':digest, 'items':items, 'export_anchor':anchor,
                'approved':bool(approval and approval['revision']==digest),
                'approved_at':approval['created_at'] if approval and approval['revision']==digest else None,
                'cells':cell_values, 'cell_reasons':cell_reasons, 'excluded_keys':sorted(excluded_keys), 'effective_amounts':effective, 'supplemental_rows':supplemental,
                'total':format(report_total.quantize(Decimal('.01')),'f'),
                'actual_total':format(actual_total.quantize(Decimal('.01')),'f')}

    def save_meeting_cells(self, month, key, values, reason, revision, actor):
        from app.services.summary_review import FIXED_ROWS
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            snapshot = self._final_review_snapshot(connection, month)
            valid_keys = {f'batch:{i["batch_id"]}' for i in snapshot['items'] if i['form_type'] not in FIXED_ROWS} | {f'fixed:{n}' for n in range(3,20)} | {'supplement:'+r['id'] for r in snapshot['supplemental_rows']}
            if snapshot['revision'] != revision or key not in valid_keys or key in snapshot['excluded_keys']:
                raise ValueError('终审内容已变化或行不存在，请刷新')
            now = utc_now()
            before = connection.execute('SELECT values_json FROM meeting_cells WHERE month=? AND row_key=?', (month,key)).fetchone()
            from app.services.summary_review import effective_amounts
            item = next((i for i in snapshot['items'] if (f"fixed:{FIXED_ROWS[i['form_type']]}" if i['form_type'] in FIXED_ROWS else f"batch:{i['batch_id']}") == key), None)
            checked = effective_amounts(key, item['form_type'] if item else None, item['total'] if item else None, item['components'] if item else None, values)
            if Decimal(checked['total']) < 0:
                raise ValueError('应发金额不能为负数')
            if key.startswith('supplement:') and values.get('other') is not None and Decimal(values['other']) < 0:
                raise ValueError('增补条目其他金额不能为负数')
            encoded = json.dumps(values,ensure_ascii=False)
            connection.execute('INSERT OR REPLACE INTO meeting_cells VALUES (?,?,?,?,?,?)',(month,key,encoded,reason,actor,now))
            if reason:
                self._append_meeting_reason(connection, month, key, reason, actor, now)
            connection.execute('INSERT INTO audit_events (id,actor,action,object_type,object_id,before_json,after_json,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (str(uuid4()),actor,'MEETING_CELLS_REVISED','month',month,before['values_json'] if before else None,json.dumps({'key':key,'values':values,'reason':reason},ensure_ascii=False),now))
        return self.final_review_snapshot(month)

    def _append_meeting_reason(self, connection, month, key, reason, actor, now):
        from app.services.summary_review import DEFAULT_RULE_NOTES
        if key.startswith('supplement:'):
            row = connection.execute('SELECT remark FROM supplemental_rows WHERE month=? AND id=?', (month,key.split(':',1)[1])).fetchone()
            text = row['remark'] if row else ''
            if reason not in text.splitlines():
                text = text + ('\n' if text else '') + reason
                if len(text) > 5000:
                    raise ValueError('备注总长度不能超过5000字，请先整理备注')
                connection.execute('UPDATE supplemental_rows SET remark=?,updated_at=? WHERE month=? AND id=?', (text,now,month,key.split(':',1)[1]))
            return
        row = connection.execute('SELECT text,revision FROM summary_remarks WHERE month=? AND row_key=?', (month,key)).fetchone()
        default = DEFAULT_RULE_NOTES.get(int(key.split(':')[1]), '') if key.startswith('fixed:') else DEFAULT_RULE_NOTES.get(20, '')
        text = row['text'] if row else default
        if reason in text.splitlines():
            return
        text = text + ('\n' if text else '') + reason
        if len(text) > 5000:
            raise ValueError('备注总长度不能超过5000字，请先整理备注')
        connection.execute('INSERT OR REPLACE INTO summary_remarks VALUES (?,?,?,?,?,?)',
                           (month,key,text,(row['revision'] if row else 0)+1,actor,now))

    def return_to_secondary(self, month, key, revision, reason, actor):
        from app.services.summary_review import FIXED_ROWS
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            snapshot = self._final_review_snapshot(connection, month)
            if snapshot['revision'] != revision:
                raise ValueError('终审内容已变化，请刷新')
            item = next((i for i in snapshot['items'] if (f"fixed:{FIXED_ROWS[i['form_type']]}" if i['form_type'] in FIXED_ROWS else f"batch:{i['batch_id']}") == key), None)
            if not item:
                raise ValueError('只有已复审通过的申报可以退回复审')
            calc_id, bid = item['calculation_id'], item['batch_id']
            # The initial amount remains available when secondary review repeats.
            initial = connection.execute('SELECT 1 FROM initial_reviews WHERE calculation_id=?', (calc_id,)).fetchone()
            if not initial:
                run = connection.execute('SELECT rounded_total_fen FROM calculation_runs WHERE id=?', (calc_id,)).fetchone()
                connection.execute('INSERT INTO initial_reviews VALUES (?,?,?,?,?)', (calc_id,run['rounded_total_fen'],'终审退回，恢复初审核算依据',actor,utc_now()))
            now = utc_now()
            connection.execute("UPDATE calculation_runs SET status='CALCULATED',confirmed_total_fen=NULL,confirmed_by=NULL,confirmed_at=NULL WHERE id=?", (calc_id,))
            connection.execute("UPDATE settlement_batches SET status='INITIAL_REVIEWED',revision=revision+1,updated_at=? WHERE id=?", (now,bid))
            # Do not silently reapply a disputed meeting override after a new review.
            connection.execute('DELETE FROM meeting_cells WHERE month=? AND row_key=?', (month,key))
            connection.execute('DELETE FROM meeting_approvals WHERE month=?', (month,))
            connection.execute('INSERT INTO audit_events (id,batch_id,actor,action,object_type,object_id,after_json,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (str(uuid4()),bid,actor,'FINAL_RETURNED_TO_SECONDARY','batch',bid,json.dumps({'reason':reason},ensure_ascii=False),now))
        return self.get_batch(bid)

    def delete_export(self, export_id, actor):
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM export_artifacts WHERE id=?', (export_id,)).fetchone()
            if not row:
                return None
            connection.execute('DELETE FROM export_artifacts WHERE id=?', (export_id,))
            connection.execute('INSERT INTO audit_events (id,actor,action,object_type,object_id,created_at) VALUES (?,?,?,?,?,?)',
                               (str(uuid4()),actor,'EXPORT_DELETED','export',export_id,utc_now()))
        return dict(row)

    def save_supplemental_row(self, month, row_id, values, documents, revision, actor, delete=False):
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            snapshot = self._final_review_snapshot(connection, month)
            if snapshot['revision'] != revision:
                raise ValueError('终审内容已变化，请刷新')
            before = connection.execute('SELECT * FROM supplemental_rows WHERE id=? AND month=?',(row_id,month)).fetchone() if row_id else None
            if row_id and before is None:
                raise ValueError('增补行不存在')
            row_id = row_id or str(uuid4())
            now = utc_now()
            if delete:
                connection.execute('DELETE FROM supplemental_rows WHERE id=?',(row_id,))
                connection.execute('DELETE FROM meeting_cells WHERE month=? AND row_key=?',(month,'supplement:'+row_id))
                after = None
            else:
                after = dict(before) if before else {'id':row_id,'month':month,'created_at':now,'attachments_json':'[]'}
                if values is not None:
                    after.update(values)
                    cells = connection.execute('SELECT values_json FROM meeting_cells WHERE month=? AND row_key=?',(month,'supplement:'+row_id)).fetchone()
                    if cells:
                        overrides = json.loads(cells['values_json'])
                        overrides['other'] = str(values['other'])
                        connection.execute('UPDATE meeting_cells SET values_json=?,updated_at=? WHERE month=? AND row_key=?',
                                           (json.dumps(overrides,ensure_ascii=False),now,month,'supplement:'+row_id))
                attachments = json.loads(after['attachments_json'])
                if len(attachments) + len(documents) > 30:
                    raise ValueError('单行最多上传30个附件')
                hashes = {d['sha256'] for d in attachments}
                for document in documents:
                    if document['sha256'] in hashes:
                        raise ValueError('同一增补行不能重复上传相同附件')
                    hashes.add(document['sha256'])
                    attachments.append(dict(document, id=str(uuid4())))
                after.update(attachments_json=json.dumps(attachments,ensure_ascii=False),actor=actor,updated_at=now)
                connection.execute('INSERT OR REPLACE INTO supplemental_rows VALUES (:id,:month,:label,:other,:remark,:attachments_json,:actor,:created_at,:updated_at)',after)
            connection.execute('INSERT INTO audit_events (id,actor,action,object_type,object_id,before_json,after_json,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (str(uuid4()),actor,'SUPPLEMENTAL_ROW_DELETED' if delete else 'SUPPLEMENTAL_ROW_SAVED','month',month,json.dumps(dict(before),ensure_ascii=False) if before else None,json.dumps(after,ensure_ascii=False),now))
        return self.final_review_snapshot(month)

    def supplemental_attachment(self, month, row_id, attachment_id):
        row = self.supplemental_row(month,row_id)
        return next((d for d in json.loads(row['attachments_json']) if d['id']==attachment_id),None) if row else None

    def supplemental_row(self, month, row_id):
        with self.connection() as connection:
            row = connection.execute('SELECT * FROM supplemental_rows WHERE month=? AND id=?',(month,row_id)).fetchone()
            if row:
                return dict(row)
            archive = connection.execute('SELECT row_json FROM archived_supplemental_rows WHERE month=? AND id=?',(month,row_id)).fetchone()
            return json.loads(archive['row_json']) if archive else None

    def initial_review(self, calculation_id, total, reason, actor):
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            run = connection.execute('SELECT * FROM calculation_runs WHERE id=?', (calculation_id,)).fetchone()
            if not run:
                raise ValueError('计算记录不存在')
            batch = connection.execute('SELECT * FROM settlement_batches WHERE id=?', (run['batch_id'],)).fetchone()
            latest = connection.execute('SELECT id FROM calculation_runs WHERE batch_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (batch['id'],)).fetchone()
            if batch['status'] not in {'CALCULATED','MANUAL_APPROVAL_REQUIRED'} or run['status'] not in {'CALCULATED','MANUAL_APPROVAL_REQUIRED'} or latest['id'] != calculation_id:
                raise ValueError('该申报已初审、退回或更新，请刷新')
            amount = yuan_to_fen(total) if total is not None else run['rounded_total_fen']
            now = utc_now()
            connection.execute('INSERT OR REPLACE INTO initial_reviews VALUES (?,?,?,?,?)', (calculation_id,amount,reason,actor,now))
            connection.execute("UPDATE settlement_batches SET status='INITIAL_REVIEWED',revision=revision+1,updated_at=? WHERE id=?", (now,batch['id']))
            connection.execute('INSERT INTO audit_events (id,batch_id,actor,action,object_type,object_id,after_json,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (str(uuid4()),batch['id'],actor,'INITIAL_REVIEW_APPROVED','calculation',calculation_id,json.dumps({'total':fen_to_yuan(amount),'reason':reason},ensure_ascii=False),now))
        return self.get_initial_review(batch['id'])

    def get_initial_review(self, batch_id):
        with self.connection() as connection:
            row = connection.execute('''SELECT i.* FROM initial_reviews i JOIN calculation_runs c ON c.id=i.calculation_id
                WHERE c.batch_id=? AND c.status NOT IN ('WITHDRAWN','SUPERSEDED') ORDER BY i.created_at DESC LIMIT 1''', (batch_id,)).fetchone()
            return dict(row) | {'total':fen_to_yuan(row['total_fen'])} if row else None

    def review_notes(self, batch_id):
        with self.connection() as connection:
            return {r['stage']:dict(r) for r in connection.execute('SELECT * FROM review_notes WHERE batch_id=?',(batch_id,))}

    def save_review_note(self, batch_id, stage, text, revision, calculation_id, actor):
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            batch = connection.execute('SELECT * FROM settlement_batches WHERE id=?',(batch_id,)).fetchone()
            latest = connection.execute('SELECT id FROM calculation_runs WHERE batch_id=? ORDER BY created_at DESC,id DESC LIMIT 1',(batch_id,)).fetchone()
            allowed = {'INITIAL_REVIEWED'} if stage == 'secondary' else {'CALCULATED','MANUAL_APPROVAL_REQUIRED'}
            if not batch or batch['status'] not in allowed or not latest or latest['id'] != calculation_id:
                raise ValueError('审核阶段已变化，请刷新后保存意见')
            old = connection.execute('SELECT revision FROM review_notes WHERE batch_id=? AND stage=?',(batch_id,stage)).fetchone()
            if revision != (old['revision'] if old else 0):
                raise ValueError('审核意见已被其他窗口修改，请刷新核对')
            now = utc_now()
            saved = dict(batch_id=batch_id,stage=stage,text=text,revision=revision+1,calculation_id=calculation_id,actor=actor,updated_at=now)
            connection.execute('INSERT OR REPLACE INTO review_notes VALUES (?,?,?,?,?,?,?)',tuple(saved.values()))
        return saved

    def return_to_initial(self, batch_id):
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            batch = connection.execute('SELECT * FROM settlement_batches WHERE id=?',(batch_id,)).fetchone()
            run = connection.execute('SELECT * FROM calculation_runs WHERE batch_id=? ORDER BY created_at DESC,id DESC LIMIT 1',(batch_id,)).fetchone()
            if not batch or batch['status'] != 'INITIAL_REVIEWED' or not run or run['status'] not in {'CALCULATED','MANUAL_APPROVAL_REQUIRED'}:
                raise ValueError('审核状态已变化，请刷新')
            connection.execute('UPDATE settlement_batches SET status=?,revision=revision+1,updated_at=? WHERE id=?',(run['status'],utc_now(),batch_id))
        return self.get_batch(batch_id)

    def exclude_row(self, month, key, revision, actor):
        from app.services.summary_review import FIXED_ROWS
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            snapshot = self._final_review_snapshot(connection,month)
            if snapshot['revision'] != revision or key not in snapshot['effective_amounts']:
                raise ValueError('终审内容已变化或条目不存在，请刷新')
            item = next((i for i in snapshot['items'] if (f"fixed:{FIXED_ROWS[i['form_type']]}" if i['form_type'] in FIXED_ROWS else f"batch:{i['batch_id']}") == key),None)
            now = utc_now()
            connection.execute('INSERT INTO excluded_rows VALUES (?,?,?,?,?)',(month,key,item['batch_id'] if item else None,actor,now))
            if item:
                connection.execute("UPDATE calculation_runs SET status='EXCLUDED' WHERE id=?",(item['calculation_id'],))
            connection.execute('INSERT INTO audit_events (id,batch_id,actor,action,object_type,object_id,after_json,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (str(uuid4()),item['batch_id'] if item else None,actor,'MONTH_ROW_REMOVED','month',month,json.dumps({'key':key,'retained_source':True}),now))
        return self.final_review_snapshot(month)

    def approve_meeting(self, month, revision, actor):
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            snapshot = self._final_review_snapshot(connection, month)
            if not snapshot['export_anchor'] or snapshot['revision'] != revision:
                raise ValueError('终审内容为空或已变化，请刷新核对后确认')
            now = utc_now()
            connection.execute('INSERT OR REPLACE INTO meeting_approvals VALUES (?,?,?,?)', (month,revision,actor,now))
            connection.execute('INSERT INTO audit_events (id,actor,action,object_type,object_id,after_json,created_at) VALUES (?,?,?,?,?,?,?)',
                (str(uuid4()),actor,'MEETING_APPROVED','month',month,json.dumps({'revision':revision,'total':snapshot['total']}),now))
        return self.final_review_snapshot(month)

    def final_review_snapshot(self, month):
        with self.connection() as connection:
            connection.execute('BEGIN')
            return self._final_review_snapshot(connection, month)

    def revise_final_amount(self, calculation_id, total, reason, revision, actor):
        """Create a new calculation snapshot; previous archived snapshots stay intact."""
        new_id, now = str(uuid4()), utc_now()
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            run = connection.execute('SELECT * FROM calculation_runs WHERE id=?', (calculation_id,)).fetchone()
            if not run:
                raise ValueError('计算记录不存在')
            batch = connection.execute('SELECT * FROM settlement_batches WHERE id=?', (run['batch_id'],)).fetchone()
            snapshot = self._final_review_snapshot(connection, batch['month'])
            if revision != snapshot['revision'] or calculation_id not in {i['calculation_id'] for i in snapshot['items']}:
                raise ValueError('终审内容已变化，请刷新后修改')
            values = dict(run)
            values.update(id=new_id,confirmed_total_fen=yuan_to_fen(total),confirmed_by=actor,confirmed_at=now,created_at=now)
            columns = ','.join(values)
            connection.execute(f"INSERT INTO calculation_runs ({columns}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            for row in connection.execute('SELECT * FROM calculation_lines WHERE calculation_id=?', (calculation_id,)).fetchall():
                line = dict(row)
                line.update(id=str(uuid4()),calculation_id=new_id)
                connection.execute(f"INSERT INTO calculation_lines ({','.join(line)}) VALUES ({','.join('?' for _ in line)})", tuple(line.values()))
            connection.execute("UPDATE calculation_runs SET status='SUPERSEDED' WHERE id=?", (calculation_id,))
            connection.execute("UPDATE settlement_batches SET status='CONFIRMED',revision=revision+1,updated_at=? WHERE id=?", (now,batch['id']))
            connection.execute("""INSERT INTO manual_overrides
                (id,batch_id,calculation_id,field,before_value,after_value,reason,approver,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(uuid4()),batch['id'],new_id,'confirmed_total',fen_to_yuan(run['confirmed_total_fen']),total,reason,actor,now))
            connection.execute("""INSERT INTO audit_events
                (id,batch_id,actor,action,object_type,object_id,before_json,after_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(uuid4()),batch['id'],actor,'FINAL_AMOUNT_REVISED','calculation',new_id,
                 json.dumps({'calculation_id':calculation_id,'total':fen_to_yuan(run['confirmed_total_fen'])}),
                 json.dumps({'total':total,'reason':reason},ensure_ascii=False),now))
        return self.get_calculation(new_id)

    def get_export(self, export_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return row_to_dict(connection.execute("SELECT * FROM export_artifacts WHERE id=?", (export_id,)).fetchone())

    def list_exports(self) -> list[dict[str, Any]]:
        """List immutable export artifacts with the business context needed by the UI."""
        query = """
            SELECT
                export_artifacts.*,
                calculation_runs.batch_id,
                calculation_runs.status AS calculation_status,
                calculation_runs.confirmed_total_fen,
                calculation_runs.confirmed_by,
                calculation_runs.confirmed_at,
                settlement_batches.batch_no,
                settlement_batches.month AS batch_month,
                settlement_batches.project_owner,
                settlement_batches.form_type
            FROM export_artifacts
            JOIN calculation_runs ON calculation_runs.id=export_artifacts.calculation_id
            JOIN settlement_batches ON settlement_batches.id=calculation_runs.batch_id
            ORDER BY export_artifacts.created_at DESC
        """
        with self.connection() as connection:
            rows = [dict(row) for row in connection.execute(query).fetchall()]
        for item in rows:
            anchor_total_fen = item.pop("confirmed_total_fen")
            report_total_fen = item.pop("report_total_fen")
            item["month"] = item.get("report_month") or item.pop("batch_month")
            item.pop("batch_month", None)
            item["confirmed_total"] = fen_to_yuan(
                report_total_fen if report_total_fen is not None else anchor_total_fen
            )
            item["project_count"] = item['project_count'] if item.get('project_count') is not None else 1
            item["file_name"] = Path(item["file_path"]).name
        return rows

    def list_confirmed_calculations(self, month: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            ids = [row["id"] for row in connection.execute(
                """SELECT calculation_runs.id FROM calculation_runs
                JOIN settlement_batches ON settlement_batches.id=calculation_runs.batch_id
                WHERE settlement_batches.month=? AND calculation_runs.status='CONFIRMED'
                AND settlement_batches.superseded_by_batch_id IS NULL
                AND NOT EXISTS(SELECT 1 FROM excluded_rows e WHERE e.batch_id=settlement_batches.id)
                ORDER BY calculation_runs.confirmed_at""",
                (month,),
            ).fetchall()]
        return [calculation for calculation_id in ids if (calculation := self.get_calculation(calculation_id))]

    def append_audit(
        self,
        *,
        actor: str,
        action: str,
        object_type: str,
        object_id: str | None = None,
        batch_id: str | None = None,
        before: Any = None,
        after: Any = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        values = {
            "id": str(uuid4()), "batch_id": batch_id, "actor": actor, "action": action,
            "object_type": object_type, "object_id": object_id,
            "before_json": json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            "after_json": json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            "request_id": request_id, "created_at": utc_now(),
        }
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO audit_events
                (id,batch_id,actor,action,object_type,object_id,before_json,after_json,request_id,created_at)
                VALUES (:id,:batch_id,:actor,:action,:object_type,:object_id,:before_json,:after_json,:request_id,:created_at)""",
                values,
            )
        return values

    def list_audit(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM audit_events WHERE batch_id=? ORDER BY created_at", (batch_id,)).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["before"] = json.loads(item.pop("before_json")) if item["before_json"] else None
            item["after"] = json.loads(item.pop("after_json")) if item["after_json"] else None
            output.append(item)
        return output

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connection() as connection:
            row = connection.execute("SELECT value_json FROM system_settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def set_setting(self, key: str, value: Any, actor: str) -> dict[str, Any]:
        values = {"key": key, "value_json": json.dumps(value, ensure_ascii=False, default=str), "updated_by": actor, "updated_at": utc_now()}
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO system_settings (key,value_json,updated_by,updated_at)
                VALUES (:key,:value_json,:updated_by,:updated_at)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                values,
            )
        return {"key": key, "value": value, "updated_by": actor, "updated_at": values["updated_at"]}

    def get_idempotent_response(self, key: str, scope: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT response_json FROM idempotency_keys WHERE key=? AND scope=?", (key, scope)).fetchone()
        return json.loads(row["response_json"]) if row else None

    def save_idempotent_response(self, key: str, scope: str, response: dict[str, Any]) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO idempotency_keys (key,scope,response_json,created_at) VALUES (?,?,?,?)",
                (key, scope, json.dumps(response, ensure_ascii=False, default=str), utc_now()),
            )
