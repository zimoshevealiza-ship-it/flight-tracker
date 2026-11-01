"""Live prices from Google Flights through SerpApi (covers practically every airline flying from TLV:
El Al, Israir, Arkia, Wizz, Ryanair, easyJet, Aegean, Lufthansa group, etc.)."""
from .base import Offer, Provider, ProviderError, http_get_json

URL = "https://serpapi.com/search.json"


class SerpApiProvider(Provider):
    name = "serpapi"

    def __init__(self, cfg, key):
        super().__init__(cfg)
        self.key = key

    def search(self, origin, destination, depart_date):
        params = {
            "engine": "google_flights",
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": depart_date,
            "type": 2,  # one way
            "currency": self.cfg.get("currency", "USD"),
            "hl": "en",
            "api_key": self.key,
        }
        data = http_get_json(URL, params)
        if "error" in data:
            msg = data["error"]
            if "hasn't returned any results" in msg:
                return []
            raise ProviderError(msg)
        offers = []
        for group in ("best_flights", "other_flights"):
            for it in data.get(group, []) or []:
                legs = it.get("flights") or []
                if not legs or not it.get("price"):
                    continue
                first = legs[0]
                fn = (first.get("flight_number") or "").replace(" ", "")
                code = fn[:2] if fn else (first.get("airline") or "??")[:2].upper()
                dep = (first.get("departure_airport") or {}).get("time", "")  # "2026-11-05 06:15"
                d, _, t = dep.partition(" ")
                offers.append(Offer(
                    provider=self.name,
                    airline=code,
                    airline_name=first.get("airline", code),
                    flight_number=fn,
                    origin=origin,
                    destination=destination,
                    depart_date=d or depart_date,
                    depart_time=t,
                    price=float(it["price"]),
                    currency=self.cfg.get("currency", "USD"),
                    stops=max(0, len(legs) - 1),
                    duration_min=int(it.get("total_duration") or 0),
                    link=data.get("search_metadata", {}).get("google_flights_url", ""),
                ))
        return offers
