from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IL = ZoneInfo("Asia/Jerusalem")


def now_il():
    return datetime.now(IL).replace(tzinfo=None)


def iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse(s):
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
