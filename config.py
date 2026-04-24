from pathlib import Path
import json

CONFIG_FILE = Path(__file__).parent / "config.json"

NOMINATIM_URL     = "https://nominatim.openstreetmap.org/search"
JOBSEARCH_URL     = "https://jobsearch.api.jobtechdev.se"
OSRM_CLOUD_URL    = "https://router.project-osrm.org/route/v1/driving"
OSRM_LOCAL_URL    = "http://localhost:5000/route/v1/driving"
OSRM_IMAGE        = "osrm/osrm-backend"
OSRM_CONTAINER    = "ams-jobbsok-osrm"
OSRM_DATA_DIR     = Path(__file__).parent / "osrm-data"
GEOFABRIK_URL     = "https://download.geofabrik.de/europe/sweden-latest.osm.pbf"
PBF_NAME          = "sweden-latest.osm.pbf"

HEADERS_NOMINATIM = {"User-Agent": "AMS-JobbSok/1.0 (jsodori@gmail.com)"}
NOMINATIM_DELAY   = 1.1
OSRM_DELAY_CLOUD  = 1.1
OSRM_DELAY_LOCAL  = 0.05

JOBS_PER_PAGE        = 100
DEFAULT_MAX_PAGES    = 20      # 0 i config = hämta alla tillgängliga sidor
DEFAULT_STRIKE_LIMIT = 5       # 0 i config = svartlista aldrig
DEFAULT_MULTIPLIER   = 1.0
SKIP_STRIKE_CITIES   = {"Okänd", "", None}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
