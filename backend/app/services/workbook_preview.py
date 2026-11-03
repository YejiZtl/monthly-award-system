"""Local, read-only HTML rendering of uploaded workbooks. Never execute formulas."""
from datetime import date, datetime, time
from html import escape
from pathlib import Path
import re
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


STYLE = '''body{font:15px/1.5 system-ui;margin:24px;color:#182332;background:#f6f8fa}
a{color:#1754ac}section{margin:20px 0}.workbook-scroll{overflow:auto;background:white;margin:12px 0}
.excel-sheet{border-collapse:collapse;table-layout:fixed}.excel-sheet td{border:1px solid #ddd;padding:4px 6px;white-space:pre-wrap;overflow-wrap:anywhere;box-sizing:border-box}
.sheet-name{font-size:18px}img{max-width:100%;max-height:900px}nav a{margin-right:18px}
.cache-missing{color:#986000}details{margin:20px 0}.field-summary{border-collapse:collapse}.field-summary td,.field-summary th{padding:8px;border:1px solid #ccc}'''


def _color(color, fallback):
    if color and color.type == 'rgb' and isinstance(color.rgb, str) and re.fullmatch(r'[A-Fa-f0-9]{8}', color.rgb):
        return '#' + color.rgb[-6:]
    return fallback


def _value(value, fmt):
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, time):
        return value.strftime('%H:%M')
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        if '%' in fmt:
            decimals = len(re.search(r'\.([0#]+)', fmt)[1]) if re.search(r'\.([0#]+)', fmt) else 0
            return f'{value * 100:.{min(decimals, 8)}f}%'
        decimal_match = re.search(r'\.([0#]+)', fmt)
        if decimal_match:
            return format(value, (',' if ',' in fmt else '') + '.' + str(min(len(decimal_match[1]), 8)) + 'f')
        return str(int(value)) if int(value) == value else f'{value:g}'
    return str(value)


def _cell_style(cell):
    parts = []
    if cell.font.b:
        parts.append('font-weight:bold')
    if cell.font.i:
        parts.append('font-style:italic')
    if cell.font.sz:
        parts.append(f'font-size:{min(36, max(8, float(cell.font.sz))):g}pt')
    parts.append('color:' + _color(cell.font.color, '#182332'))
    if cell.fill.patternType == 'solid':
        parts.append('background:' + _color(cell.fill.fgColor, '#ffffff'))
    if cell.alignment.horizontal in {'left', 'center', 'right', 'justify'}:
        parts.append('text-align:' + cell.alignment.horizontal)
    vertical = {'center':'middle', 'top':'top', 'bottom':'bottom'}.get(cell.alignment.vertical, 'middle')
    parts.append('vertical-align:' + vertical)
    for side in ('top','right','bottom','left'):
        border = getattr(cell.border, side)
        if border and border.style:
            parts.append(f'border-{side}:1px solid ' + _color(border.color, '#555555'))
    return ';'.join(parts)


