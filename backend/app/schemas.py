from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class BusinessCategory(StrEnum):
    PROJECT = "PROJECT"
    EQUIPMENT = "EQUIPMENT"
    TECHNICAL_SERVICE = "TECHNICAL_SERVICE"
    GROUP = "GROUP"
    VEHICLE = "VEHICLE"
    REPAIR = "REPAIR"


class FormType(StrEnum):
    STD_I = "STD_I"
    STD_K = "STD_K"
    STD_L = "STD_L"
    STD_M = "STD_M"
    STD_N = "STD_N"
    STD_O = "STD_O"
    STD_Q = "STD_Q"
    STD_PROJECT = "STD_PROJECT"
    STD_SERVICE = "STD_SERVICE"
    PROJECT_SETTLEMENT = "PROJECT_SETTLEMENT"
    PROJECT_BUDGET = "PROJECT_BUDGET"
    EQUIPMENT_USAGE = "EQUIPMENT_USAGE"
    EFP_SERVICE = "EFP_SERVICE"
    SCREEN_SERVICE = "SCREEN_SERVICE"
    MEETING_AUDIO = "MEETING_AUDIO"
    AUDIO_POST = "AUDIO_POST"
    NONLINEAR = "NONLINEAR"
    DRIVER = "DRIVER"
    SONY_REPAIR = "SONY_REPAIR"


class BatchCreate(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    project_owner: str | None = Field(default=None, max_length=80)
    business_category: BusinessCategory
    form_type: FormType

    @field_validator("project_owner")
    @classmethod
    def normalize_project_owner(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class BatchRecord(BatchCreate):
    id: str
    batch_no: str
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


class ExtractedFieldInput(BaseModel):
    path: str = Field(min_length=1, max_length=160)
    value_raw: str | None = None
    value_normalized: Any = None
    confidence: Decimal = Field(ge=0, le=1)
    source_document_id: str | None = None
    page_no: int | None = Field(default=None, ge=1)
    bounding_box: list[Decimal] | None = None
    confirmed: bool = False


class FieldPatch(BaseModel):
    revision: int = Field(ge=0)
    fields: list[ExtractedFieldInput]


class CalculateRequest(BaseModel):
    rule_code: str
    inputs: dict[str, Any]


class ConfirmCalculationRequest(BaseModel):
    replace_batch_id: str | None = Field(default=None, max_length=80)
    reviewer: str | None = Field(default=None, max_length=80)
    override_total: Decimal | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def require_override_reason(cls, value: str | None, info):
        override = info.data.get("override_total")
        if override is not None and not (value and value.strip()):
            raise ValueError("审核人修改金额时必须填写原因")
        return value.strip() if value else None


class ExportRequest(BaseModel):
    mode: str = Field(pattern=r"^(formula|values)$")
    final_review_revision: str | None = Field(default=None, max_length=64)


class MeetingApprovalRequest(BaseModel):
    revision: str = Field(min_length=64, max_length=64)


class MeetingCellsPatch(MeetingApprovalRequest):
    key: str = Field(max_length=100)
    total: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    other: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    weight: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=4)
    actual: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    reason: str = Field(default='', max_length=500)

    @field_validator('reason')
    @classmethod
    def require_reason(cls, value):
        return value.strip()


class MeetingReturnRequest(MeetingApprovalRequest):
    key: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def return_reason(cls, value):
        if not value.strip():
            raise ValueError('请填写退回原因')
        return value.strip()


class FinalAmountPatch(BaseModel):
    total: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    reason: str = Field(default='', max_length=500)
    revision: str = Field(min_length=64, max_length=64)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value):
        return value.strip()


class RemarkPatch(BaseModel):
    text: str = Field(max_length=5000)
    revision: int = Field(ge=0)


class MonthlyRemarkPatch(RemarkPatch):
    key: str = Field(max_length=100)


class ReturnBatchRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ReviewNotePatch(BaseModel):
    stage: str = Field(pattern=r'^(initial|secondary)$')
    text: str = Field(max_length=500)
    revision: int = Field(ge=0)
    calculation_id: str = Field(min_length=1,max_length=80)


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class SystemSettingsPatch(BaseModel):
    application_start: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    application_end: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    confidence_threshold: Decimal | None = Field(default=None, ge=Decimal("0.50"), le=Decimal("1"))
    retention_months: int | None = Field(default=None, ge=1, le=120)


class SupplementalRowPatch(MeetingApprovalRequest):
    label: str = Field(min_length=1, max_length=200)
    other: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    remark: str = Field(default='', max_length=5000)

    @field_validator('label')
    @classmethod
    def nonblank_label(cls, value):
        if not value.strip():
            raise ValueError('请填写增补行名称')
        return value.strip()
