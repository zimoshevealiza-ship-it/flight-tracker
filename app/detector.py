"""Turns each fresh price quote into stored history + events:
NEW_FLIGHT   – a flight that was not there in earlier scans of the same route/date (airline just published it)
NEW_ROUTE    – first time an airline is seen flying to this destination at all
PRICE_DROP   – price fell by at least X% / Y currency units since the last check
RECORD_LOW   – lowest price ever seen for this flight
LAST_MINUTE  – departure within N days and price in the cheapest quarter for that destination
"""
from datetime import date

from .timeutil import iso, parse


def _percentile(values, p):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


class Detector:
    def __init__(self, db, cfg):
        self.db = db
        self.cfg = cfg
        a = cfg.get("alerts", {})
        self.drop_pct = a.get("price_drop_percent", 8) / 100
        self.min_drop = a.get("min_drop_amount", 15)
        self.lm_days = cfg.get("scan", {}).get("last_minute_days", 7)
        self.lm_pct = a.get("last_minute_percentile", 0.25)
        self._dest_cache = {}
        self.heartbeat_h = cfg.get("scan", {}).get("heartbeat_hours", 6)

    def _dest_prices(self, destination, now):
        if destination not in self._dest_cache:
            rows = self.db.q(
                "SELECT price FROM observations WHERE destination=? AND observed_at>=datetime(?, '-60 days')",
                (destination, iso(now)))
            self._dest_cache[destination] = [r["price"] for r in rows]
        return self._dest_cache[destination]

    def process(self, provider, destination, depart_date, offers, now):
        """Store a batch of offers from one search. Returns list of new event dicts."""
        events = []
        ts = iso(now)
        self._dest_cache.pop(destination, None)
        scan = self.db.one("SELECT scans FROM route_scans WHERE provider=? AND destination=? AND depart_date=?",
                           (provider, destination, depart_date))
        scanned_before = bool(scan and scan["scans"] > 0)
        dtd = (date.fromisoformat(depart_date) - now.date()).days
        obs_rows = []

        for o in offers:
            if o.price <= 0:
                continue
            k = o.key
            row = self.db.one("SELECT * FROM offers WHERE key=?", (k,))
            if row is None:
                known_airline_route = self.db.one(
                    "SELECT 1 FROM offers WHERE airline=? AND destination=? LIMIT 1", (o.airline, o.destination))
                self.db.x("""INSERT INTO offers(key,provider,airline,airline_name,flight_number,origin,destination,
                             depart_date,depart_time,stops,duration_min,first_seen,last_seen,first_price,last_price,
                             min_price,max_price,currency,link) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (k, o.provider, o.airline, o.airline_name, o.flight_number, o.origin, o.destination,
                           o.depart_date, o.depart_time, o.stops, o.duration_min, ts, ts, o.price, o.price,
                           o.price, o.price, o.currency, o.link))
                if not known_airline_route and self.db.one("SELECT 1 FROM offers WHERE destination=? AND key<>? LIMIT 1",
                                                            (o.destination, k)):
                    events.append(self._ev(now, "NEW_ROUTE", o, None, o.price,
                                           f"🆕 {o.airline_name} מתחילה לטוס ל-{o.destination}! "
                                           f"טיסה ראשונה שנמצאה: {o.depart_date} ב-{o.price:.0f} {o.currency}"))
                elif scanned_before:
                    events.append(self._ev(now, "NEW_FLIGHT", o, None, o.price,
                                           f"✈️ טיסה חדשה פורסמה: {o.airline_name} {o.flight_number} "
                                           f"{o.origin}→{o.destination} ב-{o.depart_date} {o.depart_time} – "
                                           f"{o.price:.0f} {o.currency}"))
            else:
                old = row["last_price"]
                drop = old - o.price
                if drop >= self.min_drop and drop / old >= self.drop_pct:
                    record = o.price < row["min_price"]
                    events.append(self._ev(
                        now, "RECORD_LOW" if record else "PRICE_DROP", o, old, o.price,
                        f"{'🏆 שפל חדש' if record else '📉 ירידת מחיר'}: {o.airline_name} {o.flight_number} "
                        f"ל-{o.destination} ב-{o.depart_date}: {old:.0f} ← {o.price:.0f} {o.currency} "
                        f"(-{drop / old * 100:.0f}%)"))
                self.db.x("""UPDATE offers SET last_seen=?, last_price=?, min_price=MIN(min_price,?),
                             max_price=MAX(max_price,?), link=COALESCE(NULLIF(?,''),link) WHERE key=?""",
                          (ts, o.price, o.price, o.price, o.link, k))

            # last-minute bargain
            if 0 <= dtd <= self.lm_days:
                hist = self._dest_prices(o.destination, now)
                thr = _percentile(hist, self.lm_pct) if len(hist) >= 30 else None
                if thr and o.price <= thr and not self.db.one(
                        "SELECT 1 FROM events WHERE type='LAST_MINUTE' AND offer_key=? AND created_at>=datetime(?, '-1 day')",
                        (k, ts)):
                    events.append(self._ev(now, "LAST_MINUTE", o, None, o.price,
                                           f"⏰ דיל רגע אחרון: {o.airline_name} ל-{o.destination} בעוד {dtd} ימים "
                                           f"({o.depart_date} {o.depart_time}) – {o.price:.0f} {o.currency}"))
            # store an observation only when the price changed, or as a heartbeat every few hours
            # (keeps the database small enough for GitHub while preserving every price change)
            if row is None or o.price != row["last_price"] or not row.get("last_obs") or \
                    (now - parse(row["last_obs"])).total_seconds() >= self.heartbeat_h * 3600:
                obs_rows.append((k, ts, o.price, dtd, o.airline, o.destination))
                self.db.x("UPDATE offers SET last_obs=? WHERE key=?", (ts, k))

        self.db.many("INSERT INTO observations(offer_key,observed_at,price,days_to_dep,airline,destination) "
                     "VALUES(?,?,?,?,?,?)", obs_rows)
        self.db.x("""INSERT INTO route_scans(provider,destination,depart_date,first_scan,last_scan,scans)
                     VALUES(?,?,?,?,?,1) ON CONFLICT(provider,destination,depart_date)
                     DO UPDATE SET last_scan=excluded.last_scan, scans=scans+1""",
                  (provider, destination, depart_date, ts, ts))
        if events:
            self.db.many("""INSERT INTO events(created_at,type,offer_key,airline,destination,depart_date,
                            old_price,new_price,message) VALUES(?,?,?,?,?,?,?,?,?)""",
                         [(e["created_at"], e["type"], e["offer_key"], e["airline"], e["destination"],
                           e["depart_date"], e["old_price"], e["new_price"], e["message"]) for e in events])
        return events

    @staticmethod
    def _ev(now, typ, o, old, new, msg):
        return {"created_at": iso(now), "type": typ, "offer_key": o.key, "airline": o.airline,
                "destination": o.destination, "depart_date": o.depart_date,
                "old_price": old, "new_price": new, "message": msg}
