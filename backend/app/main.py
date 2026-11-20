from __future__ import annotations
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
import json, os, shutil, sqlite3, time, urllib.request, urllib.error, xml.etree.ElementTree as ET

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DB = Path(os.getenv("GW_DB_PATH", str(ROOT / "data" / "global_watch.db")))
APP_NAME = os.getenv("RELIEFWEB_APPNAME", "").strip()
TZ_NAME = os.getenv("GW_TIMEZONE", "America/Chicago")
BRIEFING_HOUR = int(os.getenv("GW_BRIEFING_HOUR", "6"))
BRIEFING_MINUTE = int(os.getenv("GW_BRIEFING_MINUTE", "30"))
AUTO_INGEST_MINUTES = int(os.getenv("GW_INGEST_INTERVAL_MINUTES", "60"))
GDELT_COOLDOWN_MINUTES = int(os.getenv("GW_GDELT_COOLDOWN_MINUTES", "20"))
PUBLIC_READ_ONLY = os.getenv("GW_PUBLIC_READ_ONLY", "false").lower() == "true"
BOOTSTRAP_BUNDLED_DB = os.getenv("GW_BOOTSTRAP_BUNDLED_DB", "true").lower() == "true"

app = FastAPI(title="The Global Watch API", version="0.2.2",
 description="Sourced global events, humanitarian updates, country indicators and daily briefing service.")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["GET","POST","PUT","DELETE"], allow_headers=["*"])

@app.middleware("http")
async def hosted_read_only_guard(request: Request, call_next):
    if PUBLIC_READ_ONLY and request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return JSONResponse(status_code=403, content={"detail":"This hosted edition is read-only. Personal prayer notes and plans remain saved in your browser on this device."})
    return await call_next(request)

def utcnow(): return datetime.now(timezone.utc).isoformat()

def require_write_access():
    if PUBLIC_READ_ONLY:
        raise HTTPException(status_code=403, detail="This hosted edition is read-only. Personal prayer notes and plans remain saved in your browser on this device.")

