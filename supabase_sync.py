"""
Sincronización y persistencia en la nube con Supabase (PostgreSQL vía PostgREST HTTP)
para BuscaLeads · FACENIA (tablas `public.buscaleads_*` dentro del proyecto Facenia).

Permite que tanto la versión desplegada en Vercel como la versión local compartan y
persistan rutas, negocios, visitas, enlaces NFC y ajustes en Supabase sin depender
de extensiones C pesadas (`psycopg2`).
"""

import sqlite3
from typing import Any, Dict, List, Optional
import requests

from config import DATABASE_PATH, get_supabase_key, get_supabase_url, is_supabase_configured

_HYDRATED_PROCESSED = False


def _is_main_db(db_path: Optional[str]) -> bool:
    """Evita sincronizar con la nube cuando se ejecutan tests unitarios aislados en tmp_path."""
    if not is_supabase_configured():
        return False
    if db_path is None:
        return True
    return str(db_path) == str(DATABASE_PATH)


def _headers(prefer: str = "return=representation") -> Dict[str, str]:
    key = get_supabase_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _rest_url(table: str) -> str:
    return f"{get_supabase_url()}/rest/v1/{table}"


def sync_setting_to_supabase(key: str, value: str, updated_at: str, db_path: Optional[str] = None) -> None:
    if not _is_main_db(db_path):
        return
    try:
        requests.post(
            _rest_url("buscaleads_settings"),
            params={"on_conflict": "key"},
            headers=_headers("resolution=merge-duplicates,return=minimal"),
            json={"key": key, "value": value, "updated_at": updated_at},
            timeout=5,
        )
    except Exception:
        pass


ALLOWED_LEAD_COLS = {
    "id", "source", "source_id", "place_id", "dedup_hash", "name", "sector",
    "address", "city", "postal_code", "phone", "email", "website", "social_url",
    "opening_hours", "map_url", "lat", "lon", "web_status", "web_status_manual",
    "verification_status", "data_source", "score", "priority", "manual_priority",
    "commercial_status", "notes", "next_action", "last_contact_date",
    "next_followup_date", "is_demo", "brand", "is_chain", "scoring_reasons_json",
    "rating", "user_ratings_total", "reviews_source", "review_url",
    "review_url_status", "review_url_updated_at", "nfc_status", "nfc_updated_at",
    "sale_service", "sale_amount", "sale_date", "discard_reason", "last_visit_at",
    "created_at", "updated_at", "last_searched_at",
}


def sync_lead_to_supabase(lead_row: Dict[str, Any], db_path: Optional[str] = None) -> None:
    if not _is_main_db(db_path) or not lead_row or not lead_row.get("id"):
        return
    payload = {k: v for k, v in lead_row.items() if k in ALLOWED_LEAD_COLS}
    try:
        requests.post(
            _rest_url("buscaleads_leads"),
            params={"on_conflict": "id"},
            headers=_headers("resolution=merge-duplicates,return=minimal"),
            json=payload,
            timeout=6,
        )
    except Exception:
        pass


def sync_route_to_supabase(
    route_row: Dict[str, Any],
    stops_rows: Optional[List[Dict[str, Any]]] = None,
    db_path: Optional[str] = None,
) -> None:
    if not _is_main_db(db_path) or not route_row or not route_row.get("id"):
        return
    allowed_route_cols = {
        "id", "created_at", "search_id", "start_label", "start_lat", "start_lon",
        "radius_m", "requested_stops", "found_count", "excluded_count",
        "categories_json", "routing_source", "routing_note", "total_distance_m",
        "total_duration_s", "geometry_json", "current_index", "status",
    }
    payload = {k: v for k, v in route_row.items() if k in allowed_route_cols}
    try:
        requests.post(
            _rest_url("buscaleads_routes"),
            params={"on_conflict": "id"},
            headers=_headers("resolution=merge-duplicates,return=minimal"),
            json=payload,
            timeout=6,
        )
        if stops_rows:
            clean_stops = [
                {
                    "route_id": s["route_id"],
                    "position": s["position"],
                    "lead_id": s["lead_id"],
                    "leg_distance_m": s.get("leg_distance_m"),
                    "leg_duration_s": s.get("leg_duration_s"),
                    "straight_distance_m": s.get("straight_distance_m"),
                }
                for s in stops_rows
                if s.get("route_id") and s.get("lead_id")
            ]
            if clean_stops:
                requests.post(
                    _rest_url("buscaleads_route_stops"),
                    headers=_headers("return=minimal"),
                    json=clean_stops,
                    timeout=8,
                )
    except Exception:
        pass


def sync_interaction_to_supabase(inter_row: Dict[str, Any], db_path: Optional[str] = None) -> None:
    if not _is_main_db(db_path) or not inter_row or not inter_row.get("id"):
        return
    allowed_cols = {
        "id", "lead_id", "route_id", "created_at", "outcome", "status_before",
        "status_after", "note", "followup_date", "sale_service", "sale_amount",
        "discard_reason", "snapshot_json", "undone", "undone_at",
    }
    payload = {k: v for k, v in inter_row.items() if k in allowed_cols}
    try:
        requests.post(
            _rest_url("buscaleads_interactions"),
            params={"on_conflict": "id"},
            headers=_headers("resolution=merge-duplicates,return=minimal"),
            json=payload,
            timeout=5,
        )
    except Exception:
        pass


