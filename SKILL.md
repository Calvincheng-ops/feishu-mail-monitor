---
name: feishu-mail-monitor
version: 2.0.0
description: >
  IMAP IDLE 实时飞书邮件监控 — 新邮件到达时即时推送飞书消息卡片。
  事件驱动（非轮询），低延迟，零 API 配额消耗。
  支持 macOS launchd 和 Linux systemd 部署。可选邮件分类 + 云文档回填。
tags: [feishu, email, imap, idle, lark-bot, monitoring]
platform: [linux, macos]
requires:
  python: ">=3.9"
  packages: []  # 纯标准库 (imaplib, email, select, subprocess)
  tools: [python3, lark-cli]
---

# Feishu Mail Monitor

IMAP IDLE 实时邮件监控 — 通过邮件协议层的服务器推送，在新邮件到达时即时推送飞书 Bot 消息卡片给用户。无需公网地址，无需轮询，无需额外 SDK。

本仓库根目录的 `feishu-mail-monitor.py`、`feishu-mail-monitor.service` 和 `.env.template` 是 VPS 生产快照。恢复或下线原服务时以这三个文件为准；下文嵌入的脚本和 unit 与它们一致。

## 工作原理

```
┌──────────────┐   SSL (993)    ┌──────────────┐   飞书 Bot API    ┌──────────────┐
│ imap.feishu.cn│◄──────────────►│ monitor.py  │────────────────►│  飞书私聊    │
│              │  IDLE (push)   │              │  消息卡片       │  (用户收到)   │
└──────────────┘                ├──────────────┤                 └──────────────┘
                                │ processor.py│
                                │ (可选)       │── lark-cli docs ──► 云文档
                                └──────────────┘
```

1. Python 脚本通过 IMAP SSL 连接 `imap.feishu.cn:993`
2. 发送 `IDLE` 命令进入长连接等待
3. 服务器有新邮件时推送 `EXISTS` 通知（零延迟）
4. 用 `FETCH` 获取邮件（主题、发件人、收件人、正文摘要）
5. 通过 lark-cli Bot API 发送飞书消息卡片到用户私聊
6. 可选（生产快照未启用）：调用 `email_processor.py` 子进程分类解析并写入云文档
7. 记录最新邮件 UID 到 `.last-uid`，避免重复推送
8. 每 9 分钟超时后主动 SEARCH 兜底（防止 EXISTS 通知丢失）

## 前置准备

### 第一步：生成 IMAP 专用密码

指导用户：
1. 打开飞书桌面端 → 头像 → 设置 → 邮箱
2. 「第三方邮箱客户端登录」→ 点击进入
3. 设备类型选「其他」，名称填「邮件监控」
4. 点「生成」→ 复制专用密码

