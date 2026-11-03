from __future__ import annotations

import json
import re
from copy import copy
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins
from openpyxl.utils import get_column_letter


SUMMARY_SHEET_NAME = "汇总"
SUMMARY_FIRST_DATA_ROW = 4

NO_FILL = PatternFill(fill_type=None)
HEADER_FONT = Font(name="宋体", size=11, color="000000", bold=True)
MONEY_FORMAT = '#,##0.00'
INTEGER_MONEY_FORMAT = '#,##0'
BANNED_EXPORT_TEXT = ("退休年龄系数", "停发", "特殊奖核算与分配", "核算方式：以项目毛利")
TABLE_SIDE = Side(style="thin", color="A6A6A6")
TABLE_BORDER = Border(left=TABLE_SIDE, right=TABLE_SIDE, top=TABLE_SIDE, bottom=TABLE_SIDE)


def _style_header(sheet, row: int, columns: int) -> None:
    for cell in sheet[row][:columns]:
        cell.fill = NO_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _auto_width(sheet) -> None:
    for index, column in enumerate(sheet.columns, start=1):
        width = min(48, max(10, max((len(str(cell.value or "")) for cell in column), default=10) + 2))
        sheet.column_dimensions[get_column_letter(index)].width = width


def _remove_all_color_blocks(workbook) -> None:
    """Remove cell/table/conditional fills while preserving values, borders and links."""

    for sheet in workbook.worksheets:
        for cell in sheet._cells.values():
            if not isinstance(cell, Cell):
                continue
            had_fill = cell.fill.fill_type is not None
            if had_fill:
                cell.fill = NO_FILL
            color = cell.font.color
            if had_fill and color is not None and color.type == "rgb":
                rgb = str(color.rgb or "").upper()[-6:]
                if rgb in {"FFFFFF", "FFFFFE", "FDFDFD"}:
                    font = copy(cell.font)
                    font.color = "000000"
                    cell.font = font
        for dimension in [*sheet.row_dimensions.values(), *sheet.column_dimensions.values()]:
            if dimension.fill.fill_type is not None:
                dimension.fill = NO_FILL
        sheet.conditional_formatting._cf_rules.clear()
        for table in sheet.tables.values():
            table.tableStyleInfo = None
        sheet.sheet_properties.tabColor = None


def _money(value: Any) -> float:
    return float(Decimal(str(value or "0")))


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _create_base_workbook(template_path: Path | None, output_path: Path):
    """Create a clean final workbook without carrying hidden template objects.

    The official template is a visual/content reference only. Copying the whole
    workbook also copies oversized row dimensions, stale defined names and
    unsupported extension records, which can make Excel repair the export.
    """
    _ = template_path, output_path
    workbook = Workbook()
    summary = workbook.active
    summary.title = SUMMARY_SHEET_NAME
    return workbook, summary, False


def _project_name(batch: dict[str, Any], fields: list[dict[str, Any]]) -> str:
    field_map = {item["path"]: item.get("value_normalized") for item in fields}
    return str(field_map.get("project.name") or batch["batch_no"])


def _override_note(calculation: dict[str, Any], audit_events: list[dict[str, Any]]) -> str:
    reference = Decimal(str(calculation["rounded_total"]))
    confirmed = Decimal(str(calculation.get("confirmed_total") or reference))
    if confirmed == reference:
        return ""
    reason = ""
    for event in reversed(audit_events):
        after = event.get("after") or {}
        if event.get("action") == "CALCULATION_CONFIRMED" and isinstance(after, dict):
            reason = str(after.get("reason") or "")
            break
    note = f"审核调整：参考奖励{reference}元，核定奖励{confirmed}元"
    return f"{note}；原因：{reason}" if reason else f"{note}；原因见审计信息"


def _formula_note(calculation: dict[str, Any]) -> str:
    """Render the frozen rule lines as readable, auditable calculation text."""

    rendered: list[str] = []
    for line in calculation.get("lines") or []:
        expression = str(line.get("expression") or "").strip()
        expression = re.sub(
            r"(\d+(?:\.\d+)?)%",
            lambda match: f"{Decimal(match.group(1)):.2f}%",
            expression,
        )
        result = Decimal(str(line.get("raw_result") or "0"))
        rendered.append(
            f"{str(line.get('label') or line.get('formula_code') or '计算项')}："
            f"{expression} = ¥{result:,.2f}"
        )
    return "；\n".join(rendered) + ("。" if rendered else "")


