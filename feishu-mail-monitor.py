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
