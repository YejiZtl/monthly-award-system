"""Package a frozen summary and its source materials for offline review.

The server workbook is unchanged. Only hyperlink XML is patched in its archived
copy, so formula caches and the reviewed spreadsheet formatting survive.
"""
from __future__ import annotations

from html import escape
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import quote
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import load_workbook

from app.services.standard_export import FIXED_ROWS
from app.services.standard_templates import STANDARDS


SHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
OFFICE_REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'


def _safe_name(name: str) -> str:
    name = str(name).replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')
    return (name[:160] or 'attachment')


def _page(title, body):
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + escape(str(title)) + '</title><style>'
            'body{font:16px/1.7 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#202020}'
            'a{color:#135f9d}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:10px;text-align:left}'
            'p,td{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body><h1>'
            + escape(str(title)) + '</h1>' + body + '</body></html>')


def _material_folder(title, used):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(title))[:100].strip(' .') or '材料'
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]', name.split('.')[0], re.I):
        name = '_' + name
    candidate = name
    number = 2
    while candidate.casefold() in used:
        candidate = f'{name}（{number}）'
        number += 1
    used.add(candidate.casefold())
    return 'materials/' + candidate


def _archived_workbook(source: bytes, row_targets: dict[int, str], url_targets: dict[str, str]) -> bytes:
    """Patch links without loading/saving the workbook and dropping caches."""
    stream = BytesIO()
    with ZipFile(BytesIO(source)) as original, ZipFile(stream, 'w', ZIP_DEFLATED) as output:
        sheet_name = 'xl/worksheets/sheet1.xml'
        rel_name = 'xl/worksheets/_rels/sheet1.xml.rels'
        sheet = ET.fromstring(original.read(sheet_name))
        rels = ET.fromstring(original.read(rel_name)) if rel_name in original.namelist() else ET.Element('Relationships', xmlns=PACKAGE_REL_NS)
        hyperlinks = sheet.find(f'{{{SHEET_NS}}}hyperlinks')
        if hyperlinks is None:
            hyperlinks = ET.Element(f'{{{SHEET_NS}}}hyperlinks')
            # OOXML orders hyperlinks before print/page settings.
            before = {'printOptions', 'pageMargins', 'pageSetup', 'headerFooter', 'drawing', 'legacyDrawing', 'extLst'}
            insertion = next((i for i, node in enumerate(sheet) if node.tag.split('}')[-1] in before), len(sheet))
            sheet.insert(insertion, hyperlinks)
        rel_by_id = {node.get('Id'): node for node in rels}
        used_ids = set(rel_by_id)
        targets_by_address = {f'L{row}': target for row, target in row_targets.items()}
        for link in hyperlinks:
            rid = link.get(f'{{{OFFICE_REL_NS}}}id')
            relation = rel_by_id.get(rid)
            if relation is None:
                continue
            target = targets_by_address.pop(link.get('ref'), None) or url_targets.get(relation.get('Target'))
            if target:
                relation.set('Target', quote(target, safe='/.-_'))
                relation.set('TargetMode', 'External')
            elif relation.get('TargetMode') == 'External':
                # An exported workbook never needs a running web application.
                relation.set('Target', 'index.html')
        for address, target in targets_by_address.items():
            index = 1
            while f'archive{index}' in used_ids:
                index += 1
            rid = f'archive{index}'
            used_ids.add(rid)
            ET.SubElement(rels, f'{{{PACKAGE_REL_NS}}}Relationship', {
                'Id': rid, 'Type': OFFICE_REL_NS + '/hyperlink',
                'Target': quote(target, safe='/.-_'), 'TargetMode': 'External'})
            ET.SubElement(hyperlinks, f'{{{SHEET_NS}}}hyperlink', {
                'ref': address, f'{{{OFFICE_REL_NS}}}id': rid, 'display': '查看本地材料和备注'})
        for member in original.infolist():
            content = original.read(member.filename)
            if member.filename == sheet_name:
                content = ET.tostring(sheet, encoding='utf-8', xml_declaration=True)
            elif member.filename == rel_name:
                content = ET.tostring(rels, encoding='utf-8', xml_declaration=True)
            output.writestr(member, content)
        if rel_name not in original.namelist():
            output.writestr(rel_name, ET.tostring(rels, encoding='utf-8', xml_declaration=True))
    return stream.getvalue()


