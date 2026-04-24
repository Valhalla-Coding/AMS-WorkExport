import time
from datetime import datetime
from pathlib import Path
from config import (DEFAULT_STRIKE_LIMIT, DEFAULT_MULTIPLIER, DEFAULT_MAX_PAGES,
                    SKIP_STRIKE_CITIES, OSRM_LOCAL_URL, OSRM_CLOUD_URL,
                    OSRM_DELAY_LOCAL, OSRM_DELAY_CLOUD)
from tui import clr, hr, pause, print_row
from geocoding import geocode_city
from osrm import get_driving_info, setup_local_osrm
from jobs import fetch_jobs, extract_job_info, export_csv, load_existing_csv, _job_id_from_url

LOG_PATH = Path(__file__).parent / "search.log"


def _log(msg: str, logfile):
    """Skriver till loggfil med tidsstämpel."""
    ts = datetime.now().strftime("%H:%M:%S")
    logfile.write(f"[{ts}] {msg}\n")
    logfile.flush()


def _progress(current: int, total: int, status: str = "", width: int = 40):
    """Skriver en uppdaterbar progress-bar på samma rad."""
    pct   = current / total if total else 0
    filled = int(width * pct)
    bar   = "█" * filled + "░" * (width - filled)
    pct_str = f"{pct*100:.1f}%"
    # Trunkera status så raden inte blir för lång
    status_short = (status[:25] + "…") if len(status) > 26 else status
    print(f"\r  [{bar}] {pct_str}  {current}/{total}  {status_short:<27}", end="", flush=True)


def _normalize_city(city: str) -> str:
    """Normaliserar stadsnamn för jämförelser – lowercase + strip."""
    return (city or "").strip().lower()


def build_status(minutes, city: str, effective_max: float, max_minutes: int,
                 city_strikes: dict, strike_limit: int,
                 city_blacklist: set, city_whitelist: set,
                 apply_strikes: bool = False) -> tuple:
    city_key = _normalize_city(city)

    # max_minutes == 0 → ta med allt
    if max_minutes == 0:
        label = f"{minutes:.0f} min" if minutes is not None else "? min"
        return f"{label}  (allt inkluderas)", True

    if minutes is None:
        return "Okänd restid", True
    if minutes <= max_minutes:
        return f"< {max_minutes} min", True
    if minutes <= effective_max:
        return f"< {effective_max:.0f} min  (inom toleransen, > {max_minutes} min)", True

    if city_key in {_normalize_city(c) for c in city_whitelist}:
        return f"> {effective_max:.0f} min  (vitlistad, ingen strike)", False

    # strike_limit == 0 → svartlista aldrig
    skip = {_normalize_city(c) for c in SKIP_STRIKE_CITIES if c}
    if strike_limit > 0 and apply_strikes and city_key not in skip:
        city_strikes[city_key] = city_strikes.get(city_key, 0) + 1
        n = city_strikes[city_key]
        if n >= strike_limit:
            city_blacklist.add(city_key)
            return (f"> {effective_max:.0f} min, "
                    f"strike {n}/{strike_limit} – {city} SVARTLISTAD", False)
        return f"> {effective_max:.0f} min, strike {n}/{strike_limit}", False

    return f"> {effective_max:.0f} min", False


