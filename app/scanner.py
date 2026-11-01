"""One scan run: picks the next slice of (destination, date) searches within the request budget,
queries every provider and feeds the results to the detector.

Budget logic: last-minute dates (<= last_minute_days) get up to half the budget each run, the rest of the
calendar is covered round-robin, so over a few runs every route/date is checked."""
import json
import logging
import time
from datetime import timedelta

from .detector import Detector
from .notify import send_events
from .providers import ProviderError, build_providers
from .providers.demo import DIST
from .timeutil import iso, now_il

log = logging.getLogger("scanner")


def _tasks(cfg, now):
    prio = list(dict.fromkeys(cfg.get("priority_destinations", [])))
    dests = [d for d in cfg["destinations"] if d not in prio]
    if cfg.get("demo_mode"):
        dests = [d for d in dests if d in DIST] or list(DIST)
        prio = [d for d in prio if d in DIST]
    lm = cfg["scan"].get("last_minute_days", 7)
    near, far, top = [], [], []
    for days in cfg["scan"]["days_ahead"]:
        d = (now.date() + timedelta(days=days)).isoformat()
        for dest in prio:
            top.append((dest, d))
        for dest in dests:
            (near if days <= lm else far).append((dest, d))
    return near, far, top


def _take(lst, cursor, n):
    if not lst or n <= 0:
        return [], cursor
    n = min(n, len(lst))
    out = [lst[(cursor + i) % len(lst)] for i in range(n)]
    return out, (cursor + n) % len(lst)


def run_scan(db, cfg, now=None, notify=True, sleep=0.0):
    now = now or now_il()
    providers = build_providers(cfg)
    if not providers:
        log.warning("No providers configured (missing API keys?)")
        return {"requests": 0, "offers": 0, "events": [], "errors": ["no providers configured"]}
    if cfg.get("_sim"):
        for p in providers:
            if hasattr(p, "now"):
                p.now = now

    detector = Detector(db, cfg)
    sc = cfg["scan"]
    free = [p for p in providers if p.cost_per_search == 0]
    paid = [p for p in providers if p.cost_per_search > 0]

    # ---- free providers: round-robin over all (destination, date) pairs ----
    near, far, top = _tasks(cfg, now)
    n_free = sc.get("max_free_requests_per_run", 60)
    # priority destinations get their own share of every run
    c_top = int(db.get_state("cursor_top", 0)) % max(1, len(top))
    t_top, c_top = _take(top, c_top, min(len(top), int(n_free * sc.get("priority_share", 0.6))))
    db.set_state("cursor_top", c_top)
    n_free -= len(t_top)
    c_near = int(db.get_state("cursor_near", 0)) % max(1, len(near))
    c_far = int(db.get_state("cursor_far", 0)) % max(1, len(far))
    t_near, c_near = _take(near, c_near, min(len(near), n_free // 2))
    t_far, c_far = _take(far, c_far, min(len(far), n_free - len(t_near)))
    db.set_state("cursor_near", c_near)
    db.set_state("cursor_far", c_far)
    jobs = [(p, dest, d) for dest, d in t_top + t_near + t_far for p in free]

    # ---- paid providers (SerpApi): strict monthly/daily quota, used to verify the hottest deals live ----
    month, day = now.strftime("%Y-%m"), now.strftime("%Y-%m-%d")
    for p in paid:
        used_m = int(db.get_state(f"quota_{p.name}_{month}", 0))
        used_d = int(db.get_state(f"quota_{p.name}_{day}", 0))
        allow = min(sc.get("paid_monthly_limit", 240) - used_m,
                    sc.get("paid_daily_limit", 8) - used_d,
                    sc.get("paid_per_run", 1))
        if allow <= 0:
            continue
        verified = {(r["destination"], r["depart_date"]) for r in db.q(
            "SELECT destination, depart_date FROM route_scans WHERE provider=? AND last_scan>=datetime(?, '-1 day')",
            (p.name, iso(now)))}
        cands = db.q("""SELECT destination, depart_date, MIN(last_price) pr FROM offers
                        WHERE provider<>? AND depart_date>? AND last_seen>=datetime(?, '-1 day')
                        GROUP BY destination, depart_date ORDER BY pr LIMIT 50""",
                     (p.name, now.date().isoformat(), iso(now)))
        picks = [(c["destination"], c["depart_date"]) for c in cands
                 if (c["destination"], c["depart_date"]) not in verified][:allow]
        if not picks:  # nothing from free sources yet - fall back to rotating last-minute dates
            c = int(db.get_state(f"cursor_{p.name}", 0))
            pool = top or near or far
            picks, c = _take(pool, c % max(1, len(pool)), allow)
            db.set_state(f"cursor_{p.name}", c)
        jobs += [(p, dest, d) for dest, d in picks]
        db.set_state(f"quota_{p.name}_{month}", used_m + len(picks))
        db.set_state(f"quota_{p.name}_{day}", used_d + len(picks))

    started = iso(now)
    requests = offers_n = 0
    events, errors = [], []
    fails, ok = {}, set()
    for p, dest, d in jobs:
        if fails.get(p.name, 0) >= 5 and p.name not in ok:
            continue  # provider is clearly broken this run – don't waste time on it
        try:
            offers = p.search(cfg["origin"], dest, d)
            requests += 1
            offers_n += len(offers)
            events += detector.process(p.name, dest, d, offers, now)
            ok.add(p.name)
        except ProviderError as e:
            fails[p.name] = fails.get(p.name, 0) + 1
            errors.append(f"{p.name} {dest} {d}: {e}")
            log.warning("%s %s %s: %s", p.name, dest, d, e)
        pause = sleep or sc.get("sleep_seconds", 0)
        if pause and not cfg.get("_sim"):
            time.sleep(pause)

    db.x("INSERT INTO runs(started_at,finished_at,requests,offers,events,errors) VALUES(?,?,?,?,?,?)",
         (started, iso(now_il() if not cfg.get("_sim") else now), requests, offers_n, len(events),
          json.dumps(errors[:20], ensure_ascii=False)))
    if notify and events:
        send_events(cfg, events)
    log.info("scan done: %d searches, %d offers, %d events, %d errors",
             len(jobs), offers_n, len(events), len(errors))
    return {"requests": requests, "offers": offers_n, "events": events, "errors": errors}
