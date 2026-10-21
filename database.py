"""
Persistencia local (SQLite) de FACENIA Lead Finder.

Principios:
- Los datos comerciales del usuario (estado, prioridad manual, notas, fechas,
  verificación manual de web) NUNCA se sobrescriben al actualizar desde la fuente.
- Las migraciones solo añaden columnas/tablas y hacen copia previa de la base.
- Los registros de demostración antiguos se marcan (is_demo) y se excluyen de
  listados y métricas; solo se borran si el usuario lo pide.
"""

import hashlib
import json
import re
import shutil
import sqlite3
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import (BACKUPS_DIR, DATABASE_PATH, DEFAULT_COMMERCIAL_STATUS,
                    LEGACY_COMMERCIAL_STATUS, OUTCOME_CONTACTED, OUTCOME_NOT_INTERESTED,
                    OUTCOME_RETURN, OUTCOME_SOLD, OUTCOME_TO_STATUS, WEB_FOUND,
                    WEB_NONE, WEB_REVIEW, WEB_SOCIAL, resolve_sector)
from scoring import calculate_lead_score

SCHEMA_VERSION = 4

# Campos que proceden de la fuente y pueden actualizarse
SOURCE_FIELDS = [
    "name", "sector", "address", "city", "postal_code", "phone", "email", "website",
    "social_url", "opening_hours", "map_url", "lat", "lon", "web_status",
    "verification_status", "data_source", "source", "source_id", "place_id", "brand", "is_chain",
]

# Expresiones SQL de valores efectivos (manual si existe; si no, el de la fuente)
EFFECTIVE_WEB = "COALESCE(NULLIF(web_status_manual, ''), web_status)"
EFFECTIVE_PRIORITY = "COALESCE(NULLIF(manual_priority, ''), priority)"


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Normalización y deduplicación
# --------------------------------------------------------------------------
def normalize_text(value: Optional[str]) -> str:
    """Minúsculas, sin tildes ni signos, espacios simples."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def normalize_phone(value: Optional[str]) -> str:
    """Últimos 9 dígitos (formato nacional español), o todos si hay menos."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-9:] if len(digits) >= 9 else digits


def generate_dedup_hash(name: str, address: Optional[str] = None, phone: Optional[str] = None) -> str:
    raw = f"{normalize_text(name)}|{normalize_text(address)}|{normalize_phone(phone)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Esquema y migraciones
# --------------------------------------------------------------------------
LEADS_COLUMNS = {
    "source": "TEXT DEFAULT 'OpenStreetMap'",
    "source_id": "TEXT",
    "place_id": "TEXT",
    "dedup_hash": "TEXT",
    "name": "TEXT NOT NULL DEFAULT ''",
    "sector": "TEXT NOT NULL DEFAULT ''",
    "address": "TEXT DEFAULT ''",
    "city": "TEXT DEFAULT ''",
    "postal_code": "TEXT DEFAULT ''",
    "phone": "TEXT DEFAULT ''",
    "email": "TEXT DEFAULT ''",
    "website": "TEXT DEFAULT ''",
    "social_url": "TEXT DEFAULT ''",
    "opening_hours": "TEXT DEFAULT ''",
    "map_url": "TEXT DEFAULT ''",
    "lat": "REAL",
    "lon": "REAL",
    "web_status": "TEXT NOT NULL DEFAULT 'Sin web indicada'",
    "web_status_manual": "TEXT DEFAULT ''",
    "verification_status": "TEXT DEFAULT ''",
    "data_source": "TEXT DEFAULT 'OpenStreetMap'",
    "score": "INTEGER DEFAULT 0",
    "priority": "TEXT DEFAULT 'Baja'",
    "manual_priority": "TEXT DEFAULT ''",
    "commercial_status": "TEXT DEFAULT 'Pendiente de revisar'",
    "notes": "TEXT DEFAULT ''",
    "next_action": "TEXT DEFAULT ''",
    "last_contact_date": "TEXT DEFAULT ''",
    "next_followup_date": "TEXT DEFAULT ''",
    "is_demo": "INTEGER DEFAULT 0",
    "brand": "TEXT DEFAULT ''",
    "is_chain": "INTEGER DEFAULT 0",
    "scoring_reasons_json": "TEXT DEFAULT '[]'",
    "rating": "REAL",
    "user_ratings_total": "INTEGER",
    "reviews_source": "TEXT DEFAULT ''",
    "review_url": "TEXT DEFAULT ''",
    "review_url_status": "TEXT DEFAULT ''",
    "review_url_updated_at": "TEXT DEFAULT ''",
    "nfc_status": "TEXT DEFAULT ''",
    "nfc_updated_at": "TEXT DEFAULT ''",
    "sale_service": "TEXT DEFAULT ''",
    "sale_amount": "REAL",
    "sale_date": "TEXT DEFAULT ''",
    "discard_reason": "TEXT DEFAULT ''",
    "last_visit_at": "TEXT DEFAULT ''",
    "created_at": "TEXT",
    "updated_at": "TEXT",
    "last_searched_at": "TEXT",
}

# Valores heredados de versiones anteriores -> valores actuales
LEGACY_WEB_STATUS = {
    "SIN WEB INDICADA": WEB_NONE,
    "WEB ENCONTRADA": WEB_FOUND,
    "REVISIÓN MANUAL": WEB_REVIEW,
    "PENDIENTE DE VERIFICAR": WEB_REVIEW,
    "Pendiente de verificar": WEB_REVIEW,
}