def run_search(config: dict, existing_csv: dict = None):
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
    effective_max = (max_minutes * multiplier) if max_minutes > 0 else float("inf")
    osrm_mode     = config.get("osrm_mode", "cloud")
    max_pages     = config.get("max_pages", DEFAULT_MAX_PAGES)
    home_lat, home_lon = addr["lat"], addr["lon"]

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

    city_strikes:   dict = {}
    city_blacklist: set  = set()
    city_whitelist: set  = set(config.get("city_whitelist", []))

    radius_km   = max(int(effective_max * 1.2), 5) if max_minutes > 0 else 500
    min_str     = f"{max_minutes} min" if max_minutes > 0 else "av (allt inkluderas)"
    strike_str  = str(strike_limit) if strike_limit > 0 else "av (svartlista aldrig)"
    pages_str   = str(max_pages) if max_pages > 0 else "alla"

    print(f"\n  Adress:      {addr['display_name'][:60]}")
    print(f"  Max restid:  {min_str}")
    print(f"  Strikes:     {strike_str}  |  OSRM: {'lokalt' if osrm_mode == 'local' else 'molnet'}")
    print(f"  Sökradie:    ~{radius_km} km  |  Max sidor: {pages_str}")
    print()

    if existing_csv is None:
        existing_csv = {}

    if existing_csv:
        print(f"  Importerad CSV:  {len(existing_csv)} kända annonser (skippar OSRM för dessa)")

    raw_jobs = fetch_jobs(home_lat, home_lon, radius_km, max_pages)
    if not raw_jobs:
        print("\n  Inga jobb hittades. Prova en större radie.")
        pause()
        return

    jobs = [extract_job_info(j) for j in raw_jobs]

    # Filtrera bort jobb som redan finns i CSV — bevara deras data istället
    new_jobs, reused_count = [], 0
    for job in jobs:
        job_id = _job_id_from_url(job.get("url", ""))
        if job_id and job_id in existing_csv:
            reused_count += 1
        else:
            new_jobs.append(job)

    if reused_count:
        print(f"  Filtrerade bort {reused_count} redan kända annonser (återanvänds från CSV).")

    jobs_with_coords = [j for j in new_jobs if j["lat"] is not None and j["lon"] is not None]
    jobs_no_coords   = [j for j in new_jobs if j["lat"] is None or j["lon"] is None]

    if jobs_no_coords:
        unique = {j["stad"] for j in jobs_no_coords} - SKIP_STRIKE_CITIES
        print(f"\n  Geocodar {len(unique)} unika städer utan koordinater...")
        cache: dict = {}
        for job in jobs_no_coords:
            lat, lon = geocode_city(job["stad"], cache)
            if lat is not None:
                job["lat"], job["lon"] = lat, lon
                job["coords_geocoded"] = True
                jobs_with_coords.append(job)
        found = sum(1 for j in jobs_no_coords if j["lat"] is not None)
        print(f"  Geocodade {found}/{len(jobs_no_coords)} jobb.")

    jobs_still_no_coords = [j for j in jobs_no_coords if j["lat"] is None]

    total_to_process = len(jobs_with_coords) + len(jobs_still_no_coords)
    print(f"\n  Beräknar restid för {total_to_process} nya jobb...")
    print(f"  (Detaljer sparas i search.log)\n")

    filtered_jobs = []
    row_idx = 0

    with open(LOG_PATH, "a", encoding="utf-8") as log:
        _log("=" * 60, log)
        _log(f"Sökning startad — {total_to_process} nya jobb att processa", log)
        _log(f"Adress: {addr['display_name']}", log)
        _log(f"Max restid: {max_minutes} min  |  Sökradie: {radius_km} km", log)
        _log("=" * 60, log)

        for job in jobs_with_coords:
            title       = job["jobbtitel"]
            city        = job["stad"]

            row_idx += 1

            if _normalize_city(city) in city_blacklist:
                _log(f"SVARTLISTAD  {title[:45]}  [{city}]", log)
                _progress(row_idx, total_to_process, f"Svartlistad: {city}")
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

            if job.get("_remote"):
                status, include = "Remote (alltid inkluderad)", True
            else:
                status, include = build_status(
                    minutes, city, effective_max, max_minutes,
                    city_strikes, strike_limit, city_blacklist, city_whitelist,
                    apply_strikes=True,
                )

            _log(f"{min_str:>6} min  {'OK' if include else 'EJ'}  {title[:45]}  [{city}]  {status}", log)
            _progress(row_idx, total_to_process, f"{min_str} min – {city}")

            if include:
                filtered_jobs.append(job)

            time.sleep(osrm_delay)

        for job in jobs_still_no_coords:
            row_idx += 1
            job["minuter_med_bil"] = "Okänd"
            job["avstand_km"]      = "Okänd"
            filtered_jobs.append(job)
            _log(f"  ?  min  OK  {job['jobbtitel'][:45]}  [{job['stad']}]  Ingen adressdata", log)
            _progress(row_idx, total_to_process, f"Ingen adress – {job['stad']}")

        _log(f"Klar — {len(filtered_jobs)} jobb inkluderade", log)

    print()  # ny rad efter progress-baren

    # Lägg tillbaka kända jobb från befintlig CSV med bevarade värden (inkl. "sökt")
    for job_id, old_row in existing_csv.items():
        filtered_jobs.append(old_row)

    def _sort_key(j):
        val = j.get("minuter_med_bil")
        try:
            f = float(val)
            return (0, f)
        except (TypeError, ValueError):
            return (1, 0)

    filtered_jobs.sort(key=_sort_key)

    filepath = export_csv(filtered_jobs)

    print()
    hr("=")
    label = "inkluderade" if max_minutes > 0 else "hittade totalt"
    print(f"  {len(filtered_jobs)} jobb {label}")
    if city_blacklist:
        print(f"  Svartlistade under sessionen: {', '.join(sorted(city_blacklist))} ({len(city_blacklist)} unika städer)")
    print(f"  Exporterat till: {filepath}")
    hr("=")

    pause("Tryck Enter för att gå tillbaka till menyn...")