def _summary_note(
    calculation: dict[str, Any],
    audit_events: list[dict[str, Any]],
    *,
    mode: str,
) -> str:
    parts: list[str] = []
    if mode == "formula":
        formula = _formula_note(calculation)
        if formula:
            parts.append(formula)
    override = _override_note(calculation, audit_events)
    if override:
        parts.append(override)
    return "\n".join(parts)


def _write_summary(
    summary,
    *,
    items: list[dict[str, Any]],
    mode: str,
) -> None:
    batch = items[0]["batch"]
    month_year, month_number = batch["month"].split("-")
    summary.merge_cells("A1:F1")
    summary["A1"] = f"{month_year}年{int(month_number)}月 月奖核算汇总（系统导出）"
    summary["A1"].font = Font(name="微软雅黑", size=16, bold=True, color="000000")
    summary["A1"].alignment = Alignment(horizontal="center", vertical="center")
    summary.row_dimensions[1].height = 30
    summary.row_dimensions[2].height = 8

    headers = ["序号", "项目名称", "参考奖励", "核定奖励", "链接", "备注"]
    for column, value in enumerate(headers, start=1):
        summary.cell(3, column, value)
    _style_header(summary, 3, len(headers))
    summary.row_dimensions[3].height = 24

    data_row = SUMMARY_FIRST_DATA_ROW
    reference_total = Decimal("0")
    confirmed_total = Decimal("0")
    for index, item in enumerate(items, start=1):
        calculation = item["calculation"]
        fields = item["fields"]
        audit_events = item.get("audit_events") or []
        row = data_row + index - 1
        reference = Decimal(str(calculation["rounded_total"]))
        confirmed = Decimal(str(calculation.get("confirmed_total") or reference))
        reference_total += reference
        confirmed_total += confirmed
        summary.cell(row, 1, index)
        summary.cell(row, 2, _project_name(item["batch"], fields))
        summary.cell(row, 3, float(reference))
        summary.cell(row, 4, float(confirmed))
        # The final workbook contains no internal detail sheet, so the link is
        # intentionally empty instead of exposing a temporary system URL.
        download_url = str(item.get("download_url") or "").strip()
        summary.cell(row, 5, "下载原表" if download_url else None)
        summary.cell(row, 5).hyperlink = download_url or None
        if download_url:
            summary.cell(row, 5).hyperlink.tooltip = "下载该项目的原始申报表"
        note = _summary_note(calculation, audit_events, mode=mode)
        summary.cell(row, 6, note)
        summary.cell(row, 3).number_format = INTEGER_MONEY_FORMAT
        summary.cell(row, 4).number_format = INTEGER_MONEY_FORMAT
        summary.cell(row, 1).alignment = Alignment(horizontal="center", vertical="center")
        summary.cell(row, 2).alignment = Alignment(horizontal="left", vertical="center")
        summary.cell(row, 3).alignment = Alignment(horizontal="right", vertical="center")
        summary.cell(row, 4).alignment = Alignment(horizontal="right", vertical="center")
        summary.cell(row, 5).alignment = Alignment(horizontal="left", vertical="center")
        summary.cell(row, 6).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        note_lines = max(1, note.count("\n") + 1) if note else 1
        summary.row_dimensions[row].height = min(120, max(24, 18 * note_lines + (8 if note else 0)))

    total_row = data_row + len(items)
    summary.cell(total_row, 2, "合计")
    summary.cell(total_row, 2).font = Font(name=summary.cell(total_row, 2).font.name, bold=True)
    if mode == "formula":
        summary.cell(total_row, 3, f"=SUM(C{data_row}:C{total_row - 1})")
        summary.cell(total_row, 4, f"=SUM(D{data_row}:D{total_row - 1})")
    else:
        summary.cell(total_row, 3, float(reference_total))
        summary.cell(total_row, 4, float(confirmed_total))
    summary.cell(total_row, 3).number_format = INTEGER_MONEY_FORMAT
    summary.cell(total_row, 4).number_format = INTEGER_MONEY_FORMAT
    summary.row_dimensions[total_row].height = 24

    for row in summary.iter_rows(min_row=3, max_row=total_row, min_col=1, max_col=6):
        for cell in row:
            cell.fill = NO_FILL
            cell.border = TABLE_BORDER
            if cell.row > 3:
                cell.font = Font(name="宋体", size=11, color="000000", bold=cell.row == total_row)
    for row in range(data_row, total_row):
        if summary.cell(row, 5).hyperlink:
            summary.cell(row, 5).font = Font(
                name="宋体", size=11, color="0563C1", underline="single"
            )
    for column in (3, 4):
        summary.cell(total_row, column).alignment = Alignment(horizontal="right", vertical="center")
    summary.cell(total_row, 2).alignment = Alignment(horizontal="center", vertical="center")

    summary.column_dimensions["A"].width = 9
    summary.column_dimensions["B"].width = 42
    summary.column_dimensions["C"].width = 16
    summary.column_dimensions["D"].width = 16
    summary.column_dimensions["E"].width = 18
    summary.column_dimensions["F"].width = 64
    summary.freeze_panes = "A4"
    summary.sheet_view.showGridLines = False
    summary.sheet_view.zoomScale = 100
    summary.print_area = f"A1:F{total_row}"
    summary.print_options.horizontalCentered = True
    summary.page_setup.orientation = "landscape"
    summary.page_setup.paperSize = summary.PAPERSIZE_A4
    summary.page_setup.fitToWidth = 1
    summary.page_setup.fitToHeight = 0
    summary.sheet_properties.pageSetUpPr.fitToPage = True
    summary.page_margins = PageMargins(left=0.25, right=0.25, top=0.5, bottom=0.5, header=0.2, footer=0.2)