def render_workbook_preview(path, preferred_sheet=None, section_id='workbook'):
    with ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError('文件解压后过大，不支持在线预览')
        for item in archive.infolist():
            if item.filename.endswith('.xml'):
                content = archive.read(item).upper()
                if b'<!DOCTYPE' in content or b'<!ENTITY' in content:
                    raise ValueError('不支持含实体声明的工作簿')
    book = load_workbook(path, data_only=False, keep_links=False)
    cached = load_workbook(path, data_only=True, keep_links=False)
    try:
        sheets = [sheet for sheet in book if sheet.sheet_state == 'visible']
        sheets.sort(key=lambda s: s.title != preferred_sheet)
        body = []
        if len(sheets) > 1:
            body.append('<nav>' + ''.join(f'<a href="#{escape(section_id)}-{i}">{escape(s.title)}</a>' for i,s in enumerate(sheets[:12])) + '</nav>')
        for index, sheet in enumerate(sheets[:12]):
            data = cached[sheet.title]
            max_row, max_col = min(sheet.max_row, 300), min(sheet.max_column, 32)
            dimensions = {}
            for dimension in sheet.column_dimensions.values():
                for col in range(dimension.min or 1, min(dimension.max or dimension.min or 1, max_col) + 1):
                    dimensions[col] = dimension
            visible_cols = [c for c in range(1, max_col + 1) if not (dimensions.get(c) and dimensions[c].hidden)]
            visible_rows = [r for r in range(1, max_row + 1) if not sheet.row_dimensions[r].hidden]
            merged, covered = {}, set()
            for area in sheet.merged_cells.ranges:
                if area.min_row > max_row or area.min_col > max_col:
                    continue
                rows = [r for r in visible_rows if area.min_row <= r <= area.max_row]
                cols = [c for c in visible_cols if area.min_col <= c <= area.max_col]
                if not rows or not cols:
                    continue
                anchor = (rows[0], cols[0])
                merged[anchor] = (len(rows), len(cols), area.min_row, area.min_col)
                covered.update((r,c) for r in rows for c in cols if (r,c) != anchor)
            body.append(f'<section id="{escape(section_id)}-{index}"><h2 class="sheet-name">{escape(sheet.title)}</h2>')
            if sheet.max_row > max_row or sheet.max_column > max_col:
                body.append('<p>部分内容超出预览范围，请下载原件查看完整表格。</p>')
            body.append('<div class="workbook-scroll"><table class="excel-sheet"><colgroup>')
            for col in visible_cols:
                width = (dimensions[col].width if col in dimensions else sheet.sheet_format.defaultColWidth) or 13
                body.append(f'<col style="width:{min(600, max(35, int(width*7+5)))}px">')
            body.append('</colgroup><tbody>')
            for row in visible_rows:
                height = sheet.row_dimensions[row].height or 18
                body.append(f'<tr style="height:{height:g}pt">')
                for col in visible_cols:
                    if (row,col) in covered:
                        continue
                    span = merged.get((row,col), (1,1,row,col))
                    cell = sheet.cell(span[2],span[3])
                    value = data.cell(span[2],span[3]).value
                    missing = cell.data_type == 'f' and value is None
                    displayed = '未缓存（请在 Excel 中计算保存后重传）' if missing else _value(value,cell.number_format)
                    body.append(f'<td rowspan="{span[0]}" colspan="{span[1]}" style="{_cell_style(cell)}"' + (' class="cache-missing"' if missing else '') + '>' + escape(displayed) + '</td>')
                body.append('</tr>')
            body.append('</tbody></table></div></section>')
        return ''.join(body)
    finally:
        book.close()
        cached.close()


def render_document_preview(document, documents=(), title=None, preferred_sheet=None):
    title = title or document['file_name']
    body = f'<h1>{escape(title)}</h1><p>以下为保存的上传原件；计算字段的人工调整请参见申报核对记录。</p>'
    ordered = [document] + [d for d in documents if d['id'] != document['id']]
    for item in ordered:
        url = '/api/v1/documents/' + escape(item['id'], quote=True) + '/content'
        name = escape(item['file_name'])
        body += f'<section><h2>{name}</h2><a href="{url}?download=true">下载原件</a>'
        if item.get('mime') in {'image/png','image/jpeg'}:
            body += f'<p><a href="{url}" target="_blank" rel="noopener"><img src="{url}" alt="{name}" loading="lazy"></a></p>'
        elif Path(item.get('storage_path','')).suffix.lower() == '.xlsx':
            try:
                body += render_workbook_preview(item['storage_path'], preferred_sheet, 'workbook-' + item['id'])
            except (OSError, ValueError, KeyError):
                body += '<p>表格预览暂不可用，请下载原件查看。</p>'
        body += '</section>'
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title) + '</title><style>' + STYLE + '</style></head><body>' + body + '</body></html>'