def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
      title TEXT NOT NULL, country TEXT, country_code TEXT, event_date TEXT,
      published_at TEXT, url TEXT NOT NULL UNIQUE, summary TEXT, category TEXT,
      confidence TEXT DEFAULT 'unreviewed', retrieved_at TEXT NOT NULL,
      raw_json TEXT DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS indicators(
      id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
      country_code TEXT NOT NULL, country TEXT NOT NULL, indicator_code TEXT NOT NULL,
      indicator_name TEXT NOT NULL, year TEXT, value REAL, unit TEXT,
      source_url TEXT NOT NULL, retrieved_at TEXT NOT NULL,
      UNIQUE(source_id,country_code,indicator_code,year)
    );
    CREATE TABLE IF NOT EXISTS humanitarian_indicators(
      id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
      country_code TEXT NOT NULL, country TEXT NOT NULL, population_type TEXT NOT NULL,
      year TEXT NOT NULL, value REAL, source_url TEXT NOT NULL, retrieved_at TEXT NOT NULL,
      UNIQUE(source_id,country_code,population_type,year)
    );
    CREATE TABLE IF NOT EXISTS people_groups(
      pgid TEXT PRIMARY KEY, country_code TEXT NOT NULL, country_name TEXT NOT NULL,
      name TEXT NOT NULL, alternate_names TEXT DEFAULT '', population INTEGER,
      religion TEXT DEFAULT '', language_code TEXT DEFAULT '', language_name TEXT DEFAULT '',
      language_speakers INTEGER, engagement_status TEXT DEFAULT '',
      engagement_progress TEXT DEFAULT '', gsec TEXT DEFAULT '', gsec_description TEXT DEFAULT '',
      lostness_priority TEXT DEFAULT '', lostness_priority_description TEXT DEFAULT '',
      resource_total INTEGER, bible_availability TEXT DEFAULT '', bible_year_published TEXT DEFAULT '',
      bible_portions_availability TEXT DEFAULT '', bible_stories_availability TEXT DEFAULT '',
      jesus_film_availability TEXT DEFAULT '', audio_resource_availability TEXT DEFAULT '',
      radio_program_availability TEXT DEFAULT '', gospel_resource_availability TEXT DEFAULT '',
      congregations_exist TEXT DEFAULT '', church_planting_last2years TEXT DEFAULT '',
      capture_date TEXT DEFAULT '', source_name TEXT NOT NULL, source_url TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_people_groups_country ON people_groups(country_code);
    CREATE TABLE IF NOT EXISTS religious_composition(
      country_code TEXT NOT NULL, country_name TEXT NOT NULL, year INTEGER NOT NULL,
      religion TEXT NOT NULL, share_percent REAL NOT NULL, source_name TEXT NOT NULL,
      source_url TEXT NOT NULL, retrieved_at TEXT NOT NULL,
      PRIMARY KEY(country_code,year,religion)
    );
    CREATE INDEX IF NOT EXISTS idx_religion_country_year ON religious_composition(country_code,year);
    CREATE TABLE IF NOT EXISTS briefings(
      id INTEGER PRIMARY KEY AUTOINCREMENT, briefing_date TEXT NOT NULL UNIQUE,
      title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
      source_event_count INTEGER DEFAULT 0, status TEXT DEFAULT 'generated'
    );
    CREATE TABLE IF NOT EXISTS countries(
      code TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT, focus TEXT, notes TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS country_profiles(
      code TEXT PRIMARY KEY, official_name TEXT, capital TEXT, region TEXT, subregion TEXT,
      area_km2 REAL, languages_json TEXT DEFAULT '[]', currencies_json TEXT DEFAULT '[]',
      timezones_json TEXT DEFAULT '[]', borders_json TEXT DEFAULT '[]', latlng_json TEXT DEFAULT 'null',
      demonym TEXT, reference_source TEXT NOT NULL, reference_url TEXT NOT NULL,
      data_note TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ingestion_runs(
      id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, started_at TEXT NOT NULL,
      finished_at TEXT, status TEXT NOT NULL, records_seen INTEGER DEFAULT 0,
      records_new INTEGER DEFAULT 0, message TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS global_research(
      id INTEGER PRIMARY KEY AUTOINCREMENT, lane TEXT NOT NULL, subject TEXT NOT NULL,
      summary TEXT NOT NULL, source_name TEXT NOT NULL, source_url TEXT NOT NULL,
      publication_date TEXT NOT NULL, evidence_type TEXT NOT NULL, confidence TEXT NOT NULL,
      methodology TEXT DEFAULT '', created_at TEXT NOT NULL,
      UNIQUE(lane,subject,source_url)
    );
    CREATE TABLE IF NOT EXISTS missions_research(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      lane TEXT NOT NULL CHECK(lane IN ('people_group','scripture_access','religious_freedom','church_missions','mission_access','humanitarian')),
      country_code TEXT NOT NULL, country_name TEXT NOT NULL,
      subject TEXT NOT NULL, summary TEXT NOT NULL,
      source_name TEXT NOT NULL, source_url TEXT NOT NULL,
      publication_date TEXT NOT NULL, event_date TEXT DEFAULT '',
      evidence_type TEXT NOT NULL DEFAULT 'reported',
      confidence TEXT NOT NULL DEFAULT 'unreviewed',
      methodology TEXT DEFAULT '', notes TEXT DEFAULT '',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS research_audit(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      record_id INTEGER NOT NULL, action TEXT NOT NULL,
      actor TEXT NOT NULL DEFAULT 'local_admin', details TEXT DEFAULT '',
      created_at TEXT NOT NULL
    );
    """)
    # Safe in-place migrations for databases created by earlier builds.
    existing={r[1] for r in c.execute("PRAGMA table_info(missions_research)").fetchall()}
    for column, definition in {
      "review_status": "TEXT NOT NULL DEFAULT 'needs_review'",
      "reviewed_at": "TEXT DEFAULT ''",
      "reviewed_by": "TEXT DEFAULT ''",
      "review_note": "TEXT DEFAULT ''"
    }.items():
      if column not in existing:
        c.execute(f"ALTER TABLE missions_research ADD COLUMN {column} {definition}")
    return c

def bootstrap_database():
    """Copy the full packaged research snapshot onto a new persistent host volume once."""
    bundled = ROOT / "data" / "global_watch.db"
    try:
        same_file = DB.resolve() == bundled.resolve()
    except OSError:
        same_file = False
    if BOOTSTRAP_BUNDLED_DB and not DB.exists() and bundled.exists() and not same_file:
        DB.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, DB)

COUNTRIES = []  # No featured or prioritized country list is maintained.

def seed():
    c=db()
    # Seed the full directory from the same ISO catalog used by the map.
    catalog_path = ROOT / "data" / "countries.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else []
    for item in catalog:
        code, name = item["code"], item["name"]
        region, focus = (item.get("region") or "Unassigned region"), "No country is featured or prioritized"
        c.execute("""INSERT INTO countries(code,name,region,focus) VALUES(?,?,?,?)
          ON CONFLICT(code) DO UPDATE SET name=excluded.name,
          region=CASE WHEN countries.region IS NULL OR countries.region='' THEN excluded.region ELSE countries.region END,
          focus=CASE WHEN countries.focus IS NULL OR countries.focus='' THEN excluded.focus ELSE countries.focus END""",
          (code, name, region, focus))
    # Populate every directory entry with a transparent geographic/administrative reference profile.
    profiles_path = ROOT / "data" / "country_profiles.json"
    if profiles_path.exists():
        for item in json.loads(profiles_path.read_text(encoding="utf-8")):
            c.execute("""INSERT INTO country_profiles(code,official_name,capital,region,subregion,area_km2,
              languages_json,currencies_json,timezones_json,borders_json,latlng_json,demonym,reference_source,
              reference_url,data_note,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(code) DO UPDATE SET official_name=excluded.official_name,capital=excluded.capital,
              region=excluded.region,subregion=excluded.subregion,area_km2=excluded.area_km2,
              languages_json=excluded.languages_json,currencies_json=excluded.currencies_json,
              timezones_json=excluded.timezones_json,borders_json=excluded.borders_json,latlng_json=excluded.latlng_json,
              demonym=excluded.demonym,reference_source=excluded.reference_source,reference_url=excluded.reference_url,
              data_note=excluded.data_note,updated_at=excluded.updated_at""",
              (item["code"],item["official_name"],item["capital"],item["region"],item["subregion"],item["area_km2"],
               json.dumps(item["languages"],ensure_ascii=False),json.dumps(item["currencies"]),json.dumps(item["timezones"]),
               json.dumps(item["borders"]),json.dumps(item["latlng"]),item["demonym"],item["reference_source"],
               item["reference_url"],item["data_note"],utcnow()))
    c.commit(); c.close()

def request_json(url, method="GET", payload=None, headers=None, timeout=30):
    h={"User-Agent":"TheGlobalWatch/0.2 (personal research workspace)","Accept":"application/json"}
    if headers: h.update(headers)
    data=None
    if payload is not None:
        data=json.dumps(payload).encode()
        h["Content-Type"]="application/json"
    req=urllib.request.Request(url,data=data,headers=h,method=method)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read()
        return json.loads(raw.decode("utf-8")), r.headers.get("Content-Type","")

def record_run(source,status,seen=0,new=0,message=""):
    c=db(); c.execute("INSERT INTO ingestion_runs(source_id,started_at,finished_at,status,records_seen,records_new,message) VALUES(?,?,?,?,?,?,?)",
       (source,utcnow(),utcnow(),status,seen,new,message[:1000])); c.commit(); c.close()

def save_event(source,title,url,country=None,country_code=None,event_date=None,published_at=None,summary="",category="global_news",raw=None):
    if not title or not url: return False
    c=db()
    try:
        c.execute("""INSERT OR IGNORE INTO events
        (source_id,title,country,country_code,event_date,published_at,url,summary,category,confidence,retrieved_at,raw_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (source,title,country,country_code,event_date,published_at,url,summary,category,"unreviewed",utcnow(),json.dumps(raw or {},ensure_ascii=False)))
        changed=c.execute("SELECT changes()").fetchone()[0]
        c.commit(); return bool(changed)
    finally: c.close()

def last_run(source):
    c=db()
    row=c.execute("SELECT * FROM ingestion_runs WHERE source_id=? ORDER BY id DESC LIMIT 1",(source,)).fetchone()
    c.close()
    return dict(row) if row else None

def gdelt_cooldown_remaining():
    run=last_run("gdelt")
    if not run or not run.get("started_at"):
        return 0
    try:
        started=datetime.fromisoformat(run["started_at"].replace("Z","+00:00"))
        elapsed=(datetime.now(timezone.utc)-started).total_seconds()/60
        return max(0,int(GDELT_COOLDOWN_MINUTES-elapsed))
    except Exception:
        return 0

def ingest_gdelt(query="(church OR christian OR religious freedom OR missionary OR bible translation)",timespan="24h",limit=75,force=False):
    remaining=gdelt_cooldown_remaining()
    if remaining and not force:
        return {"source":"gdelt","skipped":True,"reason":f"Rate-limit protection: retry in about {remaining} minute(s).","cooldown_minutes":remaining}
    params=urlencode({"query":query,"mode":"ArtList","format":"json","timespan":timespan,"maxrecords":min(max(limit,1),250),"sort":"DateDesc"})
    url="https://api.gdeltproject.org/api/v2/doc/doc?"+params
    payload,ctype=request_json(url,timeout=35)
    if "json" not in ctype.lower() and not isinstance(payload,dict): raise ValueError("GDELT response was not JSON")
    arts=payload.get("articles",[]) or []
    new=0
    for a in arts:
        seen=a.get("seendate") or ""
        new += save_event("gdelt",a.get("title"),a.get("url"),None,None,None,seen,
          a.get("title",""),"global_news",a)
    record_run("gdelt","ok",len(arts),new,"GDELT DOC article search; headlines require human verification.")
    return {"source":"gdelt","seen":len(arts),"new":new,"note":"News leads only; review the original report and corroborate."}

def ingest_worldbank(country_codes=None, indicators=None):
    """Bulk-import annual World Bank country indicators for the full directory.

    One API request per indicator is used rather than one request per country.
    Aggregates are excluded; values are stored only for ISO3 directory entries.
    """
    c=db()
    directory={r["code"]:r["name"] for r in c.execute("SELECT code,name FROM countries").fetchall()}
    c.close()
    codes=set(country_codes or directory.keys())
    inds=indicators or [
      "SP.POP.TOTL", "SP.POP.GROW", "SP.POP.DPND", "SP.URB.TOTL.IN.ZS",
      "SP.DYN.LE00.IN", "SP.DYN.TFRT.IN", "SP.DYN.IMRT.IN", "SH.DYN.MORT",
      "NY.GDP.MKTP.CD", "NY.GDP.PCAP.CD", "NY.GDP.MKTP.KD.ZG", "NY.GNP.PCAP.CD",
      "FP.CPI.TOTL.ZG", "SL.UEM.TOTL.ZS", "SL.TLF.CACT.ZS", "SI.POV.DDAY",
      "SE.ADT.LITR.ZS", "SE.PRM.CMPT.ZS", "SE.SEC.CMPT.LO.ZS", "SH.XPD.CHEX.GD.ZS",
      "IT.NET.USER.ZS", "EG.ELC.ACCS.ZS", "EN.ATM.CO2E.PC", "SP.POP.1564.TO.ZS",
      "SP.RUR.TOTL.ZS", "GC.DOD.TOTL.GD.ZS", "NE.TRD.GNFS.ZS", "SH.MED.BEDS.ZS"
    ]
    total=0; stored=0; errors=[]
    c=db()
    try:
        for ind in inds:
            latest_year=datetime.now(timezone.utc).year
            url=f"https://api.worldbank.org/v2/country/all/indicator/{ind}?date=2015:{latest_year}&format=json&per_page=20000"
            try:
                payload,_=request_json(url,timeout=60)
                rows=payload[1] if isinstance(payload,list) and len(payload)>1 and payload[1] else []
                for r in rows:
                    code=(r.get("countryiso3code") or "").upper()
                    val=r.get("value")
                    if code not in codes or val is None: continue
                    total+=1
                    c.execute("""INSERT OR REPLACE INTO indicators
                      (source_id,country_code,country,indicator_code,indicator_name,year,value,unit,source_url,retrieved_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      ("worldbank",code,directory.get(code,(r.get("country") or {}).get("value",code)),ind,
                       (r.get("indicator") or {}).get("value",ind),str(r.get("date")),float(val),
                       r.get("unit") or None,url,utcnow()))
                    stored+=1
            except Exception as e:
                errors.append(f"{ind}: {e}")
        c.commit()
    finally:
        c.close()
    status="ok" if not errors else ("partial" if stored else "error")
    record_run("worldbank",status,total,stored,"Bulk World Bank Indicators API v2; annual country-year observations for 2015–2024. Latest available year varies by indicator and country; no missing values are imputed. "+("Errors: "+" | ".join(errors) if errors else ""))
    return {"source":"worldbank","seen":total,"stored":stored,"indicators_requested":len(inds),"countries_requested":len(codes),"errors":errors,
      "note":"Annual observations for 2015–2024; latest available year varies. Missing values are not imputed."}

def ingest_unhcr_population(year_from=2015, year_to=None):
    """Import UNHCR origin and asylum totals as two distinct, non-additive views.

    The API is paginated. Read each page (up to a protective per-axis ceiling),
    and report a partial run if the ceiling is reached instead of silently
    treating the first page as complete.
    """
    year_to=year_to or date.today().year-1
    c=db(); directory={r["code"]:r["name"] for r in c.execute("SELECT code,name FROM countries").fetchall()}
    url_base="https://api.unhcr.org/population/v1/population/"
    seen=stored=0; errors=[]
    fields=("refugees","asylum_seekers","returned_refugees","idps","returned_idps","stateless","ooc","hst")
    page_size=5000; max_pages=100
    try:
        for axis,param in (("origin","coo_all=true"),("asylum","coa_all=true")):
            page=1
            while page<=max_pages:
                url=f"{url_base}?yearFrom={year_from}&yearTo={year_to}&cf_type=ISO&limit={page_size}&page={page}&{param}"
                try:
                    payload,_=request_json(url,headers={"Accept":"application/json"},timeout=90)
                    rows=payload.get("items",[]) if isinstance(payload,dict) else []
                    if not rows:
                        break
                    for row in rows:
                        code=(row.get("coo_iso") if axis=="origin" else row.get("coa_iso") or "")
                        code=(code or "").upper()
                        if not code or code not in directory: continue
                        for field in fields:
                            raw=row.get(field)
                            try: value=float(raw) if raw not in (None,"","-") else None
                            except (ValueError,TypeError): value=None
                            if value is None: continue
                            seen+=1
                            c.execute("""INSERT OR REPLACE INTO humanitarian_indicators
                              (source_id,country_code,country,population_type,year,value,source_url,retrieved_at)
                              VALUES(?,?,?,?,?,?,?,?)""",
                              ("unhcr",code,directory[code],axis+":"+field,str(row.get("year")),value,url,utcnow()))
                            stored+=1
                    if len(rows)<page_size:
                        break
                    page+=1
                except Exception as e:
                    errors.append(axis+f" page {page}: "+str(e))
                    break
            if page>max_pages:
                errors.append(axis+f" reached the {max_pages}-page safety cap; later rows may be missing")
        c.commit()
    finally: c.close()
    status="ok" if not errors else ("partial" if stored else "error")
    record_run("unhcr",status,seen,stored,f"UNHCR Refugee Statistics API, years {year_from}–{year_to}; separate country-of-origin and country-of-asylum totals. Categories can overlap and must not be summed. "+("Errors: "+" | ".join(errors) if errors else ""))
    return {"source":"unhcr","seen":seen,"stored":stored,"years":f"{year_from}–{year_to}","errors":errors,
      "note":"Origin and asylum views are separate; UNHCR population categories may overlap and must not be summed."}

def ingest_reliefweb(appname=None,limit=50):
    appname=(appname or APP_NAME).strip()
    if not appname:
        return {"source":"reliefweb","skipped":True,"reason":"Set approved RELIEFWEB_APPNAME in backend/.env to enable the API."}
    url=f"https://api.reliefweb.int/v2/reports?appname={appname}"
    body={"limit":min(max(limit,1),100),"sort":["date:desc"],"profile":"list",
          "fields":{"include":["title","date","url","country","primary_country","source","body","language"]}}
    payload,_=request_json(url,method="POST",payload=body,timeout=35)
    rows=payload.get("data",[]) or []; new=0
    for item in rows:
        f=item.get("fields",{}); ctry=(f.get("primary_country") or {})
        if isinstance(ctry,list): ctry=ctry[0] if ctry else {}
        ctryname=ctry.get("name") if isinstance(ctry,dict) else None
        d=(f.get("date") or {}).get("created") or (f.get("date") or {}).get("original") or ""
        new+=save_event("reliefweb",f.get("title"),f.get("url"),ctryname,None,d,d,
                        (f.get("body") or "")[:1200],"humanitarian",f)
    record_run("reliefweb","ok",len(rows),new,"ReliefWeb Read API; appname supplied by operator.")
    return {"source":"reliefweb","seen":len(rows),"new":new}

# Curated, first-party public-information feed. This is a broad UN Geneva feed,
# not a dedicated Christian-persecution or missions feed. Additional feeds can be
# added via GW_RSS_FEEDS in backend/.env as name|url|category entries separated by semicolons.
DEFAULT_RSS_FEEDS = [
    ("un_geneva", "UN Geneva Press Releases", "https://www.ungeneva.org/news-media/press-releases-list/rss.xml", "diplomacy_humanitarian")
]
COUNTRY_ALIASES = {
    "FJI": ["fiji"], "HND": ["honduras"], "BHS": ["bahamas", "the bahamas"],
    "HTI": ["haiti"], "NGA": ["nigeria"], "IND": ["india"],
    "PHL": ["philippines"], "SYR": ["syria"]
}

def configured_rss_feeds():
    raw = os.getenv("GW_RSS_FEEDS", "").strip()
    if not raw:
        return DEFAULT_RSS_FEEDS
    feeds=[]
    for entry in raw.split(";"):
        bits=entry.split("|", 2)
        if len(bits)==3 and all(x.strip() for x in bits):
            feeds.append((bits[0].strip().lower().replace(" ", "_"), bits[0].strip(), bits[1].strip(), bits[2].strip()))
    return feeds or DEFAULT_RSS_FEEDS

def rss_text(node, tag):
    el=node.find(tag)
    if el is None:
        el=node.find("{http://www.w3.org/2005/Atom}"+tag)
    if el is None: return ""
    return "".join(el.itertext()).strip()

def classify_headline(text):
    t=text.lower()
    rules=[
      ("religious_freedom", ["christian", "church", "religious freedom", "faith community", "worship", "religion", "persecution"]),
      ("humanitarian", ["humanitarian", "displacement", "refugee", "famine", "food insecurity", "flood", "earthquake", "cyclone", "drought"]),
      ("security_conflict", ["conflict", "armed group", "attack", "violence", "ceasefire", "security", "terror"]),
      ("governance_diplomacy", ["election", "government", "minister", "parliament", "sanction", "diplomacy", "human rights"])
    ]
    for category, words in rules:
        if any(w in t for w in words): return category
    return "global_news"

def build_country_aliases():
    """Build conservative name aliases from the installed ISO directory.
    This is a lead-attribution aid, not geolocation or analyst verification.
    """
    aliases = {}
    catalog_path = ROOT / "data" / "countries.json"
    if catalog_path.exists():
        try:
            for item in json.loads(catalog_path.read_text(encoding="utf-8")):
                code, name = item.get("code"), item.get("name")
                if code and name:
                    aliases.setdefault(code, set()).add(name.casefold())
        except (OSError, ValueError, TypeError):
            pass
    # Common editorial names that differ from formal ISO display names.
    extras = {
        "BHS": ["bahamas", "the bahamas"], "CIV": ["ivory coast"],
        "COD": ["dr congo", "drc", "congo-kinshasa"], "COG": ["republic of congo", "congo-brazzaville"],
        "KOR": ["south korea"], "PRK": ["north korea"], "RUS": ["russia"],
        "TZA": ["tanzania"], "SYR": ["syria"], "TUR": ["turkey"],
        "PSE": ["palestine", "palestinian territories"], "TLS": ["east timor"],
        "FSM": ["micronesia"], "LAO": ["laos"], "VNM": ["vietnam"],
        "MDA": ["moldova"], "BRN": ["brunei"], "SWZ": ["eswatini"],
        "MKD": ["north macedonia"], "CPV": ["cape verde"],
    }
    for code, values in extras.items():
        aliases.setdefault(code, set()).update(v.casefold() for v in values)
    return aliases

COUNTRY_TEXT_ALIASES = build_country_aliases()
COUNTRY_NAMES_BY_CODE = {item["code"]: item["name"] for item in json.loads((ROOT / "data" / "countries.json").read_text(encoding="utf-8"))}

def match_watchlist_country(text):
    """Return country attribution only when a unique country alias is present."""
    normalized = " ".join((text or "").casefold().replace("’", "'").split())
    matches = []
    for code, aliases in COUNTRY_TEXT_ALIASES.items():
        for alias in aliases:
            # Word boundaries avoid matching e.g. "chad" inside another word.
            import re
            if re.search(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", normalized):
                matches.append(code)
                break
    if len(matches) != 1:
        return None, None
    code = matches[0]
    return COUNTRY_NAMES_BY_CODE.get(code), code

def ingest_rss_feeds(only_source=None):
    results=[]
    feeds=configured_rss_feeds()
    if only_source:
        feeds=[feed for feed in feeds if feed[0] == only_source]
    for source_id, name, feed_url, default_category in feeds:
        try:
            req=urllib.request.Request(feed_url, headers={"User-Agent":"TheGlobalWatch/0.3 (personal research dashboard; RSS reader)"})
            with urllib.request.urlopen(req, timeout=25) as response:
                xml=response.read()
            root=ET.fromstring(xml)
            items=root.findall(".//item")
            atom_ns="{http://www.w3.org/2005/Atom}"
            if not items:
                items=root.findall(".//"+atom_ns+"entry")
            new=0
            for item in items[:60]:
                title=rss_text(item,"title")
                link=rss_text(item,"link")
                if not link:
                    link_el=item.find(atom_ns+"link")
                    if link_el is not None: link=link_el.attrib.get("href","")
                published=rss_text(item,"pubDate") or rss_text(item,"published") or rss_text(item,"updated")
                summary=rss_text(item,"description") or rss_text(item,"summary") or rss_text(item,"content")
                summary=" ".join(summary.split())[:1400]
                country, code=match_watchlist_country(" ".join([title,summary]))
                category=classify_headline(" ".join([title,summary]))
                if category=="global_news": category=default_category
                new += save_event(source_id,title,link,country,code,published,published,summary,category,
                                  {"feed_name":name,"feed_url":feed_url})
            record_run(source_id,"ok",len(items),new,"RSS headlines are discovery leads; country matching is keyword-based and requires analyst verification.")
            results.append({"source":source_id,"name":name,"seen":min(len(items),60),"new":new,"status":"ok"})
        except Exception as e:
            record_run(source_id,"error",0,0,str(e))
            results.append({"source":source_id,"name":name,"status":"error","error":str(e)})
    return results

def build_briefing(brief_date=None):
    day=brief_date or datetime.now(ZoneInfo(TZ_NAME)).date().isoformat()
    c=db()
    rows=c.execute("""SELECT source_id,title,country,event_date,published_at,url,summary,category,retrieved_at
      FROM events WHERE date(retrieved_at)>=date('now','-2 day')
        AND raw_json NOT LIKE '%seeded_release_record%'
      ORDER BY retrieved_at DESC LIMIT 60""").fetchall()
    c.close()
    events=[dict(r) for r in rows]
    lines=[
      f"THE GLOBAL WATCH — DAILY INTELLIGENCE BRIEFING",
      f"Briefing date: {day}",
      f"Generated: {utcnow()}",
      "",
      "IMPORTANT READING NOTE",
      "This is a source-led digest of collected public records, not a verified intelligence assessment. Headlines are leads. Confirm the underlying report, event date, location and corroboration before relying on it.",
      "",
      f"RECENT SOURCE RECORDS ({len(events)})"
    ]
    if not events: lines.append("No recent records have been ingested yet. Run a refresh or configure scheduled ingestion.")
    for i,e in enumerate(events,1):
        lines += [f"{i}. {e['title']}",f"   Source: {e['source_id']} | Country/location: {e['country'] or 'Not resolved'}",
          f"   Event/publication date: {e['event_date'] or e['published_at'] or 'Not supplied'}",
          f"   Category: {e['category']} | Status: {e['confidence']}",
          f"   URL: {e['url']}"]
    lines += ["","ANALYST REVIEW CHECKLIST",
      "• Verify claims against the original publisher and seek independent corroboration.",
      "• Separate event date from publication date and ingestion time.",
      "• Identify affected communities and local voices; avoid inferring private religious affiliation.",
      "• Consider implications for local churches, humanitarian partners, Scripture access and mission access only where evidence supports it.",
      "• Record conflicting accounts, missing data and confidence before producing a forecast.",
      "","PRAYER PROMPT",
      "After verification, write specific prayers for affected communities, local churches and leaders, peace, justice, protection, wise humanitarian response and faithful witness. Do not turn unverified allegations into prayer facts."]
    body="\n".join(lines)
    c=db()
    c.execute("INSERT OR REPLACE INTO briefings(briefing_date,title,body,created_at,source_event_count,status) VALUES(?,?,?,?,?,?)",
      (day,"Global Watch Daily Intelligence Briefing",body,utcnow(),len(events),"source_digest"))
    c.commit(); c.close()
    return {"date":day,"events":len(events),"body":body,"status":"source_digest"}

def seed_curated_research():
    """Load only dated, source-attributed public records bundled with this release."""
    c=db(); now=utcnow()
    research_path=ROOT / "data" / "seed_research.json"
    if research_path.exists():
        for item in json.loads(research_path.read_text(encoding="utf-8")):
            exists=c.execute("SELECT 1 FROM missions_research WHERE lane=? AND country_code=? AND subject=?",(item["lane"],item["country_code"],item["subject"])).fetchone()
            if exists: continue
            country=c.execute("SELECT name FROM countries WHERE code=?",(item["country_code"],)).fetchone()
            if not country: continue
            c.execute("""INSERT INTO missions_research(lane,country_code,country_name,subject,summary,source_name,source_url,
              publication_date,event_date,evidence_type,confidence,methodology,notes,created_at,updated_at,review_status)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'needs_review')""",
              (item["lane"],item["country_code"],country["name"],item["subject"],item["summary"],item["source_name"],item["source_url"],
               item["publication_date"],item["event_date"],item["evidence_type"],item["confidence"],item["methodology"],item["notes"],now,now))
    events_path=ROOT / "data" / "seed_events.json"
    if events_path.exists():
        for e in json.loads(events_path.read_text(encoding="utf-8")):
            c.execute("""INSERT OR IGNORE INTO events(source_id,title,country,country_code,event_date,published_at,url,summary,category,confidence,retrieved_at,raw_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(e["source_id"],e["title"],e.get("country"),e.get("country_code"),e.get("event_date"),e.get("published_at"),e["url"],e["summary"],e["category"],"unreviewed",now,json.dumps({"seeded_release_record":True})))
    global_path=ROOT / "data" / "global_research.json"
    if global_path.exists():
        for g in json.loads(global_path.read_text(encoding="utf-8")):
            c.execute("""INSERT OR IGNORE INTO global_research(lane,subject,summary,source_name,source_url,publication_date,evidence_type,confidence,methodology,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?)""",(g["lane"],g["subject"],g["summary"],g["source_name"],g["source_url"],g["publication_date"],g["evidence_type"],g["confidence"],g["methodology"],now))
    c.commit(); c.close()

def first_run_population_import():
    """Populate country-year development and displacement baselines on first online launch.

    Runs in a daemon thread so the interface becomes available immediately.
    Only World Bank API observations are imported; missing values remain missing.
    """
    try:
        c=db(); n=c.execute("SELECT COUNT(DISTINCT indicator_code) FROM indicators WHERE source_id='worldbank'").fetchone()[0]; h=c.execute("SELECT COUNT(*) FROM humanitarian_indicators WHERE source_id='unhcr'").fetchone()[0]; c.close()
        if n < 20:
            try:
                ingest_worldbank()
            except Exception as e:
                record_run("worldbank","error",0,0,"First-run import failed: "+str(e))
        if h == 0:
            try: ingest_unhcr_population()
            except Exception as e: record_run("unhcr","error",0,0,"First-run import failed: "+str(e))
        # UNHCR annual country snapshots and a public UN press-release RSS feed are refreshed once on first launch.
        try:
            ingest_rss_feeds()
        except Exception as e:
            record_run("un_geneva","error",0,0,"First-run RSS refresh failed: "+str(e))
    except Exception as e:
        try: record_run("first_run_population","error",0,0,str(e))
        except Exception: pass

@app.on_event("startup")
def startup():
    bootstrap_database()
    seed()
    seed_curated_research()
    if PUBLIC_READ_ONLY:
        c=db()
        no_briefings=c.execute("SELECT COUNT(*) FROM briefings").fetchone()[0] == 0
        c.close()
        if no_briefings:
            build_briefing()
    if os.getenv("GW_AUTO_FIRST_RUN_IMPORT", "true").lower() == "true":
        import threading
        threading.Thread(target=first_run_population_import, name="gw-first-run-import", daemon=True).start()
    if os.getenv("GW_ENABLE_SCHEDULER","false").lower()=="true":
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            scheduler=BackgroundScheduler(timezone=TZ_NAME)
            scheduler.add_job(scheduled_refresh,"interval",minutes=max(15,AUTO_INGEST_MINUTES),id="global_refresh",replace_existing=True)
            scheduler.add_job(scheduled_worldbank_refresh,"interval",days=7,id="weekly_worldbank_refresh",replace_existing=True)
            scheduler.add_job(scheduled_unhcr_refresh,"interval",days=7,id="weekly_unhcr_refresh",replace_existing=True)
            scheduler.add_job(lambda: build_briefing(datetime.now(ZoneInfo(TZ_NAME)).date().isoformat()),CronTrigger(hour=BRIEFING_HOUR,minute=BRIEFING_MINUTE),id="daily_briefing",replace_existing=True)
            scheduler.start()
            app.state.scheduler=scheduler
        except Exception as e:
            app.state.scheduler_error=str(e)

def scheduled_refresh():
    """Refresh time-sensitive source leads. Annual World Bank series have a separate weekly job."""
    try: ingest_gdelt()
    except Exception as e: record_run("gdelt","error",0,0,str(e))
    try: ingest_reliefweb()
    except Exception as e: record_run("reliefweb","error",0,0,str(e))
    ingest_rss_feeds()

def scheduled_worldbank_refresh():
    try: ingest_worldbank()
    except Exception as e: record_run("worldbank","error",0,0,str(e))

def scheduled_unhcr_refresh():
    try: ingest_unhcr_population()
    except Exception as e: record_run("unhcr","error",0,0,str(e))

@app.get("/api/health")
def health():
    scheduler = getattr(app.state, "scheduler", None)
    scheduler_running = bool(scheduler and scheduler.running)
    return {"status":"ok","service":"The Global Watch API","utc":utcnow(),
      "scheduler_enabled":scheduler_running,
      "scheduler_configured":os.getenv("GW_ENABLE_SCHEDULER","false").lower()=="true",
      "scheduler_error":getattr(app.state,"scheduler_error",None),
      "public_read_only":PUBLIC_READ_ONLY,
      "timezone":TZ_NAME,"daily_briefing_hour":BRIEFING_HOUR,"daily_briefing_minute":BRIEFING_MINUTE,
      "source_refresh_minutes":max(15,AUTO_INGEST_MINUTES),
      "country_directory_count": len(countries()["countries"])}

@app.get("/api/sources")
def sources():
    return {"sources":[
      {"id":"gdelt","name":"GDELT DOC API","type":"news discovery","access":"Public, no key; rate limited","enabled":True,"url":"https://www.gdeltproject.org/"},
      {"id":"worldbank","name":"World Bank Indicators API v2","type":"development indicators","access":"Public, no key","enabled":True,"url":"https://datahelpdesk.worldbank.org/knowledgebase/articles/889392"},
      {"id":"unhcr","name":"UNHCR Refugee Statistics API","type":"displacement and protection indicators","access":"Public, no key","enabled":True,"url":"https://api.unhcr.org/docs/refugee-statistics.html"},
      {"id":"reliefweb","name":"ReliefWeb Read API","type":"humanitarian reports","access":"Approved appname required","enabled":bool(APP_NAME),"url":"https://apidoc.reliefweb.int/read/"},
      {"id":"un_geneva","name":"UN Geneva Press Releases RSS","type":"official UN diplomacy and public information","access":"Public RSS feed; headline leads only","enabled":True,"url":"https://www.ungeneva.org/news-media/press-releases-list/rss.xml"},
      {"id":"acled","name":"ACLED","type":"conflict events","access":"myACLED account and OAuth credentials required; not connected","enabled":False,"url":"https://acleddata.com/api-documentation/getting-started"},
      {"id":"joshuaproject","name":"Joshua Project","type":"people-group research","access":"Free API key after email verification; not connected until key and current noncommercial attribution terms are confirmed","enabled":False,"url":"https://joshuaproject.net/resources/datasets"},
      {"id":"opendoors","name":"Open Doors World Watch Research","type":"Christian-persecution research","access":"50 publisher rank records bundled; report and methodology links retained","enabled":True,"url":"https://www.opendoors.org/persecution/countries"},
      {"id":"imb_peoplegroups","name":"IMB Global Research / PeopleGroups.org","type":"people groups, engagement, languages and resource signals","access":"12,387 source records bundled; snapshot 2026-11-19; source definitions and estimates vary","enabled":True,"url":"https://peoplegroups.org/downloads/"},
      {"id":"pew_owid","name":"Pew Research Center via Our World in Data","type":"religious affiliation estimates","access":"2010 and 2020 estimates bundled for 199 country/territory codes; 50 directory entries have no observation","enabled":True,"url":"https://ourworldindata.org/grapher/religious-composition"},
      {"id":"progressbible","name":"ProgressBible","type":"language and Scripture access","access":"Country/language translation progress is not embedded; written permission required for registered data product integration","enabled":False,"url":"https://progress.bible/data/"},
      {"id":"state_irf","name":"U.S. Department of State International Religious Freedom Reports","type":"religious freedom country reports","access":"Official annual reports linked for verification; not yet imported as a structured country-by-country data layer","enabled":False,"url":"https://www.state.gov/international-religious-freedom-reports/"},
      {"id":"state_travel","name":"U.S. Department of State Travel Advisories","type":"travel and safety reference","access":"Current official U.S. citizen advisories; review directly before planning; not mission-access analysis","enabled":True,"url":"https://travel.state.gov/en/international-travel/travel-advisories.html"},
      {"id":"imb_opportunities","name":"IMB Go","type":"mission opportunities","access":"Official opportunity pathway; IMB denominational commitments apply","enabled":True,"url":"https://www.imb.org/get-involved/individual/"},
      {"id":"cru_opportunities","name":"Cru Global Opportunities","type":"mission opportunities","access":"Official organization opportunity finder","enabled":True,"url":"https://www.cru.org/us/en/opportunities.html"},
      {"id":"wycliffe_opportunities","name":"Wycliffe Bible Translators","type":"translation service opportunities","access":"Official skills, volunteer and career pathways","enabled":True,"url":"https://www.wycliffe.org/join"}
    ]}

@app.get("/api/source-status")
def source_status():
    output=[]
    for source in ["gdelt","worldbank","unhcr","reliefweb"] + [x[0] for x in configured_rss_feeds()]:
        run=last_run(source)
        output.append({"source":source,"last_run":run,"cooldown_minutes":gdelt_cooldown_remaining() if source=="gdelt" else 0,
          "configured":(bool(APP_NAME) if source=="reliefweb" else True)})
    c=db()
    counts={r["source_id"]:r["n"] for r in c.execute("SELECT source_id,COUNT(*) n FROM events GROUP BY source_id").fetchall()}
    c.close()
    return {"sources":output,"stored_event_counts":counts}

@app.post("/api/refresh")
def refresh(source:Optional[str]=None):
    require_write_access()
    available={"gdelt":ingest_gdelt,"worldbank":ingest_worldbank,"unhcr":ingest_unhcr_population,"reliefweb":ingest_reliefweb,"rss":ingest_rss_feeds}
    # Permit refreshing a configured RSS feed by its own source ID (e.g. un_geneva).
    rss_ids={feed[0] for feed in configured_rss_feeds()}
    for rss_id in rss_ids:
        available[rss_id]=(lambda sid=rss_id: ingest_rss_feeds(sid))
    if source and source not in available:
        allowed=", ".join(sorted(available))
        raise HTTPException(status_code=400,detail=f"Unknown source. Choose one of: {allowed}.")
    selected=[(source,available[source])] if source else list(available.items())
    results=[]
    for name,fn in selected:
        try:
            results.append(fn())
        except urllib.error.HTTPError as e:
            message=f"HTTP {e.code}: {e.reason}"
            if e.code==429: message+=". Source rate limit reached; automatic retries are paused by cooldown protection."
            record_run(name,"error",0,0,message)
            results.append({"source":name,"error":message,"http_status":e.code})
        except Exception as e:
            record_run(name,"error",0,0,str(e)); results.append({"source":name,"error":str(e)})
    return {"refreshed_at":utcnow(),"results":results,"source_status":source_status(),
      "note":"RSS and GDELT records are discovery leads, not verified analysis. RSS country attribution is keyword-based. GDELT is protected by a cooldown after recent attempts."}

@app.get("/api/events")
def events(limit:int=Query(100,ge=1,le=500),country:Optional[str]=None,category:Optional[str]=None):
    c=db(); q="SELECT * FROM events WHERE 1=1"; args=[]
    if country:q+=" AND lower(country)=lower(?)";args.append(country)
    if category:q+=" AND category=?";args.append(category)
    q+=" ORDER BY retrieved_at DESC LIMIT ?";args.append(limit)
    rows=[dict(x) for x in c.execute(q,args).fetchall()];c.close()
    return {"count":len(rows),"events":rows}

@app.get("/api/indicators")
def indicators(country_code:Optional[str]=None,limit:int=100):
    c=db();q="SELECT * FROM indicators";args=[]
    if country_code:q+=" WHERE country_code=?";args.append(country_code)
    q+=" ORDER BY country_code,indicator_code,year DESC LIMIT ?";args.append(min(max(limit,1),500))
    rows=[dict(x) for x in c.execute(q,args).fetchall()];c.close()
    return {"count":len(rows),"indicators":rows}

@app.get("/api/briefings")
def briefings():
    c=db();rows=[dict(x) for x in c.execute("SELECT * FROM briefings ORDER BY briefing_date DESC LIMIT 60").fetchall()];c.close()
    return {"briefings":rows}

@app.post("/api/briefings/generate")
def generate_briefing():
    require_write_access()
    return build_briefing()

@app.get("/api/ingestion-runs")
def ingestion_runs(limit:int=50):
    c=db();rows=[dict(x) for x in c.execute("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT ?",(min(max(limit,1),200),)).fetchall()];c.close()
    return {"runs":rows}

@app.get("/api/countries")
def countries():
    c=db();rows=[dict(x) for x in c.execute("SELECT * FROM countries ORDER BY name").fetchall()];c.close()
    return {"count":len(rows),"countries":rows}

@app.get("/api/countries/{code}/intelligence")
def country_intelligence(code: str):
    """A sourced country snapshot. Empty research lanes remain explicitly empty."""
    code = code.upper()
    c = db()
    country = c.execute("SELECT * FROM countries WHERE code=?", (code,)).fetchone()
    if not country:
        c.close()
        raise HTTPException(status_code=404, detail="Country not found in directory")
    events = [dict(r) for r in c.execute(
        "SELECT id,source_id,title,country,event_date,published_at,url,summary,category,confidence,retrieved_at "
        "FROM events WHERE country_code=? ORDER BY retrieved_at DESC LIMIT 50", (code,)).fetchall()]
    indicators = [dict(r) for r in c.execute(
        "SELECT source_id,country_code,country,indicator_code,indicator_name,year,value,unit,source_url,retrieved_at "
        "FROM indicators WHERE country_code=? ORDER BY indicator_code,year DESC LIMIT 400", (code,)).fetchall()]
    humanitarian = [dict(r) for r in c.execute(
        "SELECT source_id,country_code,country,population_type,year,value,source_url,retrieved_at "
        "FROM humanitarian_indicators WHERE country_code=? ORDER BY population_type,year DESC", (code,)).fetchall()]
    profile_row = c.execute("SELECT * FROM country_profiles WHERE code=?", (code,)).fetchone()
    research = [dict(r) for r in c.execute("SELECT id,lane,subject,summary,source_name,source_url,publication_date,event_date,evidence_type,confidence,methodology,review_status FROM missions_research WHERE country_code=? ORDER BY publication_date DESC,id DESC", (code,)).fetchall()]
    profile = dict(profile_row) if profile_row else None
    if profile:
        for key in ("languages_json","currencies_json","timezones_json","borders_json","latlng_json"):
            profile[key[:-5]] = json.loads(profile.pop(key) or ("null" if key=="latlng_json" else "[]"))
    lanes = {lane: sum(1 for r in research if r["lane"]==lane) for lane in ("people_group","scripture_access","religious_freedom","church_missions","mission_access","humanitarian")}
    groups = [dict(r) for r in c.execute(
        "SELECT pgid,country_code,country_name,name,alternate_names,population,religion,language_code,language_name,language_speakers,engagement_status,engagement_progress,gsec,gsec_description,lostness_priority,lostness_priority_description,resource_total,bible_availability,bible_year_published,bible_portions_availability,bible_stories_availability,jesus_film_availability,audio_resource_availability,radio_program_availability,gospel_resource_availability,congregations_exist,church_planting_last2years,capture_date,source_name,source_url FROM people_groups WHERE country_code=? ORDER BY population DESC,name LIMIT 20", (code,)).fetchall()]
    group_total = c.execute("SELECT COUNT(*) FROM people_groups WHERE country_code=?", (code,)).fetchone()[0]
    status_counts = {r["engagement_status"] or "Not stated": r["n"] for r in c.execute(
        "SELECT engagement_status,COUNT(*) n FROM people_groups WHERE country_code=? GROUP BY engagement_status", (code,)).fetchall()}
    language_rows = [dict(r) for r in c.execute(
        "SELECT language_name,language_code,COUNT(*) group_records,SUM(COALESCE(population,0)) listed_population FROM people_groups WHERE country_code=? AND language_name<>'' GROUP BY language_name,language_code ORDER BY group_records DESC,listed_population DESC LIMIT 8", (code,)).fetchall()]
    scripture_counts = {r["bible_availability"] or "Not stated": r["n"] for r in c.execute(
        "SELECT bible_availability,COUNT(*) n FROM people_groups WHERE country_code=? GROUP BY bible_availability", (code,)).fetchall()}
    congregation_counts = {r["congregations_exist"] or "Not stated": r["n"] for r in c.execute(
        "SELECT congregations_exist,COUNT(*) n FROM people_groups WHERE country_code=? GROUP BY congregations_exist", (code,)).fetchall()}
    composition = [dict(r) for r in c.execute(
        "SELECT country_code,country_name,year,religion,share_percent,source_name,source_url FROM religious_composition WHERE country_code=? AND year IN (2010,2020) ORDER BY year DESC,religion", (code,)).fetchall()]
    c.close()
    return {
        "country": dict(country), "profile": profile, "events": events, "indicators": indicators, "humanitarian": humanitarian,
        "research": research,
        "people_groups": {"count": group_total, "sample": groups, "engagement_status_counts": status_counts,
            "language_context": language_rows, "scripture_availability_counts": scripture_counts,
            "congregation_signal_counts": congregation_counts,
            "source_name": "IMB Global Research / PeopleGroups.org", "source_url": "https://peoplegroups.org/downloads/",
            "capture_date": "2026-11-19",
            "methodology": "Source-defined people-group records and engagement categories. This is not a complete language census, a universal Christian consensus, or a count of every church. Do not use as a stand-alone travel or ministry decision."},
        "religious_composition": composition,
        "coverage": {"event_records": len(events), "indicator_records": len(indicators), "humanitarian_records": len(humanitarian), **lanes,
            "people_group_records": group_total, "religious_composition_observations": len(composition),
            "country_reference": "available" if profile else "missing"},
        "research_note": "This is a sourced country snapshot, not a complete country assessment. Missing lanes indicate unavailable structured data, not absence of need or activity. Country reference fields are not a substitute for current demographic or economic indicators."
    }

@app.get("/api/countries/{code}/people-groups")
def country_people_groups(code: str, search: str = "", page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=100)):
    """Paginated country-only IMB records. Coordinates and sensitive point data are never exposed."""
    code = code.upper()
    c = db()
    where = "country_code=?"; args: list = [code]
    if search.strip():
        where += " AND (name LIKE ? OR language_name LIKE ? OR religion LIKE ? OR engagement_status LIKE ?)"
        like = f"%{search.strip()}%"; args.extend([like, like, like, like])
    total = c.execute(f"SELECT COUNT(*) FROM people_groups WHERE {where}", args).fetchone()[0]
    rows = [dict(r) for r in c.execute(
        f"SELECT pgid,country_code,country_name,name,alternate_names,population,religion,language_code,language_name,language_speakers,engagement_status,engagement_progress,gsec,gsec_description,lostness_priority,lostness_priority_description,resource_total,bible_availability,bible_year_published,bible_portions_availability,bible_stories_availability,jesus_film_availability,audio_resource_availability,radio_program_availability,gospel_resource_availability,congregations_exist,church_planting_last2years,capture_date,source_name,source_url FROM people_groups WHERE {where} ORDER BY population DESC,name LIMIT ? OFFSET ?",
        [*args, per_page, (page-1)*per_page]).fetchall()]
    c.close()
    return {"country_code": code, "page": page, "per_page": per_page, "total": total, "records": rows}

@app.get("/api/country-profiles")
def country_profiles():
    c=db(); rows=[dict(r) for r in c.execute("SELECT * FROM country_profiles ORDER BY code").fetchall()]; c.close()
    for p in rows:
        for key in ("languages_json","currencies_json","timezones_json","borders_json","latlng_json"):
            p[key[:-5]]=json.loads(p.pop(key) or ("null" if key=="latlng_json" else "[]"))
    return {"count":len(rows),"profiles":rows}

@app.get("/api/mission-context")
def mission_context():
    """Small aggregate snapshot for the 10/40 teaching directory; no group coordinates."""
    c=db()
    groups={r["country_code"]:r["n"] for r in c.execute("SELECT country_code,COUNT(*) n FROM people_groups GROUP BY country_code")}
    engagement={}
    for r in c.execute("SELECT country_code,engagement_status,COUNT(*) n FROM people_groups GROUP BY country_code,engagement_status"):
        engagement.setdefault(r["country_code"],{})[r["engagement_status"] or "Not stated"]=r["n"]
    faith={}
    for r in c.execute("SELECT country_code,religion,share_percent FROM religious_composition WHERE year=2020 ORDER BY country_code,share_percent DESC"):
        faith.setdefault(r["country_code"],[]).append({"religion":r["religion"],"share_percent":r["share_percent"]})
    rows=[dict(r) for r in c.execute("SELECT p.code,p.official_name,p.region,p.subregion,p.latlng_json FROM country_profiles p ORDER BY p.official_name")]
    c.close()
    out=[]
    for p in rows:
        try: latlng=json.loads(p.pop("latlng_json") or "null")
        except Exception: latlng=None
        out.append({"code":p["code"],"name":p["official_name"],"region":p["region"],"subregion":p["subregion"],"latlng":latlng,
          "people_group_records":groups.get(p["code"],0),
          "imb_unengaged_records":engagement.get(p["code"],{}).get("Unengaged",0),
          "imb_engaged_records":engagement.get(p["code"],{}).get("Engaged",0),
          "engagement_status_counts":engagement.get(p["code"],{}),
          "religious_composition_2020":faith.get(p["code"],[])})
    return {"count":len(out),"countries":out,"note":"Geographic filter uses country reference coordinates. Religious shares are self-identification estimates. IMB engagement counts are source-defined people-group record counts, not populations, a complete assessment of Gospel access, a universal Christian consensus, or a recommendation to travel."}

@app.get("/api/global-research")
def global_research(lane: Optional[str] = None):
    c=db(); q="SELECT * FROM global_research"; args=[]
    if lane: q += " WHERE lane=?"; args.append(lane)
    q += " ORDER BY publication_date DESC,id DESC"
    rows=[dict(r) for r in c.execute(q,args).fetchall()]; c.close()
    return {"count":len(rows),"records":rows}

@app.get("/api/data-coverage")
def data_coverage():
    c=db()
    total=c.execute("SELECT COUNT(*) FROM countries").fetchone()[0]
    profiles=c.execute("SELECT COUNT(*) FROM country_profiles").fetchone()[0]
    indicators=c.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
    indicator_countries=c.execute("SELECT COUNT(DISTINCT country_code) FROM indicators").fetchone()[0]
    indicator_series=c.execute("SELECT COUNT(DISTINCT indicator_code) FROM indicators").fetchone()[0]
    humanitarian=c.execute("SELECT COUNT(*) FROM humanitarian_indicators").fetchone()[0]
    humanitarian_countries=c.execute("SELECT COUNT(DISTINCT country_code) FROM humanitarian_indicators").fetchone()[0]
    events=c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    research={r["lane"]:r["n"] for r in c.execute("SELECT lane,COUNT(*) n FROM missions_research GROUP BY lane").fetchall()}
    global_n=c.execute("SELECT COUNT(*) FROM global_research").fetchone()[0]
    people_group_records=c.execute("SELECT COUNT(*) FROM people_groups").fetchone()[0]
    people_group_countries=c.execute("SELECT COUNT(DISTINCT country_code) FROM people_groups").fetchone()[0]
    religion_records=c.execute("SELECT COUNT(*) FROM religious_composition").fetchone()[0]
    religion_countries=c.execute("SELECT COUNT(DISTINCT country_code) FROM religious_composition").fetchone()[0]
    c.close()
    gaps=["ProgressBible country/language translation progress is not integrated. Its registered dataset requires written product permission.",
      "IMB people-group records include limited source-defined Scripture availability signals, but are not a translation-progress census; ProgressBible integration requires written permission.",
      "Country-level church landscape, mission access, security reporting and humanitarian reporting beyond the UNHCR population series are not comprehensively populated."]
    if people_group_records:
      gaps.append(f"People-group coverage: {people_group_records:,} IMB Global Research / PeopleGroups.org records across {people_group_countries} country/territory codes (snapshot 2026-11-19). Source definitions and estimates vary; this is not a complete census or universal consensus.")
    else:
      gaps.append("People-group dataset is not loaded.")
    if religion_records:
      gaps.append(f"Religious composition: {religion_records:,} observations across {religion_countries} country/territory codes, Pew 2010/2020 affiliation estimates surfaced through Our World in Data. This is self-identification estimate data, not a precise count; 50 directory entries have no matched observation.")
    else:
      gaps.append("Religious-composition estimates are not loaded.")
    if indicators == 0:
      gaps.append("World Bank annual country indicators are not stored in this database yet.")
    else:
      gaps.append(f"World Bank coverage: {indicator_series} distinct series across {indicator_countries} directory countries/territories; compare the observation year and indicator definition before use.")
    if humanitarian == 0:
      gaps.append("UNHCR country-level displacement observations are not stored yet.")
    else:
      gaps.append(f"UNHCR coverage: {humanitarian_countries} directory countries/territories; origin and asylum views are separate and categories may overlap.")
    return {"countries":total,"country_reference_profiles":profiles,"indicator_records":indicators,"indicator_countries":indicator_countries,"indicator_series":indicator_series,"humanitarian_records":humanitarian,"humanitarian_countries":humanitarian_countries,"event_records":events,"country_research_records":research,"global_research_records":global_n,"people_group_records":people_group_records,"people_group_countries":people_group_countries,"religious_composition_records":religion_records,"religious_composition_countries":religion_countries,
      "important_gaps":gaps}

@app.get("/api/country-coverage")
def country_coverage():
    c = db()
    total = c.execute("SELECT COUNT(*) FROM countries").fetchone()[0]
    event_countries = c.execute("SELECT COUNT(DISTINCT country_code) FROM events WHERE country_code IS NOT NULL").fetchone()[0]
    indicator_countries = c.execute("SELECT COUNT(DISTINCT country_code) FROM indicators").fetchone()[0]
    humanitarian_countries = c.execute("SELECT COUNT(DISTINCT country_code) FROM humanitarian_indicators").fetchone()[0]
    total_events = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    total_indicators = c.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
    total_humanitarian = c.execute("SELECT COUNT(*) FROM humanitarian_indicators").fetchone()[0]
    c.close()
    return {"country_directory": total, "countries_with_attributed_events": event_countries,
            "countries_with_indicators": indicator_countries, "event_records": total_events,
            "indicator_records": total_indicators,"countries_with_humanitarian_observations":humanitarian_countries,
            "humanitarian_records":total_humanitarian,
            "note": "Coverage counts measure stored records only; they do not measure the completeness of country intelligence."}

class MissionsResearchCreate(BaseModel):
    lane: str
    country_code: str
    country_name: Optional[str] = None
    subject: str = Field(min_length=2, max_length=240)
    summary: str = Field(min_length=5, max_length=5000)
    source_name: str = Field(min_length=2, max_length=180)
    source_url: str = Field(min_length=8, max_length=1000)
    publication_date: str
    event_date: str = ""
    evidence_type: str = "reported"
    confidence: str = "unreviewed"
    methodology: str = ""
    notes: str = ""

@app.get("/api/missions-research")
def missions_research(lane: Optional[str] = None, country_code: Optional[str] = None,
                     review_status: Optional[str] = None, limit: int = 300):
    c=db(); q="SELECT * FROM missions_research WHERE 1=1"; args=[]
    if lane: q += " AND lane=?"; args.append(lane)
    if country_code: q += " AND country_code=?"; args.append(country_code.upper())
    if review_status: q += " AND review_status=?"; args.append(review_status)
    q += " ORDER BY updated_at DESC LIMIT ?"; args.append(min(max(limit,1),1000))
    rows=[dict(r) for r in c.execute(q,args).fetchall()]
    counts={r["lane"]:r["n"] for r in c.execute("SELECT lane,COUNT(*) n FROM missions_research GROUP BY lane").fetchall()}
    review_counts={r["review_status"]:r["n"] for r in c.execute("SELECT review_status,COUNT(*) n FROM missions_research GROUP BY review_status").fetchall()}
    c.close(); return {"count":len(rows),"records":rows,"lane_counts":counts,"review_counts":review_counts}

@app.post("/api/missions-research")
def create_missions_research(item: MissionsResearchCreate):
    require_write_access()
    allowed={"people_group","scripture_access","religious_freedom","church_missions","mission_access","humanitarian"}
    if item.lane not in allowed: raise HTTPException(status_code=400,detail="Unsupported research lane")
    if not item.source_url.startswith(("https://","http://")): raise HTTPException(status_code=400,detail="A full source URL is required")
    now=utcnow(); c=db()
    country=c.execute("SELECT name FROM countries WHERE code=?",(item.country_code.upper(),)).fetchone()
    if not country: c.close(); raise HTTPException(status_code=404,detail="Country code not found in directory")
    cur=c.execute("""INSERT INTO missions_research(lane,country_code,country_name,subject,summary,source_name,source_url,publication_date,event_date,evidence_type,confidence,methodology,notes,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(item.lane,item.country_code.upper(),country["name"],item.subject,item.summary,item.source_name,item.source_url,item.publication_date,item.event_date,item.evidence_type,item.confidence,item.methodology,item.notes,now,now))
    row=dict(c.execute("SELECT * FROM missions_research WHERE id=?",(cur.lastrowid,)).fetchone()); c.commit(); c.close(); return row

class ResearchReviewUpdate(BaseModel):
    review_status: str
    reviewed_by: str = "local_admin"
    review_note: str = ""

@app.put("/api/missions-research/{record_id}/review")
def review_missions_research(record_id: int, item: ResearchReviewUpdate):
    require_write_access()
    allowed={"needs_review","verified","disputed","superseded","archived"}
    if item.review_status not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported review status")
    c=db(); now=utcnow()
    row=c.execute("SELECT id FROM missions_research WHERE id=?",(record_id,)).fetchone()
    if not row:
        c.close(); raise HTTPException(status_code=404, detail="Research record not found")
    c.execute("UPDATE missions_research SET review_status=?, reviewed_at=?, reviewed_by=?, review_note=?, updated_at=? WHERE id=?",
      (item.review_status,now,item.reviewed_by[:120],item.review_note[:2000],now,record_id))
    c.execute("INSERT INTO research_audit(record_id,action,actor,details,created_at) VALUES(?,?,?,?,?)",
      (record_id,"review_status_changed",item.reviewed_by[:120],json.dumps({"status":item.review_status,"note":item.review_note[:2000]}),now))
    result=dict(c.execute("SELECT * FROM missions_research WHERE id=?",(record_id,)).fetchone())
    c.commit(); c.close(); return result

@app.get("/api/missions-research/{record_id}/history")
def missions_research_history(record_id: int):
    c=db(); exists=c.execute("SELECT id FROM missions_research WHERE id=?",(record_id,)).fetchone()
    if not exists: c.close(); raise HTTPException(status_code=404,detail="Research record not found")
    rows=[dict(r) for r in c.execute("SELECT action,actor,details,created_at FROM research_audit WHERE record_id=? ORDER BY id DESC",(record_id,)).fetchall()]
    c.close(); return {"record_id":record_id,"history":rows}

@app.delete("/api/missions-research/{record_id}")
def delete_missions_research(record_id:int):
    require_write_access()
    c=db(); cur=c.execute("DELETE FROM missions_research WHERE id=?",(record_id,)); c.commit(); c.close()
    if not cur.rowcount: raise HTTPException(status_code=404,detail="Research record not found")
    return {"deleted":True,"id":record_id}

class CountryUpdate(BaseModel):
    code:str
    name:str
    region:str=""
    focus:str=""
    notes:str=""

@app.put("/api/countries")
def update_country(item:CountryUpdate):
    require_write_access()
    c=db();c.execute("""INSERT INTO countries(code,name,region,focus,notes) VALUES(?,?,?,?,?)
      ON CONFLICT(code) DO UPDATE SET name=excluded.name,region=excluded.region,focus=excluded.focus,notes=excluded.notes""",
      (item.code,item.name,item.region,item.focus,item.notes));c.commit()
    row=dict(c.execute("SELECT * FROM countries WHERE code=?",(item.code,)).fetchone());c.close();return row

class BriefingSave(BaseModel):
    briefing_date:str
    title:str="Global Watch Daily Briefing"
    body:str=Field(min_length=1)

@app.post("/api/briefings")
def save_briefing(item:BriefingSave):
    require_write_access()
    c=db();c.execute("INSERT OR REPLACE INTO briefings(briefing_date,title,body,created_at,status) VALUES(?,?,?,?,?)",
      (item.briefing_date,item.title,item.body,utcnow(),"user_saved"));c.commit();c.close();return {"saved":True}


# Serve the dashboard from the same local application so users only need one address.
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="dashboard")
