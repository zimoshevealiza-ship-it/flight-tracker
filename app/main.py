"""Entry point.

  python -m app.main serve          # web dashboard + hourly scanner
  python -m app.main scan           # single scan now (for cron)
  python -m app.main export site    # static dashboard for GitHub Pages
  python -m app.main seed-demo 45   # fill DB with 45 days of simulated history (demo mode)
"""
import json
import logging
import sys
import threading
import time
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import load_config
from .db import DB
from .learner import Learner
from .scanner import run_scan
from .timeutil import iso, now_il

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")
STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.db = DB(cfg["db_path"])
        self.learner = Learner(self.db)
        self.scan_lock = threading.Lock()
        self.last_run = None

    def scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return {"busy": True}
        try:
            res = run_scan(self.db, self.cfg)
            self.db.prune(iso(now_il()))
            self.last_run = now_il()
            return {"requests": res["requests"], "offers": res["offers"],
                    "events": len(res["events"]), "errors": res["errors"][:5]}
        finally:
            self.scan_lock.release()

    def scheduler(self):
        interval = self.cfg["scan"].get("interval_minutes", 60) * 60
        while True:
            try:
                self.scan()
            except Exception:  # noqa
                log.exception("scan failed")
            time.sleep(interval)

    # ---------- API ----------
    def api(self, path, qs):
        g = lambda k, d=None: qs.get(k, [d])[0]  # noqa
        db = self.db
        today = now_il().date()
        if path == "/api/summary":
            return {
                "offers": db.one("SELECT COUNT(*) c FROM offers")["c"],
                "observations": db.one("SELECT COUNT(*) c FROM observations")["c"],
                "events_24h": db.one("SELECT COUNT(*) c FROM events WHERE created_at>=datetime(?,'-1 day')", (iso(now_il()),))["c"],
                "airlines": db.one("SELECT COUNT(DISTINCT airline) c FROM offers")["c"],
                "last_run": db.one("SELECT * FROM runs ORDER BY id DESC LIMIT 1"),
                "demo": bool(self.cfg.get("demo_mode")),
                "origin": self.cfg["origin"], "currency": self.cfg.get("currency", "USD"),
                "interval": self.cfg["scan"].get("interval_minutes", 60),
            }
        if path == "/api/events":
            where, params = [], []
            if g("type"):
                where.append("type=?"); params.append(g("type"))
            if g("dest"):
                where.append("destination=?"); params.append(g("dest"))
            sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "") + \
                  " ORDER BY id DESC LIMIT ?"
            return db.q(sql, params + [int(g("limit", 150))])
        if path == "/api/deals":
            max_days = int(g("max_days", 400))
            min_days = int(g("min_days", 0))
            params = [today.isoformat(), (today + timedelta(days=min_days)).isoformat(),
                      (today + timedelta(days=max_days)).isoformat(), iso(now_il())]
            extra = ""
            if g("dest"):
                extra += " AND destination=?"; params.append(g("dest"))
            if g("airline"):
                extra += " AND airline=?"; params.append(g("airline"))
            if g("direct") == "1":
                extra += " AND stops=0"
            rows = db.q(f"""SELECT * FROM offers WHERE depart_date>=? AND depart_date>=? AND depart_date<=?
                           AND last_seen>=datetime(?,'-3 days') {extra}
                           ORDER BY last_price ASC LIMIT ?""", params + [int(g("limit", 300))])
            now_hm = now_il().strftime("%H:%M")
            rows = [r for r in rows if not (r["depart_date"] == today.isoformat() and (r["depart_time"] or "99") < now_hm)]
            for r in rows:
                dtd = (date.fromisoformat(r["depart_date"]) - today).days
                r["days_to_dep"] = dtd
                r["advice"] = self.learner.advise(r["airline"], dtd)
            return rows
        if path == "/api/cheapest":
            return db.q("""SELECT destination, MIN(last_price) price, COUNT(*) flights,
                           (SELECT airline_name FROM offers o2 WHERE o2.destination=o.destination
                              AND o2.depart_date>=? ORDER BY last_price LIMIT 1) airline,
                           (SELECT depart_date FROM offers o2 WHERE o2.destination=o.destination
                              AND o2.depart_date>=? ORDER BY last_price LIMIT 1) depart_date
                           FROM offers o WHERE depart_date>=? GROUP BY destination ORDER BY price""",
                        (today.isoformat(),) * 3)
        if path == "/api/offer":
            k = g("key")
            off = db.one("SELECT * FROM offers WHERE key=?", (k,))
            if not off:
                return None
            off["history"] = db.q("SELECT observed_at, price FROM observations WHERE offer_key=? ORDER BY observed_at", (k,))
            off["advice"] = self.learner.advise(off["airline"], (date.fromisoformat(off["depart_date"]) - today).days)
            return off
        if path == "/api/airlines":
            return sorted(self.learner.profiles().values(), key=lambda p: -p["observations"])
        if path == "/api/meta":
            return {"destinations": [r["destination"] for r in db.q("SELECT DISTINCT destination FROM offers ORDER BY 1")],
                    "airlines": db.q("SELECT airline, MAX(airline_name) name FROM offers GROUP BY airline ORDER BY 2")}
        if path == "/api/runs":
            return db.q("SELECT * FROM runs ORDER BY id DESC LIMIT 50")
        return {"error": "not found"}


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path.startswith("/api/"):
                try:
                    self._send(200, app.api(u.path, urllib.parse.parse_qs(u.query)))
                except Exception as e:  # noqa
                    log.exception("api error")
                    self._send(500, {"error": str(e)})
                return
            f = STATIC / ("index.html" if u.path in ("/", "") else u.path.lstrip("/"))
            if f.is_file() and STATIC in f.resolve().parents:
                ctype = "text/html; charset=utf-8" if f.suffix == ".html" else "application/octet-stream"
                self._send(200, f.read_bytes(), ctype)
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/api/scan":
                self._send(200, app.scan())
            else:
                self._send(404, {"error": "not found"})
    return H


