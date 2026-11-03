# 员工月奖自动核算系统部署交接说明

## 1. 交付包说明

本部署包包含应用源码、静态前端、后端依赖清单、正式 Excel 模板、数据库初始化逻辑、测试、备份/恢复脚本，以及 Windows 和 Docker Compose 部署配置。

出于数据安全考虑，部署包**不包含**以下内容：

- 真实 `.env`、管理员口令、审核令牌、API 密钥；
- 现有 SQLite 数据库；
- 员工上传材料、证明附件和导出文件；
- Python 虚拟环境、Node.js 依赖、运行日志和 Git 历史。

收到压缩包后，请先核对同目录提供的 SHA-256 文件，再解压到服务器的固定目录。路径可包含中文，但建议使用简短路径，例如 `D:\Apps\monthly-award-system`。

## 2. 推荐部署架构

当前推荐方案为 Windows 服务器局域网部署：

~~~text
集团局域网用户
      │
      │  HTTP / TCP 8000（仅 LocalSubnet）
      ▼
FastAPI：前端页面 + API
      │
      ├── SQLite、上传文件、导出文件
      └── 独立识别 worker
~~~

不需要域名、公网 IP、Cloudflare、WARP 或路由器端口映射。正式使用前应由集团 IT 为服务器保留固定 DHCP 地址，并确认无线网络/VLAN 允许使用者访问服务器。

## 3. Windows 部署

### 3.1 前置条件

- 64 位 Windows 10/11 或 Windows Server 2019 及以上；
- 64 位 Python 3.12；
- 可访问 Python 软件源，或由运维提前准备离线 wheel 包；
- 服务器 TCP `8000` 未被占用；
- 至少 10GB 可用磁盘空间，正式数据量较大时应另行扩容；
- 用于配置防火墙的本地管理员权限。

### 3.2 解压与安装依赖

在 PowerShell 中进入解压后的项目目录：

~~~powershell
cd D:\Apps\monthly-award-system
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install --upgrade pip
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
~~~

如服务器不能联网，请在同版本 Windows/Python 环境预先下载依赖：

~~~powershell
py -3.12 -m pip download -r backend\requirements.txt -d wheelhouse
~~~

把 `wheelhouse` 一并传到服务器后安装：

~~~powershell
backend\.venv\Scripts\python.exe -m pip install --no-index --find-links wheelhouse -r backend\requirements.txt
~~~

### 3.3 创建生产配置

~~~powershell
Copy-Item .env.example .env
~~~

编辑 `.env`，至少完成以下配置：

~~~dotenv
APP_ENV=production
REVIEWER_TOKEN=<高强度随机值>
ADMIN_PASSWORD=<高强度管理员口令>
ADMIN_SESSION_SECRET=<高强度随机值>
~~~

可用 Python 生成随机值：

~~~powershell
py -3.12 -c "import secrets; print(secrets.token_urlsafe(48))"
~~~

说明：

- 员工页面右上角“审核员入口”使用 `ADMIN_PASSWORD`，验证成功后生成最长 8 小时的管理员会话并进入审核工作台；
- `REVIEWER_TOKEN` 是独立审核令牌，可作为备用审核登录方式；
- `ADMIN_SESSION_SECRET` 用于签署管理员会话和下载链接，必须随机且不得与密码相同；
- `.env` 不得提交仓库、放入共享盘或通过聊天工具发送；
- 若使用图片模型，在 `.env` 设置 `ZAI_API_KEY`；留空时图片会转人工录入，XLSX 和本地 OCR 流程仍可运行。

### 3.4 配置 Windows 防火墙

以管理员身份运行 PowerShell：

~~~powershell
New-NetFirewallRule -DisplayName "员工月奖系统（局域网）" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -RemoteAddress LocalSubnet -Profile Domain,Private,Public
~~~

规则必须保持 `RemoteAddress=LocalSubnet`。不要设置为 `Any`，不要配置公网 NAT/端口映射。

### 3.5 首次启动

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra\start_lan.ps1
~~~

启动脚本会：

