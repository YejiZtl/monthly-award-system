from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


LABEL_PATHS = {
    "项目名称": "project.name",
    "节目名称": "project.name",
    "合同金额": "project.contract_income",
    "合同收入": "project.contract_income",
    "支出总计": "project.total_expense",
    "支出合计": "project.total_expense",
    "节余": "project.gross_profit_candidate",
    "项目负责人": "project.owner",
    "负责人": "project.owner",
    "申报部门": "project.department",
    "部门": "project.department",
    "结算月份": "project.month",
    "设备使用费": "equipment.fee",
    "设备费": "equipment.fee",
    "技术服务费": "technical_service.fee",
    "项目毛利": "project.gross_profit",
    "毛利": "project.gross_profit",
    "场次数": "meeting.session_count",
    "场次": "meeting.session_count",
    "车型": "vehicle.type",
    "当月累计里程": "vehicle.monthly_km",
    "累计里程": "vehicle.monthly_km",
    "半天数": "vehicle.half_days",
    "全天数": "vehicle.full_days",
    "内部维修申报价": "repair.internal_quote_total",
    "内部申报价": "repair.internal_quote_total",
    "对外维修总计": "repair.external_repair_total",
    "零件采购费": "repair.parts_cost",
}

PROJECT_BUDGET_COMPONENT_FIELDS = (
    "project.group_equipment_labor_fee",
    "project.labor_fee",
    "project.equipment_rental_service_fee",
    "project.stage_production_fee",
)
PROJECT_BUDGET_MISC_FIELDS = (
    "project.misc_handling_fee",
    "project.misc_consumables_fee",
    "project.misc_courier_fee",
    "project.misc_venue_fee",
    "project.misc_other_fee",
)
PROJECT_BUDGET_FIELDS = (
    "project.name",
    "project.contract_income",
    *PROJECT_BUDGET_COMPONENT_FIELDS,
    *PROJECT_BUDGET_MISC_FIELDS,
    "project.misc_expense",
    "project.total_expense",
    "project.gross_profit_candidate",
)
PROJECT_SETTLEMENT_INTERNAL_FIELDS = (
    "project.internal_group_equipment_fee",
    "project.internal_labor_fee",
)
PROJECT_SETTLEMENT_TRAVEL_FIELDS = (
    "project.travel_meal_fee",
    "project.travel_lodging_fee",
    "project.travel_transportation_fee",
)
PROJECT_SETTLEMENT_EXTERNAL_COMPONENT_FIELDS = (
    "project.external_labor_fee",
    "project.travel_expense",
    "project.equipment_rental_service_fee",
    "project.stage_production_fee",
    "project.equipment_purchase_fee",
    "project.misc_expense",
)
PROJECT_SETTLEMENT_FIELDS = (
    "project.name",
    "project.contract_income",
    *PROJECT_SETTLEMENT_INTERNAL_FIELDS,
    "project.internal_cost_total",
    "project.external_labor_fee",
    *PROJECT_SETTLEMENT_TRAVEL_FIELDS,
    "project.travel_expense",
    "project.equipment_rental_service_fee",
    "project.stage_production_fee",
    "project.equipment_purchase_fee",
    *PROJECT_BUDGET_MISC_FIELDS,
    "project.misc_expense",
    "project.external_cost_total",
    "project.total_expense",
    "project.gross_profit_candidate",
)

CRITICAL_FIELDS = {
    "PROJECT_SETTLEMENT": set(PROJECT_SETTLEMENT_FIELDS),
    "PROJECT_BUDGET": set(PROJECT_BUDGET_FIELDS),
    "EQUIPMENT_USAGE": {"equipment.fee"},
    "EFP_SERVICE": {"equipment.fee", "technical_service.fee"},
    "SCREEN_SERVICE": {"technical_service.fee"},
    "MEETING_AUDIO": {"meeting.session_count"},
    "AUDIO_POST": {"equipment.fee", "project.gross_profit"},
    "NONLINEAR": {"equipment.fee", "project.gross_profit"},
    "DRIVER": {"vehicle.type", "vehicle.monthly_km"},
    "SONY_REPAIR": {"repair.internal_quote_total", "repair.external_repair_total", "repair.parts_cost"},
}


def _normalize_value(path: str, value: Any) -> Any:
    if value is None:
        return None
    if path in {
        "project.contract_income",
        "project.total_expense",
        "project.gross_profit",
        "project.gross_profit_candidate",
        "project.group_equipment_labor_fee",
        "project.labor_fee",
        "project.equipment_rental_service_fee",
        "project.stage_production_fee",
        "project.misc_expense",
        "project.misc_handling_fee",
        "project.misc_consumables_fee",
        "project.misc_courier_fee",
        "project.misc_venue_fee",
        "project.misc_other_fee",
        "project.internal_group_equipment_fee",
        "project.internal_labor_fee",
        "project.internal_cost_total",
        "project.external_labor_fee",
        "project.travel_meal_fee",
        "project.travel_lodging_fee",
        "project.travel_transportation_fee",
        "project.travel_expense",
        "project.equipment_purchase_fee",
        "project.external_cost_total",
        "equipment.fee",
        "technical_service.fee",
        "meeting.session_count",
        "vehicle.monthly_km",
        "vehicle.half_days",
        "vehicle.full_days",
        "repair.internal_quote_total",
        "repair.external_repair_total",
        "repair.parts_cost",
    }:
        cleaned = re.sub(r"[^\d.\-]", "", str(value))
        try:
            return str(Decimal(cleaned))
        except (InvalidOperation, ValueError):
            return str(value).strip()
    return str(value).strip()


def _match_label(text: str) -> tuple[str, str | None] | None:
    normalized = re.sub(r"\s+", "", text)
    for label, path in LABEL_PATHS.items():
        if label in normalized:
            inline_value = None
            if "：" in text or ":" in text:
                parts = re.split(r"[：:]", text, maxsplit=1)
                inline_value = parts[1].strip() if len(parts) > 1 else None
            return path, inline_value
    return None


