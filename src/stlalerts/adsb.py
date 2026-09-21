"""adsb.lol client (keyless, ODbL open data).

The API returns HTTP 429 if hit in bursts, so requests are paced and a 429 is
retried a couple of times before giving up on this cycle.
"""
import json
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.adsb.lol"
USER_AGENT = "stl-livery-alerts/0.1 (personal hobby project)"


class RateLimited(Exception):
    pass


class AdsbClient:
    def __init__(self, base=DEFAULT_BASE, min_interval=3.0, retries=2, backoff=15.0, api_key=None):
        self.base = base.rstrip("/")
        self.min_interval = min_interval
        self.retries = retries
        self.backoff = backoff
        self.api_key = api_key
        self._last = 0.0

    def _get(self, path):
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            req = urllib.request.Request(self.base + path, headers={"User-Agent": USER_AGENT})
            if self.api_key:
                req.add_header("api-key", self.api_key)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                if e.code == 429:
                    raise RateLimited(path)
                raise
        raise RateLimited(path)

    def point(self, lat, lon, radius_nm):
        """All aircraft within radius_nm (max 250) of a point."""
        return self._get(f"/v2/point/{lat}/{lon}/{int(radius_nm)}").get("ac", [])

    def regs(self, registrations):
        """Aircraft currently broadcasting under the given registrations (comma batch)."""
        return self._get("/v2/reg/" + ",".join(registrations)).get("ac", [])