def init_db(db_path: Optional[str] = None) -> None:
    """Crea o migra el esquema de forma segura (solo añade, nunca borra)."""
    path = Path(db_path or DATABASE_PATH)
    existed = path.exists() and path.stat().st_size > 0

    conn = _connect(str(path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)")
    row = cur.execute("SELECT value FROM app_settings WHERE key='schema_version'").fetchone()
    current_version = int(row[0]) if row else 0

    if existed and current_version < SCHEMA_VERSION:
        conn.close()
        backup_database(db_path=str(path), label="pre_migracion")
        conn = _connect(str(path))
        cur = conn.cursor()

    cur.execute("CREATE TABLE IF NOT EXISTS leads (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '')")
    existing_cols = {r[1] for r in cur.execute("PRAGMA table_info(leads)")}
    for col, ddl in LEADS_COLUMNS.items():
        if col not in existing_cols:
            # SQLite no permite NOT NULL sin default en ALTER; los DDL ya lo incluyen
            cur.execute(f"ALTER TABLE leads ADD COLUMN {col} {ddl}")

    cur.execute("""CREATE TABLE IF NOT EXISTS searches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        source TEXT, location_query TEXT, location_resolved TEXT,
        lat REAL, lon REAL, radius_km REAL, sectors_json TEXT,
        status TEXT, total INTEGER, new_count INTEGER, duplicate_count INTEGER,
        errors_json TEXT, sector_report_json TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS search_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        search_id INTEGER NOT NULL, data_json TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saved_searches (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, city TEXT NOT NULL,
        province TEXT, country TEXT DEFAULT 'España', radius_km REAL DEFAULT 10,
        sectors_json TEXT NOT NULL, provider TEXT DEFAULT 'OpenStreetMap', created_at TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS geo_cache (
        query_key TEXT PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL,
        bounding_box_json TEXT, display_name TEXT, cached_at TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS routes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, search_id INTEGER,
        start_label TEXT, start_lat REAL, start_lon REAL, radius_m INTEGER,
        requested_stops INTEGER, found_count INTEGER, excluded_count INTEGER DEFAULT 0,
        categories_json TEXT, routing_source TEXT, routing_note TEXT,
        total_distance_m REAL, total_duration_s REAL, geometry_json TEXT,
        current_index INTEGER DEFAULT 0, status TEXT DEFAULT 'Activa')""")
    cur.execute("""CREATE TABLE IF NOT EXISTS route_stops (
        id INTEGER PRIMARY KEY AUTOINCREMENT, route_id INTEGER NOT NULL, position INTEGER NOT NULL,
        lead_id INTEGER NOT NULL, leg_distance_m REAL, leg_duration_s REAL,
        straight_distance_m REAL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL, route_id INTEGER,
        created_at TEXT NOT NULL, outcome TEXT NOT NULL, status_before TEXT, status_after TEXT,
        note TEXT DEFAULT '', followup_date TEXT DEFAULT '', sale_service TEXT DEFAULT '',
        sale_amount REAL, discard_reason TEXT DEFAULT '', snapshot_json TEXT,
        undone INTEGER DEFAULT 0, undone_at TEXT DEFAULT '')""")

    if current_version < SCHEMA_VERSION:
        _migrate_data(cur)

    for idx, col in [("idx_leads_source_id", "source_id"), ("idx_leads_dedup", "dedup_hash"),
                     ("idx_leads_status", "commercial_status"), ("idx_leads_demo", "is_demo")]:
        cur.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON leads({col})")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_results_search ON search_results(search_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stops_route ON route_stops(route_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_inter_lead ON interactions(lead_id)")

    cur.execute("INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('schema_version', ?, ?)",
                (str(SCHEMA_VERSION), _now()))
    conn.commit()
    conn.close()


def _migrate_data(cur: sqlite3.Cursor) -> None:
    """Normaliza datos heredados sin borrar nada."""
    for old, new in LEGACY_WEB_STATUS.items():
        cur.execute("UPDATE leads SET web_status=? WHERE web_status=?", (new, old))
    for old, new in LEGACY_COMMERCIAL_STATUS.items():
        cur.execute("UPDATE leads SET commercial_status=? WHERE commercial_status=?", (new, old))
    cur.execute(f"""UPDATE leads SET web_status=? WHERE web_status=? AND
                    (LOWER(verification_status) LIKE '%red social%')""", (WEB_SOCIAL, WEB_REVIEW))
    cur.execute("""UPDATE leads SET source_id = place_id
                   WHERE (source_id IS NULL OR source_id='') AND place_id IS NOT NULL AND place_id!=''""")
    cur.execute("""UPDATE leads SET map_url = google_maps_url
                   WHERE (map_url IS NULL OR map_url='') AND google_maps_url IS NOT NULL""") \
        if "google_maps_url" in {r[1] for r in cur.execute("PRAGMA table_info(leads)")} else None
    cur.execute("""UPDATE leads SET is_demo=1 WHERE
                   data_source LIKE '%Demostraci%' OR data_source LIKE '%Ficticio%'
                   OR source_id LIKE 'demo_%' OR place_id LIKE 'demo_%'""")
    # Recalcular puntuación con las reglas actuales
    rows = cur.execute("SELECT * FROM leads").fetchall()
    for r in rows:
        lead = dict(r)
        lead["web_status"] = lead.get("web_status_manual") or lead.get("web_status")
        score, priority, reasons = calculate_lead_score(lead)
        cur.execute("UPDATE leads SET score=?, priority=?, scoring_reasons_json=? WHERE id=?",
                    (score, priority, json.dumps(reasons, ensure_ascii=False), r["id"]))


