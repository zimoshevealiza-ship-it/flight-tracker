from .base import Offer, ProviderError
from .serpapi import SerpApiProvider
from .travelpayouts import TravelpayoutsProvider
from .demo import DemoProvider


def build_providers(cfg):
    if cfg.get("demo_mode"):
        return [DemoProvider(cfg)]
    out = []
    s = cfg["secrets"]
    for name in cfg.get("providers", []):
        if name == "serpapi" and s.get("SERPAPI_KEY"):
            out.append(SerpApiProvider(cfg, s["SERPAPI_KEY"]))
        elif name == "travelpayouts" and s.get("TRAVELPAYOUTS_TOKEN"):
            out.append(TravelpayoutsProvider(cfg, s["TRAVELPAYOUTS_TOKEN"]))
    return out
