import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_config(path=None):
    path = Path(path or os.environ.get("FT_CONFIG", ROOT / "config.json"))
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("secrets", {})
    for key in ("SERPAPI_KEY", "TRAVELPAYOUTS_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        val = os.environ.get(key) or cfg["secrets"].get(key)
        if val:
            cfg["secrets"][key] = val
    if os.environ.get("FT_DEMO") == "1":
        cfg["demo_mode"] = True
    db = Path(cfg.get("db_path", "data/flights.db"))
    if not db.is_absolute():
        db = ROOT / db
    db.parent.mkdir(parents=True, exist_ok=True)
    cfg["db_path"] = str(db)
    return cfg