# --------------------------------------------------------------------------
# Prospectos
# --------------------------------------------------------------------------
def _hydrate_lead(row: Dict[str, Any]) -> Dict[str, Any]:
    """Enriquece un diccionario de prospecto con motivos de puntuación y campos efectivos."""
    lead = dict(row)
    lead["web_effective"] = lead.get("web_effective") or lead.get("web_status_manual") or lead.get("web_status") or WEB_NONE
    lead["priority_effective"] = lead.get("priority_effective") or lead.get("manual_priority") or lead.get("priority") or "Baja"
    raw_reasons = lead.get("scoring_reasons_json")
    reasons: List[str] = []
    if raw_reasons:
        try:
            reasons = json.loads(raw_reasons)
        except Exception:
            reasons = []
    if not reasons:
        _, _, reasons = calculate_lead_score({**lead, "web_status": lead["web_effective"]})
    lead["scoring_reasons"] = reasons
    return lead


def _find_existing(cur: sqlite3.Cursor, lead: Dict[str, Any]) -> Optional[sqlite3.Row]:
    source_id = lead.get("source_id") or lead.get("place_id") or ""
    if source_id:
        row = cur.execute("SELECT * FROM leads WHERE source_id=? OR place_id=?",
                          (source_id, source_id)).fetchone()
        if row:
            return row
    dedup = generate_dedup_hash(lead.get("name", ""), lead.get("address"), lead.get("phone"))
    row = cur.execute("SELECT * FROM leads WHERE dedup_hash=?", (dedup,)).fetchone()
    if row:
        return row
    # Coincidencia secundaria: mismo nombre normalizado y mismo teléfono
    phone = normalize_phone(lead.get("phone"))
    if phone:
        name = normalize_text(lead.get("name"))
        for cand in cur.execute("SELECT * FROM leads WHERE phone LIKE ?", (f"%{phone[-6:]}%",)):
            if normalize_phone(cand["phone"]) == phone and normalize_text(cand["name"]) == name:
                return cand
    return None


def get_discarded_keys(db_path: Optional[str] = None) -> Tuple[set, set]:
    """Devuelve (source_ids, dedup_hashes) de negocios marcados como Descartado."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT source_id, place_id, dedup_hash FROM leads WHERE is_demo=0 AND commercial_status='Descartado'"
    ).fetchall()
    conn.close()
    sids = set()
    hashes = set()
    for r in rows:
        if r["source_id"]:
            sids.add(r["source_id"])
        if r["place_id"]:
            sids.add(r["place_id"])
        if r["dedup_hash"]:
            hashes.add(r["dedup_hash"])
    return sids, hashes


def save_lead(lead: Dict[str, Any], db_path: Optional[str] = None) -> Tuple[int, bool]:
    """
    Inserta o actualiza un prospecto. Si ya existe, solo se actualizan campos de
    la fuente con valores no vacíos; los datos comerciales se conservan.
    """
    conn = _connect(db_path)
    cur = conn.cursor()
    now = _now()
    existing = _find_existing(cur, lead)

    if existing:
        current = dict(existing)
        updates: Dict[str, Any] = {}
        for field in SOURCE_FIELDS:
            value = lead.get(field)
            if value not in (None, ""):
                updates[field] = value
        for opt_field in ("rating", "user_ratings_total", "reviews_source"):
            val = lead.get(opt_field)
            if val not in (None, ""):
                updates[opt_field] = val
        for rev_field in ("review_url", "review_url_status"):
            val = lead.get(rev_field)
            if val not in (None, "") and (
                not current.get(rev_field)
                or current.get("review_url_status") != "Añadido manualmente"
            ):
                updates[rev_field] = val
        merged = {**current, **updates}
        merged["web_status"] = current.get("web_status_manual") or merged.get("web_status")
        score, priority, reasons = calculate_lead_score(merged)
        updates.update(
            score=score,
            priority=priority,
            scoring_reasons_json=json.dumps(reasons, ensure_ascii=False),
            updated_at=now,
            last_searched_at=now,
            dedup_hash=generate_dedup_hash(merged["name"], merged.get("address"), merged.get("phone")),
        )
        sets = ", ".join(f"{k}=?" for k in updates)
        cur.execute(f"UPDATE leads SET {sets} WHERE id=?", (*updates.values(), current["id"]))
        conn.commit()
        conn.close()
        return current["id"], False

    record = {f: lead.get(f, "") for f in SOURCE_FIELDS}
    record["lat"] = lead.get("lat")
    record["lon"] = lead.get("lon")
    record["is_chain"] = int(bool(lead.get("is_chain", 0)))
    record["web_status"] = record["web_status"] or WEB_NONE
    score, priority, reasons = calculate_lead_score(record)
    init_status = lead.get("commercial_status") or DEFAULT_COMMERCIAL_STATUS
    init_status = LEGACY_COMMERCIAL_STATUS.get(init_status, init_status)
    record.update(
        place_id=lead.get("source_id") or lead.get("place_id") or "",
        dedup_hash=generate_dedup_hash(record["name"], record["address"], record["phone"]),
        score=score,
        priority=priority,
        scoring_reasons_json=json.dumps(reasons, ensure_ascii=False),
        rating=lead.get("rating"),
        user_ratings_total=lead.get("user_ratings_total"),
        reviews_source=lead.get("reviews_source", ""),
        review_url=lead.get("review_url", ""),
        review_url_status=lead.get("review_url_status", ""),
        commercial_status=init_status,
        is_demo=int(bool(lead.get("is_demo", 0))),
        created_at=now,
        updated_at=now,
        last_searched_at=now,
    )
    cols = ", ".join(record)
    marks = ", ".join("?" for _ in record)
    cur.execute(f"INSERT INTO leads ({cols}) VALUES ({marks})", tuple(record.values()))
    lead_id = cur.lastrowid
    conn.commit()
    conn.close()
    return lead_id, True


def save_leads(leads: List[Dict[str, Any]], db_path: Optional[str] = None) -> Tuple[int, int]:
    new_count = updated = 0
    for lead in leads:
        _, is_new = save_lead(lead, db_path)
        new_count += int(is_new)
        updated += int(not is_new)
    return new_count, updated


def find_saved_ids(leads: List[Dict[str, Any]], db_path: Optional[str] = None) -> Dict[str, int]:
    """Para cada resultado (por source_id) devuelve el id del prospecto guardado, si existe."""
    conn = _connect(db_path)
    cur = conn.cursor()
    found = {}
    for lead in leads:
        row = _find_existing(cur, lead)
        if row and not row["is_demo"]:
            found[lead.get("source_id", "")] = row["id"]
    conn.close()
    return found


def get_leads(search_text: str = "", sectors: Optional[List[str]] = None,
              web_statuses: Optional[List[str]] = None, priorities: Optional[List[str]] = None,
              commercial_statuses: Optional[List[str]] = None, followup_due: bool = False,
              db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Prospectos reales (sin demo) con valores efectivos y filtros combinables."""
    query = (f"SELECT *, {EFFECTIVE_WEB} AS web_effective, {EFFECTIVE_PRIORITY} AS priority_effective "
             "FROM leads WHERE is_demo=0")
    params: List[Any] = []
    if search_text.strip():
        term = f"%{search_text.strip().lower()}%"
        query += " AND (LOWER(name) LIKE ? OR LOWER(address) LIKE ? OR phone LIKE ? OR LOWER(notes) LIKE ? OR LOWER(city) LIKE ?)"
        params += [term] * 5
    for column, values in ((("sector"), sectors), (EFFECTIVE_WEB, web_statuses),
                           (EFFECTIVE_PRIORITY, priorities), ("commercial_status", commercial_statuses)):
        if values:
            query += f" AND {column} IN ({','.join('?' for _ in values)})"
            params += list(values)
    if followup_due:
        query += " AND next_followup_date != '' AND next_followup_date <= ?"
        params.append(date.today().isoformat())
    query += " ORDER BY score DESC, name ASC"
    conn = _connect(db_path)
    rows = [_hydrate_lead(dict(r)) for r in conn.execute(query, params)]
    conn.close()
    return rows


