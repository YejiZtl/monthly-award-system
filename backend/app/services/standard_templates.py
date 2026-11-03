"""Exact extraction for the eight approved monthly award templates.

Uploaded totals are authoritative. Missing Excel formula caches remain empty and
must be supplied and confirmed by a reviewer; formulas are never evaluated here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re

from openpyxl import load_workbook

from app.domain.rules import CalculationLine, _progressive_profit_lines, _result


STANDARD_TEMPLATES = {
    "STD_I": {"category": "FIXED", "label": "I 蓝创工作室", "sheet": "I-蓝创工作室", "summary_row": 12},
    "STD_K": {"category": "FIXED", "label": "K 音频后期", "sheet": "K-音频后期", "summary_row": 14},
    "STD_L": {"category": "FIXED", "label": "L 会议室音频", "sheet": "L-会议室音频", "summary_row": 15},
    "STD_M": {"category": "FIXED", "label": "M SONY维修", "sheet": "M-SONY维修", "summary_row": 16},
    "STD_N": {"category": "FIXED", "label": "N 运维部非编", "sheet": "N-运维部非编", "summary_row": 17},
    "STD_O": {"category": "FIXED", "label": "O 驾驶班", "sheet": "O-驾驶班", "summary_row": 18},
    "STD_Q": {"category": "GROUP", "label": "Q 声入浙心", "sheet": "Q-声入浙心", "summary_row": 19},
    "STD_PROJECT": {"category": "PROJECT", "label": "1 项目预决算表", "sheet": "1-项目预决算表", "summary_row": 20},
    "STD_SERVICE": {"category": "PROJECT", "label": "2 设备使用/技术服务费申报表", "sheet": "2-设备使用  技术服务费申报表", "summary_row": 20},
}
for _code, _meta in STANDARD_TEMPLATES.items():
    _meta["code"] = _code
    if _meta["category"] == "FIXED":
        _meta["category"] = "GROUP"
STANDARDS = STANDARD_TEMPLATES

FIELD_LABELS = {
    "project.name": "项目名称", "project.owner": "项目负责人", "project.introducer": "项目引进人",
    "standard.total": "上传合计金额", "standard.equipment_fee": "上传设备使用费合计",
    "standard.service_fee": "上传技术服务费合计", "standard.efp_fee": "上传EFP技术服务费",
    "standard.screen_fee": "上传大屏技术服务费", "standard.gross_profit": "上传毛利",
    "standard.session_count": "上传场次数合计", "standard.profit_1": "上传毛利1",
    "standard.profit_2": "上传毛利2", "standard.detail_warning": "明细核对提示",
}
CELLS = {
    "STD_I": {"standard.total": "D21"},
    "STD_K": {"standard.equipment_fee": "J30", "standard.gross_profit": "I45"},
    "STD_L": {"standard.session_count": "C48"},
    "STD_M": {"standard.profit_1": "I17", "standard.profit_2": "I31"},
    "STD_N": {"standard.equipment_fee": "E13"},
    "STD_O": {"standard.total": "M26"},
    "STD_Q": {"standard.total": "K31"},
    "STD_PROJECT": {"project.name": "E4", "project.owner": "E38", "project.introducer": "E39", "standard.equipment_fee": "H9", "standard.efp_fee": "H10", "standard.screen_fee": "H11", "standard.gross_profit": "E37"},
    "STD_SERVICE": {"project.name": "C4", "project.owner": "C20", "project.introducer": "H21", "standard.service_fee": "H18", "standard.equipment_fee": "H19"},
}
ANCHORS = {
    "STD_I": {"C3": "姓名", "D3": "金额", "B21": "合计预发"},
    "STD_K": {"B30": "设备使用费", "F32": "合同收入（元）", "B45": "毛利"},
    "STD_L": {"B2": "会议室音频保障", "C3": "场次"},
    "STD_M": {"B17": "毛利1", "B31": "毛利2"},
    "STD_N": {"C3": "栏目", "B13": "设备使用费合计"},
    "STD_Q": {"B31": "短视频奖励合计", "K2": "合计奖励"},
    "STD_O": {"F3": "月初里程数", "G3": "月末里程数", "B26": "奖励合计"},
    "STD_PROJECT": {"C3": "项目预决算表", "C4": "项目名称", "D9": "集团设备使用费", "D10": "EFP技术服务费", "D11": "大屏技术服务费", "C37": "毛利："},
    "STD_SERVICE": {"B4": "节目/项目名称", "B18": "技术服务费", "B19": "设备使用费", "B21": "项目引进人"},
}


def _text(value):
    return re.sub(r"\s+", "", str(value or "")).replace("：", ":")


def match_standard_layout(sheet, code):
    """Match known labels rather than filenames; retain older approved layouts."""
    matches = lambda anchors: all(_text(sheet[cell].value) == _text(label) for cell, label in anchors.items())
    newer = {
        'STD_I': ({'C3':'姓名','D3':'金额','B24':'合计预发'},{'standard.total':'D24'}),
        'STD_K': ({'C1':'音频后期--设备租赁结算','C54':'设备使用费','C69':'毛利','G56':'合同收入（元）'},{'standard.equipment_fee':'K54','standard.gross_profit':'J69'}),
        'STD_M': ({'B21':'毛利1','B43':'毛利2','I4':'维修费合计','I23':'技术费（元）'},{'standard.profit_1':'I21','standard.profit_2':'I43'}),
        'STD_N': ({'C3':'栏目','B19':'设备使用费合计'},{'standard.equipment_fee':'E19'}),
        'STD_Q': ({'B34':'短视频奖励合计','K2':'合计奖励'},{'standard.total':'K34'}),
    }
    if code in newer and matches(newer[code][0]):
        return dict(newer[code][1])
    if code == 'STD_M' and matches({'B17':'毛利1', 'B31':'毛利2', 'J4':'维修费合计', 'J19':'技术费（元）'}):
        return {'standard.profit_1':'J17', 'standard.profit_2':'J31'}
    if matches(ANCHORS[code]):
        return dict(CELLS[code])
    if code == 'STD_K':
        if matches({'C1':'音频后期--设备租赁结算', 'C44':'设备使用费', 'C59':'毛利', 'G46':'合同收入（元）'}):
            return {'standard.equipment_fee':'K44', 'standard.gross_profit':'J59'}
        for anchors, cell in [
            ({'C1':'音频后期--设备租赁结算','C2':'序号','K2':'设备使用费（元）','C44':'设备使用费'}, 'K44'),
            ({'B1':'音频后期--设备租赁结算','B2':'序号','J2':'设备使用费（元）','B30':'设备使用费'}, 'J30'),
        ]:
            if matches(anchors):
                return {'standard.equipment_fee':cell}
    return None


def detect_standard_template(path):
    workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        matches = [(sheet.title, code) for sheet in workbook for code in STANDARDS if match_standard_layout(sheet, code)]
        if not matches:
            raise ValueError('无法识别主表，请下载最新版空白模板填写')
        if len(matches) != 1:
            raise ValueError('文件包含多张主表，请拆分后分别申报')
        return matches[0][1]
    finally:
        workbook.close()


def _number(value):
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise ValueError("必须填写数字")
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValueError("必须填写数字") from None
    if not number.is_finite():
        raise ValueError("必须填写有限数字")
    return number


def _values(fields):
    if isinstance(fields, dict):
        return {key: value.get("value_normalized") if isinstance(value, dict) else value for key, value in fields.items()}
    return {item["path"]: item.get("value_normalized") for item in fields}


def extract_standard(document, code):
    """Return existing extraction-service field records; reject mismatched layouts."""
    meta = STANDARD_TEMPLATES[code]
    path = Path(document["storage_path"])
    if path.suffix.lower() != ".xlsx":
        raise ValueError("标准主表必须上传XLSX文件")
    formulas = load_workbook(path, read_only=True, data_only=False)
    cached = None
    try:
        candidates = [s for s in formulas if match_standard_layout(s, code)]
        if len(candidates) != 1:
            raise ValueError(f"请上传且仅包含一张符合{meta['label']}结构的主表")
        sheet = candidates[0]
        cached = load_workbook(path, read_only=True, data_only=True)
        data = cached[sheet.title]
        page = formulas.sheetnames.index(sheet.title) + 1
        result = []
        for field, cell in match_standard_layout(sheet, code).items():
            value = data[cell].value
            normalized = format(value, '.15g') if isinstance(value, float) else (None if value is None else str(value).strip())
            if value is not None and field in {'standard.gross_profit', 'standard.profit_1', 'standard.profit_2'}:
                try:
                    normalized = str(_number(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                except (ValueError, InvalidOperation):
                    pass
            result.append({"path": field, "value_raw": None if value is None else str(value), "value_normalized": normalized, "confidence": "0" if value is None else "0.99", "source_document_id": document.get("id", ""), "page_no": page, "bounding_box": None, "confirmed": False})
        warnings = []
        if code == "STD_SERVICE":
            rates = {7: 1500, 8: 1000, 11: 1500, 12: 1000, 14: 1000, 15: 1500, 16: 1000}
            detail_total = Decimal("0")
            complete = True
            for row in range(7, 17):
                raw = [data[f"{col}{row}"].value for col in ("E", "F")]
                if all(value in (None, "") for value in raw):
                    continue
                try:
                    expected = _number(data[f"E{row}"].value) * _number(data[f"F{row}"].value) * _number(data[f"G{row}"].value)
                    detail_total += expected
                    if row in rates and _number(data[f"G{row}"].value) != rates[row]:
                        warnings.append(f"第{row}行单价与模板收费标准{rates[row]}元不一致，请审核")
                    if expected != _number(data[f"H{row}"].value):
                        warnings.append(f"第{row}行人数、天数、单价与上传金额不一致，仍采用上传合计")
                except ValueError:
                    complete = False
                    warnings.append(f"第{row}行明细或公式结果缺失，请核对原表")
            try:
                extra = data['H17'].value
                if extra not in (None, ""):
                    detail_total += _number(extra)
                if complete and detail_total != _number(data['H18'].value):
                    warnings.append(f"明细复算合计{detail_total}与上传技术服务费合计不一致，按上传合计核算，请审核")
            except ValueError:
                warnings.append("合计或非常规服务金额无法校验，请核对原表")
        if code == "STD_PROJECT":
            for row in (9, 10, 11):
                try:
                    if _number(data[f"E{row}"].value) * _number(data[f"G{row}"].value) != _number(data[f"H{row}"].value):
                        warnings.append(f"第{row}行数量、单价与上传金额不一致，仍采用上传金额")
                except ValueError:
                    continue
        if warnings:
            result.append({"path": "standard.detail_warning", "value_raw": "；".join(warnings), "value_normalized": "；".join(warnings), "confidence": "0.99", "source_document_id": document.get("id", ""), "page_no": page, "bounding_box": None, "confirmed": False})
        return result
    finally:
        formulas.close()
        if cached is not None:
            cached.close()


def validate_standard(fields, code):
    values = _values(fields)
    errors, warnings = [], []
    if isinstance(fields, list):
        for item in fields:
            if not item.get("confirmed"):
                errors.append(f"请确认字段：{FIELD_LABELS.get(item['path'], item['path'])}")
    required = dict(CELLS[code])
    if code == 'STD_K' and 'standard.gross_profit' not in values:
        required.pop('standard.gross_profit', None)
    for field in required:
        if field in ("project.owner", "project.introducer"):
            continue
        label = FIELD_LABELS[field]
        if field == "project.name":
            if not str(values.get(field) or "").strip():
                errors.append(f"请填写{label}")
            continue
        try:
            value = _number(values.get(field))
            if value < 0 and field not in ("standard.gross_profit", "standard.profit_1", "standard.profit_2"):
                errors.append(f"{label}不能为负数")
            if field == "standard.session_count" and value != value.to_integral_value():
                errors.append("场次数必须为整数")
        except ValueError as exc:
            errors.append(f"{label}：{exc}，公式无缓存时请人工填写")
    if values.get("standard.detail_warning"):
        warnings.append(str(values["standard.detail_warning"]))
    return {"errors": errors, "warnings": warnings}


def calculate_standard(code, fields):
    validation = validate_standard(fields, code)
    if validation["errors"]:
        raise ValueError("；".join(validation["errors"]))
    values = _values(fields)
    get = lambda name: _number(values["standard." + name])
    parts = {key: Decimal("0") for key in ("equipment", "technical", "profit", "other")}
    lines = []
    warnings = validation["warnings"]
    status = "CALCULATED"
    if code in ("STD_I", "STD_O", "STD_Q"):
        parts["other"] = get("total")
    elif code == "STD_L":
        parts["technical"] = get("session_count") * 70
    elif code == "STD_M":
        parts["other"] = get("profit_1") + get("profit_2")
        if parts["other"] < 0:
            raise ValueError("维修毛利合计不能为负数，请审核")
    else:
        parts["equipment"] = get("equipment_fee") * Decimal("0.15")
        if code == "STD_SERVICE":
            parts["technical"] = get("service_fee") * Decimal("0.30")
        if code == "STD_PROJECT":
            parts["technical"] = (get("efp_fee") + get("screen_fee")) * Decimal("0.30")
        if code == 'STD_PROJECT' or (code == 'STD_K' and 'standard.gross_profit' in values):
            profit_lines, profit_warnings, status = _progressive_profit_lines(get("gross_profit"))
            lines.extend(profit_lines)
            warnings.extend(profit_warnings)
            parts["profit"] = sum((line.raw_result for line in profit_lines), Decimal("0"))
    def add_line(key, label, expression, amount, operands):
        lines.append(CalculationLine(code + '_' + key, label, expression, operands, amount))
    if code in {'STD_K', 'STD_N', 'STD_PROJECT', 'STD_SERVICE'}:
        add_line('EQUIPMENT', '设备使用费奖励', f'{get("equipment_fee")} × 15%', parts['equipment'],
                 {'base':str(get('equipment_fee')), 'rate':'0.15'})
    if code == 'STD_PROJECT':
        for field, label in [('efp_fee', 'EFP技术服务费奖励'), ('screen_fee', '大屏技术服务费奖励')]:
            add_line(field.upper(), label, f'{get(field)} × 30%', get(field)*Decimal('.30'),
                     {'base':str(get(field)), 'rate':'0.30'})
    elif code == 'STD_SERVICE':
        add_line('TECHNICAL', '技术服务费奖励', f'{get("service_fee")} × 30%', parts['technical'],
                 {'base':str(get('service_fee')), 'rate':'0.30'})
    elif code == 'STD_L':
        add_line('TECHNICAL', '会议场次奖励', f'{get("session_count")} × 70元/场', parts['technical'],
                 {'count':str(get('session_count')), 'unit_price':'70'})
    elif code == 'STD_M':
        add_line('OTHER', '维修毛利奖励', f'{get("profit_1")} + {get("profit_2")}', parts['other'],
                 {'profit_1':str(get('profit_1')), 'profit_2':str(get('profit_2'))})
    elif code in {'STD_I', 'STD_O', 'STD_Q'}:
        add_line('OTHER', '上传核定金额', str(get('total')), parts['other'], {'total':str(get('total'))})
    result = _result(code, lines, status=status, warnings=warnings).to_dict()
    result["metadata"] = {"summary_components": {key: str(value) for key, value in parts.items()}}
    return result
