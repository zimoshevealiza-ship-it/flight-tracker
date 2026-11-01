import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


class ProviderError(Exception):
    pass


@dataclass
class Offer:
    provider: str
    airline: str           # IATA code, e.g. LY
    airline_name: str
    flight_number: str     # e.g. LY381 (first leg)
    origin: str
    destination: str
    depart_date: str       # YYYY-MM-DD
    depart_time: str       # HH:MM
    price: float
    currency: str
    stops: int = 0
    duration_min: int = 0
    link: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def key(self):
        return "|".join([self.provider, self.airline, self.flight_number or "-",
                         self.origin, self.destination, self.depart_date,
                         self.depart_time or "-", str(self.stops)])


def http_get_json(url, params=None, headers=None, timeout=40):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "flight-tracker/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        raise ProviderError(f"HTTP {e.code}: {body}")
    except Exception as e:  # noqa
        raise ProviderError(str(e))


class Provider:
    name = "base"
    # how many API calls one search() costs (for the per-run budget)
    cost_per_search = 1

    def __init__(self, cfg):
        self.cfg = cfg

    def search(self, origin, destination, depart_date):
        raise NotImplementedError