def _keep_only_summary(workbook, summary) -> None:
    """Remove every worksheet except the requested official summary sheet."""

    for sheet in list(workbook.worksheets):
        if sheet is not summary:
            workbook.remove(sheet)
    summary.sheet_state = "visible"
    workbook.active = 0


def _write_project_cost(workbook, calculation: dict[str, Any]) -> None:
    sheet = workbook.create_sheet("项目成本")
    sheet.append(["输入字段", "确认值", "说明"])
    _style_header(sheet, 1, 3)
    inputs = calculation.get("inputs_snapshot") or {}
    labels = {
        "contract_income": "合同收入",
        "total_expense": "支出总计",
        "equipment_fee": "设备使用费",
        "technical_service_fee": "技术服务费",
        "gross_profit": "项目毛利",
        "session_count": "场次",
        "monthly_km": "当月累计里程",
        "half_days": "半天数",
        "full_days": "全天数",
        "vehicle_type": "车型",
        "internal_quote_total": "内部申报价",
        "external_repair_total": "对外维修总计",
        "parts_cost": "零件采购费",
        "project_bonus_pool": "项目奖金池",
    }
    for key, value in inputs.items():
        sheet.append([labels.get(key, key), value, f"输入快照字段：{key}"])
    if "contract_income" in inputs and "total_expense" in inputs:
        sheet.append(["项目毛利", _money(inputs["contract_income"]) - _money(inputs["total_expense"]), "合同收入－支出总计"])
    sheet.freeze_panes = "A2"
    _auto_width(sheet)


def _write_calculation(workbook, calculation: dict[str, Any], mode: str) -> int:
    sheet = workbook.create_sheet("计算过程")
    sheet.append(["规则版本", calculation["rule_version"], "规则编码", calculation["rule_code"], "计算ID", calculation["id"], "状态", calculation["status"]])
    sheet.append(["序号", "计算项", "公式说明", "操作数", "舍入前", "参考奖励", "核定奖励", "公式编码"])
    _style_header(sheet, 2, 8)
    for index, line in enumerate(calculation.get("lines") or [], start=1):
        sheet.append([
            index,
            line["label"],
            line["expression"],
            _safe_json(line["operands"]),
            _money(line["raw_result"]),
            _money(line["rounded_result"]),
            None,
            line["formula_code"],
        ])
        sheet.cell(sheet.max_row, 5).number_format = MONEY_FORMAT
        sheet.cell(sheet.max_row, 6).number_format = INTEGER_MONEY_FORMAT

    total_row = sheet.max_row + 1
    sheet.cell(total_row, 2, "合计")
    sheet.cell(total_row, 2).font = Font(bold=True)
    if mode == "formula" and total_row > 3:
        sheet.cell(total_row, 5, f"=SUM(E3:E{total_row - 1})")
        sheet.cell(total_row, 6, f"=ROUND(E{total_row},0)")
    else:
        sheet.cell(total_row, 5, _money(calculation["raw_total"]))
        sheet.cell(total_row, 6, _money(calculation["rounded_total"]))
    sheet.cell(total_row, 7, _money(calculation.get("confirmed_total") or calculation["rounded_total"]))
    for column in (5, 6, 7):
        sheet.cell(total_row, column).number_format = MONEY_FORMAT if column == 5 else INTEGER_MONEY_FORMAT
        sheet.cell(total_row, column).fill = NO_FILL
    sheet.freeze_panes = "A3"
    _auto_width(sheet)
    return total_row


