"""Render the supplied monthly summary without changing the source workbook."""
from __future__ import annotations

from copy import copy
from decimal import Decimal
from pathlib import Path
import re
from math import ceil
from typing import Any
from unicodedata import east_asian_width

from openpyxl import load_workbook
from openpyxl.comments import Comment
from app.services.summary_review import DEFAULT_RULE_NOTES, effective_amounts


FIXED_ROWS = {"STD_I": 12, "STD_K": 14, "STD_L": 15, "STD_M": 16, "STD_N": 17, "STD_O": 18, "STD_Q": 19}
PROJECT_TYPES = {"STD_PROJECT", "STD_SERVICE"}


def _text(cell, value: Any) -> None:
    cell.value = None if value is None else str(value)
    if cell.value is not None:
        cell.data_type = "s"


def _append_reason(cell, reason: Any) -> None:
    text = str(cell.value or '')
    missing = []
    for line in str(reason or '').splitlines():
        line = line.strip().removeprefix('会议修改原因：').strip()
        if line and line not in text and line not in missing:
            missing.append(line)
    if missing:
        _text(cell, text + ('\n' if text else '') + '会议修改原因：' + '\n'.join(missing))


def _show_full_remark(summary, row: int) -> None:
    """Give appended meeting notes real space in Excel and printed summaries."""
    cell = summary.cell(row, 13)
    if not cell.value:
        return
    alignment = copy(cell.alignment)
    alignment.wrap_text = True
    alignment.vertical = 'top'
    alignment.shrink_to_fit = False
    cell.alignment = alignment
    font_size = float(cell.font.sz or 11)
    width = float(summary.column_dimensions['M'].width or 13)
    # Excel widths count Latin characters; CJK characters occupy two units.
    capacity = max(4, (width - 2) * 11 / font_size)
    lines = sum(max(1, ceil(sum(2 if east_asian_width(char) in {'W','F'} else 1 for char in line) / capacity))
                for line in str(cell.value).split('\n'))
    needed = lines * (font_size * 1.3 + 2) + 6
    summary.row_dimensions[row].height = min(409.5, max(float(summary.row_dimensions[row].height or 15), needed))


