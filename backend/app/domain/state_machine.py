from __future__ import annotations

from enum import StrEnum


class BatchStatus(StrEnum):
    DRAFT = "DRAFT"
    UPLOADED = "UPLOADED"
    EXTRACTING = "EXTRACTING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    WAITING_FOR_EVIDENCE = "WAITING_FOR_EVIDENCE"
    READY_TO_CALCULATE = "READY_TO_CALCULATE"
    CALCULATED = "CALCULATED"
    MANUAL_APPROVAL_REQUIRED = "MANUAL_APPROVAL_REQUIRED"
    INITIAL_REVIEWED = "INITIAL_REVIEWED"
    CONFIRMED = "CONFIRMED"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"


ALLOWED_TRANSITIONS: dict[BatchStatus, set[BatchStatus]] = {
    BatchStatus.DRAFT: {BatchStatus.UPLOADED, BatchStatus.FAILED},
    BatchStatus.UPLOADED: {BatchStatus.EXTRACTING, BatchStatus.FAILED},
    BatchStatus.EXTRACTING: {BatchStatus.REVIEW_REQUIRED, BatchStatus.FAILED},
    BatchStatus.REVIEW_REQUIRED: {
        BatchStatus.READY_TO_CALCULATE,
        BatchStatus.WAITING_FOR_EVIDENCE,
        BatchStatus.FAILED,
    },
    BatchStatus.WAITING_FOR_EVIDENCE: {BatchStatus.UPLOADED, BatchStatus.FAILED},
    BatchStatus.READY_TO_CALCULATE: {
        BatchStatus.CALCULATED,
        BatchStatus.MANUAL_APPROVAL_REQUIRED,
        BatchStatus.FAILED,
    },
    BatchStatus.CALCULATED: {
        BatchStatus.INITIAL_REVIEWED,
        BatchStatus.REVIEW_REQUIRED,
        BatchStatus.FAILED,
    },
    BatchStatus.MANUAL_APPROVAL_REQUIRED: {
        BatchStatus.INITIAL_REVIEWED,
        BatchStatus.REVIEW_REQUIRED,
        BatchStatus.FAILED,
    },
    BatchStatus.CONFIRMED: {BatchStatus.EXPORTED, BatchStatus.FAILED},
    BatchStatus.INITIAL_REVIEWED: {BatchStatus.CONFIRMED, BatchStatus.CALCULATED, BatchStatus.MANUAL_APPROVAL_REQUIRED},
    BatchStatus.EXPORTED: set(),
    BatchStatus.FAILED: {
        BatchStatus.UPLOADED,
        BatchStatus.EXTRACTING,
        BatchStatus.REVIEW_REQUIRED,
    },
}


class InvalidTransition(ValueError):
    pass


def transition(current: BatchStatus | str, target: BatchStatus | str) -> BatchStatus:
    current_status = BatchStatus(current)
    target_status = BatchStatus(target)
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise InvalidTransition(f"不允许从 {current_status} 进入 {target_status}")
    return target_status
