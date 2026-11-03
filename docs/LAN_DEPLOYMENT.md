# 集团局域网部署

该方式不使用域名、Cloudflare、WARP 或路由器端口映射。服务仅通过服务器当前所在的集团局域网访问。

## 安全配置

`.env` 应使用生产模式并配置以下三项：

~~~dotenv
APP_ENV=production
REVIEWER_TOKEN=<高强度随机值>
ADMIN_PASSWORD=<高强度密码>
ADMIN_SESSION_SECRET=<高强度随机值>
~~~

`.env` 已被 Git 忽略，不要复制到聊天、邮件或仓库。

## Windows 防火墙

以管理员身份执行以下命令，只允许本地子网访问应用端口：

~~~powershell
New-NetFirewallRule -DisplayName "员工月奖系统（局域网）" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -RemoteAddress LocalSubnet -Profile Domain,Private,Public
~~~

不要创建面向 `Any` 的规则，也不要在路由器上做端口映射。

## 启动与停止

启动后端和独立 worker：

~~~powershell
powershell -ExecutionPolicy Bypass -File infra\start_lan.ps1
~~~

停止：

~~~powershell
powershell -ExecutionPolicy Bypass -File infra\stop_lan.ps1
~~~

运行日志位于 `work/lan-runtime/`。

## 访问和验证

本机打开：

~~~text
http://127.0.0.1:8000/preview/
~~~

同一集团局域网中的其他设备使用服务器的当前 IPv4 地址，例如：

~~~text
http://<SERVER_IP>:8000/preview/
~~~

验证时应确认：

1. 页面右上角显示“后端已连接”。
2. 集团局域网内的设备可以访问，手机蜂窝网络不能访问。
3. 审核操作必须输入 `REVIEWER_TOKEN`，管理员功能必须输入管理员密码。

如果同一 Wi-Fi 的其他设备仍无法访问，通常是无线网络启用了客户端隔离或不同 VLAN。此限制不能通过修改应用绕过，需要集团 IT 将服务器与使用者放入可互访网段。

## 地址稳定性

服务器当前地址由 DHCP 分配，可能在重连或租约更新后变化。正式使用前应请集团 IT 为服务器网卡保留固定 DHCP 地址；不要自行占用一个未经分配的静态地址。
