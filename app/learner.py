"""Learns how each airline moves its prices, from the accumulated observation history.

For every airline it computes:
  * price curve by days-before-departure (normalised per flight, so cheap and expensive routes are comparable)
  * the cheapest booking window
  * how often prices change, share of drops vs rises, typical drop / rise size
  * which hour of day and which weekday price changes (and drops) usually happen
  * last-minute behaviour (does it get cheaper or pricier in the final days?)
and turns it into a plain-language profile + a buy/wait hint for any specific flight.
"""
import statistics
from collections import Counter, defaultdict

from .timeutil import parse

BUCKETS = [(0, 3), (4, 7), (8, 14), (15, 30), (31, 60), (61, 90), (91, 150), (151, 400)]
BUCKET_LABELS = ["0-3", "4-7", "8-14", "15-30", "31-60", "61-90", "91-150", "150+"]
WEEKDAYS_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
MIN_OBS = 40


def bucket_of(dtd):
    for i, (a, b) in enumerate(BUCKETS):
        if a <= dtd <= b:
            return i
    return len(BUCKETS) - 1


class Learner:
    def __init__(self, db):
        self.db = db
        self._cache = {}
        self._cache_n = -1

    def _load(self):
        n = self.db.one("SELECT COUNT(*) c FROM observations")["c"]
        if n == self._cache_n:
            return self._cache
        rows = self.db.q("""SELECT o.offer_key, o.observed_at, o.price, o.days_to_dep, o.airline, f.airline_name
                            FROM observations o JOIN offers f ON f.key=o.offer_key
                            ORDER BY o.offer_key, o.observed_at""")
        by_airline = defaultdict(list)
        for r in rows:
            by_airline[r["airline"]].append(r)
        self._cache = {code: self._profile(code, obs) for code, obs in by_airline.items() if len(obs) >= MIN_OBS}
        self._cache_n = n
        return self._cache

    def profiles(self):
        return self._load()

    def profile(self, code):
        return self._load().get(code)

    def _profile(self, code, obs):
        name = obs[0]["airline_name"] or code
        per_offer = defaultdict(list)
        for r in obs:
            per_offer[r["offer_key"]].append(r)

        curve = defaultdict(list)
        changes = []
        hours_change, hours_drop, wd_drop, wd_change = Counter(), Counter(), Counter(), Counter()
        days_tracked = 0.0

        for key, lst in per_offer.items():
            mean = statistics.mean(x["price"] for x in lst)
            if mean <= 0:
                continue
            for x in lst:
                curve[bucket_of(x["days_to_dep"])].append(x["price"] / mean)
            if len(lst) > 1:
                days_tracked += (parse(lst[-1]["observed_at"]) - parse(lst[0]["observed_at"])).total_seconds() / 86400
            for prev, cur in zip(lst, lst[1:]):
                if cur["price"] != prev["price"]:
                    pct = (cur["price"] - prev["price"]) / prev["price"]
                    t = parse(cur["observed_at"])
                    changes.append(pct)
                    hours_change[t.hour] += 1
                    wd_change[t.weekday()] += 1
                    if pct < 0:
                        hours_drop[t.hour] += 1
                        wd_drop[t.weekday()] += 1

        curve_out = []
        for i, label in enumerate(BUCKET_LABELS):
            vals = curve.get(i, [])
            curve_out.append({"bucket": label, "median": round(statistics.median(vals), 3) if len(vals) >= 5 else None,
                              "n": len(vals)})
        valid = [c for c in curve_out if c["median"] is not None]
        best = min(valid, key=lambda c: c["median"]) if valid else None
        worst = max(valid, key=lambda c: c["median"]) if valid else None

        drops = [c for c in changes if c < 0]
        rises = [c for c in changes if c > 0]
        late = next((c["median"] for c in curve_out if c["bucket"] == "0-3" and c["median"]), None)
        mid = next((c["median"] for c in curve_out if c["bucket"] == "15-30" and c["median"]), None)

        prof = {
            "airline": code,
            "name": name,
            "observations": len(obs),
            "flights_tracked": len(per_offer),
            "curve": curve_out,
            "best_window": best["bucket"] if best else None,
            "best_window_saving_pct": round((worst["median"] - best["median"]) / worst["median"] * 100, 1) if best and worst else None,
            "changes_per_flight_per_day": round(len(changes) / days_tracked, 2) if days_tracked > 0 else None,
            "drop_share_pct": round(len(drops) / len(changes) * 100, 1) if changes else None,
            "avg_drop_pct": round(statistics.mean(drops) * 100, 1) if drops else None,
            "avg_rise_pct": round(statistics.mean(rises) * 100, 1) if rises else None,
            "change_hours": [hours_change.get(h, 0) for h in range(24)],
            "drop_hours": [hours_drop.get(h, 0) for h in range(24)],
            "drop_weekdays": [wd_drop.get(d, 0) for d in range(7)],
            "top_drop_hour": hours_drop.most_common(1)[0][0] if hours_drop else None,
            "top_drop_weekday": WEEKDAYS_HE[wd_drop.most_common(1)[0][0]] if wd_drop else None,
            "last_minute_vs_month_pct": round((late - mid) / mid * 100, 1) if late and mid else None,
        }
        prof["summary"] = self._summary(prof)
        return prof

    @staticmethod
    def _summary(p):
        s = []
        if p["best_window"]:
            s.append(f"הכי זול בדרך כלל {p['best_window']} ימים לפני הטיסה"
                     + (f" (חיסכון של כ-{p['best_window_saving_pct']}% לעומת החלון היקר)" if p["best_window_saving_pct"] else ""))
        lm = p["last_minute_vs_month_pct"]
        if lm is not None:
            if lm <= -10:
                s.append(f"נוטה להוריד מחירים ברגע האחרון ({lm}% לעומת חודש לפני)")
            elif lm >= 10:
                s.append(f"מייקרת משמעותית ברגע האחרון (+{lm}% לעומת חודש לפני) – לא כדאי לחכות")
            else:
                s.append("מחירי הרגע האחרון דומים למחירים חודש לפני")
        if p["changes_per_flight_per_day"] is not None:
            s.append(f"משנה מחיר בממוצע {p['changes_per_flight_per_day']} פעמים ביום לטיסה")
        if p["drop_share_pct"] is not None:
            s.append(f"{p['drop_share_pct']}% מהשינויים הם ירידות (ממוצע ירידה {p['avg_drop_pct']}%, ממוצע עלייה +{p['avg_rise_pct']}%)")
        if p["top_drop_hour"] is not None:
            s.append(f"ירידות מחיר מופיעות בעיקר בסביבות {p['top_drop_hour']:02d}:00, ובעיקר ביום {p['top_drop_weekday']}")
        return s

    def advise(self, airline, days_to_dep):
        """Buy-or-wait hint for a flight of this airline that is `days_to_dep` days away."""
        p = self.profile(airline)
        if not p:
            return {"advice": "unknown", "text": "עדיין אין מספיק נתונים על חברה זו"}
        cur_b = bucket_of(days_to_dep)
        cur = p["curve"][cur_b]["median"]
        if cur is None:
            return {"advice": "unknown", "text": "אין מספיק נתונים לחלון הזמן הזה"}
        future = [c for i, c in enumerate(p["curve"]) if i < cur_b and c["median"] is not None]
        if not future:
            return {"advice": "buy", "text": "זה החלון האחרון – אין יתרון בהמתנה"}
        best = min(future, key=lambda c: c["median"])
        gain = (cur - best["median"]) / cur
        if gain > 0.05:
            return {"advice": "wait",
                    "text": f"היסטורית המחיר יורד בכ-{gain * 100:.0f}% בחלון {best['bucket']} ימים לפני – כדאי לעקוב ולחכות"}
        worst = max(future, key=lambda c: c["median"])
        rise = (worst["median"] - cur) / cur
        return {"advice": "buy",
                "text": f"היסטורית המחיר רק עולה מכאן (עד +{rise * 100:.0f}%) – כדאי לקנות עכשיו" if rise > 0.05
                else "המחיר צפוי להישאר יציב"}