def _plausible_ocr_value(path: str, value: Any) -> bool:
    text = str(value or "").strip()
    if not text or "%" in text or path in text or "第1页" in text:
        return False
    if path in {
        "project.contract_income", "project.total_expense", "project.gross_profit",
        "project.gross_profit_candidate", "equipment.fee", "technical_service.fee",
        "project.group_equipment_labor_fee", "project.labor_fee",
        "project.equipment_rental_service_fee", "project.stage_production_fee",
        "project.misc_expense", "project.misc_handling_fee", "project.misc_consumables_fee",
        "project.misc_courier_fee", "project.misc_venue_fee", "project.misc_other_fee",
        "project.internal_group_equipment_fee", "project.internal_labor_fee",
        "project.internal_cost_total", "project.external_labor_fee",
        "project.travel_meal_fee", "project.travel_lodging_fee",
        "project.travel_transportation_fee", "project.travel_expense",
        "project.equipment_purchase_fee", "project.external_cost_total",
        "meeting.session_count", "vehicle.monthly_km", "vehicle.half_days", "vehicle.full_days",
        "repair.internal_quote_total", "repair.external_repair_total", "repair.parts_cost",
    }:
        number = re.sub(r"[^\d.\-]", "", text)
        if not number or number in {"-", ".", "-."}:
            return False
        try:
            Decimal(number)
        except InvalidOperation:
            return False
    return True


def _field_record(
    field_path: str,
    value: Any,
    document_id: str,
    *,
    page_no: int = 1,
    confidence: str = "0.98",
    bounding_box: list[float] | None = None,
) -> dict[str, Any]:
    return {
        "path": field_path,
        "value_raw": None if value is None else str(value),
        "value_normalized": None if value is None else _normalize_value(field_path, value),
        "confidence": confidence if value is not None else "0",
        "source_document_id": document_id,
        "page_no": page_no,
        "bounding_box": bounding_box,
        "confirmed": False,
    }


def _decimal_value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = str(value).strip()
    if not text or text.startswith("=") or "%" in text:
        return None
    match = re.search(r"-?\d[\d,，]*(?:\.\d+)?", text.replace(" ", ""))
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", "").replace("，", ""))
    except InvalidOperation:
        return None


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _sheet_row_text(sheet: Any, row: int) -> str:
    return "".join(_compact_text(sheet.cell(row=row, column=column).value) for column in range(1, sheet.max_column + 1))


def _xlsx_row_amount(data_sheet: Any, formula_sheet: Any, row: int) -> Decimal | None:
    for sheet in (data_sheet, formula_sheet):
        for column in range(sheet.max_column, 0, -1):
            value = _decimal_value(sheet.cell(row=row, column=column).value)
            if value is not None:
                return value
    return None


def _xlsx_detail_sum(
    data_sheet: Any,
    formula_sheet: Any,
    start_row: int,
    end_row: int,
    *,
    total_column: int = 6,
    multiplier_columns: tuple[int, ...] = (3, 5),
) -> Decimal | None:
    values: list[Decimal] = []
    for row in range(start_row, end_row + 1):
        total = _decimal_value(data_sheet.cell(row=row, column=total_column).value)
        if total is None:
            total = _decimal_value(formula_sheet.cell(row=row, column=total_column).value)
        if total is None:
            factors = [_decimal_value(formula_sheet.cell(row=row, column=column).value) for column in multiplier_columns]
            if all(factor is not None for factor in factors):
                total = Decimal("1")
                for factor in factors:
                    total *= factor if factor is not None else Decimal("0")
        if total is not None:
            values.append(total)
    return sum(values, Decimal("0")) if values else None


def _xlsx_section_amount(
    data_sheet: Any,
    formula_sheet: Any,
    start_row: int,
    end_row: int,
    subtotal_label: str,
    *,
    total_column: int = 6,
    multiplier_columns: tuple[int, ...] = (3, 5),
) -> Decimal | None:
    subtotal_rows = [
        row for row in range(start_row + 1, end_row)
        if subtotal_label in _sheet_row_text(formula_sheet, row)
    ]
    if subtotal_rows:
        subtotal_row = subtotal_rows[-1]
        amount = _xlsx_row_amount(data_sheet, formula_sheet, subtotal_row)
        if amount is not None:
            return amount
        return _xlsx_detail_sum(
            data_sheet, formula_sheet, start_row + 1, subtotal_row - 1,
            total_column=total_column, multiplier_columns=multiplier_columns,
        )
    return _xlsx_detail_sum(
        data_sheet, formula_sheet, start_row + 1, end_row - 1,
        total_column=total_column, multiplier_columns=multiplier_columns,
    )


def _project_name_from_budget_title(text: str) -> str | None:
    compact = _compact_text(text)
    bracketed = re.search(r"[《〈](.+?)[》〉]", compact)
    if bracketed and bracketed.group(1).upper() != "XXXX":
        return bracketed.group(1).strip()
    prefix = re.split(r"立项预算(?:支出)?明细表", compact, maxsplit=1)[0].strip("：:·-—_")
    return prefix or None


def _reconcile_project_budget(candidates: dict[str, dict[str, Any]], document_id: str, page_no: int = 1) -> None:
    def amount(path: str) -> Decimal | None:
        item = candidates.get(path)
        return _decimal_value(item.get("value_normalized")) if item else None

    misc_values = [amount(path) for path in PROJECT_BUDGET_MISC_FIELDS]
    if all(value is not None for value in misc_values):
        calculated_misc = sum((value for value in misc_values if value is not None), Decimal("0"))
        current = amount("project.misc_expense")
        if current != calculated_misc:
            candidates["project.misc_expense"] = _field_record(
                "project.misc_expense", calculated_misc, document_id, page_no=page_no, confidence="0.99"
            )

    expense_values = [amount(path) for path in PROJECT_BUDGET_COMPONENT_FIELDS]
    expense_values.append(amount("project.misc_expense"))
    if all(value is not None for value in expense_values):
        calculated_total = sum((value for value in expense_values if value is not None), Decimal("0"))
        current = amount("project.total_expense")
        if current != calculated_total:
            candidates["project.total_expense"] = _field_record(
                "project.total_expense", calculated_total, document_id, page_no=page_no, confidence="0.99"
            )

    contract_income = amount("project.contract_income")
    total_expense = amount("project.total_expense")
    if contract_income is not None and total_expense is not None:
        calculated_profit = contract_income - total_expense
        current = amount("project.gross_profit_candidate")
        if current != calculated_profit:
            candidates["project.gross_profit_candidate"] = _field_record(
                "project.gross_profit_candidate", calculated_profit, document_id, page_no=page_no, confidence="0.99"
            )