def hydrate_or_push_initial_state(conn: sqlite3.Connection, db_path: Optional[str] = None) -> None:
    """
    Sincroniza el estado inicial entre SQLite local y Supabase (`buscaleads_*`):
    - Si SQLite está vacío (p. ej. arranque en frío en Vercel `/tmp`) y Supabase tiene datos,
      descarga los datos de Supabase a SQLite.
    - Si SQLite tiene datos locales y Supabase está vacío, sube los registros iniciales en bloque.
    """
    global _HYDRATED_PROCESSED
    if _HYDRATED_PROCESSED or not _is_main_db(db_path):
        return
    _HYDRATED_PROCESSED = True

    try:
        cur = conn.cursor()
        local_leads_count = cur.execute("SELECT COUNT(*) FROM leads WHERE is_demo=0").fetchone()[0]

        resp_remote = requests.get(
            _rest_url("buscaleads_leads"),
            params={"select": "*", "order": "id.asc", "limit": "500"},
            headers=_headers(),
            timeout=6,
        )
        if resp_remote.status_code != 200:
            return
        remote_leads = resp_remote.json() or []

        if local_leads_count == 0 and len(remote_leads) > 0:
            # Hidratar SQLite desde Supabase (caso Vercel Serverless)
            for row in remote_leads:
                cols = list(row.keys())
                placeholders = ",".join("?" for _ in cols)
                cur.execute(
                    f"INSERT OR REPLACE INTO leads ({','.join(cols)}) VALUES ({placeholders})",
                    tuple(row[c] for c in cols),
                )

            for r_tbl, l_tbl in (
                ("buscaleads_settings", "app_settings"),
                ("buscaleads_routes", "routes"),
                ("buscaleads_route_stops", "route_stops"),
                ("buscaleads_interactions", "interactions"),
            ):
                r_res = requests.get(_rest_url(r_tbl), params={"select": "*"}, headers=_headers(), timeout=5)
                if r_res.status_code == 200:
                    valid_cols = {c[1] for c in cur.execute(f"PRAGMA table_info({l_tbl})").fetchall()}
                    for item in r_res.json() or []:
                        filtered_item = {k: v for k, v in item.items() if k in valid_cols}
                        if not filtered_item:
                            continue
                        cols = list(filtered_item.keys())
                        placeholders = ",".join("?" for _ in cols)
                        cur.execute(
                            f"INSERT OR REPLACE INTO {l_tbl} ({','.join(cols)}) VALUES ({placeholders})",
                            tuple(filtered_item[c] for c in cols),
                        )
            conn.commit()

        elif local_leads_count > 0 and len(remote_leads) == 0:
            # Subir cartera local existente a Supabase en lote filtrando columnas válidas
            local_rows = [
                {k: v for k, v in dict(r).items() if k in ALLOWED_LEAD_COLS}
                for r in cur.execute("SELECT * FROM leads WHERE is_demo=0").fetchall()
            ]
            if local_rows:
                requests.post(
                    _rest_url("buscaleads_leads"),
                    params={"on_conflict": "id"},
                    headers=_headers("resolution=merge-duplicates,return=minimal"),
                    json=local_rows,
                    timeout=10,
                )
            local_routes = [dict(r) for r in cur.execute("SELECT * FROM routes").fetchall()]
            if local_routes:
                requests.post(
                    _rest_url("buscaleads_routes"),
                    params={"on_conflict": "id"},
                    headers=_headers("resolution=merge-duplicates,return=minimal"),
                    json=local_routes,
                    timeout=8,
                )
            local_stops = [
                {
                    "route_id": dict(r)["route_id"],
                    "position": dict(r)["position"],
                    "lead_id": dict(r)["lead_id"],
                    "leg_distance_m": dict(r).get("leg_distance_m"),
                    "leg_duration_s": dict(r).get("leg_duration_s"),
                    "straight_distance_m": dict(r).get("straight_distance_m"),
                }
                for r in cur.execute("SELECT * FROM route_stops").fetchall()
            ]
            if local_stops:
                requests.post(
                    _rest_url("buscaleads_route_stops"),
                    headers=_headers("return=minimal"),
                    json=local_stops,
                    timeout=8,
                )
    except Exception:
        pass


def sync_delete_lead_to_supabase(lead_id: int, db_path: Optional[str] = None) -> None:
    """Elimina el negocio en Supabase para mantener sincronización exacta."""
    if not _is_main_db(db_path):
        return
    try:
        requests.delete(
            _rest_url("buscaleads_leads"),
            params={"id": f"eq.{int(lead_id)}"},
            headers=_headers("return=minimal"),
            timeout=5,
        )
    except Exception:
        pass