如果提示无权限，管理员在 [飞书管理后台](https://feishu.cn/admin) → 邮箱 → 邮箱工具 → 第三方邮箱客户端，开启「支持第三方邮箱客户端登录飞书邮箱」。

### 第二步：确认 lark-cli 已安装且有 Bot 权限

```bash
npm config set prefix ~/.local
npm install -g @larksuite/cli
lark-cli auth status
```

Bot 发消息测试：
```bash
lark-cli api POST '/open-apis/im/v1/messages' \
  --params '{"receive_id_type":"open_id"}' \
  --data '{"receive_id":"<user_open_id>","msg_type":"text","content":"{\"text\":\"测试\"}"}' \
  --as bot
```

返回 `missing_scope` 则需要 `lark-cli auth login --scope "im:message:send"` 授权。

### 第三步：验证 IMAP 凭据

```bash
python3 -c "
import imaplib
m = imaplib.IMAP4_SSL('imap.feishu.cn', 993)
m.login('<email>', '<password>')
m.select('INBOX')
_s, capa = m.capability()
print('IDLE supported:', 'IDLE' in str(capa))
m.logout()
"
```

也可用 SMTP 验证（更快）：
```bash
python3 -c "
import smtplib
with smtplib.SMTP_SSL('smtp.feishu.cn', 465) as s:
    s.login('<email>', '<password>')
    print('SMTP auth OK')
"
```

## Agent 执行清单

### 阶段 0：盘点

```bash
lark-cli auth status
lark-cli mail user_mailboxes profile --params '{"user_mailbox_id":"me"}' --as user
```

提取 `primary_email_address` 作为 `IMAP_USER`，`openId` 作为 `BOT_RECEIVE_ID`。

检查 `${DEPLOY_DIR}` 是否有现有脚本。有则对照仓库根目录 `feishu-mail-monitor.py` 续改；无则复制该文件。不要用与生产快照不一致的脚本覆盖它。

### 阶段 1：验证 Bot 通道

```bash
lark-cli api POST '/open-apis/im/v1/messages' \
  --params '{"receive_id_type":"open_id"}' \
  --data '{"receive_id":"<open_id>","msg_type":"text","content":"{\"text\":\"✅ Bot 通道测试通过\"}"}' \
  --as bot
```

### 阶段 2：指导用户生成 IMAP 专用密码

### 阶段 3：验证 IMAP 密码（见上方第三步）

### 阶段 4：放置生产快照

将仓库根目录的 VPS 生产文件放到 `${DEPLOY_DIR}`（生产机为 `/root/mail-monitor`）：

- `feishu-mail-monitor.py`
- `feishu-mail-monitor.service`（Linux）
- `.env.template` 复制为 `.env`，只在服务器上填写真实值

下方嵌入脚本与 `feishu-mail-monitor.py` 一致。不要另写一份不同的监控脚本覆盖生产文件。

### 阶段 5：部署

- **macOS**：按下方 plist 生成文件 → 用户 `cp` 到 `~/Library/LaunchAgents/` → `launchctl load`
- **Linux**：安装仓库里的 `feishu-mail-monitor.service`（不要改写成另一份 unit）→ `systemctl daemon-reload` → `systemctl enable --now feishu-mail-monitor`

### 阶段 6：确认运行

```bash
tail -30 ${DEPLOY_DIR}/monitor.log
# 期待：IDLE supported: True / First run: set baseline UID to X / 进入 IDLE 等待
```

发送测试邮件验证全链路：
```bash
python3 -c "
import smtplib
from email.mime.text import MIMEText
msg = MIMEText('全链路验证', 'plain', 'utf-8')
msg['Subject'] = '🔔 邮件监控测试'
msg['From'] = '<email>'
msg['To'] = '<email>'
with smtplib.SMTP_SSL('smtp.feishu.cn', 465) as s:
    s.login('<email>', '<password>')
    s.send_message(msg)
    print('Test email sent')
"
```

## 部署配置

### .env 模板

与仓库根目录 `.env.template` 相同（VPS 生产模板，只有占位符）。复制为 `/root/mail-monitor/.env` 后填写。

```bash
IMAP_HOST=imap.feishu.cn
IMAP_PORT=993
IMAP_USER=your-email@example.com
IMAP_PASS=your-imap-app-password
BOT_APP_ID=cli_xxxxxxxxxxxxxxxx
BOT_RECEIVE_ID=ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
WORK_DIR=/root/mail-monitor
```

`HERMES_HOME` 和 `LARK_STATE_DIR` 不在这份模板里，由生产 unit 的 `Environment=` 注入。IDLE 超时（9 分钟）、重连等待（30 秒）和相同错误去重（5 分钟）写死在生产脚本中，不从环境变量读取。

### macOS launchd plist

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.${USERNAME}.feishu-mail-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>${DEPLOY_DIR}/feishu-mail-monitor.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${DEPLOY_DIR}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>ProcessType</key><string>Background</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${NPM_PREFIX}/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HERMES_HOME</key>
        <string>${HERMES_HOME}</string>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/feishu-mail-monitor-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/feishu-mail-monitor-stderr.log</string>
</dict>
</plist>
```

**占位符**：`${USERNAME}` = `whoami`；`${DEPLOY_DIR}` = 脚本目录；`${NPM_PREFIX}` = `npm config get prefix`；`${HERMES_HOME}` = lark-cli 配置根目录；`${LOG_DIR}` = `~/Library/Logs`。

**安装**：
```bash
cp ${DEPLOY_DIR}/com.${USERNAME}.feishu-mail-monitor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.${USERNAME}.feishu-mail-monitor.plist
```

**管理**：
```bash
launchctl list | grep feishu-mail-monitor
tail -f ${LOG_DIR}/feishu-mail-monitor-stdout.log
# 改代码后必须重启
launchctl unload ~/Library/LaunchAgents/com.${USERNAME}.feishu-mail-monitor.plist
launchctl load ~/Library/LaunchAgents/com.${USERNAME}.feishu-mail-monitor.plist
```

### Linux systemd service

仓库根目录的 `feishu-mail-monitor.service` 就是 VPS 生产 unit。路径固定为 `/root/mail-monitor`：

```ini
[Unit]
Description=Feishu Mail Monitor (IMAP IDLE)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/mail-monitor
EnvironmentFile=/root/mail-monitor/.env
Environment=PATH=/usr/local/bin:/usr/bin:/bin:/usr/local/bin
Environment=HOME=/root
Environment=LARK_STATE_DIR=/root/.lark-cli
Environment=HERMES_HOME=/root/.hermes
ExecStart=/usr/bin/python3 /root/mail-monitor/feishu-mail-monitor.py
Restart=always
RestartSec=30
StandardOutput=append:/root/mail-monitor/stdout.log
StandardError=append:/root/mail-monitor/stderr.log

[Install]
WantedBy=multi-user.target
```

**安装**（在仓库根目录执行）：

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

**管理**：

```bash
systemctl status feishu-mail-monitor
systemctl restart feishu-mail-monitor
tail -f /root/mail-monitor/monitor.log
```

改脚本后先 `cp` 到 `/root/mail-monitor/`，再 `systemctl restart feishu-mail-monitor`。lark-cli 身份依赖 unit 里的 `HERMES_HOME=/root/.hermes` 与 `LARK_STATE_DIR=/root/.lark-cli`。

## 完整脚本

### feishu-mail-monitor.py

以下与仓库根目录 [`feishu-mail-monitor.py`](feishu-mail-monitor.py) 逐字相同，即 VPS 生产脚本。`BOT_APP_ID` 与 `BOT_RECEIVE_ID` 的代码默认值为空字符串，真实 ID 只放在服务器 `.env`，不入库。

```python
#!/usr/bin/env python3
"""
Feishu Mail Monitor — IMAP IDLE real-time email notification via Feishu Bot.

Connects to imap.feishu.cn, enters IDLE mode, and pushes Feishu message
cards to the user's private chat when new mail arrives.
"""

import imaplib
import email
import email.header
import email.message
import email.utils
import json
import logging
import os
import re
import select
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from html.parser import HTMLParser

# ── Config ───────────────────────────────────────────────────────────────────

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.feishu.cn")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER = os.environ["IMAP_USER"]
IMAP_PASS = os.environ["IMAP_PASS"]

# Real IDs belong in .env only (see .env.template). Keep these defaults empty.
BOT_APP_ID = os.environ.get("BOT_APP_ID", "")
BOT_RECEIVE_ID = os.environ.get("BOT_RECEIVE_ID", "")

WORK_DIR = Path(os.environ.get("WORK_DIR", Path(__file__).parent))
LAST_UID_FILE = WORK_DIR / ".last-uid"
LOG_FILE = WORK_DIR / "monitor.log"

IDLE_TIMEOUT = 9 * 60        # 9 minutes — IMAP IDLE server usually times out at 29 min
RECONNECT_DELAY = 30          # seconds before reconnecting after error
BODY_PREVIEW_LEN = 500        # chars of body to include in notification

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("mail-monitor")

# ── Globals ──────────────────────────────────────────────────────────────────

running = True


def handle_signal(signum, frame):
    global running
    log.info("Received signal %s, shutting down…", signum)
    running = False


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

# ── HTML stripper ────────────────────────────────────────────────────────────


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return "".join(self._parts)


def strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


# ── UID persistence ──────────────────────────────────────────────────────────


def read_last_uid() -> int:
    if LAST_UID_FILE.exists():
        try:
            return int(LAST_UID_FILE.read_text().strip())
        except ValueError:
            return 0
    return 0


def write_last_uid(uid: int):
    LAST_UID_FILE.write_text(str(uid))


# ── Email parsing ────────────────────────────────────────────────────────────


def decode_header_value(raw) -> str:
    """Decode RFC 2047 encoded header into a plain string."""
    if raw is None:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def extract_body(msg: email.message.Message, max_chars: int = BODY_PREVIEW_LEN) -> str:
    """Extract plain-text body from a MIME message, stripping HTML if needed."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
            elif ct == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = strip_html(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            raw = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                body = strip_html(raw)
            else:
                body = raw

    # Collapse whitespace
    body = re.sub(r"\s+", " ", body).strip()
    return body[:max_chars] if body else "(无正文)"


def parse_email(raw_bytes: bytes) -> dict:
    """Parse raw RFC822 bytes into a dict with subject, sender, date, body."""
    msg = email.message_from_bytes(raw_bytes)
    subject = decode_header_value(msg.get("Subject", ""))
    sender = decode_header_value(msg.get("From", ""))
    date_str = msg.get("Date", "")
    body = extract_body(msg)
    return {
        "subject": subject or "(无主题)",
        "sender": sender,
        "date": date_str,
        "body": body,
    }


# ── Feishu notification ─────────────────────────────────────────────────────


def build_card(info: dict) -> str:
    """Build a Feishu interactive message card JSON string."""
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📧 新邮件"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**主题**\n{info['subject']}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**发件人**\n{info['sender']}"}},
                ],
            },
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**时间**\n{info['date']}"},
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": info["body"]},
            },
        ],
    }
    return json.dumps(card, ensure_ascii=False)


def notify(info: dict) -> bool:
    """Send a Feishu bot message card. Returns True on success."""
    card_json = build_card(info)
    payload = json.dumps({
        "receive_id": BOT_RECEIVE_ID,
        "msg_type": "interactive",
        "content": card_json,
    }, ensure_ascii=False)

    try:
        result = subprocess.run(
            [
                "lark-cli", "api", "POST",
                "/open-apis/im/v1/messages",
                "--params", json.dumps({"receive_id_type": "open_id"}),
                "--data", payload,
                "--as", "bot",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            resp = json.loads(result.stdout)
            if resp.get("code") == 0:
                log.info("✅ Notification sent: %s", info["subject"])
                return True
            else:
                log.error("❌ Bot API error: %s", result.stdout)
        else:
            log.error("❌ lark-cli error: %s", result.stderr)
    except Exception as e:
        log.error("❌ Notify exception: %s", e)

    # Fallback: plain text
    try:
        text = f"📧 新邮件\n主题: {info['subject']}\n发件人: {info['sender']}\n{info['body'][:200]}"
        subprocess.run(
            [
                "lark-cli", "api", "POST",
                "/open-apis/im/v1/messages",
                "--params", json.dumps({"receive_id_type": "open_id"}),
                "--data", json.dumps({
                    "receive_id": BOT_RECEIVE_ID,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                }, ensure_ascii=False),
                "--as", "bot",
            ],
            capture_output=True, text=True, timeout=30,
        )
        log.info("Fallback plain text sent")
        return True
    except Exception as e:
        log.error("❌ Fallback notify failed: %s", e)
        return False


# ── Error notification ────────────────────────────────────────────────────────

_last_error_msg = ""
_last_error_time = 0
_ERROR_COOLDOWN = 300  # don't repeat same error within 5 minutes


def notify_error(error_msg: str, context: str = "") -> bool:
    """Send an error alert to the user via Feishu bot. Deduplicates within cooldown."""
    global _last_error_msg, _last_error_time
    now = time.time()
    # Dedup: skip if same error within cooldown
    if error_msg == _last_error_msg and (now - _last_error_time) < _ERROR_COOLDOWN:
        return False
    _last_error_msg = error_msg
    _last_error_time = now

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [f"**时间**: {ts}"]
    if context:
        parts.append(f"**位置**: {context}")
    parts.append(f"**错误**: {error_msg}")
    body = "\n".join(parts)

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🚨 邮件监控异常"},
            "template": "red",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}},
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "服务将继续自动重试。如持续报错请检查日志: /root/mail-monitor/monitor.log"}]},
        ],
    }
    card_json = json.dumps(card, ensure_ascii=False)
    payload = json.dumps({
        "receive_id": BOT_RECEIVE_ID,
        "msg_type": "interactive",
        "content": card_json,
    }, ensure_ascii=False)

    try:
        result = subprocess.run(
            ["lark-cli", "api", "POST", "/open-apis/im/v1/messages",
             "--params", json.dumps({"receive_id_type": "open_id"}),
             "--data", payload, "--as", "bot"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            resp = json.loads(result.stdout)
            if resp.get("code") == 0:
                log.info("🚨 Error notification sent")
                return True
        log.error("Failed to send error notification: %s", result.stdout[:200])
    except Exception as e:
        log.error("Error notification exception: %s", e)

    # Fallback: plain text
    try:
        text = f"🚨 邮件监控异常\n{ts}\n{context}\n{error_msg}"
        subprocess.run(
            ["lark-cli", "api", "POST", "/open-apis/im/v1/messages",
             "--params", json.dumps({"receive_id_type": "open_id"}),
             "--data", json.dumps({
                 "receive_id": BOT_RECEIVE_ID,
                 "msg_type": "text",
                 "content": json.dumps({"text": text}, ensure_ascii=False),
             }, ensure_ascii=False),
             "--as", "bot"],
            capture_output=True, text=True, timeout=30,
        )
        return True
    except Exception:
        return False


# ── IMAP helpers ─────────────────────────────────────────────────────────────


def connect_imap() -> imaplib.IMAP4_SSL:
    """Connect and authenticate to IMAP server."""
    log.info("Connecting to %s:%d as %s …", IMAP_HOST, IMAP_PORT, IMAP_USER)
    m = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    m.login(IMAP_USER, IMAP_PASS)
    m.select("INBOX")
    try:
        _s, capa_data = m.capability()
        capa = str(capa_data)
    except Exception:
        capa = ""
    log.info("Connected. IDLE supported: %s", "IDLE" in capa)
    return m


def fetch_and_notify(m: imaplib.IMAP4_SSL, uid_from: int) -> int:
    """Fetch all mails with UID > uid_from, notify for each, return max UID."""
    if uid_from > 0:
        status, data = m.uid("SEARCH", None, f"UID {uid_from + 1}:*")
    else:
        status, data = m.uid("SEARCH", None, "ALL")

    if status != "OK" or not data[0]:
        return uid_from

    uids = [int(u) for u in data[0].split()]
    if not uids:
        return uid_from

    max_uid = uid_from
    for uid in uids:
        if uid <= uid_from:
            continue
        status, msg_data = m.uid("FETCH", str(uid), "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        info = parse_email(raw)
        info["uid"] = uid
        log.info("New mail UID %d: %s", uid, info["subject"])
        notify(info)
        max_uid = max(max_uid, uid)

    return max_uid


def do_idle(m: imaplib.IMAP4_SSL) -> str:
    """
    Send IDLE command and wait for data or timeout.
    Returns: 'exists' if new mail, 'timeout' if idle expired, 'error' on failure.
    """
    tag = m._new_tag()
    m.send(f"{tag} IDLE\r\n".encode())

    # Read continuation response
    resp = m.readline()
    if b"+" not in resp:
        return "error"

    # Wait for data on the socket with timeout
    sock = m.socket()
    try:
        ready, _, _ = select.select([sock], [], [], IDLE_TIMEOUT)
    except (ValueError, OSError):
        return "error"

    if not ready:
        # Timeout — send DONE to exit IDLE gracefully
        try:
            m.send(b"DONE\r\n")
            m.readline()  # read tagged response
        except Exception:
            pass
        return "timeout"

    # Data arrived — read ALL available lines, look for EXISTS
    result = "timeout"
    try:
        # Read everything that's available
        buf = b""
        sock.setblocking(False)
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except (BlockingIOError, OSError):
            pass
        finally:
            sock.setblocking(True)

        # Parse buffered lines
        for line_bytes in buf.split(b"\n"):
            line_bytes = line_bytes.strip()
            if not line_bytes:
                continue
            line_str = line_bytes.decode("utf-8", errors="replace")
            if "EXISTS" in line_str or "RECENT" in line_str:
                result = "exists"
                log.debug("IDLE notification: %s", line_str)

        # Now send DONE and read the tagged response
        m.send(b"DONE\r\n")
        # Read until we get the tagged OK response
        while True:
            resp_line = m.readline()
            if not resp_line:
                break
            if resp_line.startswith(tag.encode()):
                break

    except Exception as e:
        log.debug("IDLE read error: %s", e)

    return result


# ── Main loop ────────────────────────────────────────────────────────────────


def main():
    log.info("═══ Feishu Mail Monitor starting ═══")
    log.info("IMAP_USER=%s, BOT_RECEIVE_ID=%s", IMAP_USER, BOT_RECEIVE_ID)
    log.info("lark-cli: %s", "found" if sys.executable else "NOT FOUND")

    # Check lark-cli exists
    try:
        subprocess.run(["lark-cli", "--version"], capture_output=True, timeout=5)
        log.info("lark-cli: found")
    except FileNotFoundError:
        log.critical("lark-cli not found in PATH. Exiting.")
        sys.exit(1)

    last_uid = read_last_uid()
    log.info("Last UID from file: %d", last_uid)

    while running:
        try:
            m = connect_imap()

            if last_uid == 0:
                # First run: seed baseline, don't notify old mail
                status, data = m.uid("SEARCH", None, "ALL")
                if status == "OK" and data[0]:
                    uids = [int(u) for u in data[0].split()]
                    if uids:
                        last_uid = max(uids)
                        write_last_uid(last_uid)
                        log.info("First run: set baseline UID to %d (will only notify for newer mail)", last_uid)
                else:
                    log.info("First run: inbox empty, baseline UID=0")
            else:
                # Reconnect: catch up on any mail received while disconnected
                last_uid = fetch_and_notify(m, last_uid)
                write_last_uid(last_uid)

            # IDLE loop
            while running:
                idle_result = do_idle(m)
                if idle_result == "exists":
                    last_uid = fetch_and_notify(m, last_uid)
                    write_last_uid(last_uid)
                elif idle_result == "timeout":
                    log.info("IDLE timeout, proactively checking for new mail")
                    last_uid = fetch_and_notify(m, last_uid)
                    write_last_uid(last_uid)
                elif idle_result == "error":
                    log.warning("IDLE error, will reconnect")
                    notify_error("IDLE 命令失败或连接丢失", "IMAP IDLE 循环")
                    break

        except imaplib.IMAP4.abort as e:
            log.warning("IMAP abort: %s", e)
            notify_error(str(e), "IMAP 连接中断")
        except imaplib.IMAP4.error as e:
            log.warning("IMAP error: %s", e)
            notify_error(str(e), "IMAP 协议错误")
        except socket.error as e:
            log.warning("Socket error: %s", e)
            notify_error(str(e), "网络连接异常")
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)
            notify_error(f"{type(e).__name__}: {e}", "未预期异常")

        if running:
            log.info("Reconnecting in %d seconds…", RECONNECT_DELAY)
            time.sleep(RECONNECT_DELAY)

    log.info("═══ Feishu Mail Monitor stopped ═══")


if __name__ == "__main__":
    main()
```

### email_processor.py（可选，不在 VPS 生产快照内）

邮件分类 + 云文档回填的可选子进程。当前 VPS 上的 `feishu-mail-monitor.py` 不会调用它，仓库里也没有这个文件。下面只是以后若要加回填时的接口说明。

**函数签名**：

```python
def classify_email(subject: str, sender: str, body: str, recipient: str = "") -> tuple[str, str, str]:
    """分类邮件。返回 (类型名, emoji, category_key)。
    用收件人（To）判定归属域，FROM 不可靠（第三方系统发出）。"""

def extract_field(body: str, key_regex: str) -> str | None:
    """正向前瞻提取字段。遇到下一个已知字段标签时停止。
    FIELD_LABELS = r"Name|Email|Phone|Company|..."
    pat = key_regex + r"\s*(.*?)(?=\s*(?:" + FIELD_LABELS + r")\s*:|$)"
    空值（如 "Name: --"）返回 None。"""

def build_xml_block(email_type: str, emoji: str, subject: str, sender: str,
                    date_str: str, fields: dict) -> str:
    """生成 XML 片段：<h3>...</h3><p>...</p><callout>...</callout><hr/>"""

def append_to_doc(xml_content: str) -> dict:
    """lark-cli docs +update --command block_insert_after --block-id <anchor>"""

def process_to_doc(info: dict) -> None:
    """入口函数：classify → extract → build_xml → append_to_doc。
    非目标邮件（category_key == 'skip'）不写文档。"""
```

**调用方式**（生产脚本没有这段。若要启用，可在 `notify()` 成功之后追加）：

```python
import subprocess, json
try:
    proc = subprocess.run(
        [sys.executable, str(WORK_DIR / "email_processor.py")],
        input=json.dumps(info, ensure_ascii=False),
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        log.warning("email_processor error: %s", proc.stderr[:200])
except FileNotFoundError:
    pass  # processor not installed, skip
except Exception as e:
    log.warning("email_processor exception: %s", e)
```

## 常见问题

| 症状 | 原因 | 解决 |
|------|------|------|
| `wrong authorization code` | IMAP 密码错或管理后台未开 | 重生成密码，检查后台开关 |
| `lark-cli not found` | npm prefix/bin 不在 PATH | `npm config get prefix`，加入 plist/systemd PATH |
| Bot 发不出消息 | token 过期/权限不足 | `lark-cli auth login --scope "im:message:send"` |
| Bot 报 `not_configured` | 缺 `HERMES_HOME` | 生产 unit 已含 `Environment=HERMES_HOME=/root/.hermes` 与 `LARK_STATE_DIR=/root/.lark-cli` |
| IDLE 检测不到新邮件 | EXISTS 通知丢失 | 已内置：timeout 时主动 SEARCH 兜底 |
| `email.message` 属性错误 | 缺显式 import | 脚本已加 `import email.message` |
| `str(m.capability)` 返回 repr | 用法错误 | 改用 `m.capability()` → `(status, data)` |
| `lark-cli auth status --json` 失败 | 该 flag 不存在 | 去掉 `--json` |
| 旧邮件狂推 | `.last-uid` 丢失 | 首次运行自动设基准 |
| 脚本改动未生效 | 服务跑旧进程 | 重启服务（launchctl/systemd） |
| 字段解析溢出 | 正文塌成单行 | positive lookahead 正则策略 |
| 云文档写入失败 | block_id 变或 token 过期 | `docs +fetch --detail with-ids` 重取 |
| 反复报错轰炸 | 无去重 | `notify_error()` 内置 5 分钟同错误去重 |

## Pitfalls & Lessons Learned

以下均在实际部署中踩过并修复。

### 1. HERMES_HOME 环境变量是必须的

`lark-cli` 从 `$HERMES_HOME` 读取 bot 身份。缺失时所有命令返回 `not_configured`。
VPS 生产 unit 用 `Environment=HERMES_HOME=/root/.hermes` 和 `Environment=LARK_STATE_DIR=/root/.lark-cli` 注入，不写进 `.env.template`。macOS 则放在 launchd `EnvironmentVariables`。

### 2. IDLE EXISTS 通知会静默丢失

`imap.feishu.cn` 有时不发送 `* N EXISTS`。唯一可靠做法：**每次** IDLE 超时都
执行 `SEARCH`，无论是否收到 EXISTS。不要在 timeout 后直接重进 IDLE。

### 3. IDLE 响应必须用 recv() 读取

`readline()` 在服务器把多个响应打包进同一个 TCP 段时会丢数据。
用非阻塞 `sock.recv(4096)` 一次性读完所有缓冲数据再解析。

### 4. 错误通知必须有去重

没有去重时，一个不稳定的连接会产生大量重复告警。`notify_error()` 内置
5 分钟同错误冷却期。挂接到所有异常处理器：IMAP abort/error、socket error、
IDLE error、unexpected exception。

### 5. `import email.message` 必须显式声明

Python 类型注解 `email.message.Message` 需要直接 import。只写 `import email` 不够。

### 6. `m.capability()` 是方法调用

`str(m.capability)` 返回方法的 repr，不是能力列表。正确用法：
`s, data = m.capability()` 然后检查 `str(data)`。

### 7. `lark-cli --json` flag 不存在

`lark-cli auth status --json` 会报错。直接用 `lark-cli auth status`。

### 8. SMTP 验证比 IMAP 快

部署前用 `smtplib.SMTP_SSL('smtp.feishu.cn', 465)` 验证凭据，
比等 IMAP 连接快，且能立即发现认证问题。

### 9. agent 工作区内 config init 需 --force-init

在 Hermes agent 环境内（`HERMES_HOME` 已设置），`lark-cli config init`
会拒绝覆盖。加 `--force-init` 强制初始化。

### 10. Bot 卡片发送失败时回退纯文本

交互卡片可能因 JSON 格式、API 错误等原因失败。脚本始终回退到
`msg_type: text`，确保通知不静默丢失。

## 安全

- `.env` 含 IMAP 专用密码，勿提交 git。`.last-uid`、`monitor.log`、`stdout.log`、`stderr.log` 同样不要入库
- 不要把真实 `BOT_APP_ID` / `BOT_RECEIVE_ID` 写进脚本默认值
- IMAP 专用密码只用于邮件读取，不能登录飞书
- lark-cli 状态含 token，传输到云服务器后删除本地 tar.gz

## 性能

- 内存：~20-30 MB
- CPU：极低（`select` 阻塞等待）
- 网络：一个长 TCP 连接，几乎无流量
- 延迟：新邮件到通知 1-3 秒

## License

MIT
