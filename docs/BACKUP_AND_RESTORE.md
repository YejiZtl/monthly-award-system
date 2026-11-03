# 备份与恢复

## Docker 自动备份

Docker Compose 中的 backup 服务每天执行一次，备份内容包括：

- SQLite 一致性快照；
- uploads 原始表单；
- exports 正式导出文件。

备份保存在 monthly_award_backups 数据卷，文件名为 monthly-award-日期时间.zip，默认保留最近7个版本。

查看备份日志：

~~~powershell
docker compose logs backup
~~~

## 手工备份

~~~powershell
backend\.venv\Scripts\python.exe infra\backup.py --database backend\storage\monthly_award.sqlite3 --uploads backend\storage\uploads --exports backend\storage\exports --destination D:\monthly-award-backups --retention 7
~~~

脚本使用 SQLite backup API 生成一致性快照，执行完整性检查后再原子生成 ZIP。

## 离线恢复

恢复会覆盖当前数据库、原件和导出目录，必须先停止后端和 worker：

~~~powershell
docker compose stop backend worker backup
~~~

本机目录恢复示例：

~~~powershell
backend\.venv\Scripts\python.exe infra\restore.py --archive D:\monthly-award-backups\monthly-award-20260828-020000.zip --database backend\storage\monthly_award.sqlite3 --uploads backend\storage\uploads --exports backend\storage\exports --safety-backup-destination D:\monthly-award-backups\pre-restore --confirm RESTORE
~~~

恢复脚本会先为当前数据生成安全备份，再校验归档路径和 SQLite 完整性，最后替换目标数据。

## 恢复后检查

1. 启动服务并访问健康检查。
2. 在审核中心打开一个历史批次并查看原件。
3. 下载一个历史导出并核对 SHA-256 记录。
4. 新建一条测试批次，完成上传、核对和核算后再删除测试环境数据。