def seed_demo(cfg, days):
    cfg = dict(cfg, demo_mode=True, _sim=True)
    db = DB(cfg["db_path"])
    end = now_il().replace(minute=0, second=0)
    t = end - timedelta(days=days)
    n = 0
    while t <= end:
        run_scan(db, cfg, now=t, notify=False)
        t += timedelta(hours=6)
        n += 1
    print(f"seeded {n} simulated scans")


def fnv(s):
    """FNV-1a 32-bit (same function exists in index.html for static mode)."""
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def export_static(cfg, out):
    """Write the dashboard + all data as static JSON files (for GitHub Pages)."""
    import shutil
    app = App(cfg)
    out = Path(out)
    (out / "data" / "offer").mkdir(parents=True, exist_ok=True)
    shutil.copy(STATIC / "index.html", out / "index.html")
    (out / ".nojekyll").write_text("")

    def dump(name, obj):
        (out / "data" / name).write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")

    dump("summary.json", app.api("/api/summary", {}))
    dump("events.json", app.api("/api/events", {"limit": ["500"]}))
    dump("cheapest.json", app.api("/api/cheapest", {}))
    dump("airlines.json", app.api("/api/airlines", {}))
    dump("meta.json", app.api("/api/meta", {}))
    dump("runs.json", app.api("/api/runs", {}))
    deals = app.api("/api/deals", {"limit": ["5000"]})
    dump("deals.json", deals)
    keys = {d["key"] for d in deals} | {e["offer_key"] for e in app.api("/api/events", {"limit": ["500"]})}
    for k in keys:
        o = app.api("/api/offer", {"key": [k]})
        if o:
            h = fnv(k)
            (out / "data" / "offer" / f"{h}.json").write_text(json.dumps(o, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"exported {len(deals)} deals, {len(keys)} offer histories to {out}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    cfg = load_config()
    if cmd == "scan":
        app = App(cfg)
        print(json.dumps(app.scan(), ensure_ascii=False, indent=1))
    elif cmd == "export":
        export_static(cfg, sys.argv[2] if len(sys.argv) > 2 else "site")
    elif cmd == "seed-demo":
        seed_demo(cfg, int(sys.argv[2]) if len(sys.argv) > 2 else 45)
    elif cmd == "serve":
        app = App(cfg)
        if "--no-scheduler" not in sys.argv:
            threading.Thread(target=app.scheduler, daemon=True).start()
        host, port = cfg["web"].get("host", "0.0.0.0"), int(cfg["web"].get("port", 8080))
        log.info("Dashboard on http://%s:%d  (demo=%s)", host, port, cfg.get("demo_mode"))
        ThreadingHTTPServer((host, port), make_handler(app)).serve_forever()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
