# Feishu Mail Monitor

IMAP IDLE 实时飞书邮件监控 — 新邮件到达时即时推送飞书 Bot 消息卡片。

无需公网地址 · 无需轮询 · 零 API 配额消耗 · 纯 Python 标准库

## 工作原理

```
┌──────────────┐   SSL (993)    ┌──────────────┐   Bot API     ┌──────────────┐
│ imap.feishu.cn│◄─────────────►│ monitor.py  │─────────────►│  飞书私聊    │
│              │  IDLE (push)   │              │  消息卡片     │  (用户收到)   │
└──────────────┘                └──────────────┘              └──────────────┘
```

通过 IMAP IDLE 协议监听邮箱，服务器有新邮件时主动推送通知，延迟 1-3 秒。

## 特性

- 📨 **实时推送** — IMAP IDLE 事件驱动，不是轮询
- 🔴 **异常告警** — 出错时推送红色告警卡片，5 分钟同错误去重
- 🔄 **断线重连** — 网络断开自动重连，补推断连期间的邮件
- 🛡️ **兜底检查** — 每 9 分钟主动 SEARCH，防止 EXISTS 通知丢失
- 📋 **生产快照** — 脚本、systemd unit 与 `.env.template` 来自 VPS，可在下线原服务后按本文恢复
- 🐧 **跨平台** — macOS launchd / Linux systemd 均支持

## 快速开始

### 1. 生成 IMAP 专用密码

飞书桌面端 → 头像 → 设置 → 邮箱 → 第三方邮箱客户端登录 → 生成密码

