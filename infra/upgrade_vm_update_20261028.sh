#!/usr/bin/env bash
# Run from the extracted update package: sudo bash infra/upgrade_vm_update_20261028.sh
set -Eeuo pipefail
umask 027
app=/opt/monthly-award
data=/var/lib/monthly-award
env_file=/etc/monthly-award/app.env
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
python="$app/.venv/bin/python"
if [[ $EUID -ne 0 ]]; then echo '请使用 sudo 运行更新脚本。' >&2; exit 1; fi
if [[ ! -x "$python" || ! -f "$app/backend/app/main.py" || ! -f "$env_file" ]]; then
  echo '未发现标准 VMware 安装目录，未进行修改；请按部署说明核对路径。' >&2; exit 1
fi
if [[ "$package" == "$app" ]]; then echo '请在独立解压目录运行，不能覆盖后再备份。' >&2; exit 1; fi
if [[ ! -f "$package/UPDATE_SHA256SUMS.txt" ]]; then echo '缺少更新文件校验清单。' >&2; exit 1; fi
(cd "$package" && sha256sum --quiet -c UPDATE_SHA256SUMS.txt)
backup="/var/backups/monthly-award/update-20261028-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
chmod 700 "$backup"
cp -p "$env_file" "$backup/app.env"
tar --exclude='__pycache__' --exclude='*.pyc' -czf "$backup/code.tar.gz" -C "$app" backend/app frontend/preview frontend/app frontend/public templates
echo "备份目录：$backup"
was_active=0
systemctl is-active --quiet monthly-award && was_active=1
restore_on_error() {
  code=$?
  trap - ERR
  echo '更新检查失败，正在恢复原程序与配置。' >&2
  systemctl stop monthly-award-worker monthly-award || true
  tar -xzf "$backup/code.tar.gz" -C "$app"
  cp -p "$backup/app.env" "$env_file"
  # Migration adds tables and nullable columns. Keep the superset schema,
  # original records and any files; the old code remains compatible.
  if [[ $was_active -eq 1 ]]; then systemctl start monthly-award monthly-award-worker || true; fi
  echo "已执行回退。请保留备份和诊断日志：$backup" >&2
  exit "$code"
}
trap restore_on_error ERR
systemctl stop monthly-award-worker monthly-award
"$python" - "$data/monthly_award.sqlite3" "$backup/database.sqlite3" <<'PY'
import sqlite3,sys
from pathlib import Path
source=Path(sys.argv[1])
if source.is_file():
    with sqlite3.connect(str(source)) as src, sqlite3.connect(sys.argv[2]) as dst:
        src.backup(dst)
PY
cp -a "$package/backend/app/." "$app/backend/app/"
cp -a "$package/frontend/preview/." "$app/frontend/preview/"
cp -a "$package/frontend/app/." "$app/frontend/app/"
mkdir -p "$app/frontend/public/preview" "$app/frontend/public/app"
cp -a "$package/frontend/public/preview/." "$app/frontend/public/preview/"
cp -a "$package/frontend/public/app/." "$app/frontend/public/app/"
cp -a "$package/templates/." "$app/templates/"
"$python" - "$env_file" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);lines=p.read_text(encoding='utf-8-sig').splitlines()
key='STANDARD_TEMPLATE_PATH';value='/opt/monthly-award/templates/excel/monthly_award_standard_2026_10.xlsx'
lines=[line for line in lines if line.split('=',1)[0].strip()!=key]
lines.append(key+'='+value)
p.write_text('\n'.join(lines)+'\n',encoding='utf-8')
PY
chown -R monthlyaward:monthlyaward "$app/backend/app" "$app/frontend" "$app/templates"
"$python" -m compileall -q "$app/backend/app"
systemctl start monthly-award monthly-award-worker
healthy=0
for attempt in {1..20}; do
  if "$python" - <<'PY'
import json,urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3) as response:
    body=json.load(response)
assert body['status']=='ok' and body['version']=='0.7.4'
with urllib.request.urlopen('http://127.0.0.1:8000/api/v1/templates/STD_K/download',timeout=8) as response:
    assert response.read(2)==b'PK'
PY
  then healthy=1; break; fi
  sleep 2
done
[[ $healthy -eq 1 ]]
systemctl is-active --quiet monthly-award-worker
trap - ERR
echo '更新完成。数据库、已上传材料、历史导出、账号密码保持原配置。'
echo '请在浏览器按 Ctrl+F5，然后验证初审、复审、会议终审、材料返回和导出归档。'
echo "备份保存在 $backup；可用 infra/diagnose_vm.sh 采集本机诊断。"
