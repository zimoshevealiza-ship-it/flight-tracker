"""Synthetic provider for trying the system without API keys.
Each fake airline follows a different pricing strategy so the learning module has something to learn."""
import hashlib
import math
from datetime import date, datetime

from .base import Offer, Provider

AIRLINES = {
    # code: (name, base_mult, strategy)
    "LY": ("El Al", 1.35, "steady_rise"),
    "6H": ("Israir", 1.05, "late_spike"),
    "IZ": ("Arkia", 1.00, "midweek_sale"),
    "W6": ("Wizz Air", 0.60, "low_cost_ladder"),
    "FR": ("Ryanair", 0.55, "low_cost_ladder"),
    "A3": ("Aegean", 1.10, "early_bird"),
    "LH": ("Lufthansa", 1.45, "steady_rise"),
    "U2": ("easyJet", 0.70, "last_minute_dump"),
}
DIST = {"ATH": 160, "LCA": 90, "RHO": 150, "BUD": 230, "PRG": 260, "VIE": 240, "FCO": 260,
        "BCN": 330, "CDG": 330, "LHR": 360, "BER": 290, "AMS": 340, "DXB": 260, "BKK": 620, "JFK": 780}


def _h(*parts):
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


class DemoProvider(Provider):
    name = "demo"
    cost_per_search = 0

    def __init__(self, cfg, now=None):
        super().__init__(cfg)
        self.now = now  # can be overridden for back-filling history

    def _price(self, base, strategy, dtd, now, seed):
        p = base
        if strategy == "steady_rise":
            p *= 0.85 + 0.6 * math.exp(-dtd / 40)
        elif strategy == "late_spike":
            p *= 0.9 + (0.9 if dtd < 10 else 0.0) + 0.1 * math.exp(-dtd / 60)
        elif strategy == "midweek_sale":
            p *= 1.0 + 0.4 * math.exp(-dtd / 25)
            if now.weekday() in (1, 2):  # Tue/Wed sale
                p *= 0.82
        elif strategy == "low_cost_ladder":
            # fare buckets: price steps up as seats sell
            step = int(max(0, 60 - dtd) / 12)
            p *= 0.6 + 0.22 * step
        elif strategy == "early_bird":
            p *= 0.75 if dtd > 90 else 1.0 + 0.3 * math.exp(-dtd / 20)
        elif strategy == "last_minute_dump":
            p *= 1.0 + 0.3 * math.exp(-dtd / 30)
            if dtd <= 4:
                p *= 0.55
        # day-to-day noise that changes at specific hours
        bucket = now.strftime("%Y%m%d") + str(now.hour // 6)
        p *= 0.93 + 0.14 * _h(seed, bucket)
        return round(p)

    def search(self, origin, destination, depart_date):
        now = self.now or datetime.now()
        dep = date.fromisoformat(depart_date)
        dtd = (dep - now.date()).days
        dist = DIST.get(destination, 300)
        out = []
        for code, (name, mult, strategy) in AIRLINES.items():
            seed = f"{code}{destination}{depart_date}"
            if _h(seed, "flies") < 0.45:
                continue  # this airline doesn't fly this route on this day
            # schedules get "published" 330..200 days ahead
            if dtd > 200 + int(130 * _h(seed, "pub")):
                continue
            hour = 5 + int(17 * _h(seed, "hour"))
            out.append(Offer(
                provider=self.name, airline=code, airline_name=name,
                flight_number=f"{code}{100 + int(800 * _h(seed, 'fn'))}",
                origin=origin, destination=destination, depart_date=depart_date,
                depart_time=f"{hour:02d}:{int(4 * _h(seed, 'm')) * 15:02d}",
                price=self._price(dist * mult, strategy, dtd, now, seed),
                currency=self.cfg.get("currency", "USD"),
                stops=0, duration_min=int(dist / 2.2),
            ))
        return out