def write_standard_summary(items, template_path, file_path, mode, month, context=None) -> dict:
    context = context if context is not None else (items[0] if items else {})
    if mode not in {"formula", "values"}:
        raise ValueError("不支持的导出模式")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("结算月份格式错误")
    if Path(template_path).resolve() == Path(file_path).resolve():
        raise ValueError("导出文件不能覆盖标准模板")
    seen = set()
    project_names = set()
    for item in items:
        batch = item["batch"]
        kind = batch["form_type"]
        if kind not in FIXED_ROWS and kind not in PROJECT_TYPES:
            raise ValueError(f"存在旧版或未支持的表格类型 {kind}，请转换并重新审核后导出")
        if batch["month"] != month:
            raise ValueError("不能将不同结算月份的数据合并导出")
        if item["calculation"].get("status") != "CONFIRMED":
            raise ValueError("只能导出已审核确认的申报")
        if kind in FIXED_ROWS:
            if kind in seen:
                raise ValueError(f"同月 {kind} 存在多份已审核申报，请审核员合并纠正，避免重复计算基础奖")
            seen.add(kind)
        else:
            name = next((str(field.get('value_normalized') or '').strip() for field in item.get('fields', []) if field['path'] == 'project.name'), '')
            if name and name in project_names:
                raise ValueError("存在同名项目的重复申报，请核对后导出，避免两种项目表重复计奖")
            project_names.add(name)

    workbook = load_workbook(template_path, keep_links=False)
    summary = workbook.worksheets[0]
    for sheet in list(workbook.worksheets)[1:]:
        workbook.remove(sheet)
    # Defined names referring to removed input sheets are not part of the output.
    workbook.defined_names.clear()
    summary.sheet_state = "visible"
    year, number = month.split("-")
    title = str(summary["B1"].value or "广播电视制作中心月奖核算汇总")
    title = re.sub(r"\d{4}\s*年(?:度)?\s*\d{1,2}\s*月", f"{year}年 {int(number)}月", title)
    _text(summary["B1"], title)
    project_cells = [copy(summary.cell(20, column)) for column in range(1, 15)]
    project_dimension = copy(summary.row_dimensions[20])
    formula_caches = {}
    report_total = Decimal('0')
    # Reference amounts are examples, never current-month entitlements.
    for row in range(3, 20):
        for column in range(4, 15):
            summary.cell(row, column).value = None
            summary.cell(row, column).hyperlink = None
            summary.cell(row, column).comment = None
        _text(summary.cell(row, 13), DEFAULT_RULE_NOTES.get(row))
    for row in FIXED_ROWS.values():
        for column in range(4, 9):
            summary.cell(row, column).value = None
        summary.cell(row, 12).value = None
        summary.cell(row, 12).hyperlink = None
        summary.cell(row, 11).value = None
        summary.cell(row, 14).value = None
    summary.delete_rows(20, max(1, summary.max_row - 19))
    next_project_row = 20
    row_keys = {f'fixed:{n}':n for n in range(3,20)}
    for item in items:
        batch, calculation = item["batch"], item["calculation"]
        kind = batch["form_type"]
        fields = {field["path"]: field.get("value_normalized") for field in item.get("fields", [])}
        if kind in FIXED_ROWS:
            row = FIXED_ROWS[kind]
        else:
            row = next_project_row
            next_project_row += 1
            summary.row_dimensions[row] = copy(project_dimension)
            summary.row_dimensions[row].index = row
            for column, source in enumerate(project_cells, 1):
                target = summary.cell(row, column)
                target._style = copy(source._style)
                target.alignment = copy(source.alignment)
                target.protection = copy(source.protection)
            summary.cell(row, 2, row - 2)
            _text(summary.cell(row, 3), fields.get("project.name") or batch.get("batch_no"))
        components = (calculation.get("metadata") or {}).get("summary_components")
        row_keys[f'batch:{batch["id"]}'] = row
        if not isinstance(components, dict) or any(key not in components for key in ("equipment", "technical", "profit", "other")):
            workbook.close()
            raise ValueError("缺少标准模板奖金分项，请重新计算并审核后导出")
        for column, key in enumerate(("equipment", "technical", "profit"), 4):
            summary.cell(row, column, float(Decimal(str(components[key]))))
        total = calculation.get("confirmed_total")
        if total is None:
            workbook.close()
            raise ValueError("已审核申报缺少核定金额")
        approved = Decimal(str(total))
        report_total += approved
        if mode == 'formula':
            adjustment = approved - sum((Decimal(str(components[key])) for key in ('equipment','technical','profit')), Decimal('0'))
            literal = format(adjustment.normalize(), 'f')
            formula = f'=SUM(D{row}:F{row})' + ('+' if adjustment >= 0 else '') + literal
            summary.cell(row, 8, formula)
            summary.cell(row, 8).comment = Comment(f'审核核定额：{approved}元。公式为分项之和加核定差额{adjustment}元（含原表其他奖励及审核调整）。修改分项将重新计算；数值版保持审核时金额。', '月奖系统')
            formula_caches[f'H{row}'] = str(approved)
        else:
            summary.cell(row, 8, float(approved))
        _text(summary.cell(row, 11), fields.get("project.introducer"))
        _text(summary.cell(row, 14), fields.get("project.owner") or batch.get("project_owner"))
        url = item.get("evidence_url")
        _text(summary.cell(row, 12), "查看表单和证明" if url else None)
        summary.cell(row, 12).hyperlink = url or None
        if item.get("remark") is not None:
            _text(summary.cell(row, 13), item["remark"])
        elif kind in PROJECT_TYPES:
            _text(summary.cell(row, 13), DEFAULT_RULE_NOTES[20])

    for row, saved in context.get("fixed_remarks", {}).items():
        if saved is not None and 3 <= int(row) <= 19:
            _text(summary.cell(int(row), 13), saved["text"])
    meeting_cells = context.get('meeting_cells',{})
    effective = {f'fixed:{n}':effective_amounts(f'fixed:{n}',overrides=meeting_cells.get(f'fixed:{n}')) for n in range(3,20)}
    for item in items:
        batch, calc = item['batch'],item['calculation']
        key = f"fixed:{FIXED_ROWS[batch['form_type']]}" if batch['form_type'] in FIXED_ROWS else f"batch:{batch['id']}"
        effective[key] = effective_amounts(key,batch['form_type'],calc['confirmed_total'],calc['metadata']['summary_components'],meeting_cells.get(key))
    report_total = Decimal(0)
    actual_total = Decimal(0)
    formula_caches = {}
    excluded_keys = set(context.get('excluded_keys',[]))
    excluded_rows = {row_keys[key] for key in excluded_keys if key in row_keys}
    effective = {key:value for key,value in effective.items() if row_keys[key] not in excluded_rows}
    for row in excluded_rows:
        summary.row_dimensions[row].hidden = True
        for column in range(4,15):
            summary.cell(row,column).value = None
            summary.cell(row,column).hyperlink = None
    for key, amounts in effective.items():
        row = row_keys[key]
        for name,column in (('equipment',4),('technical',5),('profit',6),('other',7)):
            summary.cell(row,column,float(Decimal(amounts[name])))
        total = Decimal(amounts['total'])
        report_total += total
        residual = total - sum((Decimal(amounts[n]) for n in ('equipment','technical','profit','other')),Decimal(0))
        if mode == 'formula':
            summary.cell(row,8,f'=SUM(D{row}:G{row})' + ('+' if residual>=0 else '') + format(residual,'f'))
            formula_caches[f'H{row}'] = str(total)
        else:
            summary.cell(row,8,float(total))
        summary.cell(row,8).comment = Comment('应发包含其它栏；核定差额保留在合计公式中。','月奖系统')
        for name,column in (('weight',9),('actual',10)):
            value = meeting_cells.get(key,{}).get(name)
            if name == 'actual' and value is None:
                value = amounts['total']
            if name == 'actual':
                actual_total += Decimal(str(value))
                if mode == 'formula':
                    if meeting_cells.get(key,{}).get(name) is None:
                        summary.cell(row,column,f'=H{row}')
                        formula_caches[f'J{row}'] = str(value)
                        continue
            summary.cell(row,column).value = float(Decimal(value)) if value is not None else None
        reason = context.get('cell_reasons',{}).get(key,'')
        if reason:
            summary.cell(row,10).comment = Comment('会议修改原因：'+reason,'月奖系统')
            _append_reason(summary.cell(row,13), reason)
    from openpyxl.styles import PatternFill
    for supplemental in context.get('supplemental_rows',[]):
        row = next_project_row
        next_project_row += 1
        for column, source in enumerate(project_cells,1):
            target=summary.cell(row,column)
            target._style=copy(source._style)
            target.fill=PatternFill('solid',fgColor='FFFFF2CC')
        summary.cell(row,2,row-2)
        _text(summary.cell(row,3),supplemental['label'])
        amount=Decimal(supplemental['other'])
        payable=Decimal(str(supplemental.get('total') if supplemental.get('total') is not None else amount))
        actual=Decimal(str(supplemental.get('actual') if supplemental.get('actual') is not None else payable))
        summary.cell(row,7,float(amount))
        if mode=='formula':
            residual=payable-amount
            summary.cell(row,8,f'=SUM(D{row}:G{row})' + (('+' if residual>=0 else '')+format(residual,'f') if residual else ''))
            formula_caches[f'H{row}']=str(payable)
        else:
            summary.cell(row,8,float(payable))
        follows_payable = not supplemental.get('actual_manual', supplemental.get('actual') is not None)
        summary.cell(row,10,f'=H{row}' if mode=='formula' and follows_payable else float(actual))
        if mode=='formula' and follows_payable:
            formula_caches[f'J{row}']=str(actual)
        _text(summary.cell(row,13),supplemental['remark'])
        _append_reason(summary.cell(row,13), supplemental.get('reason') or context.get('cell_reasons',{}).get('supplement:'+str(supplemental.get('id',''))))
        if supplemental.get('evidence_url'):
            _text(summary.cell(row,12),'查看增补附件')
            summary.cell(row,12).hyperlink=supplemental['evidence_url']
        report_total += payable
        actual_total += actual
    last_row = next_project_row - 1
    number = 0
    for row in range(3,last_row+1):
        if summary.row_dimensions[row].hidden:
            summary.cell(row,2).value = None
            continue
        number += 1
        summary.cell(row,2,number)
        _show_full_remark(summary,row)
    from openpyxl.styles import Font
    for row in range(2,last_row+1):
        source = summary.cell(row,8)
        target = summary.cell(row,10)
        target.font = copy(source.font)
        font = copy(target.font)
        font.bold = True
        font.sz = max(float(font.sz or 11),11)
        target.font = font
        target.number_format = source.number_format
        target.alignment = copy(source.alignment)
    summary.column_dimensions['J'].hidden = False
    summary.column_dimensions['J'].width = max(float(summary.column_dimensions['H'].width or 12),14)
    total_row = last_row+1
    _text(summary.cell(total_row,3),'合计')
    for column,total in ((8,report_total),(10,actual_total)):
        cell=summary.cell(total_row,column)
        cell._style=copy(summary.cell(last_row,column)._style)
        cell.font=Font(name=cell.font.name or '宋体',size=max(float(cell.font.sz or 11),12),bold=True)
        if mode=='formula':
            cell.value=f'=SUM({cell.column_letter}3:{cell.column_letter}{last_row})'
            formula_caches[cell.coordinate]=str(total)
        else:
            cell.value=float(total)
    summary.cell(total_row,3).font=Font(name='宋体',size=12,bold=True)
    summary.row_dimensions[total_row].height=26
    for row in range(2,total_row+1):
        for column,color in ((8,'FF15803D'),(10,'FFDC2626')):
            font=copy(summary.cell(row,column).font)
            font.color=color
            font.bold=True
            summary.cell(row,column).font=font
    q_remark = summary.cell(FIXED_ROWS['STD_Q'], 13)
    q_font = copy(q_remark.font)
    q_font.color = 'FF000000'
    q_remark.font = q_font
    summary.column_dimensions['I'].hidden = True
    summary.cell(2,11,'项目引进人')
    summary.print_area = f"B1:N{total_row}"
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    workbook.save(file_path)
    workbook.close()
    if formula_caches:
        from zipfile import ZipFile, ZIP_DEFLATED
        from io import BytesIO
        source = Path(file_path).read_bytes()
        with ZipFile(BytesIO(source)) as archive, ZipFile(file_path, 'w', ZIP_DEFLATED) as output:
            for item in archive.infolist():
                content = archive.read(item.filename)
                if item.filename == 'xl/worksheets/sheet1.xml':
                    text = content.decode('utf-8')
                    for address, amount in formula_caches.items():
                        pattern = rf'(<c\b[^>]*\br="{address}"[^>]*>)(.*?)(</c>)'
                        def cache(match):
                            body = re.sub(r'<v>.*?</v>|<v\s*/>', '', match[2])
                            return match[1] + body + '<v>' + amount + '</v>' + match[3]
                        text = re.sub(pattern, cache, text, flags=re.S)
                    content = text.encode('utf-8')
                output.writestr(item, content)
    return {"report_total": str(report_total), "actual_total": str(actual_total), "project_count": len(items)}
