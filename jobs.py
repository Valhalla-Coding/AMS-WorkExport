import csv
import re
import time
import requests
from datetime import date, timedelta
from pathlib import Path
from config import JOBSEARCH_URL, JOBS_PER_PAGE, DEFAULT_MAX_PAGES

# JobTech API tillåter max offset 2000 per sökning.
# Om totalen överstiger detta delar vi upp sökningen per dag.
API_OFFSET_LIMIT = 2000


def _fetch_page(params: dict, page: int) -> tuple[list, int]:
    """Hämtar en sida från API:et. Returnerar (hits, total)."""
    try:
        resp = requests.get(f"{JOBSEARCH_URL}/search", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("hits", []), data.get("total", {}).get("value", 0)
    except requests.RequestException as e:
        print(f"  API-fel vid sida {page + 1}: {e}")
        return [], 0


def _fetch_window(home_lat: float, home_lon: float, radius_km: int,
                  seen_ids: set, extra_params: dict = None) -> list:
    """
    Hämtar alla annonser för ett givet parametersfönster (t.ex. ett datumintervall).
    Stannar vid API_OFFSET_LIMIT och returnerar insamlade jobb.
    """
    collected = []
    offset = 0
    page = 0
    while offset <= API_OFFSET_LIMIT:
        params = {
            "position":        f"{home_lat},{home_lon}",
            "position.radius": radius_km,
            "offset":          offset,
            "limit":           JOBS_PER_PAGE,
        }
        if extra_params:
            params.update(extra_params)

        hits, total = _fetch_page(params, page)
        if not hits:
            break

        for h in hits:
            jid = h.get("id")
            if jid and jid not in seen_ids:
                seen_ids.add(jid)
                collected.append(h)

        offset += JOBS_PER_PAGE
        page += 1
        if offset >= total:
            break
        time.sleep(0.3)

    return collected


def _fetch_remote_jobs(seen_ids: set) -> list:
    """
    Hämtar remote-jobb i två omgångar:
    1. remote_work=true utan position — fångar alla annonser märkta remote
    2. Fritextsökning på webdev-tekniker + remote — fångar annonser som
       nämner remote i texten men inte är märkta med flaggan
    Deduplicerar mot seen_ids.
    """
    collected = []

    # --- Pass 1: remote_work=true ---
    print("  Hämtar remote-jobb (remote_work=true)...")
    offset, page = 0, 0
    while offset <= API_OFFSET_LIMIT:
        params = {"remote_work": "true", "offset": offset, "limit": JOBS_PER_PAGE}
        hits, total = _fetch_page(params, page)
        if not hits:
            break
        for h in hits:
            jid = h.get("id")
            if jid and jid not in seen_ids:
                seen_ids.add(jid)
                h["_remote"] = True
                collected.append(h)
        offset += JOBS_PER_PAGE
        page += 1
        print(f"  Remote (flaggad): {len(collected)}/{total}...", end="\r")
        if offset >= total:
            break
        time.sleep(0.3)
    print(f"  Remote (flaggad): {len(collected)} jobb.          ")

    # --- Pass 2: fritextsökning webdev-tekniker + distans/hybrid ---
    # Söker utan positionsfilter — täcker hela Sverige
    webdev_queries = [
        "python distans",
        "python remote",
        "javascript distans",
        "javascript remote",
        "webbutvecklare distans",
        "webbutvecklare hybrid",
        "flask python",
        "react remote",
        "frontend distans",
        "backend distans",
    ]
    print("  Hämtar remote webdev-jobb (fritextsökning)...")
    before = len(collected)
    for q in webdev_queries:
        offset, page = 0, 0
        while offset <= API_OFFSET_LIMIT:
            params = {"q": q, "offset": offset, "limit": JOBS_PER_PAGE}
            hits, total = _fetch_page(params, page)
            if not hits:
                break
            for h in hits:
                jid = h.get("id")
                if jid and jid not in seen_ids:
                    seen_ids.add(jid)
                    h["_remote"] = True
                    collected.append(h)
            offset += JOBS_PER_PAGE
            page += 1
            if offset >= total:
                break
            time.sleep(0.3)
        print(f"  '{q}': totalt {len(collected)} remote-jobb...", end="\r")
        time.sleep(0.3)

    new_from_freetext = len(collected) - before
    print(f"  Fritextsökning: +{new_from_freetext} nya remote-jobb.          ")
    print(f"  Remote totalt: {len(collected)} jobb.          ")
    return collected


def fetch_jobs(home_lat: float, home_lon: float, radius_km: int,
               max_pages: int = DEFAULT_MAX_PAGES) -> list:
    seen_ids: set = set()
    all_jobs: list = []

    # --- Första sökning utan datumfilter ---
    offset, page = 0, 0
    first_total = 0
    while True:
        if max_pages > 0 and page >= max_pages:
            break
        params = {
            "position":        f"{home_lat},{home_lon}",
            "position.radius": radius_km,
            "offset":          offset,
            "limit":           JOBS_PER_PAGE,
        }
        hits, total = _fetch_page(params, page)
        if page == 0:
            first_total = total
        if not hits:
            break

        for h in hits:
            jid = h.get("id")
            if jid and jid not in seen_ids:
                seen_ids.add(jid)
                all_jobs.append(h)

        offset += JOBS_PER_PAGE
        page += 1
        print(f"  Hämtade {len(all_jobs)}/{first_total} annonser...", end="\r")

        if offset > API_OFFSET_LIMIT:
            print(f"\n  Totalt {first_total} annonser – använder datumfönster för att hämta resten...")
            break
        if offset >= total or not hits:
            break
        time.sleep(0.3)

    # --- Om vi nådde gränsen, fyll på med dagsvisa fönster ---
    if first_total > API_OFFSET_LIMIT and (max_pages == 0 or page < max_pages):
        today = date.today()
        for days_back in range(0, 90):
            day = today - timedelta(days=days_back)
            day_str = day.strftime("%Y-%m-%dT00:00:00")
            next_str = (day + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
            before_count = len(all_jobs)
            window_jobs = _fetch_window(
                home_lat, home_lon, radius_km, seen_ids,
                extra_params={
                    "published-after":  day_str,
                    "published-before": next_str,
                }
            )
            all_jobs.extend(window_jobs)
            new = len(all_jobs) - before_count
            if new > 0:
                print(f"  {day}  +{new} nya  (totalt {len(all_jobs)})", end="\r")
            time.sleep(0.3)
        print()

    # --- Hämta remote-jobb separat ---
    remote_jobs = _fetch_remote_jobs(seen_ids)
    all_jobs.extend(remote_jobs)

    print(f"  Hämtade {len(all_jobs)} unika annonser totalt.        ")
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
        "coords_geocoded":     False,
        "url":                 f"https://arbetsformedlingen.se/platsbanken/annonser/{job_id}" if job_id else "",
        "publiceringsdatum":   pub_date,
        "sista_ansokningsdag": deadline,
        "anstallningstyp":     employment_type,
        "lon_info":            salary,
    }


CSV_PATH = Path(__file__).parent / "AMS jobb.csv"

FIELDNAMES = [
    "jobbtitel", "arbetsgivare", "plats", "minuter_med_bil",
    "avstand_km", "anstallningstyp", "lon_info",
    "publiceringsdatum", "sista_ansokningsdag", "beskrivning", "url",
    "sökt",
]


def _job_id_from_url(url: str) -> str:
    """Extraherar annons-ID från en platsbanken-URL."""
    return url.rstrip("/").split("/")[-1] if url else ""


def load_existing_csv(path: Path = CSV_PATH) -> dict:
    """
    Läser in en befintlig CSV och returnerar en dict:
        { annons_id: rad_dict }
    Används för att skippa OSRM-beräkning på redan kända jobb
    och bevara 'sökt'-märkningar.
    """
    if not path.exists():
        return {}
    result = {}
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                job_id = _job_id_from_url(row.get("url", ""))
                if job_id:
                    result[job_id] = dict(row)
    except Exception as e:
        print(f"  Varning: kunde inte läsa befintlig CSV: {e}")
    return result


def export_csv(jobs: list) -> Path:
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES,
                                delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            writer.writerow(job)
    return CSV_PATH
