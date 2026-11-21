#!/usr/bin/env python3
"""GitHub Pages daily job: restore state, collect permitted feeds, export a read-only site."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE_DB = ROOT / "backend" / "data" / "global_watch.db"
CACHE = ROOT / ".cache"
WORK_DB = CACHE / "global_watch.db"
SITE = ROOT / "site"
DATA_DIR = SITE / "data"
TZ_NAME = os.getenv("GW_TIMEZONE", "America/Chicago")

if not BASE_DB.exists():
    raise SystemExit(f"Bundled source database not found: {BASE_DB}")
CACHE.mkdir(parents=True, exist_ok=True)
shutil.copy2(BASE_DB, WORK_DB)
os.environ["GW_DB_PATH"] = str(WORK_DB)
os.environ["GW_PUBLIC_READ_ONLY"] = "true"
sys.path.insert(0, str(ROOT / "backend"))

from app import main as app  # noqa: E402


STATE_TABLES = ("events", "indicators", "humanitarian_indicators", "ingestion_runs", "briefings")
GROUP_COLUMNS = "pgid,country_code,country_name,name,alternate_names,population,religion,language_code,language_name,language_speakers,engagement_status,engagement_progress,gsec,gsec_description,lostness_priority,lostness_priority_description,resource_total,bible_availability,bible_year_published,bible_portions_availability,bible_stories_availability,jesus_film_availability,audio_resource_availability,radio_program_availability,gospel_resource_availability,congregations_exist,church_planting_last2years,capture_date,source_name,source_url"


def previous_state_url() -> str | None:
    explicit = os.getenv("GW_PREVIOUS_STATE_URL", "").strip()
    if explicit:
        return explicit
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not repo or "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    page_root = f"https://{owner}.github.io/" if name.lower() == f"{owner}.github.io".lower() else f"https://{owner}.github.io/{name}/"
    return page_root + "data/state.json"


def fetch_previous_state() -> dict | None:
    url = previous_state_url()
    if not url:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "TheGlobalWatch/1.6.14 scheduled snapshot"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) and isinstance(payload.get("tables"), dict) else None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"No prior public state restored ({type(exc).__name__}); using the bundled baseline.")
        return None


def restore_state(state: dict | None) -> None:
    if not state:
        return
    conn = app.db()
    for table in STATE_TABLES:
        rows = state.get("tables", {}).get(table, [])
        if not isinstance(rows, list):
            continue
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        valid = [key for key in columns if rows and key in rows[0]]
        if not valid:
            continue
        conn.execute(f"DELETE FROM {table}")
        marks = ",".join("?" for _ in valid)
        names = ",".join(f'"{key}"' for key in valid)
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({marks})",
            ([row.get(key) for key in valid] for row in rows),
        )
        print(f"Restored {len(rows):,} prior {table} records")
    conn.commit()
    conn.close()


def attempt(source: str, fn) -> dict:
    try:
        result = fn()
        print(f"{source}: {result}")
        return result if isinstance(result, dict) else {"source": source, "result": str(result)}
    except Exception as exc:
        app.record_run(source, "error", 0, 0, f"Scheduled run failed: {exc}")
        print(f"{source}: ERROR {exc}")
        return {"source": source, "error": str(exc)}


def rows_from_table(table: str) -> list[dict]:
    conn = app.db()
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    conn.close()
    return rows


def export_people_groups() -> None:
    conn = app.db()
    directory = DATA_DIR / "people_groups"
    directory.mkdir(parents=True, exist_ok=True)
    current = None
    buffer: list[dict] = []
    for row in conn.execute(f"SELECT {GROUP_COLUMNS} FROM people_groups ORDER BY country_code,population DESC,name"):
        item = dict(row)
        code = item["country_code"]
        if current is not None and code != current:
            export_json(directory / f"{current}.json", buffer)
            buffer = []
        current = code
        buffer.append(item)
    if current is not None:
        export_json(directory / f"{current}.json", buffer)
    conn.close()


def export_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def build_site(prior_state: dict | None) -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    shutil.copy2(ROOT / "web" / "index.html", SITE / "index.html")
    shutil.copy2(ROOT / "web" / "offline_dossiers.js", SITE / "offline_dossiers.js")
    shutil.copy2(ROOT / "web" / "static_api.js", SITE / "static_api.js")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")

    index_path = SITE / "index.html"
    html = index_path.read_text(encoding="utf-8")
    marker = '<script src="offline_dossiers.js"></script>'
    if marker not in html:
        raise RuntimeError("Could not locate the bundled dossier script in the application page.")
    html = html.replace(marker, marker + '\n<script src="static_api.js"></script>', 1)
    html = html.replace("THE GLOBAL WATCH · PRESIDENTIAL EDITION · LOCAL RESEARCH WORKSPACE", "THE GLOBAL WATCH · PRESIDENTIAL EDITION · DAILY DATA SNAPSHOT")
    index_path.write_text(html, encoding="utf-8")

    now = datetime.now(ZoneInfo(TZ_NAME)).isoformat()
    coverage = app.data_coverage()
    health = {
        "status": "ok", "service": "The Global Watch · free static edition", "utc": app.utcnow(),
        "offline_mode": False, "static_mode": True, "public_read_only": True,
        "scheduler_enabled": True, "scheduler_configured": True, "scheduler_error": None,
        "timezone": TZ_NAME, "daily_briefing_hour": 6, "daily_briefing_minute": 17,
        "source_refresh_minutes": 1440, "country_directory_count": coverage["countries"],
    }
    data = {
        "generated_at": now,
        "health": health,
        "sources": app.sources(),
        "source_status": app.source_status(),
        "data_coverage": coverage,
        "country_coverage": app.country_coverage(),
        "countries": app.countries(),
        "country_profiles": app.country_profiles(),
        "mission_context": app.mission_context(),
        "global_research": app.global_research(),
        "missions_research": app.missions_research(limit=1000),
        "events": app.events(limit=500),
        "briefings": app.briefings(),
        "ingestion_runs": app.ingestion_runs(limit=200),
    }
    # Keep carrying source API results forward without making a daily database commit.
    state = {"updated_at": now, "tables": {table: rows_from_table(table) for table in STATE_TABLES}}
    export_json(DATA_DIR / "current.json", data)
    export_json(DATA_DIR / "indicators.json", rows_from_table("indicators"))
    export_json(DATA_DIR / "state.json", state)
    export_people_groups()
    print(f"Static data snapshot generated at {now}; previous state {'restored' if prior_state else 'not present on first deploy'}.")


def main() -> None:
    app.seed()
    app.seed_curated_research()
    prior = fetch_previous_state()
    restore_state(prior)

    # These are selected public APIs/feeds, not a general web scraper.
    if os.getenv("GW_SKIP_COLLECTION", "false").lower() != "true":
        attempt("gdelt", app.ingest_gdelt)
        attempt("un_geneva", app.ingest_rss_feeds)
        if os.getenv("RELIEFWEB_APPNAME", "").strip():
            attempt("reliefweb", app.ingest_reliefweb)
        if datetime.now(ZoneInfo(TZ_NAME)).weekday() == 0:
            attempt("worldbank", app.ingest_worldbank)
            attempt("unhcr", app.ingest_unhcr_population)
    else:
        print("Collection skipped by GW_SKIP_COLLECTION; exporting bundled data for a local check.")

    day = datetime.now(ZoneInfo(TZ_NAME)).date().isoformat()
    attempt("daily_briefing", lambda: app.build_briefing(day))
    build_site(prior)


if __name__ == "__main__":
    main()