def build_archive(workbook_path, monthly_items, supplemental_rows) -> Path:
    """Return adjacent ZIP with summary, originals, attachments and HTML notes.

    Documents and supplemental attachments must contain trusted storage_path
    values supplied by the caller. A missing original aborts export instead of
    producing a deceptively complete archive.
    """
    workbook_path = Path(workbook_path)
    source = workbook_path.read_bytes()
    book = load_workbook(BytesIO(source), data_only=True)
    try:
        summary = book.worksheets[0]
        standard = summary['B2'].value == '序号' or summary['H2'].value == '应发'
        remarks = {row: str(summary.cell(row, 13).value or '') for row in range(3, summary.max_row + 1)} if standard else {}
        labels = {row: str(summary.cell(row, 3).value or '') for row in remarks}
        visible = {row for row in remarks if not summary.row_dimensions[row].hidden}
    finally:
        book.close()
    payload: dict[str, bytes] = {}
    row_targets = {}
    url_targets = {}
    contents = []
    project_row = 20
    entries = []
    for item in monthly_items:
        batch = item['batch']
        fields = {f['path']: f.get('value_normalized') for f in item.get('fields', [])}
        row = FIXED_ROWS.get(batch.get('form_type'))
        if row is None:
            row = project_row
            project_row += 1
        title = (str(fields.get('project.name') or '').strip()
                 or STANDARDS.get(batch.get('form_type'), {}).get('label')
                 or labels.get(row) or batch.get('batch_no') or batch['id'])
        entries.append((item, row, title, item.get('documents', []), fields))
    for supplemental in supplemental_rows:
        entries.append((supplemental, project_row, supplemental['label'], supplemental.get('attachments', []), {}))
        project_row += 1
    used_folders = set()
    for item, row, title, documents, fields in entries:
        folder = _material_folder(title, used_folders)
        index_path = folder + '/index.html'
        body = '<p><a href="../../index.html">返回归档目录</a></p>'
        note = remarks.get(row) or str(item.get('remark') or '')
        body += '<h2>备注与修改原因</h2><p>' + escape(note) + '</p>'
        reason = str(item.get('reason') or item.get('final_reason') or '')
        if reason and reason not in note:
            body += '<p>' + escape(reason) + '</p>'
        body += '<h2>原始表单与证明附件</h2><ul>'
        for number, document in enumerate(documents, 1):
            file_name = str(document.get('file_name') or 'attachment')
            storage_path = document.get('storage_path')
            path = Path(storage_path) if storage_path else None
            if path is None or not path.is_file():
                raise ValueError('归档原始材料不存在：' + file_name)
            local_name = f'{number:03d}-' + _safe_name(file_name)
            payload[folder + '/' + local_name] = path.read_bytes()
            body += '<li><a href="' + escape(quote(local_name, safe='.-_'), quote=True) + '">' + escape(file_name) + '</a></li>'
        body += '</ul>' if documents else '</ul><p>本行没有上传附件。</p>'
        if fields:
            body += '<h2>核对字段</h2><table>' + ''.join('<tr><th>' + escape(str(k)) + '</th><td>' + escape(str(v) if v is not None else '') + '</td></tr>' for k, v in fields.items()) + '</table>'
        payload[index_path] = _page(title, body).encode('utf-8')
        if standard and row in visible:
            row_targets[row] = index_path
        if item.get('evidence_url'):
            url_targets[item['evidence_url']] = index_path
        contents.append('<li><a href="' + escape(quote(index_path, safe='/.-_'), quote=True) + '">' + escape(str(title)) + '</a></li>')
    root = '<p>请先完整解压本归档包，再打开汇总表或本地材料链接。</p><p><a href="' + escape(quote(workbook_path.name), quote=True) + '">打开月奖汇总表</a></p>'
    root += '<h2>原始材料与附件</h2><ul>' + ''.join(contents) + '</ul><h2>汇总备注与修改原因</h2><table><tr><th>项目</th><th>备注</th></tr>'
    root += ''.join('<tr><th>' + escape(labels[row]) + '</th><td>' + escape(remarks[row]) + '</td></tr>' for row in sorted(visible) if remarks[row]) + '</table>'
    payload['index.html'] = _page('员工月奖离线归档', root).encode('utf-8')
    payload[workbook_path.name] = _archived_workbook(source, row_targets, url_targets)
    output_path = workbook_path.with_suffix('.zip')
    temporary = output_path.with_suffix('.zip.tmp')
    try:
        with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
            for name, content in payload.items():
                archive.writestr(name, content)
        temporary.replace(output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return output_path
