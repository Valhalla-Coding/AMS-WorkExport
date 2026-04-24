import time
import requests
from config import NOMINATIM_URL, HEADERS_NOMINATIM, NOMINATIM_DELAY, SKIP_STRIKE_CITIES


def search_address(query: str) -> list:
    params = {"q": query, "format": "json", "addressdetails": 1,
              "limit": 10, "countrycodes": "se"}
    try:
        resp = requests.get(NOMINATIM_URL, params=params,
                            headers=HEADERS_NOMINATIM, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        print(f"\n  Nominatim-fel ({code}): {e}")
        return []
    except requests.exceptions.RequestException as e:
        print(f"\n  Kunde inte nå Nominatim: {e}")
        return []


def geocode_city(city: str, cache: dict) -> tuple:
    if city in cache:
        return cache[city]
    if city in SKIP_STRIKE_CITIES:
        cache[city] = (None, None)
        return (None, None)
    time.sleep(NOMINATIM_DELAY)
    params = {"q": f"{city}, Sverige", "format": "json",
              "limit": 1, "countrycodes": "se"}
    try:
        resp = requests.get(NOMINATIM_URL, params=params,
                            headers=HEADERS_NOMINATIM, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if results:
            lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
            cache[city] = (lat, lon)
            return (lat, lon)
    except Exception:
        pass
    cache[city] = (None, None)
    return (None, None)
