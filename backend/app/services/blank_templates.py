"""Split the reference package without dropping Excel form controls.

Only the selected sheet and its dependency graph are retained. Shared strings
are inlined after clearing inputs, so removed sheets cannot leak sample data.
"""
from io import BytesIO
from pathlib import PurePosixPath
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl.utils.cell import range_boundaries, coordinate_to_tuple
from app.services.standard_templates import STANDARDS, match_standard_layout

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
CLEAR = {
    'STD_I': ['C4:E20'],
    'STD_K': ['C4:H29', 'C33:G44', 'V16'],
    'STD_L': ['B5:F47', 'C48'],
    'STD_M': ['B5:I15', 'C20:I29'],
    'STD_N': ['B4:F12', 'C14', 'E14', 'G14'],
    'STD_Q': ['B6:E30'],
    'STD_O': ['F5:G24', 'J5:M24', 'N25'],
    'STD_PROJECT': ['E4', 'H5', 'E9:E11', 'G9:H11', 'D14:I14', 'E15', 'D17:I17', 'E18', 'D20:I20', 'E21', 'D24:I24', 'D26:I26', 'D28:I28', 'D30:I30', 'D32:I32', 'E38', 'E39'],
    'STD_SERVICE': ['C4', 'E7:F16', 'E17:H17', 'H18:H19', 'C20:H20', 'H21'],
}