- 强制使用生产模式；
- 启动 FastAPI 服务并监听 `0.0.0.0:8000`；
- 启动独立识别 worker；
- 将 PID 和日志写入 `work\lan-runtime\`；
- 等待 `/health` 健康检查通过后返回。

停止服务：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra\stop_lan.ps1
~~~

### 3.6 配置开机运行

生产服务器应由运维使用 Windows 计划任务、WinSW、NSSM 或现有服务管理平台托管启动脚本。建议要求：

- 使用专用低权限服务账户；
- 服务器启动或服务账户登录时执行 `infra\start_lan.ps1`；
- 失败后自动重试并监控 `work\lan-runtime\*.stderr.log`；
- 停机或升级前执行 `infra\stop_lan.ps1`；
- 不要配置多个实例同时使用同一个 SQLite 数据库。

## 4. Docker Compose 备选部署

如服务器已部署 Docker Engine 或 Docker Desktop，可使用：

~~~powershell
Copy-Item .env.example .env
# 编辑 .env 并配置生产口令和密钥
docker compose up --build -d
docker compose ps
~~~

Docker 方式的局域网访问端口为 `4173`：

~~~text
http://<服务器局域网IP>:4173/preview/
~~~

Docker 数据保存在命名卷 `monthly_award_data`，备份保存在 `monthly_award_backups`。防火墙应仅允许 `LocalSubnet` 访问 TCP `4173`。Windows 原生部署和 Docker 部署不要同时启动。

## 5. 验收检查

### 5.1 服务器本机

~~~powershell
Invoke-RestMethod http://127.0.0.1:8000/health
~~~

Windows 原生部署应满足：

- `status` 为 `ok`；
- `worker_mode` 为 `separate`；
- `reviewer_auth` 为 `required`；
- `admin_auth` 为 `configured`。

打开：

~~~text
http://127.0.0.1:8000/preview/
~~~

### 5.2 集团局域网终端

在另一台连接集团网络的电脑上打开：

~~~text
http://<服务器固定局域网IP>:8000/preview/
~~~

检查：

1. 页面样式完整，右下角/页面状态显示后端已连接；
2. 员工可以创建申报并上传测试材料；
3. 管理员口令错误时无法进入审核工作台；
4. 管理员口令正确时自动进入审核员版本；
5. 手机蜂窝网络或集团外部网络无法访问；
6. 如果同一 Wi-Fi 仍不可访问，请集团 IT 检查客户端隔离、VLAN ACL 和服务器防火墙，不要通过开放公网端口规避。

### 5.3 自动测试

~~~powershell
$env:PYTHONPATH='backend'
backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
~~~

交付版本应全部通过后再投入使用。

## 6. 数据目录与备份

Windows 原生部署默认数据位置：

| 内容 | 路径 |
|---|---|
| SQLite 数据库 | `backend\storage\monthly_award.sqlite3` |
| 上传材料 | `backend\storage\uploads\` |
| 导出文件 | `backend\storage\exports\` |
| 运行日志/PID | `work\lan-runtime\` |

数据库、上传材料和导出文件必须作为一个一致的数据集备份。手工备份和恢复方法见 `docs\BACKUP_AND_RESTORE.md`。建议：

- 每日自动备份；
- 至少保留最近 7 个版本；
- 备份目标不得与应用数据处于同一块磁盘；
- 定期在隔离环境演练恢复；
- 备份介质按员工薪酬数据的敏感等级进行访问控制和加密。

## 7. 升级与回滚

升级前：

1. 执行停止脚本；
2. 完整备份数据库、上传和导出目录；
3. 保留当前应用目录；
4. 将新版本解压到新目录，不要直接覆盖运行目录；
5. 复制生产 `.env`，确认模板版本和规则版本后启动；
6. 完成健康检查和业务冒烟测试后再切换局域网入口。

回滚时停止新版本，恢复旧应用目录及与其匹配的数据备份。不要用旧程序直接打开已经由更高版本迁移过的数据库，除非变更说明明确兼容。

## 8. 安全边界

- 本系统是局域网业务应用，不代表所有集团 Wi-Fi 用户都应获得访问权限；应由 IT 使用 VLAN、防火墙或终端准入进一步限制范围；
- 员工申报入口无需登录，任何能访问该局域网地址的人都可以发起申报；审核、导出和管理功能有独立鉴权；
- 当前 HTTP 流量未加密。涉及正式薪酬数据时，建议由集团 IT 在内网反向代理上配置 HTTPS；
- 管理员口令、审核令牌和会话密钥需要定期轮换；
- 不要把 API 文档 `/docs` 或后端端口开放到公网。

## 9. 相关文件

- `README.md`：项目能力和开发说明；
- `docs\LAN_DEPLOYMENT.md`：简版局域网部署说明；
- `docs\BACKUP_AND_RESTORE.md`：备份恢复说明；
- `infra\start_lan.ps1` / `infra\stop_lan.ps1`：Windows 启停脚本；
- `infra\backup.py` / `infra\restore.py`：备份恢复工具；
- `.env.example`：环境变量模板；
- `docker-compose.yml`：Docker Compose 配置。
