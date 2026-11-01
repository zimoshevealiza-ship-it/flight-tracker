"""Live prices straight from Google Flights (via the open-source `fast-flights` package).
No API key and no sign-up. Covers every airline Google Flights shows for TLV.
Runs on GitHub's servers; volume is kept low (a few dozen searches per hour)."""
import re
import time

from .base import Offer, Provider, ProviderError

# airline name (as Google shows it) -> IATA code; unknown names keep a short tag
AIRLINE_CODES = {
    "el al": "LY", "israir": "6H", "arkia": "IZ", "wizz air": "W6", "ryanair": "FR", "easyjet": "U2",
    "aegean": "A3", "olympic air": "OA", "lufthansa": "LH", "swiss": "LX", "austrian": "OS",
    "brussels airlines": "SN", "air france": "AF", "klm": "KL", "british airways": "BA", "ita": "AZ",
    "ita airways": "AZ", "lot": "LO", "lot polish airlines": "LO", "tarom": "RO", "air europa": "UX",
    "iberia": "IB", "vueling": "VY", "tap air portugal": "TP", "air serbia": "JU", "bulgaria air": "FB",
    "georgian airways": "A9", "smartwings": "QS", "blue bird airways": "BZ", "tus airways": "U8",
    "cyprus airways": "CY", "emirates": "EK", "flydubai": "FZ", "etihad": "EY", "air india": "AI",
    "united": "UA", "delta": "DL", "american": "AA", "air canada": "AC", "turkish airlines": "TK",
    "pegasus": "PC", "sundair": "SR", "air seychelles": "HM", "ethiopian": "ET", "air baltic": "BT",
    "airbaltic": "BT", "sas": "SK", "finnair": "AY", "condor": "DE", "eurowings": "EW", "transavia": "HV",
    "jet2": "LS", "sky express": "GQ", "air haifa": "HF", "wizz air malta": "W4", "eva air": "BR",
    "cathay pacific": "CX", "thai airways": "TG", "vietnam airlines": "VN", "hainan airlines": "HU",
}


def _code(name):
    first = (name or "").split(",")[0].strip()
    return AIRLINE_CODES.get(first.lower(), first[:12] or "??"), first


def _price(s):
    if isinstance(s, (int, float)):
        return float(s)
    digits = re.sub(r"[^\d.]", "", str(s or "").replace(",", ""))
    return float(digits) if digits else 0.0


def _time(s):
    m = re.search(r"(\d{1,2}):(\d{2})\s*([AP]M)?", str(s or ""), re.I)
    if not m:
        return ""
    h, mi, ap = int(m.group(1)), m.group(2), (m.group(3) or "").upper()
    if ap == "PM" and h != 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{mi}"


def _minutes(s):
    s = str(s or "")
    h = re.search(r"(\d+)\s*hr", s)
    m = re.search(r"(\d+)\s*min", s)
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


class GoogleFlightsProvider(Provider):
    name = "google"
    cost_per_search = 0

    def __init__(self, cfg):
        super().__init__(cfg)
        try:
            from fast_flights import FlightData, Passengers, get_flights  # noqa
        except ImportError as e:
            raise ProviderError("pip install fast-flights==2.2") from e
        self._FD, self._P, self._get = FlightData, Passengers, get_flights

    def search(self, origin, destination, depart_date):
        last = None
        for attempt in range(2):
            try:
                res = self._get(
                    flight_data=[self._FD(date=depart_date, from_airport=origin, to_airport=destination)],
                    trip="one-way", seat="economy", passengers=self._P(adults=1), fetch_mode="common")
                break
            except Exception as e:  # noqa
                msg = str(e)
                if "No flights found" in msg:
                    return []
                last = e
                time.sleep(3)
        else:
            raise ProviderError(f"google flights: {str(last)[:200]}")

        offers, seen = [], set()
        for f in getattr(res, "flights", []) or []:
            price = _price(getattr(f, "price", 0))
            if price <= 0:
                continue
            name = getattr(f, "name", "") or ""
            code, first = _code(name)
            dep = _time(getattr(f, "departure", ""))
            stops = getattr(f, "stops", 0)
            stops = stops if isinstance(stops, int) else 0
            key = (code, dep, stops)
            if key in seen:  # Google lists the same flight under "best" and "other"
                continue
            seen.add(key)
            offers.append(Offer(
                provider=self.name, airline=code, airline_name=name or first,
                flight_number=f"{code} {dep}", origin=origin, destination=destination,
                depart_date=depart_date, depart_time=dep, price=price,
                currency=self.cfg.get("currency", "USD"), stops=stops,
                duration_min=_minutes(getattr(f, "duration", "")),
                link=f"https://www.google.com/travel/flights?q=Flights%20to%20{destination}%20from%20{origin}%20on%20{depart_date}%20oneway",
            ))
        return offers
