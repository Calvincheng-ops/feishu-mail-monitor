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
- 📋 **可选扩展** — 邮件分类 + 飞书云文档自动回填
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
# 编辑 .env 填入实际值
```

### 5. 部署

**macOS (launchd)**：
```bash
cp com.yourname.feishu-mail-monitor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.yourname.feishu-mail-monitor.plist
```

**Linux (systemd)**：
```bash
sudo cp feishu-mail-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now feishu-mail-monitor
```

## 项目结构

```
├── feishu-mail-monitor.py    # 主脚本（IMAP IDLE + Bot 通知）
├── email_processor.py        # 邮件分类 + 云文档回填（可选）
├── .env                      # 配置（含密码，勿提交 git）
├── .env.template             # 配置模板
├── .last-uid                 # 最后处理的邮件 UID（自动维护）
└── monitor.log               # 运行日志
```

## 配置说明

| 变量 | 必填 | 说明 |
|------|------|------|
| `IMAP_HOST` | 否 | IMAP 服务器，默认 `imap.feishu.cn` |
| `IMAP_PORT` | 否 | 端口，默认 `993` |
| `IMAP_USER` | **是** | 飞书邮箱地址 |
| `IMAP_PASS` | **是** | IMAP 专用密码（非登录密码） |
| `BOT_RECEIVE_ID` | **是** | 接收通知的用户 open_id |
| `HERMES_HOME` | **是** | lark-cli 配置目录，如 `/root/.hermes` |
| `FEISHU_WEBHOOK` | 否 | Webhook 兜底地址 |
| `IDLE_TIMEOUT` | 否 | IDLE 超时秒数，默认 `540` |
| `RECONNECT_DELAY` | 否 | 重连等待秒数，默认 `30` |
| `ERROR_DEDUP_SECONDS` | 否 | 错误去重间隔，默认 `300` |

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
| Bot 报 `not_configured` | service 文件加 `Environment=HERMES_HOME=/root/.hermes` |
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