def recalculate_project_budget_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonicalize the three formula fields from the confirmed budget components."""
    candidates = {item["path"]: dict(item) for item in fields}
    first = next(iter(candidates.values()), {})
    document_id = str(first.get("source_document_id") or "")
    page_no = int(first.get("page_no") or 1)
    _reconcile_project_budget(candidates, document_id, page_no)
    confirmed_sources = {
        "project.misc_expense": PROJECT_BUDGET_MISC_FIELDS,
        "project.total_expense": (*PROJECT_BUDGET_COMPONENT_FIELDS, "project.misc_expense"),
        "project.gross_profit_candidate": ("project.contract_income", "project.total_expense"),
    }
    for field_path, source_paths in confirmed_sources.items():
        item = candidates.get(field_path)
        if item:
            item["confirmed"] = all(bool(candidates.get(path, {}).get("confirmed")) for path in source_paths)
    ordered = [candidates[path] for path in PROJECT_BUDGET_FIELDS if path in candidates]
    ordered.extend(item for path, item in candidates.items() if path not in PROJECT_BUDGET_FIELDS)
    return ordered


def _project_name_from_settlement_title(text: str) -> str | None:
    compact = _compact_text(text)
    bracketed = re.search(r"[《〈](.+?)[》〉]", compact)
    if bracketed and bracketed.group(1).upper() != "XXXX":
        return bracketed.group(1).strip()
    prefix = re.split(r"(?:项目)?报销明细表", compact, maxsplit=1)[0].strip("：:·-—_")
    return prefix or None


def _reconcile_project_settlement(candidates: dict[str, dict[str, Any]], document_id: str, page_no: int = 1) -> None:
    def amount(path: str) -> Decimal | None:
        item = candidates.get(path)
        return _decimal_value(item.get("value_normalized")) if item else None

    derived_sources = (
        ("project.internal_cost_total", PROJECT_SETTLEMENT_INTERNAL_FIELDS),
        ("project.travel_expense", PROJECT_SETTLEMENT_TRAVEL_FIELDS),
        ("project.misc_expense", PROJECT_BUDGET_MISC_FIELDS),
        ("project.external_cost_total", PROJECT_SETTLEMENT_EXTERNAL_COMPONENT_FIELDS),
        ("project.total_expense", ("project.internal_cost_total", "project.external_cost_total")),
    )
    for field_path, source_paths in derived_sources:
        values = [amount(path) for path in source_paths]
        if all(value is not None for value in values):
            calculated = sum((value for value in values if value is not None), Decimal("0"))
            if amount(field_path) != calculated:
                candidates[field_path] = _field_record(
                    field_path, calculated, document_id, page_no=page_no, confidence="0.99"
                )
    contract_income = amount("project.contract_income")
    total_expense = amount("project.total_expense")
    if contract_income is not None and total_expense is not None:
        calculated_profit = contract_income - total_expense
        if amount("project.gross_profit_candidate") != calculated_profit:
            candidates["project.gross_profit_candidate"] = _field_record(
                "project.gross_profit_candidate", calculated_profit, document_id, page_no=page_no, confidence="0.99"
            )


def recalculate_project_settlement_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = {item["path"]: dict(item) for item in fields}
    if not any(path in candidates for path in PROJECT_SETTLEMENT_INTERNAL_FIELDS):
        return [dict(item) for item in fields]
    first = next(iter(candidates.values()), {})
    document_id = str(first.get("source_document_id") or "")
    page_no = int(first.get("page_no") or 1)
    _reconcile_project_settlement(candidates, document_id, page_no)
    confirmed_sources = {
        "project.internal_cost_total": PROJECT_SETTLEMENT_INTERNAL_FIELDS,
        "project.travel_expense": PROJECT_SETTLEMENT_TRAVEL_FIELDS,
        "project.misc_expense": PROJECT_BUDGET_MISC_FIELDS,
        "project.external_cost_total": PROJECT_SETTLEMENT_EXTERNAL_COMPONENT_FIELDS,
        "project.total_expense": ("project.internal_cost_total", "project.external_cost_total"),
        "project.gross_profit_candidate": ("project.contract_income", "project.total_expense"),
    }
    for field_path, source_paths in confirmed_sources.items():
        item = candidates.get(field_path)
        if item:
            item["confirmed"] = all(bool(candidates.get(path, {}).get("confirmed")) for path in source_paths)
    ordered = [candidates[path] for path in PROJECT_SETTLEMENT_FIELDS if path in candidates]
    ordered.extend(item for path, item in candidates.items() if path not in PROJECT_SETTLEMENT_FIELDS)
    return ordered


def _extract_project_budget_xlsx(path: Path, document_id: str) -> list[dict[str, Any]]:
    formula_workbook = load_workbook(path, read_only=False, data_only=False)
    data_workbook = load_workbook(path, read_only=False, data_only=True)
    candidates: dict[str, dict[str, Any]] = {}
    section_aliases = (
        (("集团设备使用费及人工费", "集团设备使用费"), "project.group_equipment_labor_fee"),
        (("劳务费",), "project.labor_fee"),
        (("设备租赁费/技术服务费", "设备租赁费／技术服务费", "设备租赁费"), "project.equipment_rental_service_fee"),
        (("舞美制作费",), "project.stage_production_fee"),
    )
    misc_aliases = (
        (("搬运费",), "project.misc_handling_fee"),
        (("设备耗材费",), "project.misc_consumables_fee"),
        (("快递费",), "project.misc_courier_fee"),
        (("场租费",), "project.misc_venue_fee"),
        (("其它费用", "其他费用"), "project.misc_other_fee"),
    )

    for sheet_index, (formula_sheet, data_sheet) in enumerate(
        zip(formula_workbook.worksheets, data_workbook.worksheets), start=1
    ):
        rows = {row: _sheet_row_text(formula_sheet, row) for row in range(1, formula_sheet.max_row + 1)}
        if not any("立项预算" in text for text in rows.values()):
            continue

        for row, text in rows.items():
            if "立项预算" in text and "明细表" in text:
                name = _project_name_from_budget_title(text)
                if name:
                    candidates["project.name"] = _field_record("project.name", name, document_id, page_no=sheet_index)
                break
        for row, text in rows.items():
            if "合同金额" in text or "合同收入" in text:
                value = _xlsx_row_amount(data_sheet, formula_sheet, row)
                if value is not None:
                    candidates["project.contract_income"] = _field_record(
                        "project.contract_income", value, document_id, page_no=sheet_index
                    )
                break

        section_rows: list[tuple[int, str]] = []
        for aliases, field_path in section_aliases:
            row = next((row for row, text in rows.items() if any(alias in text for alias in aliases)), None)
            if row is not None:
                section_rows.append((row, field_path))
        misc_row = next((row for row, text in rows.items() if "杂费" in text), None)
        total_row = next((row for row, text in rows.items() if "支出总计" in text or "支出合计" in text), formula_sheet.max_row + 1)

        ordered_boundaries = sorted([row for row, _ in section_rows] + ([misc_row] if misc_row else []) + [total_row])
        for row, field_path in section_rows:
            next_row = next((value for value in ordered_boundaries if value > row), total_row)
            value = _xlsx_section_amount(data_sheet, formula_sheet, row, next_row, "合计")
            if value is not None:
                candidates[field_path] = _field_record(field_path, value, document_id, page_no=sheet_index)

        if misc_row is not None:
            misc_headers: list[tuple[int, str]] = []
            for aliases, field_path in misc_aliases:
                row = next((row for row, text in rows.items() if row > misc_row and any(alias in text for alias in aliases)), None)
                if row is not None:
                    misc_headers.append((row, field_path))
            misc_headers.sort()
            for index, (row, field_path) in enumerate(misc_headers):
                next_row = misc_headers[index + 1][0] if index + 1 < len(misc_headers) else total_row
                value = _xlsx_section_amount(data_sheet, formula_sheet, row, next_row, "小计")
                if value is not None:
                    candidates[field_path] = _field_record(field_path, value, document_id, page_no=sheet_index)
            value = _xlsx_section_amount(data_sheet, formula_sheet, misc_row, total_row, "合计")
            if value is not None:
                candidates["project.misc_expense"] = _field_record(
                    "project.misc_expense", value, document_id, page_no=sheet_index
                )

        for row, text in rows.items():
            field_path = None
            if "支出总计" in text or "支出合计" in text:
                field_path = "project.total_expense"
            elif "节余" in text:
                field_path = "project.gross_profit_candidate"
            if field_path:
                value = _xlsx_row_amount(data_sheet, formula_sheet, row)
                if value is not None:
                    candidates[field_path] = _field_record(field_path, value, document_id, page_no=sheet_index)

        _reconcile_project_budget(candidates, document_id, sheet_index)
        break

    formula_workbook.close()
    data_workbook.close()
    for field_path in PROJECT_BUDGET_FIELDS:
        candidates.setdefault(field_path, _field_record(field_path, None, document_id))
    return [candidates[field_path] for field_path in PROJECT_BUDGET_FIELDS]


def _extract_project_settlement_xlsx(path: Path, document_id: str) -> list[dict[str, Any]]:
    formula_workbook = load_workbook(path, read_only=False, data_only=False)
    data_workbook = load_workbook(path, read_only=False, data_only=True)
    candidates: dict[str, dict[str, Any]] = {}

    for sheet_index, (formula_sheet, data_sheet) in enumerate(
        zip(formula_workbook.worksheets, data_workbook.worksheets), start=1
    ):
        rows = {row: _sheet_row_text(formula_sheet, row) for row in range(1, formula_sheet.max_row + 1)}
        if not any("报销明细表" in text for text in rows.values()):
            continue

        def find_row(aliases: tuple[str, ...], *, after: int = 0, before: int | None = None) -> int | None:
            upper = before if before is not None else formula_sheet.max_row + 1
            return next((
                row for row, text in rows.items()
                if after < row < upper and any(alias in text for alias in aliases)
            ), None)

        title_row = find_row(("报销明细表",))
        if title_row:
            name = _project_name_from_settlement_title(rows[title_row])
            if name:
                candidates["project.name"] = _field_record("project.name", name, document_id, page_no=sheet_index)
        contract_row = find_row(("合同金额", "合同收入"))
        if contract_row:
            value = _xlsx_row_amount(data_sheet, formula_sheet, contract_row)
            if value is not None:
                candidates["project.contract_income"] = _field_record(
                    "project.contract_income", value, document_id, page_no=sheet_index
                )

        internal_header = find_row(("内部成本费用",))
        external_header = find_row(("外购成本费用", "外部成本费用"), after=internal_header or 0)
        total_row = find_row(("支出总计", "支出合计")) or formula_sheet.max_row + 1
        if internal_header and external_header:
            group_row = find_row(("集团设备使用费",), after=internal_header, before=external_header)
            labor_row = find_row(("人工费",), after=internal_header, before=external_header)
            internal_total_row = find_row(("内部成本费用合计",), after=internal_header, before=external_header)
            if group_row and labor_row:
                value = _xlsx_section_amount(
                    data_sheet, formula_sheet, group_row, labor_row, "小计",
                    total_column=7, multiplier_columns=(3, 5, 6),
                )
                if value is not None:
                    candidates["project.internal_group_equipment_fee"] = _field_record(
                        "project.internal_group_equipment_fee", value, document_id, page_no=sheet_index
                    )
                value = _xlsx_section_amount(
                    data_sheet, formula_sheet, labor_row, internal_total_row or external_header, "小计",
                    total_column=7, multiplier_columns=(3, 5, 6),
                )
                if value is not None:
                    candidates["project.internal_labor_fee"] = _field_record(
                        "project.internal_labor_fee", value, document_id, page_no=sheet_index
                    )
            if internal_total_row:
                value = _xlsx_row_amount(data_sheet, formula_sheet, internal_total_row)
                if value is not None:
                    candidates["project.internal_cost_total"] = _field_record(
                        "project.internal_cost_total", value, document_id, page_no=sheet_index
                    )

        if external_header:
            labor_row = find_row(("劳务费",), after=external_header, before=total_row)
            travel_row = find_row(("差旅费",), after=external_header, before=total_row)
            equipment_row = find_row(("设备租赁费/技术服务费", "设备租赁费／技术服务费", "设备租赁费"), after=external_header, before=total_row)
            stage_row = find_row(("舞美制作费",), after=external_header, before=total_row)
            purchase_row = find_row(("设备购置费",), after=external_header, before=total_row)
            misc_row = find_row(("杂费",), after=external_header, before=total_row)
            external_total_row = find_row(
                ("外部成本费用合计", "外购成本费用合计"), after=external_header, before=total_row
            )
            primary = [
                row for row in (
                    labor_row, travel_row, equipment_row, stage_row, purchase_row,
                    misc_row, external_total_row, total_row,
                ) if row
            ]

            def next_boundary(row: int) -> int:
                return next(value for value in sorted(primary) if value > row)

            if labor_row:
                value = _xlsx_section_amount(
                    data_sheet, formula_sheet, labor_row, next_boundary(labor_row), "合计",
                    total_column=7, multiplier_columns=(3, 5, 6),
                )
                if value is not None:
                    candidates["project.external_labor_fee"] = _field_record(
                        "project.external_labor_fee", value, document_id, page_no=sheet_index
                    )
            if travel_row and equipment_row:
                travel_specs = (
                    (("伙食费",), "project.travel_meal_fee"),
                    (("住宿费",), "project.travel_lodging_fee"),
                    (("交通费",), "project.travel_transportation_fee"),
                )
                travel_headers: list[tuple[int, str]] = []
                for aliases, field_path in travel_specs:
                    row = find_row(aliases, after=travel_row, before=equipment_row)
                    if row:
                        travel_headers.append((row, field_path))
                travel_headers.sort()
                for index, (row, field_path) in enumerate(travel_headers):
                    boundary = travel_headers[index + 1][0] if index + 1 < len(travel_headers) else equipment_row
                    value = _xlsx_section_amount(
                        data_sheet, formula_sheet, row, boundary, "小计",
                        total_column=7, multiplier_columns=(3, 5, 6),
                    )
                    if value is not None:
                        candidates[field_path] = _field_record(field_path, value, document_id, page_no=sheet_index)
                if travel_headers:
                    value = _xlsx_section_amount(
                        data_sheet, formula_sheet, travel_headers[-1][0], equipment_row, "合计",
                        total_column=7, multiplier_columns=(3, 5, 6),
                    )
                    if value is not None:
                        candidates["project.travel_expense"] = _field_record(
                            "project.travel_expense", value, document_id, page_no=sheet_index
                        )

            for row, field_path in (
                (equipment_row, "project.equipment_rental_service_fee"),
                (stage_row, "project.stage_production_fee"),
                (purchase_row, "project.equipment_purchase_fee"),
            ):
                if row:
                    value = _xlsx_section_amount(
                        data_sheet, formula_sheet, row, next_boundary(row), "合计",
                        total_column=7, multiplier_columns=(3, 5, 6),
                    )
                    if value is not None:
                        candidates[field_path] = _field_record(field_path, value, document_id, page_no=sheet_index)

            if misc_row:
                misc_specs = (
                    (("搬运费",), "project.misc_handling_fee"),
                    (("设备耗材费",), "project.misc_consumables_fee"),
                    (("快递费",), "project.misc_courier_fee"),
                    (("场租费",), "project.misc_venue_fee"),
                    (("其它费用", "其他费用"), "project.misc_other_fee"),
                )
                misc_headers: list[tuple[int, str]] = []
                for aliases, field_path in misc_specs:
                    row = find_row(aliases, after=misc_row, before=total_row)
                    if row:
                        misc_headers.append((row, field_path))
                misc_headers.sort()
                for index, (row, field_path) in enumerate(misc_headers):
                    boundary = misc_headers[index + 1][0] if index + 1 < len(misc_headers) else (external_total_row or total_row)
                    value = _xlsx_section_amount(
                        data_sheet, formula_sheet, row, boundary, "小计",
                        total_column=7, multiplier_columns=(3, 5, 6),
                    )
                    if value is not None:
                        candidates[field_path] = _field_record(field_path, value, document_id, page_no=sheet_index)
                if misc_headers:
                    value = _xlsx_section_amount(
                        data_sheet, formula_sheet, misc_headers[-1][0], external_total_row or total_row, "合计",
                        total_column=7, multiplier_columns=(3, 5, 6),
                    )
                    if value is not None:
                        candidates["project.misc_expense"] = _field_record(
                            "project.misc_expense", value, document_id, page_no=sheet_index
                        )

        for row, text in rows.items():
            field_path = None
            if "外部成本费用合计" in text or "外购成本费用合计" in text:
                field_path = "project.external_cost_total"
            elif "支出总计" in text or "支出合计" in text:
                field_path = "project.total_expense"
            elif "节余" in text:
                field_path = "project.gross_profit_candidate"
            if field_path:
                value = _xlsx_row_amount(data_sheet, formula_sheet, row)
                if value is not None:
                    candidates[field_path] = _field_record(field_path, value, document_id, page_no=sheet_index)

        _reconcile_project_settlement(candidates, document_id, sheet_index)
        break

    formula_workbook.close()
    data_workbook.close()
    for field_path in PROJECT_SETTLEMENT_FIELDS:
        candidates.setdefault(field_path, _field_record(field_path, None, document_id))
    return [candidates[field_path] for field_path in PROJECT_SETTLEMENT_FIELDS]


def _extract_xlsx(path: Path, document_id: str, form_type: str) -> list[dict[str, Any]]:
    if form_type == "PROJECT_BUDGET":
        return _extract_project_budget_xlsx(path, document_id)
    if form_type == "PROJECT_SETTLEMENT":
        specialized = _extract_project_settlement_xlsx(path, document_id)
        if any(
            item["path"] in PROJECT_SETTLEMENT_INTERNAL_FIELDS
            and item.get("value_normalized") is not None
            for item in specialized
        ):
            return specialized
    workbook = load_workbook(path, read_only=True, data_only=True)
    candidates: dict[str, dict[str, Any]] = {}
    for sheet_index, sheet in enumerate(workbook.worksheets, start=1):
        for row in sheet.iter_rows():
            for column_index, cell in enumerate(row, start=1):
                if not isinstance(cell.value, str):
                    continue
                matched = _match_label(cell.value)
                if not matched:
                    continue
                field_path, inline_value = matched
                value = inline_value
                if not value and column_index < sheet.max_column:
                    value = sheet.cell(row=cell.row, column=column_index + 1).value
                if value in (None, "") and cell.row < sheet.max_row:
                    value = sheet.cell(row=cell.row + 1, column=column_index).value
                if value in (None, ""):
                    continue
                raw = str(value)
                candidates[field_path] = {
                    "path": field_path,
                    "value_raw": raw,
                    "value_normalized": _normalize_value(field_path, value),
                    "confidence": "0.96",
                    "source_document_id": document_id,
                    "page_no": sheet_index,
                    "bounding_box": None,
                    "confirmed": False,
                }
    workbook.close()
    return list(candidates.values())


def _ocr_numeric_token(text: str) -> Decimal | None:
    compact = str(text or "").replace(" ", "")
    if not compact or "%" in compact or "第1页" in compact or ".fee" in compact or "project." in compact:
        return None
    matches = re.findall(r"-?\d[\d,，]*(?:\.\d+)?", compact)
    if not matches:
        return None
    try:
        return Decimal(matches[-1].replace(",", "").replace("，", ""))
    except InvalidOperation:
        return None


def _ocr_field_from_line(
    field_path: str,
    label_line: dict[str, Any],
    table_lines: list[dict[str, Any]],
    document_id: str,
    image_width: int,
    image_height: int,
) -> dict[str, Any] | None:
    value_line = label_line
    value = _ocr_numeric_token(label_line["text"])
    if value is None:
        same_row = [
            item for item in table_lines
            if item is not label_line
            and item["box"][0] >= label_line["box"][2] - 6
            and abs(item["cy"] - label_line["cy"]) <= max(label_line["height"], item["height"]) * 0.9
            and _ocr_numeric_token(item["text"]) is not None
        ]
        if same_row:
            value_line = min(
                same_row,
                key=lambda item: (abs(item["cy"] - label_line["cy"]), -item["box"][0]),
            )
            value = _ocr_numeric_token(value_line["text"])
    if value is None:
        return None
    base_score = min(label_line["score"], value_line["score"])
    confidence = min(0.99, max(0.90, 0.75 + base_score * 0.25))
    x1, y1, x2, y2 = value_line["box"]
    return _field_record(
        field_path,
        value,
        document_id,
        confidence=f"{confidence:.3f}",
        bounding_box=[
            max(0, min(1, x1 / image_width)),
            max(0, min(1, y1 / image_height)),
            max(0, min(1, x2 / image_width)),
            max(0, min(1, y2 / image_height)),
        ],
    )


def _extract_project_budget_ocr(
    normalized_lines: list[dict[str, Any]],
    *,
    document_id: str,
    image_width: int,
    image_height: int,
) -> list[dict[str, Any]]:
    title_lines = [
        line for line in normalized_lines
        if "立项预算" in line["text"] and "明细表" in line["text"]
    ]
    title_line = min(title_lines, key=lambda line: (line["box"][0], line["box"][1])) if title_lines else None
    if title_line:
        left = max(0.0, title_line["box"][0] - 160)
        right = min(float(image_width), title_line["box"][2] + 190)
        top = max(0.0, title_line["box"][1] - 50)
        table_lines = [
            line for line in normalized_lines
            if left <= line["cx"] <= right and line["box"][1] >= top
        ]
    else:
        table_lines = normalized_lines
    table_lines = sorted(table_lines, key=lambda line: (line["box"][1], line["box"][0]))
    candidates: dict[str, dict[str, Any]] = {}

    def find_line(aliases: tuple[str, ...], *, after: float = -1, before: float = float("inf")) -> dict[str, Any] | None:
        return next((
            line for line in table_lines
            if after < line["cy"] < before and any(alias in line["text"] for alias in aliases)
        ), None)

    def add_labeled_amount(field_path: str, line: dict[str, Any] | None) -> None:
        if line is None:
            return
        field = _ocr_field_from_line(
            field_path, line, table_lines, document_id, image_width, image_height
        )
        if field:
            candidates[field_path] = field

    def add_section_amount(field_path: str, start: dict[str, Any] | None, end_y: float, subtotal_label: str) -> None:
        if start is None:
            return
        subtotal_lines = [
            line for line in table_lines
            if start["cy"] < line["cy"] < end_y and subtotal_label in line["text"]
        ]
        if subtotal_lines:
            add_labeled_amount(field_path, subtotal_lines[-1])

    if title_line:
        project_name = _project_name_from_budget_title(title_line["text"])
        if project_name:
            x1, y1, x2, y2 = title_line["box"]
            candidates["project.name"] = _field_record(
                "project.name",
                project_name,
                document_id,
                confidence=f"{min(0.99, max(0.90, 0.75 + title_line['score'] * 0.25)):.3f}",
                bounding_box=[x1 / image_width, y1 / image_height, x2 / image_width, y2 / image_height],
            )

    contract_line = find_line(("合同金额", "合同收入"))
    add_labeled_amount("project.contract_income", contract_line)

    primary_specs = (
        (("集团设备使用费及人工费", "设备使用费及人工费"), "project.group_equipment_labor_fee"),
        (("劳务费",), "project.labor_fee"),
        (("设备租赁费/技术服务费", "设备租赁费／技术服务费", "设备租赁费"), "project.equipment_rental_service_fee"),
        (("舞美制作费",), "project.stage_production_fee"),
    )
    primary_headers: list[tuple[dict[str, Any], str]] = []
    for aliases, field_path in primary_specs:
        line = find_line(aliases)
        if line:
            primary_headers.append((line, field_path))
    primary_headers.sort(key=lambda item: item[0]["cy"])
    misc_header = find_line(("杂费",))
    total_line = find_line(("支出总计", "支出合计"))
    total_y = total_line["cy"] if total_line else float(image_height)
    for index, (line, field_path) in enumerate(primary_headers):
        later_y = primary_headers[index + 1][0]["cy"] if index + 1 < len(primary_headers) else total_y
        if misc_header and line["cy"] < misc_header["cy"] < later_y:
            later_y = misc_header["cy"]
        add_section_amount(field_path, line, later_y, "合计")

    misc_specs = (
        (("搬运费",), "project.misc_handling_fee"),
        (("设备耗材费",), "project.misc_consumables_fee"),
        (("快递费",), "project.misc_courier_fee"),
        (("场租费",), "project.misc_venue_fee"),
        (("其它费用", "其他费用"), "project.misc_other_fee"),
    )
    misc_headers: list[tuple[dict[str, Any], str]] = []
    if misc_header:
        for aliases, field_path in misc_specs:
            line = find_line(aliases, after=misc_header["cy"], before=total_y)
            if line:
                misc_headers.append((line, field_path))
        misc_headers.sort(key=lambda item: item[0]["cy"])
        for index, (line, field_path) in enumerate(misc_headers):
            later_y = misc_headers[index + 1][0]["cy"] if index + 1 < len(misc_headers) else total_y
            add_section_amount(field_path, line, later_y, "小计")
        if misc_headers:
            add_section_amount("project.misc_expense", misc_headers[-1][0], total_y, "合计")

    add_labeled_amount("project.total_expense", total_line)
    profit_line = find_line(("节余", "项目毛利"), after=total_y)
    add_labeled_amount("project.gross_profit_candidate", profit_line)
    _reconcile_project_budget(candidates, document_id)

    for field_path in PROJECT_BUDGET_FIELDS:
        candidates.setdefault(field_path, _field_record(field_path, None, document_id))
    return [candidates[field_path] for field_path in PROJECT_BUDGET_FIELDS]


def _extract_project_settlement_ocr(
    normalized_lines: list[dict[str, Any]],
    *,
    document_id: str,
    image_width: int,
    image_height: int,
) -> list[dict[str, Any]]:
    title_lines = [line for line in normalized_lines if "报销明细表" in line["text"]]
    title_line = min(title_lines, key=lambda line: (line["box"][0], line["box"][1])) if title_lines else None
    if title_line:
        left = max(0.0, title_line["box"][0] - 180)
        right = min(float(image_width), title_line["box"][2] + 200)
        top = max(0.0, title_line["box"][1] - 50)
        table_lines = [
            line for line in normalized_lines
            if left <= line["cx"] <= right and line["box"][1] >= top
        ]
    else:
        table_lines = normalized_lines
    table_lines = sorted(table_lines, key=lambda line: (line["box"][1], line["box"][0]))
    candidates: dict[str, dict[str, Any]] = {}

    def find_line(aliases: tuple[str, ...], *, after: float = -1, before: float = float("inf")) -> dict[str, Any] | None:
        return next((
            line for line in table_lines
            if after < line["cy"] < before and any(alias in line["text"] for alias in aliases)
        ), None)

    def add_labeled_amount(field_path: str, line: dict[str, Any] | None) -> None:
        if line is None:
            return
        field = _ocr_field_from_line(field_path, line, table_lines, document_id, image_width, image_height)
        if field:
            candidates[field_path] = field

    def add_section_amount(
        field_path: str,
        start: dict[str, Any] | None,
        end_y: float,
        subtotal_label: str,
    ) -> None:
        if start is None:
            return
        subtotal_lines = [
            line for line in table_lines
            if start["cy"] < line["cy"] < end_y and subtotal_label in line["text"]
        ]
        if subtotal_lines:
            add_labeled_amount(field_path, subtotal_lines[-1])

    if title_line:
        project_name = _project_name_from_settlement_title(title_line["text"])
        if project_name:
            x1, y1, x2, y2 = title_line["box"]
            candidates["project.name"] = _field_record(
                "project.name", project_name, document_id,
                confidence=f"{min(0.99, max(0.90, 0.75 + title_line['score'] * 0.25)):.3f}",
                bounding_box=[x1 / image_width, y1 / image_height, x2 / image_width, y2 / image_height],
            )
    add_labeled_amount("project.contract_income", find_line(("合同金额", "合同收入")))

    internal_header = find_line(("内部成本费用",))
    external_header = find_line(("外购成本费用", "外部成本费用"), after=internal_header["cy"] if internal_header else -1)
    total_line = find_line(("支出总计", "支出合计"))
    total_y = total_line["cy"] if total_line else float(image_height)

    if internal_header and external_header:
        group_line = find_line(("集团设备使用费",), after=internal_header["cy"], before=external_header["cy"])
        labor_line = find_line(("人工费",), after=internal_header["cy"], before=external_header["cy"])
        internal_total = find_line(("内部成本费用合计",), after=internal_header["cy"], before=external_header["cy"])
        if group_line and labor_line:
            add_section_amount("project.internal_group_equipment_fee", group_line, labor_line["cy"], "小计")
            add_section_amount(
                "project.internal_labor_fee", labor_line,
                internal_total["cy"] if internal_total else external_header["cy"], "小计",
            )
        add_labeled_amount("project.internal_cost_total", internal_total)

    if external_header:
        external_labor = find_line(("劳务费",), after=external_header["cy"], before=total_y)
        travel_header = find_line(("差旅费",), after=external_header["cy"], before=total_y)
        equipment_line = find_line(
            ("设备租赁费/技术服务费", "设备租赁费／技术服务费", "设备租赁费"),
            after=external_header["cy"], before=total_y,
        )
        stage_line = find_line(("舞美制作费",), after=external_header["cy"], before=total_y)
        purchase_line = find_line(("设备购置费",), after=external_header["cy"], before=total_y)
        misc_header = find_line(("杂费",), after=external_header["cy"], before=total_y)
        external_total = find_line(("外部成本费用合计", "外购成本费用合计"), after=external_header["cy"], before=total_y)
        boundaries = sorted(
            line["cy"] for line in (
                external_labor, travel_header, equipment_line, stage_line, purchase_line,
                misc_header, external_total, total_line,
            ) if line
        )

        def next_y(line: dict[str, Any]) -> float:
            return next(value for value in boundaries if value > line["cy"])

        if external_labor:
            add_section_amount("project.external_labor_fee", external_labor, next_y(external_labor), "合计")
        if travel_header and equipment_line:
            travel_specs = (
                (("伙食费",), "project.travel_meal_fee"),
                (("住宿费",), "project.travel_lodging_fee"),
                (("交通费",), "project.travel_transportation_fee"),
            )
            travel_headers: list[tuple[dict[str, Any], str]] = []
            for aliases, field_path in travel_specs:
                line = find_line(aliases, after=travel_header["cy"], before=equipment_line["cy"])
                if line:
                    travel_headers.append((line, field_path))
            travel_headers.sort(key=lambda item: item[0]["cy"])
            for index, (line, field_path) in enumerate(travel_headers):
                boundary = travel_headers[index + 1][0]["cy"] if index + 1 < len(travel_headers) else equipment_line["cy"]
                add_section_amount(field_path, line, boundary, "小计")
            if travel_headers:
                add_section_amount("project.travel_expense", travel_headers[-1][0], equipment_line["cy"], "合计")

        for line, field_path in (
            (equipment_line, "project.equipment_rental_service_fee"),
            (stage_line, "project.stage_production_fee"),
            (purchase_line, "project.equipment_purchase_fee"),
        ):
            if line:
                add_section_amount(field_path, line, next_y(line), "合计")

        if misc_header:
            misc_specs = (
                (("搬运费",), "project.misc_handling_fee"),
                (("设备耗材费",), "project.misc_consumables_fee"),
                (("快递费",), "project.misc_courier_fee"),
                (("场租费",), "project.misc_venue_fee"),
                (("其它费用", "其他费用"), "project.misc_other_fee"),
            )
            misc_headers: list[tuple[dict[str, Any], str]] = []
            misc_end = external_total["cy"] if external_total else total_y
            for aliases, field_path in misc_specs:
                line = find_line(aliases, after=misc_header["cy"], before=misc_end)
                if line:
                    misc_headers.append((line, field_path))
            misc_headers.sort(key=lambda item: item[0]["cy"])
            for index, (line, field_path) in enumerate(misc_headers):
                boundary = misc_headers[index + 1][0]["cy"] if index + 1 < len(misc_headers) else misc_end
                add_section_amount(field_path, line, boundary, "小计")
            if misc_headers:
                add_section_amount("project.misc_expense", misc_headers[-1][0], misc_end, "合计")
        add_labeled_amount("project.external_cost_total", external_total)

    add_labeled_amount("project.total_expense", total_line)
    add_labeled_amount("project.gross_profit_candidate", find_line(("节余", "项目毛利"), after=total_y))
    _reconcile_project_settlement(candidates, document_id)
    for field_path in PROJECT_SETTLEMENT_FIELDS:
        candidates.setdefault(field_path, _field_record(field_path, None, document_id))
    return [candidates[field_path] for field_path in PROJECT_SETTLEMENT_FIELDS]


def extract_ocr_lines(
    lines: list[dict[str, Any]],
    *,
    document_id: str,
    form_type: str,
    image_width: int,
    image_height: int,
) -> list[dict[str, Any]]:
    """Convert positioned OCR text into the same field records as XLSX extraction."""

    normalized_lines: list[dict[str, Any]] = []
    for line in lines:
        text = re.sub(r"\s+", "", str(line.get("text") or "")).strip()
        box = line.get("box") or []
        if not text or len(box) != 4:
            continue
        x1, y1, x2, y2 = (float(value) for value in box)
        normalized_lines.append({
            "text": text,
            "score": max(0.0, min(1.0, float(line.get("score") or 0))),
            "box": [x1, y1, x2, y2],
            "cx": (x1 + x2) / 2,
            "cy": (y1 + y2) / 2,
            "height": max(1.0, y2 - y1),
        })

    if form_type == "PROJECT_BUDGET":
        return _extract_project_budget_ocr(
            normalized_lines,
            document_id=document_id,
            image_width=image_width,
            image_height=image_height,
        )
    if form_type == "PROJECT_SETTLEMENT" and any("报销明细表" in line["text"] for line in normalized_lines):
        return _extract_project_settlement_ocr(
            normalized_lines,
            document_id=document_id,
            image_width=image_width,
            image_height=image_height,
        )

    candidates: dict[str, dict[str, Any]] = {}
    for label_line in normalized_lines:
        matched = _match_label(label_line["text"])
        if not matched:
            continue
        field_path, inline_value = matched
        value_line = label_line
        value = inline_value if _plausible_ocr_value(field_path, inline_value) else None
        if not value:
            same_row = [
                item for item in normalized_lines
                if item is not label_line
                and item["box"][0] >= label_line["box"][2] - 4
                and abs(item["cy"] - label_line["cy"]) <= max(label_line["height"], item["height"]) * 0.8
                and _plausible_ocr_value(field_path, item["text"])
            ]
            if same_row:
                value_line = min(same_row, key=lambda item: item["box"][0])
                value = value_line["text"]
            else:
                below = [
                    item for item in normalized_lines
                    if item is not label_line
                    and item["box"][1] >= label_line["box"][3] - 2
                    and item["box"][1] - label_line["box"][3] <= label_line["height"] * 2.5
                    and abs(item["cx"] - label_line["cx"]) <= max(label_line["box"][2] - label_line["box"][0], 80)
                    and _plausible_ocr_value(field_path, item["text"])
                ]
                if below:
                    value_line = min(below, key=lambda item: item["box"][1])
                    value = value_line["text"]
        if value in (None, ""):
            continue
        raw = str(value).strip()
        base_score = min(label_line["score"], value_line["score"])
        confidence = min(0.99, max(0.80, 0.75 + base_score * 0.25))
        x1, y1, x2, y2 = value_line["box"]
        field = {
            "path": field_path,
            "value_raw": raw,
            "value_normalized": _normalize_value(field_path, raw),
            "confidence": f"{confidence:.3f}",
            "source_document_id": document_id,
            "page_no": 1,
            "bounding_box": [
                max(0, min(1, x1 / image_width)),
                max(0, min(1, y1 / image_height)),
                max(0, min(1, x2 / image_width)),
                max(0, min(1, y2 / image_height)),
            ],
            "confirmed": False,
        }
        current = candidates.get(field_path)
        if not current or float(field["confidence"]) > float(current["confidence"]):
            candidates[field_path] = field

    if "project.name" not in candidates:
        title_suffixes = ("立项预算明细表", "项目决算明细表", "预算明细表", "决算明细表")
        for line in normalized_lines:
            suffix = next((item for item in title_suffixes if item in line["text"]), None)
            if not suffix:
                continue
            project_name = line["text"].replace(suffix, "").strip("：:·-— ")
            if len(project_name) < 4 or "上传" in project_name or "核对" in project_name:
                continue
            x1, y1, x2, y2 = line["box"]
            confidence = min(0.99, max(0.90, 0.75 + line["score"] * 0.25))
            candidates["project.name"] = {
                "path": "project.name",
                "value_raw": project_name,
                "value_normalized": project_name,
                "confidence": f"{confidence:.3f}",
                "source_document_id": document_id,
                "page_no": 1,
                "bounding_box": [
                    max(0, min(1, x1 / image_width)), max(0, min(1, y1 / image_height)),
                    max(0, min(1, x2 / image_width)), max(0, min(1, y2 / image_height)),
                ],
                "confirmed": False,
            }
            break

    required_fields = CRITICAL_FIELDS.get(form_type, {"project.name"})
    if form_type == "PROJECT_SETTLEMENT":
        required_fields = {"project.name", "project.contract_income", "project.total_expense"}
    for field_path in sorted(required_fields):
        candidates.setdefault(field_path, {
            "path": field_path,
            "value_raw": None,
            "value_normalized": None,
            "confidence": "0",
            "source_document_id": document_id,
            "page_no": 1,
            "bounding_box": None,
            "confirmed": False,
        })
    return list(candidates.values())


def extract_document(document: dict[str, Any], form_type: str) -> list[dict[str, Any]]:
    if form_type.startswith("STD_"):
        from app.services.standard_templates import extract_standard
        return extract_standard(document, form_type)
    path = Path(document["storage_path"])
    if path.suffix.lower() == ".xlsx":
        return _extract_xlsx(path, document["id"], form_type)
    required = CRITICAL_FIELDS.get(form_type, {"project.name"})
    return [
        {
            "path": field_path,
            "value_raw": None,
            "value_normalized": None,
            "confidence": "0",
            "source_document_id": document["id"],
            "page_no": 1,
            "bounding_box": None,
            "confirmed": False,
        }
        for field_path in sorted(required)
    ]


def validate_confirmed_fields(fields: list[dict[str, Any]], form_type: str, threshold: Decimal = Decimal("0.90")) -> dict[str, list[str]]:
    if form_type.startswith("STD_"):
        from app.services.standard_templates import validate_standard
        return validate_standard(fields, form_type)
    by_path = {item["path"]: item for item in fields}
    errors: list[str] = []
    warnings: list[str] = []
    required_fields = CRITICAL_FIELDS.get(form_type, set())
    if form_type == "PROJECT_SETTLEMENT" and not any(path in by_path for path in PROJECT_SETTLEMENT_INTERNAL_FIELDS):
        required_fields = {"project.name", "project.contract_income", "project.total_expense"}
    for required in sorted(required_fields):
        item = by_path.get(required)
        if not item or item.get("value_normalized") in (None, ""):
            errors.append(f"缺少必填字段：{required}")
            continue
        confidence = Decimal(str(item.get("confidence", 0)))
        if not item.get("confirmed"):
            label = "低置信度字段" if confidence < threshold else "关键字段"
            errors.append(f"{label}尚未人工确认：{required}")
    for item in fields:
        if Decimal(str(item.get("confidence", 0))) < threshold:
            warnings.append(f"{item['path']} 识别置信度低于 {threshold}")
    return {"errors": errors, "warnings": warnings}
