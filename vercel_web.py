"""
Servidor WSGI y aplicación web móvil (SPA + API REST) para despliegue directo en Vercel
y ejecución web rápida de BuscaLeads · FACENIA.

Expone la variable `wsgi_app` (exportada como `app` y `application` en `app.py`)
compatible con `@vercel/python` sin requerir demonios WebSocket de Streamlit.
"""

from datetime import date, timedelta
import json
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs

from config import (
    COMMERCIAL_STATUSES,
    DATABASE_PATH,
    DEFAULT_COMMERCIAL_STATUS,
    DEFAULT_DATA_PROVIDER,
    DEFAULT_SECTORS,
    DEFAULT_START,
    FACENIA_SERVICES,
    OUTCOME_NOT_INTERESTED,
    OUTCOME_RETURN,
    OUTCOME_SOLD,
    PLACES_MAX_REQUESTS_PER_SEARCH,
    PLACES_MAX_RESULTS_PER_SEARCH,
    ROUTE_MAX_STOPS,
    ROUTE_MIN_STOPS,
    ROUTE_RADIUS_OPTIONS_M,
    SECTOR_NAMES,
    SECTORS,
    WEB_CONFIRMED_NONE,
    WEB_FOUND,
    WEB_NONE,
    WEB_REVIEW,
    WEB_SOCIAL,
    WEB_STATUSES,
    get_web_label,
    is_google_maps_configured,
)
from database import (
    get_api_usage_stats,
    get_lead,
    get_leads,
    get_recent_interactions,
    get_route,
    get_setting,
    get_stats,
    init_db,
    list_routes,
    record_visit_outcome,
    reset_api_usage_stats,
    set_manual_web_status,
    set_setting,
    undo_interaction,
    update_lead_commercial,
    update_lead_review_nfc,
    update_route_progress,
)
from export_service import export_leads_to_csv_bytes, export_leads_to_excel_bytes
from pitch_service import (
    NFC_STATUS_OPTIONS,
    REVIEW_URL_STATUS_OPTIONS,
    generate_commercial_recommendation,
    generate_sales_pitch,
    prepare_review_link_info,
)
from places_client import PlacesAPIError
from providers import GooglePlacesProvider, OSMOverpassProvider
from routing_service import (
    build_google_maps_directions_to_lead,
    build_google_maps_place_url,
    build_google_maps_walking_route_url,
)
from search_service import generate_prospecting_route
from verification import check_website

MALAGA_ZONE_PRESETS = [
    {"label": "Centro Histórico (Plaza Constitución)", "lat": 36.7213, "lon": -4.4214},
    {"label": "Soho / Alameda Principal", "lat": 36.7168, "lon": -4.4238},
    {"label": "Teatinos (Av. Plutarco)", "lat": 36.7196, "lon": -4.4765},
    {"label": "Carretera de Cádiz / Huelin", "lat": 36.7015, "lon": -4.4422},
    {"label": "El Palo / Pedregalejo", "lat": 36.7214, "lon": -4.3608},
    {"label": "Ciudad Jardín", "lat": 36.7438, "lon": -4.4225},
]


def _enrich_lead_for_client(
    lead: Dict[str, Any],
    origin_lat: Optional[float] = None,
    origin_lon: Optional[float] = None,
) -> Dict[str, Any]:
    item = dict(lead)
    web_eff = item.get("web_effective") or item.get("web_status_manual") or item.get("web_status") or WEB_NONE
    item["web_label"] = get_web_label(web_eff, item.get("source") or item.get("data_source") or "")
    item["recommendation"] = generate_commercial_recommendation(item)
    item["pitch"] = generate_sales_pitch(item)
    item["review_info"] = prepare_review_link_info(item)
    item["gmaps_place_url"] = build_google_maps_place_url(item)
    item["gmaps_walk_url"] = build_google_maps_directions_to_lead(
        item, origin_lat=origin_lat, origin_lon=origin_lon
    )
    return item


def _build_full_state() -> Dict[str, Any]:
    init_db(DATABASE_PATH)
    stats = get_stats(DATABASE_PATH)
    active_route_id_str = get_setting("active_route_id", "", DATABASE_PATH)
    active_route = None
    if active_route_id_str and str(active_route_id_str).isdigit():
        active_route = get_route(int(active_route_id_str), DATABASE_PATH)
    if not active_route:
        active_route = get_route(None, DATABASE_PATH)

    if active_route and active_route.get("stops"):
        orig_lat = active_route.get("start_lat")
        orig_lon = active_route.get("start_lon")
        active_route["stops"] = [
            _enrich_lead_for_client(s, orig_lat, orig_lon) for s in active_route["stops"]
        ]
        active_route["gmaps_full_route_url"] = build_google_maps_walking_route_url(
            active_route.get("start_lat") or DEFAULT_START["lat"],
            active_route.get("start_lon") or DEFAULT_START["lon"],
            active_route["stops"],
        )

    recent_routes = list_routes(limit=8, db_path=DATABASE_PATH)
    all_leads_raw = get_leads(db_path=DATABASE_PATH)
    all_leads = [_enrich_lead_for_client(l) for l in all_leads_raw[:250]]
    recent_interactions = get_recent_interactions(limit=20, db_path=DATABASE_PATH)

    default_provider = get_setting("default_data_provider", DEFAULT_DATA_PROVIDER, DATABASE_PATH)
    if default_provider not in ("google_places", "osm"):
        default_provider = "google_places"

    max_results_cfg = int(
        get_setting("places_max_results", str(PLACES_MAX_RESULTS_PER_SEARCH), DATABASE_PATH)
        or PLACES_MAX_RESULTS_PER_SEARCH
    )
    max_requests_cfg = int(
        get_setting("places_max_requests", str(PLACES_MAX_REQUESTS_PER_SEARCH), DATABASE_PATH)
        or PLACES_MAX_REQUESTS_PER_SEARCH
    )
    default_zone = get_setting("default_zone_label", MALAGA_ZONE_PRESETS[0]["label"], DATABASE_PATH)
    default_stops = int(get_setting("default_stops", "12", DATABASE_PATH) or 12)
    default_radius = int(get_setting("default_radius_m", "500", DATABASE_PATH) or 500)

    categories_meta = [
        {
            "name": name,
            "priority_weight": info.get("priority_weight", 10),
            "nfc": bool(info.get("nfc", False)),
            "services": info.get("services", []),
        }
        for name, info in SECTORS.items()
    ]

    return {
        "stats": stats,
        "active_route": active_route,
        "recent_routes": recent_routes,
        "leads": all_leads,
        "recent_interactions": recent_interactions,
        "google_Configured": is_google_maps_configured(),
        "api_usage": get_api_usage_stats(DATABASE_PATH),
        "settings": {
            "default_provider": default_provider,
            "places_max_results": max_results_cfg,
            "places_max_requests": max_requests_cfg,
            "default_zone_label": default_zone,
            "default_stops": default_stops,
            "default_radius_m": default_radius,
        },
        "catalog": {
            "zone_presets": MALAGA_ZONE_PRESETS,
            "radius_options_m": ROUTE_RADIUS_OPTIONS_M,
            "min_stops": ROUTE_MIN_STOPS,
            "max_stops": ROUTE_MAX_STOPS,
            "categories": categories_meta,
            "default_sectors": DEFAULT_SECTORS,
            "commercial_statuses": COMMERCIAL_STATUSES,
            "web_statuses": WEB_STATUSES,
            "nfc_statuses": NFC_STATUS_OPTIONS,
            "review_url_statuses": REVIEW_URL_STATUS_OPTIONS,
            "facenia_services": FACENIA_SERVICES,
        },
    }