def blank_template(source, code):
    with ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    workbook_text = parts['xl/workbook.xml'].decode('utf-8')
    book = ET.fromstring(workbook_text)
    sheets = list(book.find(f'{{{S}}}sheets'))
    selected = next(s for s in sheets if s.attrib['name'] == STANDARDS[code]['sheet'])
    selected_index = sheets.index(selected)
    selected_rid = selected.attrib[f'{{{R}}}id']
    relroot = ET.fromstring(parts['xl/_rels/workbook.xml.rels'])
    sheet_path = None
    for rel in list(relroot):
        kind = rel.attrib['Type'].rsplit('/', 1)[-1]
        if kind == 'worksheet' and rel.attrib['Id'] == selected_rid:
            sheet_path = posixpath.normpath(posixpath.join('xl', rel.attrib['Target'])).lstrip('/') if not rel.attrib['Target'].startswith('/') else rel.attrib['Target'].lstrip('/')
        elif kind in {'worksheet', 'sharedStrings', 'calcChain', 'externalLink'}:
            relroot.remove(rel)
    if sheet_path is None:
        raise ValueError('标准工作表关系缺失')
    parts['xl/_rels/workbook.xml.rels'] = ET.tostring(relroot, encoding='utf-8', xml_declaration=True)
    # Keep the original namespace declarations for Office control extensions.
    sheet_tag = next(tag for tag in re.findall(r'<sheet\b[^>]*/>', workbook_text) if f'{selected_rid}"' in tag)
    workbook_text = re.sub(r'<sheets>.*?</sheets>', '<sheets>'+sheet_tag+'</sheets>', workbook_text, flags=re.S)
    def names(match):
        kept = []
        for tag in re.findall(r'<definedName\b[^>]*>.*?</definedName>', match.group(0), re.S):
            if f'localSheetId="{selected_index}"' in tag:
                kept.append(tag.replace(f'localSheetId="{selected_index}"', 'localSheetId="0"'))
        return '<definedNames>'+''.join(kept)+'</definedNames>' if kept else ''
    workbook_text = re.sub(r'<definedNames>.*?</definedNames>', names, workbook_text, flags=re.S)
    workbook_text = re.sub(r'(activeTab|firstSheet)="\d+"', r'\1="0"', workbook_text)
    workbook_text = re.sub(r'<calcPr\b[^>]*/>', '<calcPr fullCalcOnLoad="1" forceFullCalc="1"/>', workbook_text)
    parts['xl/workbook.xml'] = workbook_text.encode('utf-8')
    shared = ET.fromstring(parts['xl/sharedStrings.xml']) if 'xl/sharedStrings.xml' in parts else []
    clear_ranges = CLEAR[code]
    from openpyxl import load_workbook
    layout_book = load_workbook(source, read_only=True)
    try:
        layout = match_standard_layout(layout_book[STANDARDS[code]['sheet']],code) or {}
    finally:
        layout_book.close()
    if code == 'STD_I' and layout.get('standard.total') == 'D24':
        clear_ranges = ['C4:E23']
    elif code == 'STD_N' and layout.get('standard.equipment_fee') == 'E19':
        clear_ranges = ['B4:F18']
    elif code == 'STD_Q' and layout.get('standard.total') == 'K34':
        clear_ranges = ['B6:E33']
    elif code == 'STD_M' and layout.get('standard.profit_1') == 'I21':
        clear_ranges = ['B5:I18','C24:I41']
    elif code == 'STD_K' and layout.get('standard.equipment_fee') == 'K54':
        clear_ranges = ['D4:I53','D57:H68','K57:K68']
    october_audio = False
    if code == 'STD_K':
        from openpyxl import load_workbook
        layout_book = load_workbook(source, read_only=True)
        try:
            layout = match_standard_layout(layout_book[STANDARDS[code]['sheet']], code) or {}
            october_audio = layout.get('standard.equipment_fee') == 'K44'
        finally:
            layout_book.close()
        if october_audio:
            clear_ranges = ['D4:I43']
            if layout.get('standard.gross_profit') == 'J59':
                clear_ranges += ['D47:H58']
    ranges = [range_boundaries(ref) for ref in clear_ranges]
    text = parts[sheet_path].decode('utf-8')
    def cell(match):
        raw = match.group(0)
        ref = re.search(r'\br="([A-Z]+[0-9]+)"', raw).group(1)
        row, col = coordinate_to_tuple(ref)
        is_formula = re.search(r'<f(?:\s|>|/)', raw) is not None
        clear = any(left <= col <= right and top <= row <= bottom for left, top, right, bottom in ranges)
        if is_formula:
            return re.sub(r'<v(?:\s[^>]*)?>.*?</v>|<v\s*/>', '', raw, flags=re.S)
        if clear:
            opening = raw.split('>')[0].rstrip('/')
            opening = re.sub(r'\s+t="[^"]*"', '', opening)
            return opening+'/>'
        if 't="s"' in raw:
            index = int(re.search(r'<v>(\d+)</v>', raw).group(1))
            si = shared[index]
            # Serialize just the rich-string children with explicit namespaces.
            inline = '<is xmlns="'+S+'">'+''.join(ET.tostring(child, encoding='unicode') for child in si)+'</is>'
            raw = re.sub(r'<v>\d+</v>', lambda _: inline, raw).replace('t="s"', 't="inlineStr"')
        return raw
    text = re.sub(r'<c\b[^>]*?(?:/>|>.*?</c>)', cell, text, flags=re.S)
    if october_audio:
        # The supplied example leaves later work-time formulas empty. Complete
        # every supported input row in the downloadable form only.
        from html import escape
        for row in range(4, 44):
            formulas = {
                'J': f'IF(OR(H{row}="",I{row}=""),"",IF(H{row}<=I{row},I{row}-H{row},1+I{row}-H{row})*24)',
                'K': f'IF(F{row}="","",IFERROR(INDEX($B$4:$B$9,MATCH(F{row},$A$4:$A$9,0))*MAX(2,MIN(10,J{row})),"名称不匹配"))',
            }
            for col, formula in formulas.items():
                address = f'{col}{row}'
                pattern = rf'<c\b[^>]*\br="{address}"[^>]*?(?:/>|>.*?</c>)'
                existing = re.search(pattern, text, re.S)
                if existing and re.search(r'<f(?:\s|>)',existing.group(0)):
                    continue
                opening = existing.group(0).split('>')[0].rstrip('/') if existing else f'<c r="{address}"'
                opening = re.sub(r'\s+t="[^"]*"', '', opening)
                replacement = opening + '><f>' + escape(formula, quote=False) + '</f></c>'
                if existing:
                    text = text[:existing.start()] + replacement + text[existing.end():]
                else:
                    row_pattern = rf'(<row\b[^>]*\br="{row}"[^>]*>)(.*?)(</row>)'
                    text = re.sub(row_pattern, lambda m: m[1] + m[2] + replacement + m[3], text, flags=re.S)
    text = re.sub(r'tabSelected="[01]"', 'tabSelected="1"', text)
    def hide_zeros(match):
        tag = re.sub(r'\s+showZeros="[^"]*"', '', match.group(0))
        ending = '/>' if tag.endswith('/>') else '>'
        return tag[:-len(ending)] + ' showZeros="0"' + ending
    text = re.sub(r'<sheetView\b[^>]*>', hide_zeros, text)
    if code == 'STD_L':
        # The source accidentally formats the session count as a date.
        styles = ET.fromstring(parts['xl/styles.xml'])
        xfs = styles.find(f'{{{S}}}cellXfs')
        tag = re.search(r'<c\b[^>]*\br="C48"[^>]*>', text).group(0)
        index = int(re.search(r'\bs="(\d+)"', tag).group(1))
        from copy import deepcopy
        style = deepcopy(xfs[index])
        style.set('numFmtId', '0')
        style.set('applyNumberFormat', '1')
        xfs.append(style)
        xfs.set('count', str(len(xfs)))
        text = text.replace(tag, re.sub(r'\bs="\d+"', f's="{len(xfs)-1}"', tag), 1)
        # Retain root namespaces used by Office compatibility attributes.
        styles_text = parts['xl/styles.xml'].decode('utf-8')
        fragment = ET.tostring(xfs, encoding='unicode')
        styles_text = re.sub(r'<cellXfs\b[^>]*>.*?</cellXfs>', lambda _: fragment, styles_text, flags=re.S)
        parts['xl/styles.xml'] = styles_text.encode('utf-8')
    if code == 'STD_N':
        text = text.replace('2026年', '年')
    parts[sheet_path] = text.encode('utf-8')
    # Rebuild the root relationships without author/thumbnail metadata.
    rootrels = ET.Element(f'{{{P}}}Relationships')
    ET.SubElement(rootrels, f'{{{P}}}Relationship', Id='rId1', Type=R+'/officeDocument', Target='xl/workbook.xml')
    parts['_rels/.rels'] = ET.tostring(rootrels, encoding='utf-8', xml_declaration=True)
    keep = {'_rels/.rels'}
    def visit(part):
        if part in keep:
            return
        keep.add(part)
        p = PurePosixPath(part)
        relpath = str(p.parent / '_rels' / (p.name+'.rels'))
        if relpath not in parts:
            return
        keep.add(relpath)
        for rel in ET.fromstring(parts[relpath]):
            if rel.attrib.get('TargetMode') == 'External':
                continue
            target = rel.attrib['Target']
            resolved = target.lstrip('/') if target.startswith('/') else posixpath.normpath(str(p.parent / target))
            if resolved not in parts:
                raise ValueError('模板依赖缺失：'+resolved)
            visit(resolved)
    visit('xl/workbook.xml')
    types = ET.fromstring(parts['[Content_Types].xml'])
    for item in list(types):
        if 'PartName' in item.attrib and item.attrib['PartName'].lstrip('/') not in keep:
            types.remove(item)
    parts['[Content_Types].xml'] = ET.tostring(types, encoding='utf-8', xml_declaration=True)
    keep.add('[Content_Types].xml')
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for part in sorted(keep):
            archive.writestr(part, parts[part])
    return output.getvalue()
