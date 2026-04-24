import time
from pathlib import Path
from config import (DEFAULT_STRIKE_LIMIT, DEFAULT_MULTIPLIER, DEFAULT_MAX_PAGES,
                    JOBS_PER_PAGE, load_config, save_config)
from tui import clr, hr, pause
from geocoding import search_address
from osrm import get_docker_status
from search import run_search
from jobs import load_existing_csv, CSV_PATH


def change_address(config: dict):
    clr(); hr()
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
    clr(); hr()
    print("  Ändra max restid")
    hr()
    current = config.get("max_minutes", 120)
    print(f"\n  Nuvarande: {current} min  (0 = ta med allt i CSV, filtrera inget)\n")
    while True:
        val = input("  Ny max restid i minuter (0 = allt, Enter = behåll): ").strip()
        if not val:
            return
        if val.isdigit():
            config["max_minutes"] = int(val)
            return
        print("  Ange ett heltal (0 eller mer).")


def change_strike_limit(config: dict):
    clr(); hr()
    print("  Ändra strike-gräns")
    hr()
    current = config.get("city_strike_limit", DEFAULT_STRIKE_LIMIT)
    print(f"\n  Nuvarande: {current} strikes  (0 = svartlista aldrig)")
    print("  Hur många jobb i en stad måste överskrida restiden")
    print("  innan staden ignoreras för resten av sessionen.\n")
    while True:
        val = input("  Ny strike-gräns (0 = av, Enter = behåll): ").strip()
        if not val:
            return
        if val.isdigit():
            config["city_strike_limit"] = int(val)
            return
        print("  Ange ett heltal (0 eller mer).")


def change_multiplier(config: dict):
    clr(); hr()
    print("  Ändra toleransmultiplikator")
    hr()
    current = config.get("max_minutes_multiplier", DEFAULT_MULTIPLIER)
    max_min = config.get("max_minutes", 120)
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
                print(f"  Satt till {f}×  →  {max_min * f:.0f} min effektivt")
                return
        except ValueError:
            pass
        print("  Ange ett positivt tal, t.ex. 1.0 eller 1.5")


def change_osrm_mode(config: dict):
    clr(); hr()
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
    print(f"\n  Nuvarande: {'Lokalt (Docker)' if current == 'local' else 'Molnet'}\n")
    print("  1.  Lokalt via Docker  – snabbt, inga rate-limits")
    print("  2.  Via molnet          – långsamt (~1 förfrågan/sek)\n")
    while True:
        val = input("  Välj (1/2, Enter = avbryt): ").strip()
        if not val:
            return
        if val == "1":
            config["osrm_mode"] = "local"; return
        if val == "2":
            config["osrm_mode"] = "cloud"; return
        print("  Ange 1 eller 2.")


