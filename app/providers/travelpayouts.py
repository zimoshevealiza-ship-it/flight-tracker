"""Travelpayouts / Aviasales Data API. Cheap and free, but prices come from a cache of recent
user searches (not a live quote), so treat it as a supplementary signal."""
from .base import Offer, Provider, ProviderError, http_get_json

URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


class TravelpayoutsProvider(Provider):
    name = "travelpayouts"
    cost_per_search = 0  # free tier, does not consume the paid budget

    def __init__(self, cfg, token):
        super().__init__(cfg)
        self.token = token

    def search(self, origin, destination, depart_date):
        params = {
            "origin": origin,
            "destination": destination,
            "departure_at": depart_date,
            "one_way": "true",
            "direct": "false",
            "sorting": "price",
            "unique": "false",
            "limit": 30,
            "cy": self.cfg.get("currency", "USD").lower(),
            "token": self.token,
        }
        data = http_get_json(URL, params)
        if not data.get("success", True):
            raise ProviderError(str(data.get("error")))
        offers = []
        for it in data.get("data", []) or []:
            dep = it.get("departure_at", "")  # 2026-11-05T06:15:00+02:00
            d, t = dep[:10], dep[11:16]
            code = it.get("airline", "??")
            offers.append(Offer(
                provider=self.name,
                airline=code,
                airline_name=code,
                flight_number=f"{code}{it.get('flight_number', '')}",
                origin=it.get("origin_airport") or origin,
                destination=it.get("destination_airport") or destination,
                depart_date=d,
                depart_time=t,
                price=float(it.get("price", 0)),
                currency=self.cfg.get("currency", "USD"),
                stops=int(it.get("transfers") or 0),
                duration_min=int(it.get("duration") or 0),
                link=("https://www.aviasales.com" + it["link"]) if it.get("link") else "",
            ))
        return offers
