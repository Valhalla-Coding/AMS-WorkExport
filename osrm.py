import time
import subprocess
import requests
from config import (OSRM_LOCAL_URL, OSRM_IMAGE, OSRM_CONTAINER,
                    OSRM_DATA_DIR, GEOFABRIK_URL, PBF_NAME)


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


def download_with_progress(url: str, dest) -> bool:
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