def change_whitelist(config: dict):
    whitelist: list = list(config.get("city_whitelist", []))

    while True:
        clr(); hr()
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

        match = next((c for c in whitelist if c.lower() == val.lower()), None)
        if match:
            whitelist.remove(match)
            print(f"  Tog bort: {match}")
            time.sleep(0.8)
            continue

        print("  Söker...")
        results = search_address(val)
        if not results:
            print("  Inga träffar – försök igen.")
            time.sleep(1.2)
            continue

        hit      = results[0]
        addr_obj = hit.get("address", {})
        city_name = (
            addr_obj.get("city") or addr_obj.get("town")
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


def change_max_pages(config: dict):
    clr(); hr()
    print("  Ändra max antal sidor att hämta")
    hr()
    current = config.get("max_pages", DEFAULT_MAX_PAGES)
    print(f"\n  Nuvarande: {current}  (0 = hämta alla tillgängliga annonser)")
    print(f"  Varje sida = {JOBS_PER_PAGE} annonser.")
    print(f"  Standard: {DEFAULT_MAX_PAGES} sidor = {DEFAULT_MAX_PAGES * JOBS_PER_PAGE} annonser max.\n")
    while True:
        val = input("  Nytt max antal sidor (0 = alla, Enter = behåll): ").strip()
        if not val:
            return
        if val.isdigit():
            config["max_pages"] = int(val)
            return
        print("  Ange ett heltal (0 eller mer).")


def import_csv(config: dict) -> dict:
    """Låter användaren välja en CSV att importera. Returnerar inläst dict."""
    clr(); hr()
    print("  Importera befintlig CSV")
    hr()
    print(f"\n  Standard-CSV: {CSV_PATH}")
    print("  Ange sökväg till CSV-fil, eller tryck Enter för att använda standard.\n")
    path_input = input("  Sökväg (Enter = standard): ").strip().strip('"')
    path = Path(path_input) if path_input else CSV_PATH

    if not path.exists():
        print(f"\n  Filen hittades inte: {path}")
        pause()
        return {}

    data = load_existing_csv(path)
    if data:
        print(f"\n  Laddade {len(data)} annonser från {path.name}.")
        print("  Dessa hoppas över vid OSRM-beräkning och deras 'sökt'-märkning bevaras.")
    else:
        print("\n  Filen var tom eller kunde inte läsas.")
    pause()
    return data


def show_menu(config: dict, imported_count: int = 0) -> str:
    clr()
    max_min  = config.get("max_minutes", 120)
    strikes  = config.get("city_strike_limit", DEFAULT_STRIKE_LIMIT)
    mult     = config.get("max_minutes_multiplier", DEFAULT_MULTIPLIER)
    osrm     = config.get("osrm_mode", "cloud")
    max_pgs  = config.get("max_pages", DEFAULT_MAX_PAGES)
    addr_obj = config.get("address") or {}
    addr_raw = addr_obj.get("display_name", "Ej inställd")
    addr     = (addr_raw[:54] + "...") if len(addr_raw) > 57 else addr_raw

    if max_min == 0:
        min_disp = "av  (allt inkluderas)"
        eff_disp = "–"
    else:
        min_disp = f"{max_min} min"
        eff_disp = f"{max_min * mult:.0f} min" if mult != 1.0 else f"{max_min} min"

    mult_str    = f"{mult}×  →  {eff_disp}" if mult != 1.0 else f"{mult}×  (ingen tolerans)"
    strike_str  = str(strikes) if strikes > 0 else "av  (svartlista aldrig)"
    osrm_str    = "Lokalt (Docker)" if osrm == "local" else "Molnet (långsamt)"
    if max_pgs > 0:
        pages_disp = f"{max_pgs} sidor  (~{max_pgs * JOBS_PER_PAGE} annonser max)"
    else:
        pages_disp = "alla  (ingen begränsning)"
    wl     = config.get("city_whitelist", [])
    wl_str = ", ".join(wl) if wl else "(ingen)"
    if len(wl_str) > 48:
        wl_str = wl_str[:45] + "..."

    import_disp = f"{imported_count} annonser inladdade" if imported_count else "(ingen)"

    print("=" * 72)
    print("  AMS JobbSök – Arbetsförmedlingens jobbannonser")
    print("=" * 72)
    print()
    print(f"  [1]  Adress        {addr}")
    print(f"  [2]  Max restid    {min_disp}")
    print(f"  [3]  Strikes       {strike_str}")
    print(f"  [4]  Tolerans      {mult_str}")
    print(f"  [5]  OSRM-läge     {osrm_str}")
    print(f"  [6]  Vitlista      {wl_str}")
    print(f"  [7]  Max annonser  {pages_disp}")
    print(f"  [8]  Importera CSV {import_disp}")
    print()
    hr()
    print("  [0]  Kör sökning")
    print("  [Q]  Avsluta")
    hr()
    print()
    return input("  Välj: ").strip().lower()


def main():
    config = load_config()
    existing_csv: dict = {}

    while True:
        choice = show_menu(config, imported_count=len(existing_csv))

        if choice in ("", "0"):
            run_search(config, existing_csv=existing_csv)
        elif choice == "1":
            change_address(config);      save_config(config)
        elif choice == "2":
            change_max_minutes(config);  save_config(config)
        elif choice == "3":
            change_strike_limit(config); save_config(config)
        elif choice == "4":
            change_multiplier(config);   save_config(config)
        elif choice == "5":
            change_osrm_mode(config);    save_config(config)
        elif choice == "6":
            change_whitelist(config);    save_config(config)
        elif choice == "7":
            change_max_pages(config);    save_config(config)
        elif choice == "8":
            existing_csv = import_csv(config)
        elif choice in ("q", "avsluta", "exit"):
            clr()
            print("Hejdå!")
            break