> 如无权限，需管理员在 [飞书管理后台](https://feishu.cn/admin) → 邮箱 → 邮箱工具 → 第三方邮箱客户端 开启。

### 2. 安装 lark-cli

```bash
npm config set prefix ~/.local
npm install -g @larksuite/cli
lark-cli auth login
```

### 3. 验证凭据

```bash
# 验证 IMAP
python3 -c "
import imaplib
m = imaplib.IMAP4_SSL('imap.feishu.cn', 993)
m.login('your@email.com', 'your-app-password')
s, capa = m.capability()
print('IDLE supported:', 'IDLE' in str(capa))
m.logout()
"

# 验证 SMTP（可选，更快）
python3 -c "
import smtplib
with smtplib.SMTP_SSL('smtp.feishu.cn', 465) as s:
    s.login('your@email.com', 'your-app-password')
    print('SMTP OK')
"

# 验证 Bot
lark-cli api POST '/open-apis/im/v1/messages' \
  --params '{"receive_id_type":"open_id"}' \
  --data '{"receive_id":"<your_open_id>","msg_type":"text","content":"{\"text\":\"✅ Bot OK\"}"}' \
  --as bot
```

### 4. 配置

```bash
cp .env.template .env
# 编辑 .env，填入 IMAP_USER、IMAP_PASS、BOT_APP_ID、BOT_RECEIVE_ID
```

`.env` 已被 `.gitignore` 忽略。VPS 上的正式文件是 `/root/mail-monitor/.env`。

### 5. 部署

生产环境是 Linux VPS，工作目录 `/root/mail-monitor`。`feishu-mail-monitor.service` 里的路径是写死的，恢复时保持这一布局。下面的命令只在服务器上还没有 `.env` 时从模板复制，避免盖掉已经填好的密码。

```bash
sudo mkdir -p /root/mail-monitor
sudo cp feishu-mail-monitor.py /root/mail-monitor/feishu-mail-monitor.py
if [ ! -f /root/mail-monitor/.env ]; then
  sudo cp .env.template /root/mail-monitor/.env
  sudo chmod 600 /root/mail-monitor/.env
fi
# 编辑 /root/mail-monitor/.env，填入 IMAP_USER、IMAP_PASS、BOT_APP_ID、BOT_RECEIVE_ID
sudo cp feishu-mail-monitor.service /etc/systemd/system/feishu-mail-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now feishu-mail-monitor
systemctl status feishu-mail-monitor
```

改脚本后重新装上并重启：

```bash
sudo cp feishu-mail-monitor.py /root/mail-monitor/feishu-mail-monitor.py
sudo systemctl restart feishu-mail-monitor
tail -f /root/mail-monitor/monitor.log
```

unit 通过 `Environment=` 注入 `HERMES_HOME=/root/.hermes` 和 `LARK_STATE_DIR=/root/.lark-cli`，lark-cli 靠这两项目录找到 bot 身份。标准输出和错误分别追加到 `/root/mail-monitor/stdout.log` 与 `stderr.log`。

macOS launchd 的 plist 模板在 [SKILL.md](SKILL.md)。本仓库不附带 plist 文件。

## 项目结构

```
├── feishu-mail-monitor.py       # 主脚本（VPS 生产快照）
├── feishu-mail-monitor.service  # systemd unit（工作目录 /root/mail-monitor）
├── .env.template                # 生产配置模板（占位符，无密钥）
├── .gitignore                   # 忽略 .env、.last-uid、*.log
├── README.md
└── SKILL.md
```

运行时文件不要提交：`.env`（由模板复制）、`.last-uid`、`monitor.log`、`stdout.log`、`stderr.log`。

## 配置说明

`.env` 由 `.env.template` 复制。不要把 `.env`、`.last-uid` 或日志提交到 git。脚本里 `BOT_APP_ID` / `BOT_RECEIVE_ID` 的默认值是空字符串，真实 ID 只放在服务器 `.env`。

| 变量 | 必填 | 说明 |
|------|------|------|
| `IMAP_HOST` | 否 | IMAP 服务器，默认 `imap.feishu.cn` |
| `IMAP_PORT` | 否 | 端口，默认 `993` |
| `IMAP_USER` | **是** | 飞书邮箱地址 |
| `IMAP_PASS` | **是** | IMAP 专用密码（非登录密码） |
| `BOT_APP_ID` | 否 | 飞书应用 ID。生产脚本会读取；发消息使用本机已登录的 lark-cli |
| `BOT_RECEIVE_ID` | **是** | 接收通知的用户 open_id |
| `WORK_DIR` | 否 | `.last-uid` 与 `monitor.log` 所在目录，默认脚本所在目录。生产为 `/root/mail-monitor` |

systemd unit 另外注入（不写在 `.env.template` 里）：

| 变量 | 生产值 | 说明 |
|------|--------|------|
| `HERMES_HOME` | `/root/.hermes` | lark-cli bot 身份目录。缺失时 API 返回 `not_configured` |
| `LARK_STATE_DIR` | `/root/.lark-cli` | lark-cli 状态目录 |
| `HOME` | `/root` | 服务用户的家目录 |
| `PATH` | `/usr/local/bin:/usr/bin:/bin:/usr/local/bin` | 保证能找到 `lark-cli` |

脚本内写死、不读环境变量：IDLE 超时 9 分钟，重连等待 30 秒，相同错误告警去重 5 分钟。

## 通知效果

**新邮件** — 蓝色卡片：
```
📧 新邮件
┌─────────────────────────────┐
│ 主题: 会议通知              │ 发件人: hr@company.com
│ 时间: 2026-07-01 10:30      │
├─────────────────────────────┤
│ 明天下午 2 点全员会议...     │
└─────────────────────────────┘
```

**异常告警** — 红色卡片：
```
🚨 邮件监控异常
┌─────────────────────────────┐
│ 时间: 2026-07-01 10:35      │
│ 位置: IMAP 连接中断         │
│ 错误: socket timeout        │
├─────────────────────────────┤
│ 服务将继续自动重试          │
└─────────────────────────────┘
```

## 常见问题

| 症状 | 解决 |
|------|------|
| `wrong authorization code` | 重新生成 IMAP 密码，检查管理后台是否开启第三方登录 |
| Bot 报 `not_configured` | 生产 unit 已设置 `HERMES_HOME=/root/.hermes` 与 `LARK_STATE_DIR=/root/.lark-cli`，确认这两行还在 |
| IDLE 检测不到新邮件 | 已内置兜底：每 9 分钟主动 SEARCH |
| `lark-cli auth status --json` 失败 | 去掉 `--json`，该 flag 不存在 |
| 脚本改动未生效 | 重启服务（`systemctl restart` 或 `launchctl unload/load`） |

## 踩坑记录

详见 [SKILL.md](SKILL.md) 的「Pitfalls & Lessons Learned」章节，包含 10 条生产环境踩过的坑。

## 环境要求

- Python 3.9+（纯标准库，无需 pip install）
- lark-cli（npm 全局安装）
- 飞书邮箱账号 + IMAP 专用密码

## License

MIT