def _read_json_body(environ: Dict[str, Any]) -> Dict[str, Any]:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    raw = environ["wsgi.input"].read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _json_response(
    start_response: Callable,
    payload: Any,
    status: str = "200 OK",
) -> List[bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    start_response(status, headers)
    return [body]


def wsgi_app(environ: Dict[str, Any], start_response: Callable) -> List[bytes]:
    """Punto de entrada WSGI estándar para Vercel (`@vercel/python`) y servidores HTTP."""
    init_db(DATABASE_PATH)
    method = (environ.get("REQUEST_METHOD") or "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    query = parse_qs(environ.get("QUERY_STRING") or "")

    try:
        if method == "GET" and path == "/favicon.ico":
            start_response("204 No Content", [("Content-Length", "0")])
            return [b""]

        if method == "GET" and path in ("/", "/index.html"):
            body = _RENDER_SPA_HTML().encode("utf-8")
            start_response(
                "200 OK",
                [
                    ("Content-Type", "text/html; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                ],
            )
            return [body]

        if method == "GET" and path == "/api/state":
            return _json_response(start_response, _build_full_state())

        if method == "GET" and path == "/api/geocode":
            q = (query.get("q") or [""])[0].strip()
            if not q:
                return _json_response(start_response, {"error": "Indica una calle o barrio de Málaga."}, "400 Bad Request")
            osm = OSMOverpassProvider()
            full_q = q if "málaga" in q.lower() or "malaga" in q.lower() else f"{q}, Málaga, España"
            coords = osm.geocode_location(full_q)
            if not coords:
                return _json_response(
                    start_response,
                    {"error": f"No se encontró la ubicación '{q}' en OpenStreetMap Nominatim."},
                    "404 Not Found",
                )
            return _json_response(
                start_response,
                {"lat": coords[0], "lon": coords[1], "label": coords[2] or q},
            )

        if method == "POST" and path == "/api/routes/generate":
            data = _read_json_body(environ)
            provider_id = (data.get("provider_id") or "google_places").strip()
            start_lat = float(data.get("start_lat") or DEFAULT_START["lat"])
            start_lon = float(data.get("start_lon") or DEFAULT_START["lon"])
            start_label = (data.get("start_label") or DEFAULT_START["label"]).strip()
            radius_m = int(data.get("radius_m") or 500)
            requested_stops = int(data.get("requested_stops") or 12)
            categories = data.get("categories") or list(DEFAULT_SECTORS)

            max_res = int(
                get_setting("places_max_results", str(PLACES_MAX_RESULTS_PER_SEARCH), DATABASE_PATH)
                or PLACES_MAX_RESULTS_PER_SEARCH
            )
            max_req = int(
                get_setting("places_max_requests", str(PLACES_MAX_REQUESTS_PER_SEARCH), DATABASE_PATH)
                or PLACES_MAX_REQUESTS_PER_SEARCH
            )

            try:
                route = generate_prospecting_route(
                    start_lat=start_lat,
                    start_lon=start_lon,
                    start_label=start_label,
                    radius_m=radius_m,
                    categories=categories,
                    requested_stops=requested_stops,
                    city_hint="Málaga",
                    provider_id=provider_id,
                    max_results_limit=max_res,
                    max_requests_limit=max_req,
                    db_path=DATABASE_PATH,
                )
                if route and route.get("id"):
                    set_setting("active_route_id", str(route["id"]), DATABASE_PATH)
                return _json_response(start_response, {"ok": True, "state": _build_full_state()})
            except PlacesAPIError as e:
                return _json_response(
                    start_response,
                    {
                        "ok": False,
                        "error": str(e),
                        "error_type": e.error_type,
                        "honest_notice": (
                            "Por transparencia y control de costes, no se han simulado datos ni se ha "
                            "cambiado silenciosamente de proveedor. Configura GOOGLE_MAPS_API_KEY en "
                            "las variables de entorno o selecciona OpenStreetMap manualmente."
                        ),
                    },
                    "400 Bad Request",
                )

        if method == "POST" and path == "/api/routes/select":
            data = _read_json_body(environ)
            route_id = int(data.get("route_id") or 0)
            current_index = data.get("current_index")
            if route_id:
                set_setting("active_route_id", str(route_id), DATABASE_PATH)
                if current_index is not None:
                    update_route_progress(route_id, int(current_index), db_path=DATABASE_PATH)
            return _json_response(start_response, {"ok": True, "state": _build_full_state()})

        if method == "POST" and path == "/api/leads/place-details":
            data = _read_json_body(environ)
            lead_id = int(data.get("lead_id") or 0)
            lead = get_lead(lead_id, DATABASE_PATH)
            if not lead:
                return _json_response(start_response, {"ok": False, "error": "Negocio no encontrado."}, "404 Not Found")
            place_id = (lead.get("place_id") or lead.get("source_id") or "").strip()
            try:
                gp = GooglePlacesProvider(db_path=DATABASE_PATH)
                updated = gp.fetch_place_details_on_demand(
                    lead_id=lead_id,
                    place_id=place_id,
                    sector_name=lead.get("sector") or "Comercios",
                    default_city=lead.get("city") or "Málaga",
                    db_path=DATABASE_PATH,
                )
                return _json_response(
                    start_response,
                    {
                        "ok": True,
                        "lead": _enrich_lead_for_client(updated),
                        "state": _build_full_state(),
                    },
                )
            except PlacesAPIError as e:
                return _json_response(start_response, {"ok": False, "error": str(e)}, "400 Bad Request")

        if method == "POST" and path == "/api/leads/visit":
            data = _read_json_body(environ)
            lead_id = int(data.get("lead_id") or 0)
            outcome = (data.get("outcome") or "").strip()
            route_id = data.get("route_id")
            note = (data.get("note") or "").strip()
            followup_date = (data.get("followup_date") or "").strip()
            if outcome == OUTCOME_RETURN and not followup_date:
                followup_date = (date.today() + timedelta(days=3)).isoformat()
            sale_service = (data.get("sale_service") or "").strip()
            sale_amount_raw = data.get("sale_amount")
            sale_amount = float(sale_amount_raw) if sale_amount_raw not in (None, "") else None

            record_visit_outcome(
                lead_id=lead_id,
                outcome=outcome,
                route_id=int(route_id) if route_id else None,
                note=note,
                followup_date=followup_date,
                sale_service=sale_service,
                sale_amount=sale_amount,
                db_path=DATABASE_PATH,
            )
            return _json_response(start_response, {"ok": True, "state": _build_full_state()})

        if method == "POST" and path == "/api/visits/undo":
            res = undo_interaction(db_path=DATABASE_PATH)
            return _json_response(
                start_response,
                {"ok": bool(res), "undone": res, "state": _build_full_state()},
            )

        if method == "POST" and path == "/api/leads/update":
            data = _read_json_body(environ)
            lead_id = int(data.get("lead_id") or 0)
            if not lead_id:
                return _json_response(start_response, {"ok": False, "error": "ID inválido"}, "400 Bad Request")

            if "commercial_status" in data or "notes" in data:
                update_lead_commercial(
                    lead_id=lead_id,
                    commercial_status=data.get("commercial_status") or DEFAULT_COMMERCIAL_STATUS,
                    notes=data.get("notes") or "",
                    next_action=data.get("next_action") or "",
                    manual_priority=data.get("manual_priority") or "",
                    next_followup_date=data.get("next_followup_date") or "",
                    db_path=DATABASE_PATH,
                )
            if "web_status_manual" in data or "website" in data:
                set_manual_web_status(
                    lead_id=lead_id,
                    status=data.get("web_status_manual") or "",
                    website=data.get("website") or "",
                    detail="Actualizado manualmente por el usuario",
                    db_path=DATABASE_PATH,
                )
            if "nfc_status" in data or "review_url" in data:
                update_lead_review_nfc(
                    lead_id=lead_id,
                    review_url=data.get("review_url"),
                    review_url_status=data.get("review_url_status"),
                    nfc_status=data.get("nfc_status"),
                    db_path=DATABASE_PATH,
                )
            updated = get_lead(lead_id, DATABASE_PATH)
            return _json_response(
                start_response,
                {
                    "ok": True,
                    "lead": _enrich_lead_for_client(updated) if updated else None,
                    "state": _build_full_state(),
                },
            )

        if method == "POST" and path == "/api/leads/verify-web":
            data = _read_json_body(environ)
            lead_id = int(data.get("lead_id") or 0)
            lead = get_lead(lead_id, DATABASE_PATH)
            if not lead:
                return _json_response(start_response, {"ok": False, "error": "No encontrado"}, "404 Not Found")
            url = (data.get("website") or lead.get("website") or "").strip()
            status, detail = check_website(url)
            set_manual_web_status(lead_id, status, website=url, detail=detail, db_path=DATABASE_PATH)
            updated = get_lead(lead_id, DATABASE_PATH)
            return _json_response(
                start_response,
                {
                    "ok": True,
                    "status": status,
                    "detail": detail,
                    "lead": _enrich_lead_for_client(updated) if updated else None,
                    "state": _build_full_state(),
                },
            )

        if method == "POST" and path == "/api/settings":
            data = _read_json_body(environ)
            action = data.get("action") or "save"
            if action == "reset_counter":
                reset_api_usage_stats(DATABASE_PATH)
                return _json_response(start_response, {"ok": True, "state": _build_full_state()})
            if action == "validate_key":
                gp = GooglePlacesProvider(db_path=DATABASE_PATH)
                valid, msg = gp.validate_connection()
                return _json_response(
                    start_response,
                    {"ok": valid, "message": msg, "state": _build_full_state()},
                )

            for key in (
                "default_data_provider",
                "places_max_results",
                "places_max_requests",
                "default_zone_label",
                "default_stops",
                "default_radius_m",
            ):
                if key in data and data[key] is not None:
                    set_setting(key, str(data[key]), DATABASE_PATH)
            return _json_response(start_response, {"ok": True, "state": _build_full_state()})

        if method == "GET" and path == "/api/export/csv":
            leads = get_leads(db_path=DATABASE_PATH)
            csv_bytes = export_leads_to_csv_bytes(leads)
            start_response(
                "200 OK",
                [
                    ("Content-Type", "text/csv; charset=utf-8"),
                    ("Content-Disposition", 'attachment; filename="facenia_leads_notion.csv"'),
                    ("Content-Length", str(len(csv_bytes))),
                ],
            )
            return [csv_bytes]

        if method == "GET" and path == "/api/export/excel":
            leads = get_leads(db_path=DATABASE_PATH)
            xlsx_bytes = export_leads_to_excel_bytes(leads)
            start_response(
                "200 OK",
                [
                    ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                    ("Content-Disposition", 'attachment; filename="facenia_leads_comercial.xlsx"'),
                    ("Content-Length", str(len(xlsx_bytes))),
                ],
            )
            return [xlsx_bytes]

        return _json_response(start_response, {"error": f"Ruta no encontrada: {path}"}, "404 Not Found")
    except Exception as exc:
        return _json_response(
            start_response,
            {"ok": False, "error": f"Error interno del servidor: {exc}"},
            "500 Internal Server Error",
        )


def _RENDER_SPA_HTML() -> str:
    return """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
  <title>BuscaLeads · FACENIA — Prospección Comercial en Calle</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    :root {
      --bg: #0B101B;
      --card: #131B2E;
      --card-2: #19233A;
      --border: rgba(148, 163, 184, 0.16);
      --text: #F8FAFC;
      --muted: #94A3B8;
      --gold: #F59E0B;
      --gold-hover: #D97706;
      --green: #10B981;
      --blue: #3B82F6;
      --red: #EF4444;
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    body {
      margin: 0;
      background: #060911;
      color: var(--text);
      font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
      display: flex;
      justify-content: center;
      min-height: 100vh;
    }
    .app-shell {
      width: 100%;
      max-width: 480px;
      min-height: 100vh;
      background: var(--bg);
      padding: 16px 14px 96px;
      position: relative;
      border-left: 1px solid rgba(255,255,255,0.05);
      border-right: 1px solid rgba(255,255,255,0.05);
    }
    .header-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
    }
    .brand-tag {
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--gold);
    }
    .header-title {
      font-size: 20px;
      font-weight: 800;
      margin: 2px 0;
    }
    .header-sub {
      font-size: 12px;
      color: var(--muted);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 9px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 700;
    }
    .badge-gold { background: rgba(245, 158, 11, 0.16); color: #FBBF24; border: 1px solid rgba(245, 158, 11, 0.35); }
    .badge-green { background: rgba(16, 185, 129, 0.16); color: #34D399; border: 1px solid rgba(16, 185, 129, 0.35); }
    .badge-red { background: rgba(239, 68, 68, 0.16); color: #F87171; border: 1px solid rgba(239, 68, 68, 0.35); }
    .badge-blue { background: rgba(59, 130, 246, 0.16); color: #60A5FA; border: 1px solid rgba(59, 130, 246, 0.35); }
    .kpi-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-bottom: 14px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 13px;
      margin-bottom: 12px;
    }
    .kpi-label { font-size: 11px; color: var(--muted); font-weight: 600; }
    .kpi-val { font-family: 'JetBrains Mono', monospace; font-size: 22px; font-weight: 700; margin: 4px 0 2px; }
    .kpi-sub { font-size: 11px; color: var(--muted); }
    .btn {
      width: 100%;
      border: none;
      border-radius: 12px;
      padding: 12px 14px;
      font-family: inherit;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: all 0.15s ease;
      text-decoration: none;
    }
    .btn-primary { background: var(--gold); color: #0B101B; }
    .btn-primary:hover { background: var(--gold-hover); }
    .btn-secondary { background: var(--card-2); color: var(--text); border: 1px solid var(--border); }
    .btn-green { background: rgba(16, 185, 129, 0.18); color: #34D399; border: 1px solid rgba(16, 185, 129, 0.4); }
    .btn-red { background: rgba(239, 68, 68, 0.15); color: #F87171; border: 1px solid rgba(239, 68, 68, 0.35); }
    .btn-row { display: flex; gap: 8px; margin-top: 8px; }
    .chip-group { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
    .chip {
      background: var(--card-2);
      color: var(--muted);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 11px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
    }
    .chip.active {
      background: rgba(245, 158, 11, 0.2);
      color: #FBBF24;
      border-color: var(--gold);
    }
    input, select, textarea {
      width: 100%;
      background: #0B101B;
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 12px;
      font-family: inherit;
      font-size: 13px;
      margin-top: 5px;
    }
    label { font-size: 12px; font-weight: 600; color: var(--muted); display: block; margin-top: 10px; }
    #map-container { width: 100%; height: 340px; border-radius: 14px; overflow: hidden; border: 1px solid var(--border); margin-bottom: 10px; }
    .bottom-nav {
      position: fixed;
      bottom: 0;
      left: 50%;
      transform: translateX(-50%);
      width: 100%;
      max-width: 480px;
      background: rgba(11, 16, 27, 0.96);
      backdrop-filter: blur(12px);
      border-top: 1px solid var(--border);
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      padding: 6px 4px calc(6px + env(safe-area-inset-bottom, 0px));
      z-index: 999;
    }
    .nav-item {
      background: transparent;
      border: none;
      color: var(--muted);
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 3px;
      padding: 6px 2px;
      font-size: 10px;
      font-weight: 700;
      cursor: pointer;
    }
    .nav-item.active { color: var(--gold); }
    .nav-item svg { width: 20px; height: 20px; stroke-width: 2.1; }
    .alert-error {
      background: rgba(239, 68, 68, 0.12);
      border: 1px solid rgba(239, 68, 68, 0.45);
      color: #FCA5A5;
      padding: 12px;
      border-radius: 12px;
      font-size: 12px;
      margin-top: 10px;
      line-height: 1.45;
    }
    .section-label {
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin: 14px 0 8px;
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <div id="app-content">Cargando BuscaLeads · FACENIA...</div>
  </div>

  <nav class="bottom-nav" aria-label="Navegación principal">
    <button class="nav-item active" data-tab="inicio" onclick="switchTab('inicio')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 10.5 12 3l9 7.5V21a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1v-10.5Z"/></svg>
      <span>Inicio</span>
    </button>
    <button class="nav-item" data-tab="mapa" onclick="switchTab('mapa')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21"/><line x1="9" y1="3" x2="9" y2="18"/><line x1="15" y1="6" x2="15" y2="21"/></svg>
      <span>Mapa</span>
    </button>
    <button class="nav-item" data-tab="negocios" onclick="switchTab('negocios')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 9h6M9 13h6M9 17h4"/></svg>
      <span>Negocios</span>
    </button>
    <button class="nav-item" data-tab="visitas" onclick="switchTab('visitas')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
      <span>Visitas</span>
    </button>
    <button class="nav-item" data-tab="perfil" onclick="switchTab('perfil')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 12 0v1"/><circle cx="19" cy="19" r="2"/></svg>
      <span>Perfil</span>
    </button>
  </nav>

  <script>
    let STATE = null;
    let CURRENT_TAB = 'inicio';
    let SUBSCREEN = null; // 'crear_ruta' | 'ficha_negocio'
    let SELECTED_LEAD_ID = null;
    let LEAFLET_MAP = null;

    let ROUTE_FORM = {
      provider_id: 'google_places',
      start_label: 'Centro Histórico (Plaza Constitución)',
      start_lat: 36.7213,
      start_lon: -4.4214,
      requested_stops: 12,
      radius_m: 500,
      categories: ['Restauración', 'Belleza y peluquerías', 'Comercios'],
      error_msg: '',
      error_notice: '',
      loading: false
    };

    let FILTER_FORM = { q: '', scope: 'all', web: '', priority: '' };

    async function loadState() {
      const res = await fetch('/api/state');
      STATE = await res.json();
      if (STATE && STATE.settings) {
        ROUTE_FORM.provider_id = STATE.settings.default_provider || 'google_places';
      }
      render();
    }

    function switchTab(tab) {
      CURRENT_TAB = tab;
      SUBSCREEN = null;
      document.querySelectorAll('.nav-item').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tab);
      });
      render();
    }

    function openCreateRoute() {
      SUBSCREEN = 'crear_ruta';
      ROUTE_FORM.error_msg = '';
      render();
    }

    function openLeadDetail(leadId) {
      SELECTED_LEAD_ID = Number(leadId);
      SUBSCREEN = 'ficha_negocio';
      render();
    }

    function findLeadById(id) {
      if (!STATE) return null;
      if (STATE.active_route && STATE.active_route.stops) {
        const s = STATE.active_route.stops.find(x => Number(x.id) === Number(id) || Number(x.lead_id) === Number(id));
        if (s) return s;
      }
      return (STATE.leads || []).find(x => Number(x.id) === Number(id));
    }

    function escapeHtml(str) {
      return String(str || '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
    }

    function render() {
      const root = document.getElementById('app-content');
      if (!STATE) {
        root.innerHTML = '<div class="card">Cargando estado comercial...</div>';
        return;
      }
      if (SUBSCREEN === 'crear_ruta') {
        root.innerHTML = renderCrearRuta();
        return;
      }
      if (SUBSCREEN === 'ficha_negocio') {
        root.innerHTML = renderFichaNegocio();
        return;
      }
      if (CURRENT_TAB === 'inicio') root.innerHTML = renderInicio();
      else if (CURRENT_TAB === 'mapa') {
        root.innerHTML = renderMapa();
        setTimeout(initLeafletMap, 40);
      }
      else if (CURRENT_TAB === 'negocios') root.innerHTML = renderNegocios();
      else if (CURRENT_TAB === 'visitas') root.innerHTML = renderVisitas();
      else if (CURRENT_TAB === 'perfil') root.innerHTML = renderPerfil();
    }

    function renderInicio() {
      const s = STATE.stats || {};
      const ar = STATE.active_route;
      let routeHtml = '';
      if (ar && ar.stops && ar.stops.length > 0) {
        const visited = ar.stops.filter(x => Boolean(x.last_visit_at) || !['Nuevo', 'Pendiente', 'Pendiente de revisar', 'Pendiente de contactar'].includes(x.commercial_status || 'Nuevo')).length;
        const pct = Math.round((visited / ar.stops.length) * 100);
        routeHtml = `
          <div class="section-label">Ruta activa en curso</div>
          <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <strong>Ruta #${ar.id} · ${escapeHtml(ar.start_label)}</strong>
              <span class="badge badge-gold">${visited}/${ar.stops.length} (${pct}%)</span>
            </div>
            <div class="header-sub" style="margin:6px 0 10px;">
              ${ar.stops.length} paradas · Radio ${ar.radius_m} m
            </div>
            <div class="btn-row">
              <button class="btn btn-primary" onclick="switchTab('mapa')">Continuar en Mapa</button>
              <button class="btn btn-secondary" onclick="switchTab('visitas')">Registrar visitas</button>
            </div>
          </div>
        `;
      }

      const recents = (STATE.recent_routes || []).slice(0, 4).map(r => `
        <div class="card" style="display:flex;justify-content:space-between;align-items:center;padding:10px 12px;">
          <div>
            <div style="font-size:13px;font-weight:700;">Ruta #${r.id} · ${escapeHtml(r.start_label)}</div>
            <div class="header-sub">${r.found_count} encontrados · Radio ${r.radius_m} m</div>
          </div>
          <button class="btn btn-secondary" style="width:auto;padding:7px 12px;" onclick="selectRoute(${r.id})">Retomar</button>
        </div>
      `).join('');

      return `
        <div class="header-bar">
          <div>
            <div class="brand-tag">FACENIA</div>
            <div class="header-title">BuscaLeads</div>
            <div class="header-sub">Prospección comercial en calle · Málaga</div>
          </div>
          <span class="badge badge-gold">${s.no_web || 0} sin web</span>
        </div>

        <div class="kpi-grid">
          <div class="card" style="margin:0;">
            <div class="kpi-label">Negocios en cartera</div>
            <div class="kpi-val">${s.total || 0}</div>
            <div class="kpi-sub">${s.no_web || 0} sin web identificada</div>
          </div>
          <div class="card" style="margin:0;">
            <div class="kpi-label">Visitas realizadas</div>
            <div class="kpi-val">${s.visited_count || 0}</div>
            <div class="kpi-sub">Puerta fría y presencial</div>
          </div>
          <div class="card" style="margin:0;">
            <div class="kpi-label">Seguimientos</div>
            <div class="kpi-val">${s.followup_pending || 0}</div>
            <div class="kpi-sub">Volver otro día / pendientes</div>
          </div>
          <div class="card" style="margin:0;">
            <div class="kpi-label">Ventas cerradas</div>
            <div class="kpi-val">${s.sales_count || 0}</div>
            <div class="kpi-sub">Conversión: ${(s.conversion_rate || 0).toFixed(1)}%</div>
          </div>
        </div>

        <button class="btn btn-primary" onclick="openCreateRoute()">+ Crear ruta comercial a pie</button>
        ${routeHtml}
        ${recents ? `<div class="section-label">Rutas recientes</div>${recents}` : ''}
      `;
    }

    function renderCrearRuta() {
      const gOk = STATE.google_Configured;
      const presets = (STATE.catalog.zone_presets || []).map(z => `
        <button class="chip ${ROUTE_FORM.start_label === z.label ? 'active' : ''}"
          onclick="setZonePreset('${escapeHtml(z.label)}', ${z.lat}, ${z.lon})">${escapeHtml(z.label)}</button>
      `).join('');

      const stopChips = [5, 10, 12, 15, 20, 30].map(n => `
        <button class="chip ${ROUTE_FORM.requested_stops === n ? 'active' : ''}"
          onclick="ROUTE_FORM.requested_stops=${n};render();">${n}</button>
      `).join('');

      const radChips = (STATE.catalog.radius_options_m || [250,500,750,1000,1500,2000]).map(r => `
        <button class="chip ${ROUTE_FORM.radius_m === r ? 'active' : ''}"
          onclick="ROUTE_FORM.radius_m=${r};render();">${r >= 1000 ? (r/1000)+' km' : r+' m'}</button>
      `).join('');

      const catChips = (STATE.catalog.categories || []).map(c => {
        const active = ROUTE_FORM.categories.includes(c.name);
        return `<button class="chip ${active ? 'active' : ''}" onclick="toggleCategory('${escapeHtml(c.name)}')">${escapeHtml(c.name)}</button>`;
      }).join('');

      return `
        <button class="btn btn-secondary" style="width:auto;padding:8px 12px;margin-bottom:10px;" onclick="SUBSCREEN=null;render();">← Volver a Inicio</button>
        <div class="header-title">Crear ruta comercial a pie</div>

        <div class="card" style="margin-top:10px;">
          <div style="font-size:13px;font-weight:700;">Fuente de datos para la búsqueda</div>
          <div class="chip-group">
            <button class="chip ${ROUTE_FORM.provider_id === 'google_places' ? 'active' : ''}"
              onclick="ROUTE_FORM.provider_id='google_places';ROUTE_FORM.error_msg='';render();">
              Google Places API (New) · Principal
            </button>
            <button class="chip ${ROUTE_FORM.provider_id === 'osm' ? 'active' : ''}"
              onclick="ROUTE_FORM.provider_id='osm';ROUTE_FORM.error_msg='';render();">
              OpenStreetMap · Alternativa abierta
            </button>
          </div>
          <div style="margin-top:8px;font-size:12px;">
            ${gOk
              ? '<span class="badge badge-green">GOOGLE_MAPS_API_KEY configurada en servidor</span>'
              : '<span class="badge badge-gold">GOOGLE_MAPS_API_KEY pendiente de configurar en entorno</span>'}
          </div>
        </div>

        <div class="card">
          <div style="font-size:13px;font-weight:700;">1. Punto de salida en Málaga</div>
          <div class="header-sub" style="margin-top:4px;">Activo: <strong>${escapeHtml(ROUTE_FORM.start_label)}</strong> (${ROUTE_FORM.start_lat.toFixed(4)}, ${ROUTE_FORM.start_lon.toFixed(4)})</div>
          <div class="btn-row">
            <button class="btn btn-secondary" onclick="useGpsLocation()">Usar mi ubicación GPS</button>
          </div>
          <div class="chip-group">${presets}</div>
          <div class="btn-row" style="margin-top:10px;">
            <input id="geo-input" placeholder="Buscar calle, plaza o barrio en Málaga..." style="margin:0;" />
            <button class="btn btn-secondary" style="width:auto;" onclick="geocodeStreet()">Buscar</button>
          </div>
        </div>

        <div class="card">
          <div style="font-size:13px;font-weight:700;">2. Cantidad de paradas (${ROUTE_FORM.requested_stops} negocios)</div>
          <div class="chip-group">${stopChips}</div>
          <div style="font-size:13px;font-weight:700;margin-top:12px;">Radio máximo caminando (${ROUTE_FORM.radius_m} m)</div>
          <div class="chip-group">${radChips}</div>
        </div>

        <div class="card">
          <div style="font-size:13px;font-weight:700;">3. Categorías de negocio (${ROUTE_FORM.categories.length} seleccionadas)</div>
          <div class="btn-row" style="margin-bottom:6px;">
            <button class="btn btn-secondary" style="padding:6px 10px;font-size:11px;" onclick="selectAllCategories()">Todas</button>
            <button class="btn btn-secondary" style="padding:6px 10px;font-size:11px;" onclick="selectNfcCategories()">Ideales NFC</button>
          </div>
          <div class="chip-group">${catChips}</div>
        </div>

        <button class="btn btn-primary" ${ROUTE_FORM.loading ? 'disabled' : ''} onclick="submitGenerateRoute()">
          ${ROUTE_FORM.loading ? 'Generando ruta comercial...' : `Generar ruta (${ROUTE_FORM.requested_stops} negocios)`}
        </button>

        ${ROUTE_FORM.error_msg ? `
          <div class="alert-error">
            <strong>No se pudo consultar Google Places API (New):</strong> ${escapeHtml(ROUTE_FORM.error_msg)}
            <div style="margin-top:6px;opacity:0.9;">${escapeHtml(ROUTE_FORM.error_notice)}</div>
          </div>
        ` : ''}
      `;
    }

    function renderMapa() {
      const ar = STATE.active_route;
      if (!ar || !ar.stops || ar.stops.length === 0) {
        return `
          <div class="header-title">Mapa a pie</div>
          <div class="card" style="margin-top:12px;">
            No hay ninguna ruta activa. Crea una ruta comercial para ver las paradas numeradas en el mapa.
            <button class="btn btn-primary" style="margin-top:10px;" onclick="openCreateRoute()">Crear ruta ahora</button>
          </div>
        `;
      }
      const idx = Math.min(Math.max(0, Number(ar.current_index || 0)), ar.stops.length - 1);
      const stop = ar.stops[idx];
      return `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
          <div>
            <div class="brand-tag">RUTA #${ar.id} · PARADA ${idx + 1} DE ${ar.stops.length}</div>
            <div style="font-size:16px;font-weight:800;">${escapeHtml(ar.start_label)}</div>
          </div>
          <a class="badge badge-blue" href="${escapeHtml(ar.gmaps_full_route_url)}" target="_blank" rel="noopener">Ruta Google Maps ↗</a>
        </div>

        <div id="map-container"></div>

        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
            <div>
              <span class="badge badge-gold">Parada #${idx + 1} · Score ${stop.score}/100</span>
              <div style="font-size:16px;font-weight:800;margin-top:4px;">${escapeHtml(stop.name)}</div>
              <div class="header-sub">${escapeHtml(stop.sector)} · ${escapeHtml(stop.address || 'Dirección en mapa')}</div>
            </div>
            <span class="badge ${stop.website ? 'badge-blue' : 'badge-red'}">${escapeHtml(stop.web_label)}</span>
          </div>

          <div style="font-size:12px;margin-top:8px;color:#CBD5E1;">
            <strong>Servicio clave:</strong> ${escapeHtml((stop.recommendation && stop.recommendation.primary_service) || 'Página web profesional')}
          </div>

          <div class="btn-row">
            <a class="btn btn-secondary" href="${escapeHtml(stop.gmaps_walk_url)}" target="_blank" rel="noopener">Cómo llegar a pie</a>
            <button class="btn btn-primary" onclick="openLeadDetail(${stop.lead_id || stop.id})">Ficha y NFC</button>
          </div>

          <div class="btn-row">
            <button class="btn btn-green" onclick="quickVisit(${stop.lead_id || stop.id}, 'Interesado')">Interesado</button>
            <button class="btn btn-secondary" onclick="quickVisit(${stop.lead_id || stop.id}, 'Volver otro día')">Volver otro día</button>
            <button class="btn btn-red" onclick="quickVisit(${stop.lead_id || stop.id}, 'No interesa')">No interesa</button>
          </div>

          <div class="btn-row" style="margin-top:10px;">
            <button class="btn btn-secondary" ${idx <= 0 ? 'disabled' : ''} onclick="stepRoute(${ar.id}, ${idx - 1})">← Anterior</button>
            <button class="btn btn-secondary" ${idx >= ar.stops.length - 1 ? 'disabled' : ''} onclick="stepRoute(${ar.id}, ${idx + 1})">Siguiente →</button>
          </div>
        </div>
      `;
    }

    function initLeafletMap() {
      const ar = STATE.active_route;
      const container = document.getElementById('map-container');
      if (!ar || !ar.stops || !container || typeof L === 'undefined') return;
      if (LEAFLET_MAP) {
        LEAFLET_MAP.remove();
        LEAFLET_MAP = null;
      }
      const idx = Math.min(Math.max(0, Number(ar.current_index || 0)), ar.stops.length - 1);
      const currentStop = ar.stops[idx] || ar.stops[0];
      const centerLat = currentStop.lat || ar.start_lat || 36.7213;
      const centerLon = currentStop.lon || ar.start_lon || -4.4214;

      LEAFLET_MAP = L.map('map-container', { zoomControl: true }).setView([centerLat, centerLon], 16);
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap'
      }).addTo(LEAFLET_MAP);

      if (ar.start_lat && ar.start_lon) {
        L.circleMarker([ar.start_lat, ar.start_lon], {
          radius: 8, color: '#10B981', fillColor: '#10B981', fillOpacity: 1
        }).addTo(LEAFLET_MAP).bindPopup('Punto de salida: ' + (ar.start_label || ''));
      }

      const latlngs = [];
      if (ar.geometry && ar.geometry.length > 1) {
        ar.geometry.forEach(pt => latlngs.push([pt[0], pt[1]]));
      } else {
        if (ar.start_lat && ar.start_lon) latlngs.push([ar.start_lat, ar.start_lon]);
        ar.stops.forEach(s => { if (s.lat && s.lon) latlngs.push([s.lat, s.lon]); });
      }
      if (latlngs.length > 1) {
        L.polyline(latlngs, { color: '#F59E0B', weight: 4, opacity: 0.85 }).addTo(LEAFLET_MAP);
      }

      ar.stops.forEach((s, i) => {
        if (!s.lat || !s.lon) return;
        const isCurrent = i === idx;
        const bg = isCurrent ? '#F59E0B' : (s.commercial_status === 'Pendiente' ? '#3B82F6' : '#10B981');
        const icon = L.divIcon({
          className: '',
          html: `<div style="width:26px;height:26px;border-radius:50%;background:${bg};color:#0B101B;font-weight:800;font-size:12px;display:flex;align-items:center;justify-content:center;border:2px solid #fff;">${i + 1}</div>`,
          iconSize: [26, 26],
          iconAnchor: [13, 13]
        });
        const m = L.marker([s.lat, s.lon], { icon }).addTo(LEAFLET_MAP);
        m.on('click', () => stepRoute(ar.id, i));
      });
    }

    function renderNegocios() {
      const sourceList = (FILTER_FORM.scope === 'route' && STATE.active_route && STATE.active_route.stops)
        ? STATE.active_route.stops
        : (STATE.leads || []);

      const q = (FILTER_FORM.q || '').toLowerCase().trim();
      const filtered = sourceList.filter(l => {
        if (q && !(String(l.name||'').toLowerCase().includes(q) || String(l.address||'').toLowerCase().includes(q) || String(l.sector||'').toLowerCase().includes(q))) return false;
        if (FILTER_FORM.priority && (l.priority_effective || l.priority) !== FILTER_FORM.priority) return false;
        return true;
      });

      const cards = filtered.slice(0, 60).map(l => {
        const lid = l.lead_id || l.id;
        return `
          <div class="card" onclick="openLeadDetail(${lid})" style="cursor:pointer;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
              <div>
                <div style="font-size:15px;font-weight:800;">${escapeHtml(l.name)}</div>
                <div class="header-sub">${escapeHtml(l.sector)} · ${escapeHtml(l.address || l.city || 'Málaga')}</div>
              </div>
              <span class="badge badge-gold">${l.score}/100</span>
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;">
              <span class="badge ${l.website ? 'badge-blue' : 'badge-red'}">${escapeHtml(l.web_label)}</span>
              <span class="badge badge-green">${escapeHtml(l.commercial_status || 'Pendiente')}</span>
              ${l.rating != null ? `<span class="badge badge-gold">★ ${l.rating} (${l.user_ratings_total || 0})</span>` : ''}
            </div>
          </div>
        `;
      }).join('');

      return `
        <div class="header-title">Cartera de Negocios (${filtered.length})</div>
        <div class="chip-group" style="margin-bottom:8px;">
          <button class="chip ${FILTER_FORM.scope === 'all' ? 'active' : ''}" onclick="FILTER_FORM.scope='all';render();">Toda la cartera</button>
          <button class="chip ${FILTER_FORM.scope === 'route' ? 'active' : ''}" onclick="FILTER_FORM.scope='route';render();">Ruta activa</button>
          <button class="chip ${FILTER_FORM.priority === 'Alta' ? 'active' : ''}" onclick="FILTER_FORM.priority=FILTER_FORM.priority==='Alta'?'':'Alta';render();">Solo Prioridad Alta</button>
        </div>
        <input placeholder="Filtrar por nombre, calle o categoría..." value="${escapeHtml(FILTER_FORM.q)}" oninput="FILTER_FORM.q=this.value;render();" style="margin-bottom:12px;" />
        ${cards || '<div class="card">No hay negocios que coincidan con el filtro actual.</div>'}
      `;
    }

    function renderFichaNegocio() {
      const lead = findLeadById(SELECTED_LEAD_ID);
      if (!lead) {
        return `<button class="btn btn-secondary" onclick="SUBSCREEN=null;render();">← Volver</button><div class="card">Negocio no encontrado.</div>`;
      }
      const lid = lead.lead_id || lead.id;
      const rec = lead.recommendation || {};
      const pitch = lead.pitch || {};
      const rev = lead.review_info || {};
      const hasPlaceId = Boolean(lead.place_id && !String(lead.place_id).startsWith('osm_'));

      return `
        <button class="btn btn-secondary" style="width:auto;padding:8px 12px;margin-bottom:10px;" onclick="SUBSCREEN=null;render();">← Volver</button>
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              <div class="brand-tag">${escapeHtml(lead.sector)}</div>
              <div class="header-title">${escapeHtml(lead.name)}</div>
              <div class="header-sub">${escapeHtml(lead.address || 'Sin dirección detallada')}</div>
            </div>
            <span class="badge badge-gold">${lead.score}/100</span>
          </div>

          <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;">
            <span class="badge ${lead.website ? 'badge-blue' : 'badge-red'}">${escapeHtml(lead.web_label)}</span>
            <span class="badge badge-blue">Fuente: ${escapeHtml(lead.data_source || lead.source || 'OpenStreetMap')}</span>
            ${lead.rating != null ? `<span class="badge badge-gold">★ ${lead.rating} (${lead.user_ratings_total || 0} reseñas)</span>` : '<span class="badge badge-gold">Reseñas no disponibles en fuente</span>'}
          </div>

          <div style="font-size:12px;margin-top:10px;line-height:1.6;">
            <div><strong>Teléfono:</strong> ${lead.phone ? `<a href="tel:${escapeHtml(lead.phone)}" style="color:#60A5FA;">${escapeHtml(lead.phone)}</a>` : 'No disponible'}</div>
            <div><strong>Web:</strong> ${lead.website ? `<a href="${escapeHtml(lead.website)}" target="_blank" style="color:#60A5FA;">${escapeHtml(lead.website)}</a>` : 'No identificada'}</div>
            <div><strong>Horario:</strong> ${escapeHtml(lead.opening_hours || 'No disponible')}</div>
          </div>

          <div class="btn-row" style="margin-top:10px;">
            <a class="btn btn-secondary" href="${escapeHtml(lead.gmaps_walk_url)}" target="_blank">Cómo llegar</a>
            <button class="btn btn-secondary" onclick="verifyLeadWeb(${lid})">Verificar web HTTP</button>
          </div>
          ${hasPlaceId ? `
            <button class="btn btn-secondary" style="margin-top:8px;" onclick="fetchPlaceDetails(${lid})">
              Ampliar / actualizar detalles desde Google Places (1 petición)
            </button>
          ` : ''}
        </div>

        <div class="card">
          <div style="font-size:13px;font-weight:800;color:var(--gold);">Oportunidad y Argumentario FACENIA</div>
          <div style="font-size:13px;font-weight:700;margin-top:6px;">Servicio principal: ${escapeHtml(rec.primary_service || 'Página web profesional')}</div>
          <div style="font-size:12px;color:#CBD5E1;margin-top:6px;line-height:1.5;">${escapeHtml(pitch.opening || '')}</div>
          <div style="font-size:12px;color:#94A3B8;margin-top:6px;line-height:1.5;">${escapeHtml(pitch.value_prop || '')}</div>
        </div>

        <div class="card">
          <div style="font-size:13px;font-weight:800;">Tarjeta NFC de Reseñas Google</div>
          <div class="header-sub" style="margin-top:4px;">Estado del enlace: ${escapeHtml(rev.status_label || lead.review_url_status || 'Búsqueda asistida')}</div>
          <div class="btn-row" style="margin-top:8px;">
            <a class="btn btn-secondary" href="${escapeHtml(rev.target_url || lead.gmaps_place_url)}" target="_blank">Abrir enlace de reseñas / Maps ↗</a>
          </div>
          <label>Enlace directo de reseñas (para grabar en NFC)</label>
          <input id="ficha-review-url" value="${escapeHtml(lead.review_url || '')}" placeholder="https://g.page/r/.../review" />
          <label>Estado de tarjeta NFC</label>
          <select id="ficha-nfc-status">
            ${(STATE.catalog.nfc_statuses || []).map(st => `<option value="${escapeHtml(st)}" ${lead.nfc_status === st ? 'selected' : ''}>${escapeHtml(st)}</option>`).join('')}
          </select>
        </div>

        <div class="card">
          <div style="font-size:13px;font-weight:800;">Gestión comercial propia (FACENIA CRM)</div>
          <label>Estado comercial</label>
          <select id="ficha-comm-status">
            ${(STATE.catalog.commercial_statuses || []).map(st => `<option value="${escapeHtml(st)}" ${lead.commercial_status === st ? 'selected' : ''}>${escapeHtml(st)}</option>`).join('')}
          </select>
          <label>Próxima acción</label>
          <input id="ficha-next-action" value="${escapeHtml(lead.next_action || '')}" placeholder="Ej. Enviar propuesta por WhatsApp" />
          <label>Notas de visita</label>
          <textarea id="ficha-notes" rows="3" placeholder="Anota con quién hablaste y qué necesita...">${escapeHtml(lead.notes || '')}</textarea>
          <button class="btn btn-primary" style="margin-top:10px;" onclick="saveFichaCommercial(${lid})">Guardar cambios en ficha</button>
        </div>
      `;
    }

    function renderVisitas() {
      const s = STATE.stats || {};
      const inters = (STATE.recent_interactions || []).slice(0, 12).map(it => `
        <div class="card" style="padding:10px 12px;">
          <div style="display:flex;justify-content:space-between;">
            <strong>${escapeHtml(it.lead_name || 'Negocio #' + it.lead_id)}</strong>
            <span class="badge badge-gold">${escapeHtml(it.outcome)}</span>
          </div>
          <div class="header-sub">${escapeHtml(it.created_at)} · ${escapeHtml(it.note || 'Sin nota adicional')}</div>
        </div>
      `).join('');

      return `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
          <div class="header-title">Registro de Visitas</div>
          <button class="btn btn-secondary" style="width:auto;padding:7px 12px;" onclick="undoLastVisit()">Deshacer última</button>
        </div>
        <div class="kpi-grid">
          <div class="card" style="margin:0;">
            <div class="kpi-label">Visitados</div>
            <div class="kpi-val">${s.visited_count || 0}</div>
          </div>
          <div class="card" style="margin:0;">
            <div class="kpi-label">Ventas (${(s.sales_amount_total || 0).toFixed(0)} €)</div>
            <div class="kpi-val">${s.sales_count || 0}</div>
          </div>
        </div>
        <div class="section-label">Historial reciente en calle</div>
        ${inters || '<div class="card">Todavía no has registrado visitas en esta sesión.</div>'}
      `;
    }

    function renderPerfil() {
      const cfg = STATE.settings || {};
      const u = STATE.api_usage || {};
      const gOk = STATE.google_Configured;

      return `
        <div class="brand-tag">FACENIA</div>
        <div class="header-title">Perfil y Configuración</div>
        <div class="header-sub" style="margin-bottom:12px;">Fuentes de datos, control de costes y exportación</div>

        <div class="card">
          <div style="font-size:13px;font-weight:800;">Fuentes de datos y control de consumo (Google Places API New)</div>
          <div style="margin-top:8px;font-size:12px;">
            Google Places API (New):
            ${gOk
              ? '<span class="badge badge-green">GOOGLE_MAPS_API_KEY activa en servidor</span>'
              : '<span class="badge badge-gold">Pendiente de configurar GOOGLE_MAPS_API_KEY</span>'}
          </div>
          <div class="header-sub" style="margin-top:6px;">
            Por seguridad, GOOGLE_MAPS_API_KEY se lee exclusivamente en las variables de entorno del servidor (Vercel / .env) y nunca se expone en el navegador.
          </div>

          <label>Proveedor predeterminado</label>
          <select id="cfg-provider">
            <option value="google_places" ${cfg.default_provider === 'google_places' ? 'selected' : ''}>Google Places API (New) · Principal</option>
            <option value="osm" ${cfg.default_provider === 'osm' ? 'selected' : ''}>OpenStreetMap · Alternativa abierta</option>
          </select>

          <label>Máx. resultados por búsqueda (5–40)</label>
          <input id="cfg-max-res" type="number" min="5" max="40" value="${cfg.places_max_results || 20}" />

          <label>Máx. peticiones API por ruta (1–5)</label>
          <input id="cfg-max-req" type="number" min="1" max="5" value="${cfg.places_max_requests || 3}" />

          <button class="btn btn-primary" style="margin-top:10px;" onclick="saveSettings()">Guardar proveedor y límites</button>
        </div>

        <div class="card">
          <div style="font-size:13px;font-weight:800;">Contador interno de peticiones a Google Places API (New)</div>
          <div style="font-size:13px;margin-top:6px;">
            Total registradas: <strong>${u.total_requests || 0}</strong>
            (Nearby: ${u.nearby_search || 0} · Text: ${u.text_search || 0} · Details: ${u.place_details || 0})
          </div>
          <div class="header-sub" style="margin-top:6px;">
            Nota: Registra únicamente llamadas HTTP enviadas por la app. Consulta Google Cloud Billing para facturación oficial.
          </div>
          <button class="btn btn-secondary" style="margin-top:8px;" onclick="resetApiCounter()">Reiniciar contador interno</button>
        </div>

        <div class="card">
          <div style="font-size:13px;font-weight:800;">Exportar cartera para Notion / Excel</div>
          <div class="btn-row" style="margin-top:10px;">
            <a class="btn btn-secondary" href="/api/export/csv" download>Descargar CSV (Notion)</a>
            <a class="btn btn-primary" href="/api/export/excel" download>Descargar Excel (.xlsx)</a>
          </div>
        </div>
      `;
    }

    function setZonePreset(label, lat, lon) {
      ROUTE_FORM.start_label = label;
      ROUTE_FORM.start_lat = lat;
      ROUTE_FORM.start_lon = lon;
      render();
    }

    function toggleCategory(name) {
      const idx = ROUTE_FORM.categories.indexOf(name);
      if (idx >= 0) ROUTE_FORM.categories.splice(idx, 1);
      else ROUTE_FORM.categories.push(name);
      render();
    }

    function selectAllCategories() {
      ROUTE_FORM.categories = (STATE.catalog.categories || []).map(c => c.name);
      render();
    }

    function selectNfcCategories() {
      ROUTE_FORM.categories = (STATE.catalog.categories || []).filter(c => c.nfc).map(c => c.name);
      render();
    }

    function useGpsLocation() {
      if (!navigator.geolocation) {
        alert('Tu navegador no soporta geolocalización GPS.');
        return;
      }
      navigator.geolocation.getCurrentPosition(pos => {
        ROUTE_FORM.start_lat = pos.coords.latitude;
        ROUTE_FORM.start_lon = pos.coords.longitude;
        ROUTE_FORM.start_label = 'Mi ubicación GPS actual';
        render();
      }, err => {
        alert('No se pudo obtener el GPS: ' + err.message);
      }, { enableHighAccuracy: true, timeout: 8000 });
    }

    async function geocodeStreet() {
      const inp = document.getElementById('geo-input');
      const q = inp ? inp.value.trim() : '';
      if (!q) return;
      const res = await fetch('/api/geocode?q=' + encodeURIComponent(q));
      const data = await res.json();
      if (!res.ok || data.error) {
        alert(data.error || 'No se pudo localizar la dirección.');
        return;
      }
      ROUTE_FORM.start_lat = data.lat;
      ROUTE_FORM.start_lon = data.lon;
      ROUTE_FORM.start_label = data.label;
      render();
    }

    async function submitGenerateRoute() {
      ROUTE_FORM.loading = true;
      ROUTE_FORM.error_msg = '';
      render();
      try {
        const res = await fetch('/api/routes/generate', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(ROUTE_FORM)
        });
        const data = await res.json();
        ROUTE_FORM.loading = false;
        if (!res.ok || !data.ok) {
          ROUTE_FORM.error_msg = data.error || 'Error al generar la ruta.';
          ROUTE_FORM.error_notice = data.honest_notice || '';
          render();
          return;
        }
        STATE = data.state;
        SUBSCREEN = null;
        switchTab('mapa');
      } catch (e) {
        ROUTE_FORM.loading = false;
        ROUTE_FORM.error_msg = String(e);
        render();
      }
    }

    async function selectRoute(routeId) {
      const res = await fetch('/api/routes/select', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ route_id: routeId })
      });
      const data = await res.json();
      if (data.state) STATE = data.state;
      switchTab('mapa');
    }

    async function stepRoute(routeId, nextIndex) {
      const res = await fetch('/api/routes/select', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ route_id: routeId, current_index: nextIndex })
      });
      const data = await res.json();
      if (data.state) STATE = data.state;
      render();
    }

    async function quickVisit(leadId, outcome) {
      const routeId = STATE.active_route ? STATE.active_route.id : null;
      const res = await fetch('/api/leads/visit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ lead_id: leadId, outcome, route_id: routeId })
      });
      const data = await res.json();
      if (data.state) STATE = data.state;
      render();
    }

    async function undoLastVisit() {
      const res = await fetch('/api/visits/undo', { method: 'POST' });
      const data = await res.json();
      if (data.state) STATE = data.state;
      render();
    }

    async function fetchPlaceDetails(leadId) {
      const res = await fetch('/api/leads/place-details', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ lead_id: leadId })
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        alert(data.error || 'No se pudieron ampliar detalles desde Google Places.');
        return;
      }
      STATE = data.state;
      render();
    }

    async function verifyLeadWeb(leadId) {
      const res = await fetch('/api/leads/verify-web', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ lead_id: leadId })
      });
      const data = await res.json();
      if (data.state) STATE = data.state;
      render();
    }

    async function saveFichaCommercial(leadId) {
      const payload = {
        lead_id: leadId,
        commercial_status: document.getElementById('ficha-comm-status').value,
        next_action: document.getElementById('ficha-next-action').value,
        notes: document.getElementById('ficha-notes').value,
        review_url: document.getElementById('ficha-review-url').value,
        nfc_status: document.getElementById('ficha-nfc-status').value
      };
      const res = await fetch('/api/leads/update', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.state) STATE = data.state;
      alert('Ficha guardada correctamente.');
      render();
    }

    async function saveSettings() {
      const payload = {
        action: 'save',
        default_data_provider: document.getElementById('cfg-provider').value,
        places_max_results: Number(document.getElementById('cfg-max-res').value || 20),
        places_max_requests: Number(document.getElementById('cfg-max-req').value || 3)
      };
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.state) STATE = data.state;
      ROUTE_FORM.provider_id = payload.default_data_provider;
      alert('Configuración guardada.');
      render();
    }

    async function resetApiCounter() {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ action: 'reset_counter' })
      });
      const data = await res.json();
      if (data.state) STATE = data.state;
      render();
    }

    loadState();
  </script>
</body>
</html>"""
