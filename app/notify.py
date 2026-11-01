import json
import logging
import urllib.request

log = logging.getLogger("notify")

SEND_TYPES = {"NEW_FLIGHT", "NEW_ROUTE", "PRICE_DROP", "RECORD_LOW", "LAST_MINUTE"}


def send_events(cfg, events):
    s = cfg.get("secrets", {})
    tok, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHAT_ID")
    if not (cfg.get("alerts", {}).get("telegram") and tok and chat):
        return
    msgs = [e["message"] for e in events if e["type"] in SEND_TYPES]
    # Telegram limit is 4096 chars per message – pack into chunks
    chunk = ""
    for m in msgs:
        if len(chunk) + len(m) + 2 > 3800:
            _tg(tok, chat, chunk)
            chunk = ""
        chunk += m + "\n\n"
    if chunk:
        _tg(tok, chat, chunk)


def _tg(tok, chat, text):
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # noqa
        log.warning("telegram failed: %s", e)
