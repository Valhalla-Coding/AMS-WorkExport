#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AMS JobbSök – Hämtar lediga jobb från Arbetsförmedlingen,
beräknar restid med bil och exporterar till CSV.

Kräver:
  pip install requests

Använder:
  - Nominatim (OpenStreetMap) för adress- och stadskoordinater
  - JobTechDev JobSearch API för jobbannonser
  - OSRM (lokalt via Docker eller demo-server i molnet) för restid
"""

import json
import csv
import os
import re
import time
import sys
import subprocess
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("Paketet 'requests' saknas. Installera med:")
    print("  pip install requests")
    sys.exit(1)


# ─── Konstanter ─────────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "config.json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
JOBSEARCH_URL = "https://jobsearch.api.jobtechdev.se"

OSRM_CLOUD_URL = "https://router.project-osrm.org/route/v1/driving"
OSRM_LOCAL_URL = "http://localhost:5000/route/v1/driving"
OSRM_IMAGE     = "osrm/osrm-backend"
OSRM_CONTAINER = "ams-jobbsok-osrm"
OSRM_DATA_DIR  = Path(__file__).parent / "osrm-data"
GEOFABRIK_URL  = "https://download.geofabrik.de/europe/sweden-latest.osm.pbf"
PBF_NAME       = "sweden-latest.osm.pbf"

HEADERS_NOMINATIM = {"User-Agent": "AMS-JobbSok/1.0 (jsodori@gmail.com)"}
NOMINATIM_DELAY   = 1.1
OSRM_DELAY_CLOUD  = 1.1
OSRM_DELAY_LOCAL  = 0.05

JOBS_PER_PAGE = 100
MAX_PAGES     = 20

DEFAULT_STRIKE_LIMIT = 5
DEFAULT_MULTIPLIER   = 1.0
SKIP_STRIKE_CITIES   = {"Okänd", "", None}


# ─── Konfiguration ──────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ─── Terminal-hjälp ─────────────────────────────────────────────────────────

def clr():
    os.system("cls" if os.name == "nt" else "clear")


def hr(char="─", width=72):
    print(char * width)


def pause(msg="Tryck Enter för att gå tillbaka till menyn..."):
    input(f"\n{msg}")


# ─── Nominatim ──────────────────────────────────────────────────────────────

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


# ─── Inställningar – ändra-funktioner ───────────────────────────────────────

def change_address(config: dict):
    clr()
    hr()
    print("  Ändra adress")
    hr()
    current = (config.get("address") or {}).get("display_name", "")
    if current:
        print(f"\n  Nuvarande: {current}\n")

    while True:
        query = input("  Sök ny adress (eller Enter för att avbryta): ").strip()
        if not query:
            return
        print("  Söker...")
        results = search_address(query)
        if not results:
            print("  Inga träffar. Försök igen.")
            continue

        if len(results) == 1:
            chosen = results[0]
        else:
            print(f"\n  Hittade {len(results)} träffar:")
            for i, r in enumerate(results, 1):
                print(f"    {i}. {r['display_name']}")
            while True:
                val = input(f"\n  Välj nummer (1-{len(results)}), Enter = avbryt: ").strip()
                if not val:
                    return
                if val.isdigit() and 1 <= int(val) <= len(results):
                    chosen = results[int(val) - 1]
                    break
                print("  Ogiltigt val.")

        config["address"] = {
            "display_name": chosen["display_name"],
            "lat": float(chosen["lat"]),
            "lon": float(chosen["lon"]),
        }
        print(f"\n  Vald: {chosen['display_name']}")
        return


def change_max_minutes(config: dict):
    clr()
    hr()
    print("  Ändra max restid")
    hr()
    current = config.get("max_minutes", 120)
    print(f"\n  Nuvarande: {current} min\n")
    while True:
        val = input("  Ny max restid i minuter (Enter = behåll): ").strip()
        if not val:
            return
        if val.isdigit() and int(val) > 0:
            config["max_minutes"] = int(val)
            return
        print("  Ange ett positivt heltal.")


def change_strike_limit(config: dict):
    clr()
    hr()
    print("  Ändra strike-gräns")
    hr()
    current = config.get("city_strike_limit", DEFAULT_STRIKE_LIMIT)
    print(f"\n  Nuvarande: {current} strikes")
    print("  Hur många jobb i en stad måste överskrida restiden")
    print("  innan staden ignoreras för resten av sessionen.\n")
    while True:
        val = input("  Ny strike-gräns (Enter = behåll): ").strip()
        if not val:
            return
        if val.isdigit() and int(val) > 0:
            config["city_strike_limit"] = int(val)
            return
        print("  Ange ett positivt heltal.")


def change_multiplier(config: dict):
    clr()
    hr()
    print("  Ändra toleransmultiplikator")
    hr()
    current   = config.get("max_minutes_multiplier", DEFAULT_MULTIPLIER)
    max_min   = config.get("max_minutes", 120)
    print(f"\n  Nuvarande: {current}×  (= {max_min * current:.0f} min effektivt)")
    print("  1.0 = exakt max restid  |  1.5 = 50% mer tolerans  osv.\n")
    while True:
        val = input("  Ny multiplikator (Enter = behåll): ").strip().replace(",", ".")
        if not val:
            return
        try:
            f = float(val)
            if f > 0:
                config["max_minutes_multiplier"] = f
                eff = max_min * f
                print(f"  Satt till {f}×  →  {eff:.0f} min effektivt")
                return
        except ValueError:
            pass
        print("  Ange ett positivt tal, t.ex. 1.0 eller 1.5")


def change_osrm_mode(config: dict):
    clr()
    hr()
    print("  Ändra OSRM-läge")
    hr()
    docker_status = get_docker_status()

    if docker_status == "not_installed":
        print("\n  Docker Desktop är inte installerat.")
        print("  Endast moln-läge tillgängligt.\n")
        config["osrm_mode"] = "cloud"
        pause("Tryck Enter för att gå tillbaka...")
        return

    if docker_status == "not_running":
        print("\n  Docker Desktop hittades men är inte igång.")
        print("  Starta Docker Desktop för att använda lokal OSRM.\n")
        config["osrm_mode"] = "cloud"
        pause("Tryck Enter för att gå tillbaka...")
        return

    current = config.get("osrm_mode", "cloud")
    print(f"\n  Nuvarande: {'Lokalt (Docker)' if current == 'local' else 'Molnet'}")
    print()
    print("  1.  Lokalt via Docker  – snabbt, inga rate-limits")
    print("  2.  Via molnet          – långsamt (~1 förfrågan/sek)")
    print()
    while True:
        val = input("  Välj (1/2, Enter = avbryt): ").strip()
        if not val:
            return
        if val == "1":
            config["osrm_mode"] = "local"
            return
        if val == "2":
            config["osrm_mode"] = "cloud"
            return
        print("  Ange 1 eller 2.")


# ─── Vitlista ───────────────────────────────────────────────────────────────

def change_whitelist(config: dict):
    whitelist: list = list(config.get("city_whitelist", []))

    while True:
        clr()
        hr()
        print("  Vitlistade städer  (kan aldrig svartlistas)")
        hr()
        if whitelist:
            for city in whitelist:
                print(f"  • {city}")
        else:
            print("  (listan är tom)")
        print()
        print("  Skriv ett stadsnamn för att lägga till eller ta bort.")
        print("  Enter = tillbaka till menyn.")
        hr()

        val = input("  Stad: ").strip()
        if not val:
            config["city_whitelist"] = whitelist
            return

        # Exakt träff i vitlistan → ta bort
        match = next((c for c in whitelist if c.lower() == val.lower()), None)
        if match:
            whitelist.remove(match)
            print(f"  Tog bort: {match}")
            time.sleep(0.8)
            continue

        # Annars → sök via Nominatim och ta första träffen
        print("  Söker...")
        results = search_address(val)
        if not results:
            print("  Inga träffar – försök igen.")
            time.sleep(1.2)
            continue

        # Extrahera stadsnamn ur första träffen
        hit      = results[0]
        addr_obj = hit.get("address", {})
        city_name = (
            addr_obj.get("city")
            or addr_obj.get("town")
            or addr_obj.get("municipality")
            or hit.get("display_name", "").split(",")[0]
        ).strip()

        if city_name in whitelist:
            print(f"  {city_name} finns redan i vitlistan.")
            time.sleep(1)
            continue

        whitelist.append(city_name)
        print(f"  Lade till: {city_name}  ({hit['display_name'][:60]})")
        time.sleep(1)


# ─── Huvud-meny ─────────────────────────────────────────────────────────────

def show_menu(config: dict) -> str:
    clr()
    max_min  = config.get("max_minutes", 120)
    strikes  = config.get("city_strike_limit", DEFAULT_STRIKE_LIMIT)
    mult     = config.get("max_minutes_multiplier", DEFAULT_MULTIPLIER)
    osrm     = config.get("osrm_mode", "cloud")
    addr_obj = config.get("address") or {}
    addr_raw = addr_obj.get("display_name", "Ej inställd")
    addr     = (addr_raw[:54] + "...") if len(addr_raw) > 57 else addr_raw

    eff_str   = f"{max_min * mult:.0f} min" if mult != 1.0 else f"{max_min} min"
    mult_str  = f"{mult}×  →  {eff_str}" if mult != 1.0 else f"{mult}×  (ingen tolerans)"
    osrm_str  = "Lokalt (Docker)" if osrm == "local" else "Molnet (långsamt)"
    wl        = config.get("city_whitelist", [])
    wl_str    = ", ".join(wl) if wl else "(ingen)"
    if len(wl_str) > 48:
        wl_str = wl_str[:45] + "..."

    print("=" * 72)
    print("  AMS JobbSök – Arbetsförmedlingens jobbannonser")
    print("=" * 72)
    print()
    print(f"  [1]  Adress       {addr}")
    print(f"  [2]  Max restid   {max_min} min")
    print(f"  [3]  Strikes      {strikes}  (strikes per stad innan svartlistning)")
    print(f"  [4]  Tolerans     {mult_str}")
    print(f"  [5]  OSRM-läge    {osrm_str}")
    print(f"  [6]  Vitlista     {wl_str}")
    print()
    hr()
    print("  [0]  Kör sökning")
    print("  [Q]  Avsluta")
    hr()
    print()
    return input("  Välj: ").strip().lower()


# ─── Docker / OSRM lokalt ───────────────────────────────────────────────────

def get_docker_status() -> str:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
        return "available" if r.returncode == 0 else "not_running"
    except FileNotFoundError:
        return "not_installed"
    except subprocess.TimeoutExpired:
        return "not_running"


def get_container_state() -> str:
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", OSRM_CONTAINER],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return "absent"
        return "running" if r.stdout.strip() == "running" else "stopped"
    except Exception:
        return "absent"


def download_with_progress(url: str, dest: Path) -> bool:
    try:
        resp = requests.get(url, stream=True, timeout=60,
                            headers={"User-Agent": "AMS-JobbSok/1.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"\n  Nedladdningsfel: {e}")
        return False

    total      = int(resp.headers.get("content-length", 0))
    downloaded = 0
    bar_w      = 36

    try:
        with open(dest, "wb") as f:
            for data in resp.iter_content(chunk_size=1024 * 1024):
                if data:
                    f.write(data)
                    downloaded += len(data)
                    if total:
                        pct  = downloaded / total
                        done = int(bar_w * pct)
                        bar  = "█" * done + "░" * (bar_w - done)
                        mb_d = downloaded / 1_048_576
                        mb_t = total / 1_048_576
                        print(f"\r  [{bar}] {pct*100:5.1f}%  {mb_d:6.0f}/{mb_t:.0f} MB",
                              end="", flush=True)
        print()
        return True
    except Exception as e:
        print(f"\n  Fel under nedladdning: {e}")
        if dest.exists():
            dest.unlink()
        return False


def run_docker_step(label: str, cmd: list) -> bool:
    print(f"\n  {label}")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            if "[info]" in line:
                parts = line.split("] ", 3)
                msg   = parts[-1] if len(parts) >= 4 else line
                print(f"\r    {msg[:70]:<70}", end="", flush=True)
            elif "[warning]" in line or "[error]" in line:
                print(f"\n    {line[:74]}")
        proc.wait()
        print()
        if proc.returncode != 0:
            print(f"  Kommandot misslyckades (kod {proc.returncode}).")
            return False
        return True
    except Exception as e:
        print(f"\n  Fel: {e}")
        return False


def wait_for_osrm(max_wait: int = 60) -> bool:
    print("  Väntar på OSRM", end="", flush=True)
    for _ in range(max_wait):
        time.sleep(1)
        print(".", end="", flush=True)
        try:
            resp = requests.get(f"{OSRM_LOCAL_URL}/16.0,59.0;16.1,59.1", timeout=2)
            if resp.status_code in (200, 400):
                print(" Redo!")
                return True
        except Exception:
            pass
    print(" Timeout.")
    return False


def setup_local_osrm() -> bool:
    state = get_container_state()

    if state == "running":
        print("  OSRM-container kör redan.")
        return True

    if state == "stopped":
        print("  Startar stoppad OSRM-container...")
        r = subprocess.run(["docker", "start", OSRM_CONTAINER],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return wait_for_osrm()
        print(f"  Kunde inte starta ({r.stderr.strip()[:80]}). Skapar ny...")

    OSRM_DATA_DIR.mkdir(parents=True, exist_ok=True)
    pbf_path  = OSRM_DATA_DIR / PBF_NAME
    osrm_path = OSRM_DATA_DIR / "sweden-latest.osrm"

    if not run_docker_step(f"Hämtar Docker-image {OSRM_IMAGE}...",
                           ["docker", "pull", OSRM_IMAGE]):
        return False

    if not pbf_path.exists():
        print(f"\n  Sverige-kartfil saknas  (~1.5 GB).")
        print(f"  Sparas i: {OSRM_DATA_DIR}")
        if input("  Ladda ner nu? (J/n): ").strip().lower() not in ("", "j", "ja"):
            return False
        print(f"\n  Laddar ner {PBF_NAME}...")
        if not download_with_progress(GEOFABRIK_URL, pbf_path):
            return False
        print("  Nedladdning klar.")

    if not osrm_path.exists():
        vol   = f"{OSRM_DATA_DIR.resolve()}:/data"
        steps = [
            ("Steg 1/3 – Extraherar vägdata (kan ta 10–30 min)...",
             ["docker", "run", "--rm", "-v", vol, OSRM_IMAGE,
              "osrm-extract", "-p", "/opt/car.lua", f"/data/{PBF_NAME}"]),
            ("Steg 2/3 – Partitionerar data...",
             ["docker", "run", "--rm", "-v", vol, OSRM_IMAGE,
              "osrm-partition", "/data/sweden-latest.osrm"]),
            ("Steg 3/3 – Anpassar data...",
             ["docker", "run", "--rm", "-v", vol, OSRM_IMAGE,
              "osrm-customize", "/data/sweden-latest.osrm"]),
        ]
        print("\n  Förbereder vägdata (görs bara en gång).")
        for label, cmd in steps:
            if not run_docker_step(label, cmd):
                return False

    vol = f"{OSRM_DATA_DIR.resolve()}:/data"
    print("\n  Startar OSRM-routing-container...")
    r = subprocess.run(
        ["docker", "run", "-d", "-p", "5000:5000", "-v", vol,
         "--name", OSRM_CONTAINER, "--restart", "unless-stopped",
         OSRM_IMAGE, "osrm-routed", "--algorithm", "mld",
         "/data/sweden-latest.osrm"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        print(f"  Kunde inte starta container: {r.stderr.strip()[:100]}")
        return False

    return wait_for_osrm()


# ─── Jobbhämtning ────────────────────────────────────────────────────────────

def fetch_jobs(home_lat: float, home_lon: float, radius_km: int) -> list:
    all_jobs, offset = [], 0
    for page in range(MAX_PAGES):
        params = {
            "position": f"{home_lat},{home_lon}",
            "position.radius": radius_km,
            "offset": offset,
            "limit": JOBS_PER_PAGE,
        }
        try:
            resp = requests.get(f"{JOBSEARCH_URL}/search", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  API-fel vid sida {page + 1}: {e}")
            break

        hits  = data.get("hits", [])
        total = data.get("total", {}).get("value", 0)
        all_jobs.extend(hits)
        offset += JOBS_PER_PAGE
        print(f"  Hämtade {len(all_jobs)}/{total} annonser...", end="\r")
        if offset >= total or not hits:
            break
        time.sleep(0.3)

    print(f"  Hämtade {len(all_jobs)} annonser totalt.        ")
    return all_jobs


def extract_job_info(job: dict) -> dict:
    wp     = job.get("workplace_address") or {}
    city   = wp.get("city") or wp.get("municipality") or wp.get("region") or "Okänd"
    street = wp.get("street_address") or ""
    plats  = f"{street}, {city}".strip(", ") if street else city

    coords = wp.get("coordinates") or [None, None]
    if isinstance(coords, list) and len(coords) == 2:
        job_lon, job_lat = coords
    elif isinstance(coords, dict):
        job_lat, job_lon = coords.get("lat"), coords.get("lon")
    else:
        job_lat = job_lon = None

    desc = job.get("description", {})
    text = (desc.get("text") or desc.get("text_formatted") or "") \
           if isinstance(desc, dict) else str(desc)
    text = re.sub(r"<[^>]+>", "", text)
    if len(text) > 500:
        text = text[:497] + "..."

    salary = (job.get("salary_description")
              or (job.get("salary_type") or {}).get("label", "") or "")

    parts = []
    for key in ("duration", "working_hours_type"):
        obj = job.get(key) or {}
        if isinstance(obj, dict) and obj.get("label"):
            parts.append(obj["label"])
    employment_type = " / ".join(parts)

    deadline = job.get("application_deadline") or ""
    if deadline and "T" in deadline:
        deadline = deadline.split("T")[0]
    pub_date = job.get("publication_date") or ""
    if pub_date and "T" in pub_date:
        pub_date = pub_date.split("T")[0]

    job_id = job.get("id", "")
    return {
        "jobbtitel":           job.get("headline", ""),
        "arbetsgivare":        (job.get("employer") or {}).get("name", ""),
        "beskrivning":         text,
        "stad":                city,
        "plats":               plats,
        "lat":                 job_lat,
        "lon":                 job_lon,
        "coords_geocoded":     False,   # sätts True om vi geocodade stadsnamnet
        "url":                 f"https://arbetsformedlingen.se/platsbanken/annonser/{job_id}" if job_id else "",
        "publiceringsdatum":   pub_date,
        "sista_ansokningsdag": deadline,
        "anstallningstyp":     employment_type,
        "lon_info":            salary,
    }


# ─── OSRM ───────────────────────────────────────────────────────────────────

def get_driving_info(osrm_url: str, home_lat, home_lon, dest_lat, dest_lon):
    url = f"{osrm_url}/{home_lon},{home_lat};{dest_lon},{dest_lat}"
    try:
        resp = requests.get(url, params={"overview": "false"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == "Ok" and data.get("routes"):
            route = data["routes"][0]
            return round(route["duration"] / 60, 1), round(route["distance"] / 1000, 1)
    except Exception:
        pass
    return None, None


# ─── Status & konsolutskrift ─────────────────────────────────────────────────

def build_status(minutes, city: str, effective_max: float, max_minutes: int,
                 city_strikes: dict, strike_limit: int,
                 city_blacklist: set, city_whitelist: set,
                 apply_strikes: bool = False) -> tuple:
    """
    Returnerar (status_text, inkludera). Uppdaterar strikes/blacklist in-place.
    apply_strikes=True endast för geocodade jobb – de delar alla exakt samma koordinat,
    så om en är för långt bort är alla det. Jobb med egna specifika koordinater
    ska alltid kontrolleras individuellt, aldrig svartlistas.
    Vitlistade städer kan aldrig få strikes eller svartlistas.
    """
    if minutes is None:
        return "Okänd restid", True
    if minutes <= max_minutes:
        return f"< {max_minutes} min", True
    if minutes <= effective_max:
        return f"< {effective_max:.0f} min  (inom toleransen, > {max_minutes} min)", True

    if city in city_whitelist:
        return f"> {effective_max:.0f} min  (vitlistad, ingen strike)", False

    if apply_strikes and city not in SKIP_STRIKE_CITIES:
        city_strikes[city] = city_strikes.get(city, 0) + 1
        n = city_strikes[city]
        if n >= strike_limit:
            city_blacklist.add(city)
            return (f"> {effective_max:.0f} min, "
                    f"strike {n}/{strike_limit} – {city} SVARTLISTAD", False)
        return f"> {effective_max:.0f} min, strike {n}/{strike_limit}", False

    return f"> {effective_max:.0f} min", False


def print_row(title: str, city: str, min_str: str, status: str,
              counter: str = ""):
    t = (title[:33] + "..") if len(title) > 35 else title
    c = (city[:13]  + "..") if len(city)  > 15 else city
    ctr = f"{counter:<8}" if counter else "        "
    print(f"  {ctr}{t:<35}  {c:<15}  {min_str:>6}  {status}")


# ─── CSV-export ─────────────────────────────────────────────────────────────

def export_csv(jobs: list, filename: str) -> Path:
    fieldnames = [
        "jobbtitel", "arbetsgivare", "plats", "minuter_med_bil",
        "avstand_km", "anstallningstyp", "lon_info",
        "publiceringsdatum", "sista_ansokningsdag", "beskrivning", "url",
    ]
    filepath = Path(__file__).parent / filename
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames,
                                delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            writer.writerow(job)
    return filepath


# ─── Kör sökning ────────────────────────────────────────────────────────────

def run_search(config: dict):
    clr()
    hr("=")
    print("  Kör sökning")
    hr("=")

    addr = config.get("address")
    if not addr:
        print("\n  Ingen adress inställd. Gå till menyn och sätt en adress först.")
        pause()
        return

    max_minutes   = config.get("max_minutes", 120)
    strike_limit  = config.get("city_strike_limit", DEFAULT_STRIKE_LIMIT)
    multiplier    = config.get("max_minutes_multiplier", DEFAULT_MULTIPLIER)
    effective_max = max_minutes * multiplier
    osrm_mode     = config.get("osrm_mode", "cloud")
    home_lat, home_lon = addr["lat"], addr["lon"]

    # Sätt upp OSRM
    if osrm_mode == "local":
        print("\n  Kontrollerar lokal OSRM-setup...")
        if setup_local_osrm():
            osrm_url   = OSRM_LOCAL_URL
            osrm_delay = OSRM_DELAY_LOCAL
        else:
            print("  Faller tillbaka till molnet.")
            osrm_url   = OSRM_CLOUD_URL
            osrm_delay = OSRM_DELAY_CLOUD
            osrm_mode  = "cloud"
    else:
        osrm_url   = OSRM_CLOUD_URL
        osrm_delay = OSRM_DELAY_CLOUD

    # Strike-data är bara in-memory per session
    city_strikes:   dict = {}
    city_blacklist: set  = set()
    city_whitelist: set  = set(config.get("city_whitelist", []))

    radius_km = max(int(effective_max * 1.2), 5)
    print(f"\n  Adress:    {addr['display_name'][:60]}")
    print(f"  Max restid: {max_minutes} min × {multiplier} = {effective_max:.0f} min effektivt")
    print(f"  Strikes:   {strike_limit} per stad  |  OSRM: "
          f"{'lokalt' if osrm_mode == 'local' else 'molnet'}")
    print(f"  Sökradie:  ~{radius_km} km")
    print()

    # Hämta jobb
    raw_jobs = fetch_jobs(home_lat, home_lon, radius_km)
    if not raw_jobs:
        print("\n  Inga jobb hittades. Prova en större radie.")
        pause()
        return

    jobs = [extract_job_info(j) for j in raw_jobs]

    jobs_with_coords = [j for j in jobs if j["lat"] is not None and j["lon"] is not None]
    jobs_no_coords   = [j for j in jobs if j["lat"] is None or j["lon"] is None]

    # Geocoda städer utan koordinater
    if jobs_no_coords:
        unique = {j["stad"] for j in jobs_no_coords} - SKIP_STRIKE_CITIES
        print(f"\n  Geocodar {len(unique)} unika städer utan koordinater...")
        cache: dict = {}
        for job in jobs_no_coords:
            lat, lon = geocode_city(job["stad"], cache)
            if lat is not None:
                job["lat"], job["lon"] = lat, lon
                job["coords_geocoded"] = True   # delar stadscentrum med andra jobb i samma stad
                jobs_with_coords.append(job)
        found = sum(1 for j in jobs_no_coords if j["lat"] is not None)
        print(f"  Geocodade {found}/{len(jobs_no_coords)} jobb.")

    jobs_still_no_coords = [j for j in jobs_no_coords if j["lat"] is None]

    # Beräkna restid
    total_to_process = len(jobs_with_coords) + len(jobs_still_no_coords)
    print(f"\n  Beräknar restid för {len(jobs_with_coords)} jobb...")
    print()
    print(f"  {'#':<8}{'Jobbtitel':<35}  {'Stad':<15}  {'Min':>6}  Status")
    hr()

    filtered_jobs = []
    row_idx = 0

    for job in jobs_with_coords:
        title        = job["jobbtitel"]
        city         = job["stad"]
        is_geocoded  = job.get("coords_geocoded", False)

        row_idx += 1
        ctr = f"{row_idx}/{total_to_process}"

        # Hoppa bara över svartlistad stad om jobbet använder geocodade stadscentrum-
        # koordinater – specifika adresskoordinater kontrolleras alltid individuellt.
        if is_geocoded and city in city_blacklist:
            print_row(title, city, "", "SVARTLISTAD – hoppar over", ctr)
            continue

        minutes, km = get_driving_info(osrm_url, home_lat, home_lon,
                                       job["lat"], job["lon"])
        if minutes is not None:
            job["minuter_med_bil"] = minutes
            job["avstand_km"]      = km
            min_str = f"{minutes:.0f}"
        else:
            job["minuter_med_bil"] = "Okänd"
            job["avstand_km"]      = "Okänd"
            min_str = "?"

        status, include = build_status(
            minutes, city, effective_max, max_minutes,
            city_strikes, strike_limit, city_blacklist, city_whitelist,
            apply_strikes=is_geocoded,
        )
        print_row(title, city, min_str, status, ctr)
        if include:
            filtered_jobs.append(job)

        time.sleep(osrm_delay)

    for job in jobs_still_no_coords:
        row_idx += 1
        ctr = f"{row_idx}/{total_to_process}"
        job["minuter_med_bil"] = "Okänd"
        job["avstand_km"]      = "Okänd"
        filtered_jobs.append(job)
        print_row(job["jobbtitel"], job["stad"], "?", "Ingen adressdata", ctr)

    # Sortera och exportera
    filtered_jobs.sort(key=lambda j: (
        0 if isinstance(j.get("minuter_med_bil"), (int, float)) else 1,
        j.get("minuter_med_bil") if isinstance(j.get("minuter_med_bil"), (int, float)) else 0,
    ))

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    filepath  = export_csv(filtered_jobs, f"jobb_{timestamp}.csv")

    print()
    hr("=")
    print(f"  {len(filtered_jobs)} jobb inom {effective_max:.0f} min restid")
    if city_blacklist:
        print(f"  Svartlistade under sessionen: {', '.join(sorted(city_blacklist))}")
    print(f"  Exporterat till: {filepath}")
    hr("=")

    pause("Tryck Enter för att gå tillbaka till menyn...")


# ─── Huvudprogram ────────────────────────────────────────────────────────────

def main():
    config = load_config()

    while True:
        choice = show_menu(config)

        if choice in ("", "0"):
            run_search(config)
        elif choice == "1":
            change_address(config)
            save_config(config)
        elif choice == "2":
            change_max_minutes(config)
            save_config(config)
        elif choice == "3":
            change_strike_limit(config)
            save_config(config)
        elif choice == "4":
            change_multiplier(config)
            save_config(config)
        elif choice == "5":
            change_osrm_mode(config)
            save_config(config)
        elif choice == "6":
            change_whitelist(config)
            save_config(config)
        elif choice in ("q", "avsluta", "exit"):
            clr()
            print("Hejdå!")
            break


if __name__ == "__main__":
    main()