def _write_fields(workbook, fields: list[dict[str, Any]], documents: list[dict[str, Any]]) -> None:
    sheet = workbook.create_sheet("识别结果")
    sheet.append(["字段路径", "原始值", "确认值", "置信度", "来源文件", "页码", "人工确认"])
    _style_header(sheet, 1, 7)
    document_names = {document["id"]: document.get("file_name", "") for document in documents}
    for item in fields:
        sheet.append([
            item["path"],
            item.get("value_raw"),
            str(item.get("value_normalized") if item.get("value_normalized") is not None else ""),
            float(item.get("confidence") or 0),
            document_names.get(item.get("source_document_id"), item.get("source_document_id") or ""),
            item.get("page_no"),
            "是" if item.get("confirmed") else "否",
        ])
        sheet.cell(sheet.max_row, 4).number_format = "0.0%"
    sheet.freeze_panes = "A2"
    _auto_width(sheet)


def _write_exceptions(workbook, calculation: dict[str, Any], fields: list[dict[str, Any]]) -> None:
    sheet = workbook.create_sheet("异常待确认")
    sheet.append(["类型", "内容", "处理状态"])
    _style_header(sheet, 1, 3)
    issues: list[tuple[str, str, str]] = []
    for warning in calculation.get("warnings") or []:
        issues.append(("规则提示", str(warning), "已由审核人确认"))
    for item in fields:
        if not item.get("confirmed"):
            issues.append(("字段未确认", item["path"], "禁止正式导出"))
    if Decimal(str(calculation.get("confirmed_total") or calculation["rounded_total"])) != Decimal(str(calculation["rounded_total"])):
        issues.append(("人工调整", "核定奖励与参考奖励不一致", "已留痕"))
    if not issues:
        issues.append(("无", "本次导出不存在未处理异常", "通过"))
    for issue in issues:
        sheet.append(list(issue))
    sheet.freeze_panes = "A2"
    _auto_width(sheet)


def _write_sources(workbook, documents: list[dict[str, Any]]) -> None:
    sheet = workbook.create_sheet("来源清单")
    sheet.append(["文件名", "MIME", "大小（字节）", "SHA-256", "页数", "上传时间"])
    _style_header(sheet, 1, 6)
    for document in documents:
        sheet.append([
            document.get("file_name"),
            document.get("mime"),
            document.get("size_bytes"),
            document.get("sha256"),
            document.get("pages"),
            document.get("created_at"),
        ])
    sheet.freeze_panes = "A2"
    _auto_width(sheet)


def _write_audit(
    workbook,
    calculation: dict[str, Any],
    batch: dict[str, Any],
    audit_events: list[dict[str, Any]],
    template_version: str,
) -> None:
    sheet = workbook.create_sheet("审计信息")
    sheet.append(["批次号", batch["batch_no"], "结算月份", batch["month"]])
    sheet.append(["批次状态", batch["status"], "项目负责人", batch.get("project_owner") or ""])
    sheet.append(["计算ID", calculation["id"], "规则版本", calculation["rule_version"]])
    sheet.append(["确认人", calculation.get("confirmed_by") or "", "确认时间", calculation.get("confirmed_at") or ""])
    sheet.append(["模板版本", template_version, "生成时间", datetime.now().astimezone().isoformat(timespec="seconds")])
    sheet.append([])
    sheet.append(["时间", "操作者", "动作", "对象类型", "对象ID", "修改前", "修改后", "请求ID"])
    _style_header(sheet, 7, 8)
    for event in audit_events:
        sheet.append([
            event.get("created_at"), event.get("actor"), event.get("action"), event.get("object_type"),
            event.get("object_id"), _safe_json(event.get("before")) if event.get("before") is not None else "",
            _safe_json(event.get("after")) if event.get("after") is not None else "", event.get("request_id"),
        ])
    sheet.freeze_panes = "A8"
    _auto_width(sheet)


def _clear_all_formulas(workbook) -> None:
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell, Cell) and cell.data_type == "f":
                    cell.value = None


