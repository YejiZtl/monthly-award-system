"""Assemble the source update without local data, credentials, or environments."""
from pathlib import Path
from hashlib import sha256
import json
import zipfile

root = Path(__file__).resolve().parents[1]
manifest = root / 'templates/excel/manifest.json'
data = json.loads(manifest.read_text(encoding='utf-8'))
data['standard_version'] = 'TMPL-2026.09'
data['standard_template'] = {
    'file': 'monthly_award_standard_2026_09.xlsx',
    'sha256': sha256((root / 'templates/excel/monthly_award_standard_2026_09.xlsx').read_bytes()).hexdigest(),
    'sheet_count': 9, 'summary_sheet': '汇总表', 'blank_columns': ['I','J'], 'read_only_source': True,
}
manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

main = root / 'backend/app/main.py'
source = main.read_text(encoding='utf-8')
marker = '    # Legacy definitions are retained below'
if marker in source:
    start = source.index(marker)
    end = source.index('@app.post("/api/v1/batches"', start)
    source = source[:start] + '\n\n' + source[end:]
    main.write_text(source, encoding='utf-8')

for file in (root / 'frontend/preview').iterdir():
    if file.is_file():
        target = root / 'frontend/public/preview' / file.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file.read_bytes())

excluded = {'.venv','node_modules','storage','outputs','__pycache__','.pytest_cache','.git','.next','dist','.pnpm-store'}
files = [p for p in root.rglob('*') if p.is_file() and not excluded.intersection(p.relative_to(root).parts)
         and p.name not in {'.env','SHA256SUMS.txt'} and not p.name.endswith(('.pyc','.sqlite3','.sqlite3-wal','.sqlite3-shm'))]
checksums = root / 'SHA256SUMS.txt'
checksums.write_text(''.join(f'{sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}\n' for p in sorted(files)), encoding='utf-8')
files.append(checksums)
destination = root.parent / '员工月奖系统_标准模板更新包_20260912.zip'
with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
    for file in files:
        archive.write(file, Path(root.name) / file.relative_to(root))
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
    assert not any('/storage/' in name or '/.venv/' in name or name.endswith('/.env') for name in archive.namelist())
destination.with_suffix('.zip.sha256').write_text(sha256(destination.read_bytes()).hexdigest()+'  '+destination.name+'\n', encoding='utf-8')
print(destination.name, destination.stat().st_size, 'bytes', len(files), 'files')
