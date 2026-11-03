"""Summary remarks and share pages; source files and user text are untrusted."""
from __future__ import annotations

from html import escape
from pathlib import Path
from functools import lru_cache
from openpyxl import load_workbook


FIXED_ROWS = {"STD_I": 12, "STD_K": 14, "STD_L": 15, "STD_M": 16, "STD_N": 17, "STD_O": 18, "STD_Q": 19}

# New installations start without historical amounts or personal remarks.
DEFAULT_OTHER = {}
DEFAULT_RULE_NOTES = {20: ''}


def effective_amounts(key, kind=None, approved=None, components=None, overrides=None):
    """One amount policy for review, digest and both export modes.

    I replaces its provisional base; K/L add a base to earned amounts. An
    explicit Other value replaces the displayed Other, adding only its delta
    to the approved amount. A legacy manual total remains an explicit total.
    """
    from decimal import Decimal
    components, overrides = components or {}, overrides or {}
    fixed = int(key.split(':')[1]) if key.startswith('fixed:') else None
    base = Decimal(DEFAULT_OTHER.get(fixed, 0))
    amounts = {n: Decimal(str(components.get(n) or 0)) for n in ('equipment','technical','profit')}
    if kind == 'STD_L' and not amounts['technical']:
        amounts['technical'] = Decimal(str(components.get('other') or 0))
    default_other = base
    if approved is not None:
        approved = Decimal(str(approved))
        if kind in {'STD_I','STD_M','STD_O'}:
            default_other = approved
            amounts = {n: Decimal(0) for n in amounts}
            base = Decimal(0)
        elif kind != 'STD_L':
            default_other += Decimal(str(components.get('other') or 0))
        total = approved + base
    else:
        total = base
    other = Decimal(str(overrides['other'])) if overrides.get('other') is not None else default_other
    total += other - default_other
    if overrides.get('total') is not None:
        total = Decimal(str(overrides['total']))
    return {**{n:format(v.quantize(Decimal('.01')),'f') for n,v in amounts.items()},
            'other':format(other.quantize(Decimal('.01')),'f'), 'total':format(total.quantize(Decimal('.01')),'f')}


def remark_key(batch):
    if batch["form_type"] in {"STD_PROJECT", "STD_SERVICE"}:
        return f"batch:{batch['id']}"
    row = FIXED_ROWS.get(batch["form_type"])
    return f"fixed:{row}" if row else f"batch:{batch['id']}"


def template_rows(path: Path):
    path = Path(path)
    stat = path.stat()
    return {row: dict(item) for row,item in _template_rows(str(path.resolve()),stat.st_mtime_ns,stat.st_size).items()}


@lru_cache(maxsize=8)
def _template_rows(path, mtime_ns, size):
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = book["汇总表"]
        return {row: {"label": str(sheet.cell(row, 3).value or ""), "text": DEFAULT_RULE_NOTES.get(row, '')} for row in range(3, 21)}
    finally:
        book.close()


def source_table(document, sheet_name):
    from app.services.workbook_preview import render_workbook_preview
    return render_workbook_preview(document['storage_path'], preferred_sheet=sheet_name)


def evidence_html(batch, fields, documents, labels, sheet_name):
    from app.services.workbook_preview import render_document_preview, STYLE
    values = {f['path']: f.get('value_normalized') for f in fields}
    title = str(values.get('project.name') or batch['batch_no'])
    material = next((doc for doc in documents if doc.get('document_role','MATERIAL') == 'MATERIAL'), None)
    if material:
        page = render_document_preview(material, documents, title, sheet_name)
    else:
        page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>' + STYLE + '</style><body><h1>' + escape(title) + '</h1><p>暂无提交材料。</p></body></html>'
    details = '<details><summary>查看申报核对字段（包含人工确认修改）</summary><table class="field-summary">'
    for field in fields:
        value = field.get('value_normalized')
        details += '<tr><th>' + escape(labels.get(field['path'],field['path'])) + '</th><td>' + escape(str(value) if value is not None else '') + '</td></tr>'
    return page.replace('</body>', details + '</table></details></body>')