def get_lead(lead_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _connect(db_path)
    row = conn.execute(f"SELECT *, {EFFECTIVE_WEB} AS web_effective, {EFFECTIVE_PRIORITY} AS priority_effective "
                       "FROM leads WHERE id=?", (lead_id,)).fetchone()
    conn.close()
    return _hydrate_lead(dict(row)) if row else None


def update_lead_commercial(lead_id: int, commercial_status: str, notes: str, next_action: str = "",
                           manual_priority: str = "", last_contact_date: str = "",
                           next_followup_date: str = "", db_path: Optional[str] = None) -> bool:
    conn = _connect(db_path)
    cur = conn.execute(
        """UPDATE leads SET commercial_status=?, notes=?, next_action=?, manual_priority=?,
           last_contact_date=?, next_followup_date=?, updated_at=? WHERE id=?""",
        (commercial_status, notes, next_action, manual_priority, last_contact_date,
         next_followup_date, _now(), lead_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def set_manual_web_status(lead_id: int, status: str, website: str = "", detail: str = "",
                          db_path: Optional[str] = None) -> None:
    """Registra una verificación manual (o HTTP) de la web y recalcula la puntuación."""
    lead = get_lead(lead_id, db_path)
    if not lead:
        return
    lead["web_status"] = status or lead["web_status"]
    if website:
        lead["website"] = website
    score, priority, reasons = calculate_lead_score(lead)
    conn = _connect(db_path)
    conn.execute("""UPDATE leads SET web_status_manual=?, website=?, verification_status=?,
                    score=?, priority=?, scoring_reasons_json=?, updated_at=? WHERE id=?""",
                 (status, lead["website"], detail or lead.get("verification_status", ""),
                  score, priority, json.dumps(reasons, ensure_ascii=False), _now(), lead_id))
    conn.commit()
    conn.close()


def update_lead_review_nfc(
    lead_id: int,
    review_url: Optional[str] = None,
    review_url_status: Optional[str] = None,
    nfc_status: Optional[str] = None,
    rating: Optional[float] = None,
    user_ratings_total: Optional[int] = None,
    reviews_source: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Actualiza el enlace de reseñas, el estado de tarjeta NFC y datos públicos de reseñas."""
    now = _now()
    updates: Dict[str, Any] = {"updated_at": now}
    if review_url is not None:
        updates["review_url"] = review_url.strip()
        updates["review_url_updated_at"] = now
    if review_url_status is not None:
        updates["review_url_status"] = review_url_status.strip()
    if nfc_status is not None:
        updates["nfc_status"] = nfc_status.strip()
        updates["nfc_updated_at"] = now
    if rating is not None:
        updates["rating"] = float(rating) if rating != "" else None
    if user_ratings_total is not None:
        updates["user_ratings_total"] = int(user_ratings_total) if user_ratings_total != "" else None
    if reviews_source is not None:
        updates["reviews_source"] = reviews_source.strip()
    sets = ", ".join(f"{k}=?" for k in updates)
    conn = _connect(db_path)
    conn.execute(f"UPDATE leads SET {sets} WHERE id=?", (*updates.values(), lead_id))
    conn.commit()
    conn.close()
    return get_lead(lead_id, db_path)


def delete_lead(lead_id: int, db_path: Optional[str] = None) -> bool:
    conn = _connect(db_path)
    cur = conn.execute("DELETE FROM leads WHERE id=?", (lead_id,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def count_demo_leads(db_path: Optional[str] = None) -> int:
    conn = _connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM leads WHERE is_demo=1").fetchone()[0]
    conn.close()
    return n


def delete_demo_leads(db_path: Optional[str] = None) -> int:
    conn = _connect(db_path)
    cur = conn.execute("DELETE FROM leads WHERE is_demo=1")
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


# --------------------------------------------------------------------------
# Rutas a pie (ETAPA 2)
# --------------------------------------------------------------------------
def save_route(route_meta: Dict[str, Any], stops: List[Dict[str, Any]], db_path: Optional[str] = None) -> int:
    """Persiste una ruta generada y sus paradas ordenadas."""
    conn = _connect(db_path)
    cur = conn.cursor()
    now = _now()
    cur.execute(
        """INSERT INTO routes (
            created_at, search_id, start_label, start_lat, start_lon, radius_m,
            requested_stops, found_count, excluded_count, categories_json,
            routing_source, routing_note, total_distance_m, total_duration_s,
            geometry_json, current_index, status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            now,
            route_meta.get("search_id"),
            route_meta.get("start_label", ""),
            route_meta.get("start_lat"),
            route_meta.get("start_lon"),
            int(route_meta.get("radius_m", 1000)),
            int(route_meta.get("requested_stops", len(stops))),
            int(route_meta.get("found_count", len(stops))),
            int(route_meta.get("excluded_count", 0)),
            json.dumps(route_meta.get("categories", []), ensure_ascii=False),
            route_meta.get("routing_source", ""),
            route_meta.get("routing_note", ""),
            route_meta.get("total_distance_m"),
            route_meta.get("total_duration_s"),
            json.dumps(route_meta.get("geometry") or [], ensure_ascii=False),
            0,
            "Activa",
        ),
    )
    route_id = cur.lastrowid
    for idx, stop in enumerate(stops, start=1):
        cur.execute(
            """INSERT INTO route_stops (route_id, position, lead_id, leg_distance_m, leg_duration_s, straight_distance_m)
               VALUES (?,?,?,?,?,?)""",
            (
                route_id,
                int(stop.get("position", idx)),
                int(stop["lead_id"]),
                stop.get("leg_distance_m"),
                stop.get("leg_duration_s"),
                stop.get("straight_distance_m"),
            ),
        )
    conn.commit()
    conn.close()
    return route_id


def get_route(route_id: Optional[int] = None, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Recupera una ruta (o la más reciente si route_id es None) con todas sus paradas y datos de negocio."""
    conn = _connect(db_path)
    if route_id is None:
        r_row = conn.execute("SELECT * FROM routes ORDER BY id DESC LIMIT 1").fetchone()
    else:
        r_row = conn.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
    if not r_row:
        conn.close()
        return None
    route = dict(r_row)
    route["categories"] = json.loads(route.pop("categories_json") or "[]")
    route["geometry"] = json.loads(route.pop("geometry_json") or "[]")

    stop_rows = conn.execute(
        """SELECT rs.position, rs.lead_id, rs.leg_distance_m, rs.leg_duration_s, rs.straight_distance_m,
                  l.*, COALESCE(NULLIF(l.web_status_manual, ''), l.web_status) AS web_effective,
                  COALESCE(NULLIF(l.manual_priority, ''), l.priority) AS priority_effective
           FROM route_stops rs
           JOIN leads l ON l.id = rs.lead_id
           WHERE rs.route_id = ?
           ORDER BY rs.position ASC""",
        (route["id"],),
    ).fetchall()
    conn.close()

    stops = []
    for sr in stop_rows:
        item = _hydrate_lead(dict(sr))
        stops.append(item)
    route["stops"] = stops
    return route


def list_routes(limit: int = 20, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    rows = conn.execute("SELECT * FROM routes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["categories"] = json.loads(d.pop("categories_json") or "[]")
        d.pop("geometry_json", None)
        result.append(d)
    return result


def update_route_progress(route_id: int, current_index: int, status: Optional[str] = None,
                          db_path: Optional[str] = None) -> bool:
    conn = _connect(db_path)
    if status:
        cur = conn.execute("UPDATE routes SET current_index=?, status=? WHERE id=?",
                           (int(current_index), status, route_id))
    else:
        cur = conn.execute("UPDATE routes SET current_index=? WHERE id=?", (int(current_index), route_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


# --------------------------------------------------------------------------
# Resultados de visita, interacciones y deshacer (ETAPA 5)
# --------------------------------------------------------------------------
SNAPSHOT_FIELDS = [
    "commercial_status", "notes", "next_action", "last_contact_date", "next_followup_date",
    "sale_service", "sale_amount", "sale_date", "discard_reason", "last_visit_at",
]


def record_visit_outcome(
    lead_id: int,
    outcome: str,
    route_id: Optional[int] = None,
    note: str = "",
    next_action: str = "",
    followup_date: str = "",
    sale_service: str = "",
    sale_amount: Optional[float] = None,
    discard_reason: str = "",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Registra el resultado de una visita comercial (Vendido, Volver otro día, No interesa, etc.),
    guarda una instantánea previa para permitir deshacer y actualiza el prospecto.
    """
    lead = get_lead(lead_id, db_path)
    if not lead:
        raise ValueError(f"No existe el negocio con ID {lead_id}")

    snapshot = {k: lead.get(k) for k in SNAPSHOT_FIELDS}
    status_before = lead.get("commercial_status") or DEFAULT_COMMERCIAL_STATUS
    status_after = OUTCOME_TO_STATUS.get(outcome, outcome if outcome in LEGACY_COMMERCIAL_STATUS.values() else status_before)

    now = _now()
    today = date.today().isoformat()

    new_notes = lead.get("notes") or ""
    if note and note.strip():
        stamp = datetime.now().strftime("%d/%m %H:%M")
        entry = f"[{stamp} · {outcome}] {note.strip()}"
        new_notes = f"{new_notes}\n{entry}".strip() if new_notes else entry

    new_followup = followup_date.strip() if followup_date else (lead.get("next_followup_date") or "")
    new_next_action = next_action.strip() if next_action else (lead.get("next_action") or "")
    new_sale_service = lead.get("sale_service") or ""
    new_sale_amount = lead.get("sale_amount")
    new_sale_date = lead.get("sale_date") or ""
    new_discard_reason = lead.get("discard_reason") or ""

    if outcome == OUTCOME_SOLD or status_after == "Cliente conseguido":
        if sale_service and sale_service.strip():
            new_sale_service = sale_service.strip()
        if sale_amount is not None and str(sale_amount).strip() != "":
            new_sale_amount = float(sale_amount)
        new_sale_date = today
    elif outcome == OUTCOME_NOT_INTERESTED or status_after == "Descartado":
        if discard_reason and discard_reason.strip():
            new_discard_reason = discard_reason.strip()
        new_followup = ""

    conn = _connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """UPDATE leads SET
            commercial_status=?, notes=?, next_action=?, last_contact_date=?,
            next_followup_date=?, sale_service=?, sale_amount=?, sale_date=?,
            discard_reason=?, last_visit_at=?, updated_at=?
           WHERE id=?""",
        (
            status_after,
            new_notes,
            new_next_action,
            today,
            new_followup,
            new_sale_service,
            new_sale_amount,
            new_sale_date,
            new_discard_reason,
            now,
            now,
            lead_id,
        ),
    )
    cur.execute(
        """INSERT INTO interactions (
            lead_id, route_id, created_at, outcome, status_before, status_after,
            note, followup_date, sale_service, sale_amount, discard_reason, snapshot_json, undone
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)""",
        (
            lead_id,
            route_id,
            now,
            outcome,
            status_before,
            status_after,
            note.strip() if note else "",
            new_followup,
            new_sale_service,
            new_sale_amount if (outcome == OUTCOME_SOLD and sale_amount is not None) else None,
            new_discard_reason,
            json.dumps(snapshot, ensure_ascii=False),
        ),
    )
    interaction_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {
        "interaction_id": interaction_id,
        "lead": get_lead(lead_id, db_path),
        "outcome": outcome,
        "status_after": status_after,
    }


def undo_interaction(
    interaction_id: Optional[int] = None,
    lead_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Deshace la última interacción registrada y restaura el estado previo del prospecto."""
    conn = _connect(db_path)
    cur = conn.cursor()
    if interaction_id is not None:
        row = cur.execute("SELECT * FROM interactions WHERE id=? AND undone=0", (interaction_id,)).fetchone()
    elif lead_id is not None:
        row = cur.execute(
            "SELECT * FROM interactions WHERE lead_id=? AND undone=0 ORDER BY id DESC LIMIT 1", (lead_id,)
        ).fetchone()
    else:
        row = cur.execute("SELECT * FROM interactions WHERE undone=0 ORDER BY id DESC LIMIT 1").fetchone()

    if not row:
        conn.close()
        return None

    inter = dict(row)
    snapshot = json.loads(inter.get("snapshot_json") or "{}")
    now = _now()
    if snapshot:
        sets = ", ".join(f"{k}=?" for k in SNAPSHOT_FIELDS)
        vals = [snapshot.get(k) for k in SNAPSHOT_FIELDS]
        cur.execute(f"UPDATE leads SET {sets}, updated_at=? WHERE id=?", (*vals, now, inter["lead_id"]))
    else:
        cur.execute(
            "UPDATE leads SET commercial_status=?, updated_at=? WHERE id=?",
            (inter.get("status_before") or DEFAULT_COMMERCIAL_STATUS, now, inter["lead_id"]),
        )
    cur.execute("UPDATE interactions SET undone=1, undone_at=? WHERE id=?", (now, inter["id"]))
    conn.commit()
    conn.close()
    return {
        "undone_interaction_id": inter["id"],
        "lead": get_lead(inter["lead_id"], db_path),
    }


def get_lead_interactions(lead_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM interactions WHERE lead_id=? ORDER BY id DESC", (lead_id,)
        ).fetchall()
    ]
    conn.close()
    return rows


def get_recent_interactions(limit: int = 30, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    rows = [
        dict(r)
        for r in conn.execute(
            """SELECT i.*, l.name AS lead_name, l.sector AS lead_sector
               FROM interactions i
               JOIN leads l ON l.id = i.lead_id
               WHERE l.is_demo = 0
               ORDER BY i.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    ]
    conn.close()
    return rows


# --------------------------------------------------------------------------
# Estadísticas comerciales reales (Sección 7)
# --------------------------------------------------------------------------
def get_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Métricas reales sobre prospectos guardados (excluye demo)."""
    conn = _connect(db_path)
    q = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    today = date.today().isoformat()
    total = q("SELECT COUNT(*) FROM leads WHERE is_demo=0")
    no_web = q(
        f"SELECT COUNT(*) FROM leads WHERE is_demo=0 AND {EFFECTIVE_WEB} IN (?,?,?)",
        WEB_NONE, WEB_SOCIAL, "Sin web (verificado)",
    )
    high = q(f"SELECT COUNT(*) FROM leads WHERE is_demo=0 AND {EFFECTIVE_PRIORITY}='Alta'")
    to_review = q(
        "SELECT COUNT(*) FROM leads WHERE is_demo=0 AND commercial_status IN (?, 'Pendiente de revisar', 'Pendiente de contactar')",
        DEFAULT_COMMERCIAL_STATUS,
    )
    visited = q(
        "SELECT COUNT(*) FROM leads WHERE is_demo=0 AND (last_visit_at != '' OR commercial_status IN ('Visitado','Contactado','Seguimiento pendiente','Cliente conseguido','Descartado','Cliente'))"
    )
    contacted = q(
        "SELECT COUNT(*) FROM leads WHERE is_demo=0 AND commercial_status IN ('Contactado','Seguimiento pendiente','Cliente conseguido','Cliente','Ha respondido')"
    )
    followup_pending = q(
        "SELECT COUNT(*) FROM leads WHERE is_demo=0 AND (commercial_status IN ('Seguimiento pendiente','Seguimiento') OR next_followup_date != '')"
    )
    followup_due = q(
        "SELECT COUNT(*) FROM leads WHERE is_demo=0 AND next_followup_date!='' AND next_followup_date<=?",
        today,
    )
    clients = q(
        "SELECT COUNT(*) FROM leads WHERE is_demo=0 AND commercial_status IN ('Cliente conseguido','Cliente')"
    )
    discarded = q(
        "SELECT COUNT(*) FROM leads WHERE is_demo=0 AND commercial_status IN ('Descartado','No interesado')"
    )
    revenue_row = conn.execute(
        "SELECT COALESCE(SUM(sale_amount), 0.0), COUNT(sale_amount) FROM leads WHERE is_demo=0 AND commercial_status IN ('Cliente conseguido','Cliente') AND sale_amount IS NOT NULL"
    ).fetchone()
    sales_amount_total = float(revenue_row[0] or 0.0)
    sales_with_amount_count = int(revenue_row[1] or 0)
    conversion_rate = round((clients / visited) * 100.0, 1) if visited > 0 else 0.0

    # Desglose por categoría
    cat_rows = conn.execute(
        """SELECT sector,
                  COUNT(*) AS found,
                  SUM(CASE WHEN last_visit_at != '' OR commercial_status IN ('Visitado','Contactado','Seguimiento pendiente','Cliente conseguido','Descartado','Cliente') THEN 1 ELSE 0 END) AS visited,
                  SUM(CASE WHEN commercial_status IN ('Contactado','Seguimiento pendiente','Cliente conseguido','Cliente') THEN 1 ELSE 0 END) AS contacted,
                  SUM(CASE WHEN commercial_status IN ('Seguimiento pendiente','Seguimiento') OR next_followup_date != '' THEN 1 ELSE 0 END) AS followup,
                  SUM(CASE WHEN commercial_status IN ('Cliente conseguido','Cliente') THEN 1 ELSE 0 END) AS sold,
                  COALESCE(SUM(CASE WHEN commercial_status IN ('Cliente conseguido','Cliente') AND sale_amount IS NOT NULL THEN sale_amount ELSE 0 END), 0.0) AS amount
           FROM leads
           WHERE is_demo=0
           GROUP BY sector
           ORDER BY sold DESC, visited DESC, found DESC"""
    ).fetchall()
    by_category = [dict(r) for r in cat_rows]

    conn.close()
    return {
        "total": total,
        "found_total": total,
        "no_web": no_web,
        "high": high,
        "to_review": to_review,
        "visited_count": visited,
        "contacted_count": contacted,
        "followup_pending": followup_pending,
        "followup_due": followup_due,
        "clients": clients,
        "sales_count": clients,
        "discarded_count": discarded,
        "sales_amount_total": sales_amount_total,
        "sales_with_amount_count": sales_with_amount_count,
        "conversion_rate": conversion_rate,
        "by_category": by_category,
    }


# --------------------------------------------------------------------------
# Búsquedas e historial
# --------------------------------------------------------------------------
def record_search(report: Dict[str, Any], results: List[Dict[str, Any]], db_path: Optional[str] = None) -> int:
    """Guarda el informe de la búsqueda y sus resultados (los anteriores se conservan en historial)."""
    conn = _connect(db_path)
    cur = conn.cursor()
    cur.execute("""INSERT INTO searches (created_at, source, location_query, location_resolved, lat, lon,
                   radius_km, sectors_json, status, total, new_count, duplicate_count, errors_json, sector_report_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_now(), report.get("source"), report.get("location_query"), report.get("location_resolved"),
                 report.get("lat"), report.get("lon"), report.get("radius_km"),
                 json.dumps(report.get("sectors", []), ensure_ascii=False), report.get("status"),
                 report.get("total", 0), report.get("new_count", 0), report.get("duplicate_count", 0),
                 json.dumps(report.get("errors", []), ensure_ascii=False),
                 json.dumps(report.get("sector_report", []), ensure_ascii=False)))
    search_id = cur.lastrowid
    cur.executemany("INSERT INTO search_results (search_id, data_json) VALUES (?, ?)",
                    [(search_id, json.dumps(r, ensure_ascii=False)) for r in results])
    # Conservar solo los resultados de las últimas 20 búsquedas
    cur.execute("DELETE FROM search_results WHERE search_id NOT IN (SELECT id FROM searches ORDER BY id DESC LIMIT 20)")
    conn.commit()
    conn.close()
    return search_id


def _search_row_to_report(row: sqlite3.Row) -> Dict[str, Any]:
    rep = dict(row)
    rep["sectors"] = json.loads(rep.pop("sectors_json") or "[]")
    rep["errors"] = json.loads(rep.pop("errors_json") or "[]")
    rep["sector_report"] = json.loads(rep.pop("sector_report_json") or "[]")
    return rep


def get_search(search_id: Optional[int] = None, db_path: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Informe y resultados de una búsqueda (por defecto, la última)."""
    conn = _connect(db_path)
    if search_id is None:
        row = conn.execute("SELECT * FROM searches ORDER BY id DESC LIMIT 1").fetchone()
    else:
        row = conn.execute("SELECT * FROM searches WHERE id=?", (search_id,)).fetchone()
    if not row:
        conn.close()
        return None, []
    results = [json.loads(r[0]) for r in conn.execute(
        "SELECT data_json FROM search_results WHERE search_id=? ORDER BY id", (row["id"],))]
    conn.close()
    return _search_row_to_report(row), results


def get_search_history(limit: int = 30, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    rows = [_search_row_to_report(r) for r in conn.execute(
        "SELECT * FROM searches ORDER BY id DESC LIMIT ?", (limit,))]
    conn.close()
    return rows


# --------------------------------------------------------------------------
# Búsquedas guardadas
# --------------------------------------------------------------------------
def save_search_query(name: str, city: str, province: str, country: str, radius_km: float,
                      sectors: List[str], provider: str = "OpenStreetMap", db_path: Optional[str] = None) -> int:
    conn = _connect(db_path)
    cur = conn.execute("""INSERT INTO saved_searches (name, city, province, country, radius_km, sectors_json, provider, created_at)
                          VALUES (?,?,?,?,?,?,?,?)""",
                       (name.strip(), city.strip(), province.strip(), country.strip(), radius_km,
                        json.dumps(sectors, ensure_ascii=False), provider, _now()))
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def get_saved_searches(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    items = []
    for r in conn.execute("SELECT * FROM saved_searches ORDER BY name"):
        item = dict(r)
        item["sectors"] = json.loads(item["sectors_json"] or "[]")
        items.append(item)
    conn.close()
    return items


def delete_saved_search(search_id: int, db_path: Optional[str] = None) -> bool:
    conn = _connect(db_path)
    cur = conn.execute("DELETE FROM saved_searches WHERE id=?", (search_id,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


# --------------------------------------------------------------------------
# Ajustes y caché geográfica
# --------------------------------------------------------------------------
def get_setting(key: str, default: str = "", db_path: Optional[str] = None) -> str:
    conn = _connect(db_path)
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key: str, value: str, db_path: Optional[str] = None) -> None:
    conn = _connect(db_path)
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?,?,?)", (key, str(value), _now()))
    conn.commit()
    conn.close()


def get_api_usage_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Devuelve el contador interno de peticiones HTTP realizadas por BuscaLeads a Google Places API (New).
    Nota: Es un contador técnico interno de llamadas de la aplicación, no un importe de facturación.
    """
    raw = get_setting("places_api_usage_json", "", db_path=db_path)
    default_stats: Dict[str, Any] = {
        "total_requests": 0,
        "nearby_search": 0,
        "text_search": 0,
        "place_details": 0,
        "validate_key": 0,
        "last_request_at": "",
    }
    if not raw:
        return default_stats
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for k in default_stats:
                if k in parsed:
                    default_stats[k] = parsed[k]
    except Exception:
        pass
    return default_stats


def increment_api_usage(endpoint_type: str, count: int = 1, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Incrementa el contador interno de peticiones a Google Places API (New)."""
    stats = get_api_usage_stats(db_path=db_path)
    inc = max(1, int(count))
    stats["total_requests"] = int(stats.get("total_requests", 0)) + inc
    if endpoint_type in ("nearby_search", "text_search", "place_details", "validate_key"):
        stats[endpoint_type] = int(stats.get(endpoint_type, 0)) + inc
    stats["last_request_at"] = _now()
    set_setting("places_api_usage_json", json.dumps(stats, ensure_ascii=False), db_path=db_path)
    return stats


def reset_api_usage_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Reinicia el contador interno de peticiones de la aplicación."""
    empty_stats: Dict[str, Any] = {
        "total_requests": 0,
        "nearby_search": 0,
        "text_search": 0,
        "place_details": 0,
        "validate_key": 0,
        "last_request_at": "",
    }
    set_setting("places_api_usage_json", json.dumps(empty_stats, ensure_ascii=False), db_path=db_path)
    return empty_stats


def get_geo_cache(query_key: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM geo_cache WHERE query_key=?", (query_key.strip().lower(),)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_geo_cache(query_key: str, lat: float, lon: float, bounding_box_json: str, display_name: str,
                  db_path: Optional[str] = None) -> None:
    conn = _connect(db_path)
    conn.execute("INSERT OR REPLACE INTO geo_cache VALUES (?,?,?,?,?,?)",
                 (query_key.strip().lower(), lat, lon, bounding_box_json, display_name, _now()))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Copias de seguridad
# --------------------------------------------------------------------------
def backup_database(destination_path: Optional[str] = None, db_path: Optional[str] = None,
                    label: str = "backup") -> str:
    src = Path(db_path or DATABASE_PATH)
    if destination_path:
        dest = Path(destination_path)
    else:
        dest = BACKUPS_DIR / f"facenia_leads_{label}_{datetime.now():%Y%m%d_%H%M%S}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        # API de backup de SQLite: copia consistente aunque haya conexiones abiertas
        with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dest)) as d:
            s.backup(d)
    return str(dest)


def restore_database(source_path: str, db_path: Optional[str] = None) -> bool:
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe el archivo: {source_path}")
    with sqlite3.connect(str(src)) as test:
        tables = {r[0] for r in test.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "leads" not in tables:
        raise ValueError("El archivo no es una copia válida de FACENIA Lead Finder (falta la tabla de prospectos).")
    dest = Path(db_path or DATABASE_PATH)
    if dest.exists():
        backup_database(db_path=str(dest), label="antes_de_restaurar")
    shutil.copy2(src, dest)
    init_db(str(dest))
    return True


# --------------------------------------------------------------------------
# Compatibilidad con versiones anteriores
# --------------------------------------------------------------------------
def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    return _connect(db_path)


def save_or_update_lead(lead: Dict[str, Any], db_path: Optional[str] = None) -> Tuple[int, bool]:
    return save_lead(lead, db_path)


def save_selected_leads_batch(leads: List[Dict[str, Any]], db_path: Optional[str] = None) -> Tuple[int, int]:
    return save_leads(leads, db_path)


def set_search_results_temp(leads: List[Dict[str, Any]], db_path: Optional[str] = None) -> int:
    report = {"source": "Búsqueda rápida", "total": len(leads), "status": "Completada"}
    return record_search(report, leads, db_path)


def get_search_results_temp(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    _, results = get_search(None, db_path)
    return results