def _assert_export_safe(workbook, *, mode: str) -> None:
    errors: list[str] = []
    prohibited_content: list[str] = []
    formulas: list[str] = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell, Cell) and isinstance(cell.value, str):
                    if any(text in cell.value for text in BANNED_EXPORT_TEXT):
                        prohibited_content.append(f"{sheet.title}!{cell.coordinate}")
                if not isinstance(cell, Cell) or cell.data_type != "f":
                    continue
                formula = str(cell.value or "")
                formulas.append(f"{sheet.title}!{cell.coordinate}")
                if "#REF!" in formula or "[" in formula:
                    errors.append(f"{sheet.title}!{cell.coordinate}")
    if errors:
        raise ValueError(f"导出模板仍含无效或外部公式：{', '.join(errors[:10])}")
    if prohibited_content:
        raise ValueError(f"导出模板仍含不应输出的说明文字：{', '.join(prohibited_content[:10])}")
    if workbook.sheetnames != [SUMMARY_SHEET_NAME]:
        raise ValueError(f"导出必须且只能包含工作表：{SUMMARY_SHEET_NAME}")
    if mode == "values" and formulas:
        raise ValueError("数值版不应包含公式")


def create_export_workbook(
    *,
    calculation: dict[str, Any],
    batch: dict[str, Any],
    fields: list[dict[str, Any]],
    mode: str,
    export_root: Path,
    template_version: str,
    template_path: Path | None = None,
    documents: list[dict[str, Any]] | None = None,
    audit_events: list[dict[str, Any]] | None = None,
    monthly_items: list[dict[str, Any]] | None = None,
    summary_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode not in {"formula", "values"}:
        raise ValueError("导出模式必须是 formula 或 values")

    documents = documents or []
    audit_events = audit_events or []
    items = monthly_items if monthly_items is not None else [{
        "calculation": calculation,
        "batch": batch,
        "fields": fields,
        "documents": documents,
        "audit_events": audit_events,
    }]
    if not items and summary_context is None:
        raise ValueError("该月份没有已审核项目")
    report_month = str(items[0]["batch"]["month"] if items else batch['month'])
    for item in items:
        if item["calculation"].get("status") != "CONFIRMED":
            raise ValueError("只有已确认的计算结果可以正式导出")
        if str(item["batch"].get("month")) != report_month:
            raise ValueError("月度汇总不能包含其他月份的项目")
        if any(not field.get("confirmed") for field in item.get("fields") or []):
            raise ValueError("存在未确认字段，禁止正式导出")

    export_root.mkdir(parents=True, exist_ok=True)
    export_id = str(uuid4())
    mode_label = "公式版" if mode == "formula" else "数值版"
    file_name = f"{report_month}员工月奖汇总_{mode_label}_{export_id[:6]}.xlsx"
    file_path = export_root / file_name

    if summary_context is not None or any(str(item["batch"].get("form_type", "")).startswith("STD_") for item in items):
        from app.services.standard_export import write_standard_summary
        if template_path is None or not template_path.is_file():
            raise ValueError("未安装新版月奖汇总模板")
        totals = write_standard_summary(items, template_path, file_path, mode, report_month, summary_context)
        return {"file_path": str(file_path), "sha256": sha256(file_path.read_bytes()).hexdigest(), "mode": mode, "template_version": template_version, "report_month": report_month, **totals}

    workbook, summary, _ = _create_base_workbook(template_path, file_path)
    _write_summary(
        summary,
        items=items,
        mode=mode,
    )

    _keep_only_summary(workbook, summary)

    if mode == "values":
        _clear_all_formulas(workbook)
    else:
        try:
            workbook.calculation.fullCalcOnLoad = True
            workbook.calculation.forceFullCalc = True
        except AttributeError:
            pass

    _remove_all_color_blocks(workbook)
    _assert_export_safe(workbook, mode=mode)
    workbook.save(file_path)
    workbook.close()
    digest = sha256(file_path.read_bytes()).hexdigest()
    report_total = sum(
        Decimal(str(item["calculation"].get("confirmed_total") or item["calculation"]["rounded_total"]))
        for item in items
    )
    return {
        "file_path": str(file_path),
        "sha256": digest,
        "mode": mode,
        "template_version": template_version,
        "report_month": report_month,
        "report_total": str(report_total.quantize(Decimal("0.01"))),
        "project_count": len(items),
    }
