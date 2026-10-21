"""
Servidor WSGI y aplicación web móvil (SPA + API REST) para despliegue directo en Vercel
y ejecución web rápida de BuscaLeads · FACENIA, sincronizada con Supabase (Proyecto Facenia).

Expone la variable `wsgi_app` (exportada como `app` y `application` en `app.py`)
compatible con `@vercel/python`.
"""

from datetime import date, timedelta
import json
from typing import Any, Callable, Dict, List, Optional
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
    SECTORS,
    WEB_NONE,
    WEB_STATUSES,
    get_web_label,
    is_google_maps_configured,
    is_supabase_configured,
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
    {"label": "Málaga centro (Plaza de la Constitución)", "lat": 36.7213, "lon": -4.4214},
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
    default_radius = int(get_setting("default_radius_m", "1000", DATABASE_PATH) or 1000)

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
        "supabase_configured": is_supabase_configured(),
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
    raw_path = environ.get("PATH_INFO") or "/"
    query = parse_qs(environ.get("QUERY_STRING") or "")

    rewritten = (query.get("__path") or [""])[0].strip()
    if rewritten:
        path = rewritten if rewritten.startswith("/") else f"/{rewritten}"
    elif raw_path in ("/app.py", "/app"):
        path = "/"
    else:
        path = raw_path

    if path in ("/app.py", "/app", ""):
        path = "/"

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
            radius_m = int(data.get("radius_m") or 1000)
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

            res = record_visit_outcome(
                lead_id=lead_id,
                outcome=outcome,
                route_id=int(route_id) if route_id else None,
                note=note,
                followup_date=followup_date,
                sale_service=sale_service,
                sale_amount=sale_amount,
                db_path=DATABASE_PATH,
            )
            return _json_response(
                start_response,
                {"ok": True, "outcome": outcome, "result": res, "state": _build_full_state()},
            )

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
  <title>BuscaLeads · FACENIA</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    :root {
      --bg: #0D0D10;
      --surface: #17171C;
      --surface-2: #222229;
      --surface-3: #2C2C35;
      --border: rgba(255, 255, 255, 0.08);
      --text: #FFFFFF;
      --muted: #9CA3AF;
      --gold: #E2B325;
      --gold-bright: #FACC15;
      --gold-bg: rgba(226, 179, 37, 0.18);
      --green: #22C55E;
      --green-cta: #15803D;
      --mint-bg: #E8F6EE;
      --mint-text: #14532D;
      --rose-bg: #FCE8E6;
      --rose-text: #7F1D1D;
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    body {
      margin: 0;
      background: #050507;
      color: var(--text);
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      display: flex;
      justify-content: center;
      min-height: 100vh;
    }
    .app-shell {
      width: 100%;
      max-width: 440px;
      min-height: 100vh;
      background: var(--bg);
      padding: 16px 16px 110px;
      position: relative;
      border-left: 1px solid rgba(255,255,255,0.06);
      border-right: 1px solid rgba(255,255,255,0.06);
    }
    /* Top Header & Circular Icon Buttons */
    .top-nav-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
      gap: 10px;
    }
    .circle-btn {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: var(--surface-2);
      border: 1px solid var(--border);
      color: #FFFFFF;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 16px;
      text-decoration: none;
      flex-shrink: 0;
    }
    .circle-btn:active { transform: scale(0.96); }
    .screen-title {
      font-size: 24px;
      font-weight: 800;
      letter-spacing: -0.02em;
      margin: 0;
    }
    /* Cards */
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 16px;
      margin-bottom: 14px;
    }
    .card-title {
      font-size: 15px;
      font-weight: 700;
      color: #FFFFFF;
      margin: 0 0 12px;
    }
    /* Screen 1: Nueva Ruta controls */
    .btn-gps {
      width: 100%;
      background: rgba(226, 179, 37, 0.2);
      border: 1px solid rgba(226, 179, 37, 0.35);
      color: #FDE047;
      border-radius: 14px;
      padding: 13px 16px;
      font-size: 14px;
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      cursor: pointer;
      font-family: inherit;
    }
    .search-row {
      display: flex;
      gap: 8px;
      margin-top: 10px;
    }
    .search-input-wrap {
      flex: 1;
      background: #101014;
      border: 1px solid var(--border);
      border-radius: 14px;
      display: flex;
      align-items: center;
      padding: 0 12px;
    }
    .search-input-wrap input {
      background: transparent;
      border: none;
      color: #FFFFFF;
      font-size: 14px;
      padding: 12px 6px;
      width: 100%;
      outline: none;
      margin: 0;
    }
    .btn-white-pill {
      background: #FFFFFF;
      color: #0D0D10;
      border: none;
      border-radius: 14px;
      padding: 0 18px;
      font-weight: 800;
      font-size: 14px;
      cursor: pointer;
      font-family: inherit;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      text-decoration: none;
    }
    .mint-location-pill {
      margin-top: 10px;
      background: var(--mint-bg);
      color: var(--mint-text);
      border-radius: 14px;
      padding: 11px 14px;
      font-size: 14px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    /* Segmented toggle */
    .seg-bar {
      background: var(--surface-2);
      border-radius: 14px;
      padding: 4px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 4px;
    }
    .seg-btn {
      background: transparent;
      border: none;
      color: var(--muted);
      font-family: inherit;
      font-size: 13px;
      font-weight: 700;
      padding: 9px 10px;
      border-radius: 10px;
      cursor: pointer;
    }
    .seg-btn.active {
      background: #111115;
      color: #FFFFFF;
      box-shadow: 0 2px 6px rgba(0,0,0,0.4);
    }
    /* Stepper */
    .stepper-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 16px;
      padding: 4px 6px;
    }
    .stepper-btn {
      width: 44px;
      height: 44px;
      border-radius: 50%;
      background: var(--surface-2);
      border: 1px solid var(--border);
      color: #FFFFFF;
      font-size: 22px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .stepper-val {
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: #FFFFFF;
    }
    /* Radius segmented track */
    .radius-track {
      background: var(--surface-2);
      border-radius: 14px;
      padding: 4px;
      display: grid;
      grid-template-columns: repeat(6, 1fr);
      gap: 3px;
    }
    .radius-pill {
      background: transparent;
      border: none;
      color: var(--muted);
      font-family: inherit;
      font-size: 12px;
      font-weight: 700;
      padding: 9px 2px;
      border-radius: 10px;
      cursor: pointer;
      text-align: center;
    }
    .radius-pill.active {
      background: #111115;
      color: #FFFFFF;
    }
    /* White / Dark Category Pills */
    .cat-wrap {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .cat-pill {
      background: var(--surface-2);
      color: #D1D5DB;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 9px 16px;
      font-family: inherit;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }
    .cat-pill.active {
      background: #FFFFFF;
      color: #0D0D10;
      border-color: #FFFFFF;
    }
    /* Gold CTA button */
    .btn-gold-cta {
      width: 100%;
      background: var(--gold);
      color: #0D0D10;
      border: none;
      border-radius: 16px;
      padding: 16px;
      font-family: inherit;
      font-size: 16px;
      font-weight: 800;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      box-shadow: 0 6px 20px rgba(226, 179, 37, 0.25);
    }
    /* Screen 3: Stop Cards with % Bar */
    .stop-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 14px 16px;
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      gap: 12px;
      cursor: pointer;
      transition: transform 0.12s ease;
    }
    .stop-card.featured {
      border: 1.5px solid var(--gold);
      box-shadow: 0 0 22px rgba(226, 179, 37, 0.2);
      background: linear-gradient(180deg, rgba(226,179,37,0.08) 0%, var(--surface) 100%);
    }
    .stop-num-circle {
      width: 32px;
      height: 32px;
      border-radius: 50%;
      background: var(--gold);
      color: #0D0D10;
      font-weight: 800;
      font-size: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .stop-progress-track {
      width: 100%;
      height: 5px;
      background: rgba(255,255,255,0.1);
      border-radius: 999px;
      margin: 7px 0;
      overflow: hidden;
    }
    .stop-progress-fill {
      height: 100%;
      border-radius: 999px;
    }
    .go-for-it-banner {
      display: inline-flex;
      align-items: center;
      background: var(--gold);
      color: #0D0D10;
      font-weight: 800;
      font-size: 13px;
      padding: 5px 12px;
      border-radius: 999px;
      margin: 4px 0 6px;
    }
    /* Screen 4 & 5: Ficha del negocio */
    .prob-card {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .nfc-gold-box {
      width: 100%;
      background: var(--gold);
      color: #0D0D10;
      border: none;
      border-radius: 20px;
      padding: 18px;
      text-align: left;
      display: flex;
      align-items: center;
      gap: 14px;
      cursor: pointer;
      margin-bottom: 14px;
      font-family: inherit;
    }
    .nfc-green-box {
      background: rgba(20, 83, 45, 0.22);
      border: 1.5px solid #22C55E;
      border-radius: 20px;
      padding: 16px;
      margin-bottom: 14px;
    }
    .nfc-url-mono {
      background: rgba(0, 0, 0, 0.45);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 12px;
      padding: 10px 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: #E5E7EB;
      word-break: break-all;
      margin: 10px 0;
    }
    .rose-pill {
      display: inline-flex;
      align-items: center;
      background: var(--rose-bg);
      color: var(--rose-text);
      font-size: 12px;
      font-weight: 700;
      padding: 5px 12px;
      border-radius: 999px;
    }
    .mint-pill {
      display: inline-flex;
      align-items: center;
      background: var(--mint-bg);
      color: var(--mint-text);
      font-size: 12px;
      font-weight: 700;
      padding: 5px 12px;
      border-radius: 999px;
    }
    .visit-action-bar {
      display: flex;
      gap: 8px;
      margin-top: 14px;
    }
    .btn-sold {
      flex: 1.4;
      background: var(--green-cta);
      border: 1px solid #22C55E;
      color: #FFFFFF;
      border-radius: 16px;
      padding: 14px 12px;
      font-family: inherit;
      font-size: 15px;
      font-weight: 800;
      cursor: pointer;
    }
    .btn-dark-outcome {
      flex: 1;
      background: var(--surface-2);
      border: 1px solid var(--border);
      color: #FFFFFF;
      border-radius: 16px;
      padding: 12px 8px;
      font-family: inherit;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      line-height: 1.2;
    }
    /* Map Callout Label */
    .map-callout {
      background: rgba(13, 13, 16, 0.92);
      border: 1.5px solid var(--gold);
      color: #FFFFFF;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
      box-shadow: 0 4px 12px rgba(0,0,0,0.6);
    }
    .map-callout span { color: var(--gold-bright); margin-left: 4px; }
    #map-container {
      width: 100%;
      height: 430px;
      border-radius: 22px;
      overflow: hidden;
      border: 1px solid var(--border);
      margin-bottom: 12px;
    }
    /* Toast & Confetti */
    .toast-pill {
      position: fixed;
      top: 14px;
      left: 50%;
      transform: translateX(-50%);
      background: #18181F;
      border: 1px solid rgba(255,255,255,0.16);
      color: #FFFFFF;
      padding: 8px 18px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 800;
      z-index: 2000;
      box-shadow: 0 8px 24px rgba(0,0,0,0.6);
    }
    /* Bottom Nav */
    .bottom-nav {
      position: fixed;
      bottom: 0;
      left: 50%;
      transform: translateX(-50%);
      width: 100%;
      max-width: 440px;
      background: rgba(13, 13, 16, 0.96);
      backdrop-filter: blur(14px);
      border-top: 1px solid var(--border);
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      padding: 7px 4px calc(7px + env(safe-area-inset-bottom, 0px));
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
      padding: 5px 2px;
      font-size: 10px;
      font-weight: 700;
      cursor: pointer;
      font-family: inherit;
    }
    .nav-item.active { color: var(--gold); }
    .nav-item svg { width: 20px; height: 20px; stroke-width: 2.1; }
    input, select, textarea {
      width: 100%;
      background: #101014;
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px 12px;
      font-family: inherit;
      font-size: 13px;
      margin-top: 5px;
    }
    label { font-size: 12px; font-weight: 600; color: var(--muted); display: block; margin-top: 10px; }
    .alert-error {
      background: rgba(239, 68, 68, 0.14);
      border: 1px solid rgba(239, 68, 68, 0.45);
      color: #FCA5A5;
      padding: 12px;
      border-radius: 14px;
      font-size: 12px;
      margin-top: 12px;
      line-height: 1.45;
    }
  </style>
</head>
<body>
  <div id="toast-box"></div>
  <canvas id="confetti-canvas" style="position:fixed;inset:0;pointer-events:none;z-index:1999;display:none;"></canvas>

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
    let NFC_BOX_EXPANDED = false;
    let LEAFLET_MAP = null;

    let ROUTE_FORM = {
      provider_id: 'google_places',
      walk_mode: 'locales', // 'locales' | 'horas'
      start_label: 'Málaga centro (Plaza de la Constitución)',
      start_lat: 36.7213,
      start_lon: -4.4214,
      requested_stops: 12,
      radius_m: 1000,
      categories: ['Restauración', 'Belleza y peluquerías', 'Salud y servicios', 'Comercios'],
      error_msg: '',
      error_notice: '',
      loading: false
    };

    let FILTER_FORM = { q: '', scope: 'route' };

    function showToast(text) {
      const box = document.getElementById('toast-box');
      box.innerHTML = `<div class="toast-pill">${escapeHtml(text)}</div>`;
      setTimeout(() => { box.innerHTML = ''; }, 2600);
    }

    function launchConfetti() {
      const canvas = document.getElementById('confetti-canvas');
      if (!canvas) return;
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      canvas.style.display = 'block';
      const ctx = canvas.getContext('2d');
      const colors = ['#E2B325', '#22C55E', '#FFFFFF', '#FDE047', '#4ADE80'];
      const pieces = Array.from({length: 65}, () => ({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height * 0.4,
        w: 7 + Math.random() * 6,
        h: 5 + Math.random() * 5,
        vy: 3 + Math.random() * 5,
        vx: (Math.random() - 0.5) * 4,
        c: colors[Math.floor(Math.random() * colors.length)]
      }));
      let frame = 0;
      function tick() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        pieces.forEach(p => {
          p.x += p.vx;
          p.y += p.vy;
          ctx.fillStyle = p.c;
          ctx.fillRect(p.x, p.y, p.w, p.h);
        });
        frame++;
        if (frame < 55) requestAnimationFrame(tick);
        else canvas.style.display = 'none';
      }
      tick();
    }

    async function loadState() {
      const res = await fetch('/api/state');
      STATE = await res.json();
      if (STATE && STATE.settings) {
        ROUTE_FORM.provider_id = STATE.google_Configured ? (STATE.settings.default_provider || 'google_places') : 'osm';
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
      NFC_BOX_EXPANDED = false;
      SUBSCREEN = 'ficha_negocio';
      window.scrollTo({ top: 0, behavior: 'smooth' });
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

    function isLeadVisited(x) {
      return Boolean(x.last_visit_at) || !['Nuevo', 'Pendiente', 'Pendiente de revisar', 'Pendiente de contactar', 'Pendiente de visitar'].includes(x.commercial_status || 'Nuevo');
    }

    function render() {
      const root = document.getElementById('app-content');
      if (!STATE) {
        root.innerHTML = '<div class="card">Cargando BuscaLeads...</div>';
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

    /* =====================================================================
       INICIO
       ===================================================================== */
    function renderInicio() {
      const s = STATE.stats || {};
      const ar = STATE.active_route;
      let activeCard = '';
      if (ar && ar.stops && ar.stops.length > 0) {
        const visited = ar.stops.filter(isLeadVisited).length;
        activeCard = `
          <div class="card" style="border:1.5px solid var(--gold);margin-top:14px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <span style="font-size:12px;font-weight:800;color:var(--gold);text-transform:uppercase;">Ruta activa · ${visited} de ${ar.stops.length} visitados</span>
              <span class="mint-pill">En curso</span>
            </div>
            <div style="font-size:18px;font-weight:800;margin:6px 0 4px;">${escapeHtml(ar.start_label)}</div>
            <div style="font-size:13px;color:var(--muted);margin-bottom:12px;">
              ${ar.stops.length} locales seleccionados · Radio ${ar.radius_m >= 1000 ? (ar.radius_m/1000)+' km' : ar.radius_m+' m'}
            </div>
            <div style="display:flex;gap:8px;">
              <button class="btn-gold-cta" style="padding:12px;font-size:14px;" onclick="switchTab('negocios')">Ver % de venta (${ar.stops.length})</button>
              <button class="btn-white-pill" style="padding:12px 16px;" onclick="switchTab('mapa')">Mapa</button>
            </div>
          </div>
        `;
      }

      return `
        <div class="top-nav-row">
          <div>
            <div style="font-size:11px;font-weight:800;color:var(--gold);letter-spacing:0.08em;">FACENIA · PROSPECCIÓN</div>
            <h1 class="screen-title">BuscaLeads</h1>
          </div>
          <span class="mint-pill">${STATE.supabase_configured ? '✓ Supabase activo' : 'SQLite local'}</span>
        </div>

        <button class="btn-gold-cta" onclick="openCreateRoute()">
          + Nueva ruta comercial a pie
        </button>

        ${activeCard}

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px;">
          <div class="card" style="margin:0;">
            <div style="font-size:12px;color:var(--muted);font-weight:600;">Locales en cartera</div>
            <div style="font-size:26px;font-weight:800;margin-top:4px;">${s.total || 0}</div>
            <div style="font-size:11px;color:var(--gold-bright);margin-top:2px;">${s.no_web || 0} sin web</div>
          </div>
          <div class="card" style="margin:0;">
            <div style="font-size:12px;color:var(--muted);font-weight:600;">Visitas / Ventas</div>
            <div style="font-size:26px;font-weight:800;margin-top:4px;">${s.visited_count || 0} <span style="font-size:15px;color:#4ADE80;">(${s.sales_count || 0} ✓)</span></div>
            <div style="font-size:11px;color:var(--muted);margin-top:2px;">Seguimientos: ${s.followup_pending || 0}</div>
          </div>
        </div>
      `;
    }

    /* =====================================================================
       PANTALLA 1 (CAPTURA 1): NUEVA RUTA
       ===================================================================== */
    function renderCrearRuta() {
      const hoursApprox = (ROUTE_FORM.requested_stops / 8).toFixed(1).replace('.0', '');
      const centerText = ROUTE_FORM.walk_mode === 'horas'
        ? `${hoursApprox} h · ${ROUTE_FORM.requested_stops} locales`
        : `${ROUTE_FORM.requested_stops} locales`;

      const radOptions = [
        { m: 250, label: '250 m' },
        { m: 500, label: '500 m' },
        { m: 750, label: '750 m' },
        { m: 1000, label: '1 km' },
        { m: 1500, label: '1,5 km' },
        { m: 2000, label: '2 km' }
      ].map(r => `
        <button class="radius-pill ${ROUTE_FORM.radius_m === r.m ? 'active' : ''}"
          onclick="ROUTE_FORM.radius_m=${r.m};render();">${r.label}</button>
      `).join('');

      const shortLabels = {
        'Belleza y peluquerías': 'Belleza',
        'Cafeterías y panaderías': 'Cafeterías',
        'Reformas y construcción': 'Reformas',
        'Instalaciones y mantenimiento': 'Instalaciones',
        'Academias y formación': 'Academias',
        'Consultorías y despachos': 'Despachos',
        'Distribución y logística': 'Logística'
      };

      const catPills = (STATE.catalog.categories || []).map(c => {
        const active = ROUTE_FORM.categories.includes(c.name);
        const label = shortLabels[c.name] || c.name;
        return `<button class="cat-pill ${active ? 'active' : ''}" onclick="toggleCategory('${escapeHtml(c.name)}')">${escapeHtml(label)}</button>`;
      }).join('');

      const zoneChips = (STATE.catalog.zone_presets || []).map(z => `
        <button class="cat-pill ${ROUTE_FORM.start_label === z.label ? 'active' : ''}" style="padding:6px 11px;font-size:11px;"
          onclick="setZonePreset('${escapeHtml(z.label)}', ${z.lat}, ${z.lon})">${escapeHtml(z.label.split(' (')[0])}</button>
      `).join('');

      return `
        <div class="top-nav-row">
          <div style="display:flex;align-items:center;gap:12px;">
            <button class="circle-btn" onclick="SUBSCREEN=null;render();" aria-label="Volver">‹</button>
            <h1 class="screen-title">Nueva ruta</h1>
          </div>
          <div class="seg-bar" style="width:170px;">
            <button class="seg-btn ${ROUTE_FORM.provider_id === 'google_places' ? 'active' : ''}" style="font-size:11px;padding:6px;"
              onclick="ROUTE_FORM.provider_id='google_places';ROUTE_FORM.error_msg='';render();">Google</button>
            <button class="seg-btn ${ROUTE_FORM.provider_id === 'osm' ? 'active' : ''}" style="font-size:11px;padding:6px;"
              onclick="ROUTE_FORM.provider_id='osm';ROUTE_FORM.error_msg='';render();">OSM</button>
          </div>
        </div>

        <div class="card">
          <div class="card-title">Punto de salida</div>
          <button class="btn-gps" onclick="useGpsLocation()">
            <span>⌖</span> Usar mi ubicación
          </button>
          <div class="search-row">
            <div class="search-input-wrap">
              <span style="color:var(--muted);font-size:14px;">🔍</span>
              <input id="geo-input" placeholder="Calle, barrio o ciudad" onkeydown="if(event.key==='Enter')geocodeStreet()" />
            </div>
            <button class="btn-white-pill" onclick="geocodeStreet()">Buscar</button>
          </div>
          <div class="mint-location-pill">
            <span>✓</span>
            <span>${escapeHtml(ROUTE_FORM.start_label)}</span>
          </div>
          <div class="cat-wrap" style="margin-top:10px;">${zoneChips}</div>
        </div>

        <div class="card">
          <div class="card-title">¿Cuánto quieres andar?</div>
          <div class="seg-bar">
            <button class="seg-btn ${ROUTE_FORM.walk_mode === 'locales' ? 'active' : ''}"
              onclick="ROUTE_FORM.walk_mode='locales';render();">Nº de locales</button>
            <button class="seg-btn ${ROUTE_FORM.walk_mode === 'horas' ? 'active' : ''}"
              onclick="ROUTE_FORM.walk_mode='horas';render();">Horas en ruta</button>
          </div>
          <div class="stepper-row">
            <button class="stepper-btn" onclick="stepLocales(-2)">−</button>
            <div class="stepper-val">${centerText}</div>
            <button class="stepper-btn" onclick="stepLocales(2)">+</button>
          </div>
        </div>

        <div class="card">
          <div class="card-title">Radio de búsqueda</div>
          <div class="radius-track">${radOptions}</div>
        </div>

        <div class="card">
          <div class="card-title">Tipos de negocio</div>
          <div class="cat-wrap">${catPills}</div>
        </div>

        <button class="btn-gold-cta" ${ROUTE_FORM.loading ? 'disabled' : ''} onclick="submitGenerateRoute()">
          ${ROUTE_FORM.loading ? 'Calculando ruta a pie...' : `Generar ruta · ${ROUTE_FORM.requested_stops} locales`}
        </button>

        ${ROUTE_FORM.error_msg ? `
          <div class="alert-error">
            <strong>Aviso de proveedor:</strong> ${escapeHtml(ROUTE_FORM.error_msg)}
            <div style="margin-top:5px;opacity:0.9;">${escapeHtml(ROUTE_FORM.error_notice)}</div>
          </div>
        ` : ''}
      `;
    }

    function stepLocales(delta) {
      const next = Math.min(30, Math.max(5, ROUTE_FORM.requested_stops + delta));
      ROUTE_FORM.requested_stops = next;
      render();
    }

    /* =====================================================================
       PANTALLA 2 (CAPTURA 2): MAPA A PIE CON ETIQUETAS FLOTANTES %
       ===================================================================== */
    function renderMapa() {
      const ar = STATE.active_route;
      if (!ar || !ar.stops || ar.stops.length === 0) {
        return `
          <div class="top-nav-row"><h1 class="screen-title">Ruta a pie</h1></div>
          <div class="card">
            Aún no hay una ruta activa. Elige tu zona y cuántos locales quieres visitar.
            <button class="btn-gold-cta" style="margin-top:12px;" onclick="openCreateRoute()">Nueva ruta</button>
          </div>
        `;
      }
      const idx = Math.min(Math.max(0, Number(ar.current_index || 0)), ar.stops.length - 1);
      const stop = ar.stops[idx];
      const lid = stop.lead_id || stop.id;

      return `
        <div class="top-nav-row">
          <div>
            <div style="font-size:11px;font-weight:800;color:var(--gold);text-transform:uppercase;">Ruta ordenada a pie · Parada ${idx + 1}/${ar.stops.length}</div>
            <h1 class="screen-title" style="font-size:20px;">${escapeHtml(ar.start_label)}</h1>
          </div>
          <a class="btn-white-pill" style="padding:9px 14px;font-size:12px;" href="${escapeHtml(ar.gmaps_full_route_url)}" target="_blank" rel="noopener">◁ Google Maps</a>
        </div>

        <div id="map-container"></div>

        <div class="stop-card featured" onclick="openLeadDetail(${lid})">
          <div class="stop-num-circle">${idx + 1}</div>
          <div style="flex:1;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <strong style="font-size:16px;">${escapeHtml(stop.name)}</strong>
              <span style="font-weight:800;color:var(--gold-bright);font-size:15px;">${stop.score}%</span>
            </div>
            <div class="stop-progress-track">
              <div class="stop-progress-fill" style="width:${stop.score}%;background:var(--gold);"></div>
            </div>
            <div style="font-size:12px;color:var(--muted);">
              ${stop.rating != null ? `★ ${stop.rating} · ${stop.user_ratings_total || 0} reseñas · ` : ''}${escapeHtml(stop.sector)} · Toca para abrir ficha
            </div>
          </div>
        </div>

        <div style="display:flex;gap:8px;">
          <button class="circle-btn" style="flex:1;border-radius:14px;font-size:13px;font-weight:700;" ${idx <= 0 ? 'disabled' : ''} onclick="stepRoute(${ar.id}, ${idx - 1})">‹ Anterior</button>
          <button class="btn-gold-cta" style="flex:1.5;padding:10px;font-size:13px;" onclick="openLeadDetail(${lid})">Qué ofrecerle y NFC</button>
          <button class="circle-btn" style="flex:1;border-radius:14px;font-size:13px;font-weight:700;" ${idx >= ar.stops.length - 1 ? 'disabled' : ''} onclick="stepRoute(${ar.id}, ${idx + 1})">Siguiente ›</button>
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

      LEAFLET_MAP = L.map('map-container', { zoomControl: false }).setView([centerLat, centerLon], 16);
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap'
      }).addTo(LEAFLET_MAP);

      const latlngs = [];
      if (ar.geometry && ar.geometry.length > 1) {
        ar.geometry.forEach(pt => latlngs.push([pt[0], pt[1]]));
      } else {
        if (ar.start_lat && ar.start_lon) latlngs.push([ar.start_lat, ar.start_lon]);
        ar.stops.forEach(s => { if (s.lat && s.lon) latlngs.push([s.lat, s.lon]); });
      }
      if (latlngs.length > 1) {
        L.polyline(latlngs, { color: 'rgba(250, 204, 21, 0.28)', weight: 10 }).addTo(LEAFLET_MAP);
        L.polyline(latlngs, { color: '#FACC15', weight: 4, opacity: 0.95 }).addTo(LEAFLET_MAP);
      }

      if (ar.start_lat && ar.start_lon) {
        L.circleMarker([ar.start_lat, ar.start_lon], {
          radius: 7, color: '#FFFFFF', weight: 3, fillColor: '#3B82F6', fillOpacity: 1
        }).addTo(LEAFLET_MAP);
      }

      ar.stops.forEach((s, i) => {
        if (!s.lat || !s.lon) return;
        const isGold = s.score >= 82 || i === idx;
        const bg = isGold ? '#E2B325' : '#4ADE80';
        const showCallout = i === idx || s.score >= 85 || i < 3;
        const calloutHtml = showCallout
          ? `<div class="map-callout" style="position:absolute;left:34px;top:-2px;">${escapeHtml(s.name.slice(0, 18))}<span>${s.score}%</span></div>`
          : '';
        const icon = L.divIcon({
          className: '',
          html: `
            <div style="position:relative;display:flex;align-items:center;">
              <div style="width:28px;height:28px;border-radius:50%;background:${bg};color:#0D0D10;font-weight:800;font-size:13px;display:flex;align-items:center;justify-content:center;border:2px solid #0D0D10;box-shadow:0 0 12px ${bg};">${i + 1}</div>
              ${calloutHtml}
            </div>
          `,
          iconSize: [28, 28],
          iconAnchor: [14, 14]
        });
        const m = L.marker([s.lat, s.lon], { icon }).addTo(LEAFLET_MAP);
        m.on('click', () => stepRoute(ar.id, i));
      });
    }

    /* =====================================================================
       PANTALLA 3 (CAPTURA 3): EL % DE VENTA DE CADA NEGOCIO
       ===================================================================== */
    function renderNegocios() {
      const ar = STATE.active_route;
      const useRoute = FILTER_FORM.scope === 'route' && ar && ar.stops && ar.stops.length > 0;
      const list = useRoute ? ar.stops : (STATE.leads || []);

      const q = (FILTER_FORM.q || '').toLowerCase().trim();
      const filtered = list.filter(l => {
        if (!q) return true;
        return String(l.name||'').toLowerCase().includes(q) || String(l.sector||'').toLowerCase().includes(q) || String(l.address||'').toLowerCase().includes(q);
      });

      const visitedCount = useRoute ? ar.stops.filter(isLeadVisited).length : 0;
      const gmapsUrl = useRoute ? ar.gmaps_full_route_url : 'https://www.google.com/maps';

      // Encontrar el índice del negocio con mayor % para destacarlo como en la captura 3
      let bestId = null;
      let bestScore = -1;
      filtered.forEach(item => {
        if ((item.score || 0) > bestScore) {
          bestScore = item.score || 0;
          bestId = item.lead_id || item.id;
        }
      });

      const cards = filtered.slice(0, 60).map((l, idx) => {
        const lid = l.lead_id || l.id;
        const isBest = lid === bestId && l.score >= 75;
        const isHigh = (l.score || 0) >= 80;
        const barColor = isHigh ? 'var(--gold)' : '#22C55E';
        const pctColor = isHigh ? 'var(--gold-bright)' : '#4ADE80';
        const ratingLine = l.rating != null
          ? `★ ${String(l.rating).replace('.', ',')} · ${l.user_ratings_total || 0} reseñas · ${escapeHtml(l.sector)}`
          : `★ Sin reseñas en fuente · ${escapeHtml(l.sector)} · ${escapeHtml(l.web_label)}`;

        return `
          ${isBest ? `<div class="go-for-it-banner">${l.score} % · ¡Ve a por él!</div>` : ''}
          <div class="stop-card ${isBest ? 'featured' : ''}" onclick="openLeadDetail(${lid})">
            <div class="stop-num-circle">${l.position || (idx + 1)}</div>
            <div style="flex:1;min-width:0;">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
                <strong style="font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(l.name)}</strong>
                <span style="font-weight:800;font-size:14px;color:${pctColor};">${l.score}%</span>
              </div>
              <div class="stop-progress-track">
                <div class="stop-progress-fill" style="width:${l.score}%;background:${barColor};"></div>
              </div>
              <div style="font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                ${ratingLine}
              </div>
            </div>
          </div>
        `;
      }).join('');

      return `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
          <span style="font-size:13px;color:var(--muted);font-weight:700;">
            ${useRoute ? `${visitedCount} de ${ar.stops.length} visitados` : `${filtered.length} locales en cartera`}
          </span>
          <div class="seg-bar" style="width:190px;">
            <button class="seg-btn ${useRoute ? 'active' : ''}" style="font-size:11px;padding:6px;" onclick="FILTER_FORM.scope='route';render();">Ruta actual</button>
            <button class="seg-btn ${!useRoute ? 'active' : ''}" style="font-size:11px;padding:6px;" onclick="FILTER_FORM.scope='all';render();">Cartera (${(STATE.leads||[]).length})</button>
          </div>
        </div>

        <a class="btn-white-pill" style="width:100%;padding:14px;font-size:15px;margin-bottom:8px;" href="${escapeHtml(gmapsUrl)}" target="_blank" rel="noopener">
          ◁ Abrir en Google Maps
        </a>
        <button class="circle-btn" style="width:100%;border-radius:14px;padding:13px;font-size:14px;font-weight:700;gap:8px;margin-bottom:8px;" onclick="shareCurrentRoute()">
          ↑ Compartir ruta
        </button>
        <div style="font-size:12px;color:var(--muted);text-align:center;margin-bottom:14px;line-height:1.4;">
          Abre las próximas paradas pendientes. Al marcar visitas, el botón sigue con las siguientes.
        </div>

        <input id="search-negocios" name="search_negocios" placeholder="Buscar local por nombre o categoría..." value="${escapeHtml(FILTER_FORM.q)}" oninput="FILTER_FORM.q=this.value;render();" style="margin-bottom:12px;" />

        ${cards || '<div class="card">No hay locales en esta vista.</div>'}
      `;
    }

    /* =====================================================================
       PANTALLAS 4 Y 5 (CAPTURAS 4 Y 5): FICHA DEL NEGOCIO + NFC + VENDIDO
       ===================================================================== */
    function renderFichaNegocio() {
      const lead = findLeadById(SELECTED_LEAD_ID);
      if (!lead) {
        return `<button class="circle-btn" onclick="SUBSCREEN=null;render();">‹</button><div class="card" style="margin-top:12px;">Local no encontrado.</div>`;
      }
      const lid = lead.lead_id || lead.id;
      const rec = lead.recommendation || {};
      const pitch = lead.pitch || {};
      const rev = lead.review_info || {};
      const score = Number(lead.score || 75);
      const radius = 28;
      const circum = 2 * Math.PI * radius;
      const dashOffset = circum - (score / 100) * circum;

      const isSold = lead.commercial_status === 'Cliente conseguido' || lead.commercial_status === 'Cliente';
      const statusBadge = isSold
        ? `<div style="margin-top:8px;"><span class="mint-pill">Estado: Vendido</span></div>`
        : (lead.commercial_status && lead.commercial_status !== 'Nuevo' && lead.commercial_status !== 'Pendiente de revisar'
            ? `<div style="margin-top:8px;"><span class="mint-pill">Estado: ${escapeHtml(lead.commercial_status)}</span></div>`
            : '');

      const probText = score >= 85
        ? 'Máxima: de los mejores negocios de tu zona para vender. ¡Ve a por él!'
        : (score >= 70
            ? 'Alta oportunidad comercial: carece de activos digitales clave para captar clientes.'
            : 'Oportunidad media: explora renovación digital, automatización o tarjeta NFC.');

      const targetReviewUrl = lead.review_url || rev.review_url || lead.gmaps_place_url;
      const showGreenNfc = NFC_BOX_EXPANDED || Boolean(lead.review_url) || isSold;

      const ratingNum = lead.rating != null ? String(lead.rating).replace('.', ',') : '—';
      const starsCount = lead.rating != null ? Math.round(Number(lead.rating)) : 0;
      const starsHtml = '★'.repeat(starsCount) + '<span style="opacity:0.25;">' + '★'.repeat(Math.max(0, 5 - starsCount)) + '</span>';
      const hasPlaceId = Boolean(lead.place_id && !String(lead.place_id).startsWith('osm_'));
      const socialHref = lead.social_url ? (String(lead.social_url).startsWith('http') ? lead.social_url : 'https://instagram.com/' + String(lead.social_url).replace(/^@/, '')) : '';
      const recServices = (rec.services && rec.services.length) ? rec.services.slice(0, 3).join(' · ') : 'Página web + Tarjeta NFC de reseñas';

      return `
        <div class="top-nav-row">
          <button class="circle-btn" onclick="SUBSCREEN=null;render();" aria-label="Volver">‹</button>
          <div style="display:flex;gap:8px;">
            <button class="circle-btn" onclick="showToast('Añadido a prioritarios ★')" title="Prioritario">☆</button>
            <a class="circle-btn" href="${escapeHtml(lead.gmaps_walk_url)}" target="_blank" rel="noopener" title="Cómo llegar">◁</a>
          </div>
        </div>

        <div style="margin-bottom:16px;">
          <h1 style="font-size:28px;font-weight:800;margin:0;letter-spacing:-0.02em;">${escapeHtml(lead.name)}</h1>
          <div style="font-size:14px;color:var(--muted);margin-top:4px;line-height:1.4;">
            ${escapeHtml(lead.sector)} · ${escapeHtml(lead.address || lead.city || 'Málaga')}${lead.opening_hours ? ' · ' + escapeHtml(lead.opening_hours.split('·')[0]) : ''}
          </div>
          ${statusBadge}
        </div>

        <!-- Card 1: Probabilidad de venta con anillo circular -->
        <div class="card prob-card">
          <div style="position:relative;width:72px;height:72px;flex-shrink:0;">
            <svg width="72" height="72" viewBox="0 0 72 72">
              <circle cx="36" cy="36" r="${radius}" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="6" />
              <circle cx="36" cy="36" r="${radius}" fill="none" stroke="#E2B325" stroke-width="6"
                stroke-linecap="round"
                stroke-dasharray="${circum}"
                stroke-dashoffset="${dashOffset}"
                transform="rotate(-90 36 36)" />
            </svg>
            <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:16px;">
              ${score}%
            </div>
          </div>
          <div>
            <div style="font-size:16px;font-weight:800;">Probabilidad de venta</div>
            <div style="font-size:13px;color:var(--muted);margin-top:4px;line-height:1.45;">
              ${escapeHtml(probText)}
            </div>
          </div>
        </div>

        <!-- Card 2: Generar link de reseñas / Link de reseñas listo (NFC Tools) -->
        ${!showGreenNfc ? `
          <button class="nfc-gold-box" onclick="NFC_BOX_EXPANDED=true;render();">
            <span style="font-size:22px;">🔗</span>
            <div>
              <div style="font-size:16px;font-weight:800;">Generar link de reseñas</div>
              <div style="font-size:13px;opacity:0.85;margin-top:2px;">Listo para grabar en la tarjeta con NFC Tools</div>
            </div>
          </button>
        ` : `
          <div class="nfc-green-box">
            <div style="display:flex;align-items:center;gap:8px;font-size:16px;font-weight:800;color:#FFFFFF;">
              <span style="background:#22C55E;color:#0D0D10;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:13px;">✓</span>
              Link de reseñas listo
            </div>
            <div class="nfc-url-mono" id="nfc-url-text">${escapeHtml(targetReviewUrl)}</div>
            <div style="font-size:12px;color:#D1D5DB;margin-bottom:12px;">
              Abre directamente la ventana de Google para dejar una reseña de este negocio.
            </div>
            <div style="display:flex;gap:8px;margin-bottom:14px;">
              <button class="btn-white-pill" style="flex:1;padding:11px;" onclick="copyNfcLink('${escapeHtml(targetReviewUrl)}')">Copiar link</button>
              <button class="circle-btn" style="flex:1;border-radius:14px;font-size:13px;font-weight:700;gap:6px;" onclick="shareNfcLink('${escapeHtml(lead.name)}', '${escapeHtml(targetReviewUrl)}')">↑ Compartir</button>
            </div>
            <div style="font-size:12px;color:#D1D5DB;line-height:1.7;">
              <div><strong style="color:var(--gold-bright);">1</strong> Copia el link</div>
              <div><strong style="color:var(--gold-bright);">2</strong> NFC Tools &gt; Escribir &gt; Añadir un registro &gt; URL</div>
              <div><strong style="color:var(--gold-bright);">3</strong> Pega el link, pulsa Escribir y acerca la tarjeta</div>
            </div>
          </div>
        `}

        <!-- Card 3: Reseñas y Presencia Digital -->
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:baseline;">
            <div>
              <span style="font-size:32px;font-weight:800;">${ratingNum}</span>
              <span style="color:var(--gold);font-size:16px;margin-left:6px;">${starsHtml}</span>
            </div>
            <span style="font-size:13px;color:var(--muted);font-weight:600;">
              ${lead.user_ratings_total != null ? lead.user_ratings_total + ' reseñas' : 'Reseñas no indicadas'}
            </span>
          </div>

          <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;">
            ${!socialHref ? '<span class="rose-pill">Instagram no encontrado</span>' : `<a class="mint-pill" href="${escapeHtml(socialHref)}" target="_blank" rel="noopener">Red social activa ↗</a>`}
            ${!lead.website ? `<span class="rose-pill">${escapeHtml(lead.web_label || 'Sin web')}</span>` : `<a class="mint-pill" href="${escapeHtml(lead.website)}" target="_blank" rel="noopener">Web activa ↗</a>`}
          </div>

          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;font-size:12px;color:var(--muted);">
            <span>Fuente: ${escapeHtml(lead.data_source || lead.source || 'OpenStreetMap')}</span>
            ${lead.phone ? `<a href="tel:${escapeHtml(lead.phone)}" style="color:var(--gold-bright);font-weight:700;text-decoration:none;">📞 ${escapeHtml(lead.phone)}</a>` : ''}
          </div>

          ${hasPlaceId ? `
            <button class="circle-btn" style="width:100%;border-radius:12px;margin-top:10px;font-size:12px;font-weight:700;" onclick="fetchPlaceDetails(${lid})">
              Actualizar detalles desde Google Places (1 petición)
            </button>
          ` : ''}
        </div>

        <!-- Card 4: Qué ofrecerle y qué decirle (FACENIA) -->
        <div class="card">
          <div class="card-title" style="color:var(--gold-bright);">Qué ofrecerle y qué decirle</div>
          <div style="font-size:13px;font-weight:800;margin-bottom:6px;color:#FFFFFF;">
            Servicios recomendados: <span style="color:var(--gold-bright);">${escapeHtml(recServices)}</span>
          </div>
          ${rec.headline ? `<div style="font-size:12px;color:var(--muted);margin-bottom:8px;line-height:1.45;">${escapeHtml(rec.headline)}</div>` : ''}
          <div style="font-size:13px;color:#E5E7EB;line-height:1.55;background:#101014;padding:12px;border-radius:12px;border:1px solid var(--border);">
            ${escapeHtml(pitch.text || '')}
          </div>

          <label>Notas de la visita comercial</label>
          <textarea id="ficha-notes" rows="2" placeholder="Anota el nombre del encargado o detalles...">${escapeHtml(lead.notes || '')}</textarea>
          <button class="circle-btn" style="width:100%;border-radius:12px;margin-top:8px;font-size:13px;font-weight:700;" onclick="saveFichaNotes(${lid})">
            Guardar nota comercial
          </button>
        </div>

        <!-- Barra de cierre de visita (Captura 5) -->
        <div class="visit-action-bar">
          <button class="btn-sold" onclick="markVisitOutcome(${lid}, 'Vendido')">✓ Vendido</button>
          <button class="btn-dark-outcome" onclick="markVisitOutcome(${lid}, 'Volver otro día')">Volver otro<br>día</button>
          <button class="btn-dark-outcome" onclick="markVisitOutcome(${lid}, 'No interesa')">No<br>interesa</button>
        </div>
      `;
    }

    function copyNfcLink(url) {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(url);
      }
      showToast('✓ Link copiado para NFC Tools');
    }

    function shareNfcLink(name, url) {
      if (navigator.share) {
        navigator.share({ title: name, text: 'Enlace directo de reseñas Google', url });
      } else {
        copyNfcLink(url);
      }
    }

    function shareCurrentRoute() {
      const ar = STATE.active_route;
      const url = ar ? ar.gmaps_full_route_url : window.location.href;
      if (navigator.share) {
        navigator.share({ title: 'Ruta BuscaLeads · FACENIA', url });
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(url);
        showToast('✓ Enlace de ruta copiado');
      }
    }

    async function markVisitOutcome(leadId, outcome) {
      const routeId = STATE.active_route ? STATE.active_route.id : null;
      const notesEl = document.getElementById('ficha-notes');
      const note = notesEl ? notesEl.value : '';
      const res = await fetch('/api/leads/visit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ lead_id: leadId, outcome, route_id: routeId, note })
      });
      const data = await res.json();
      if (data.state) STATE = data.state;
      if (outcome === 'Vendido' || outcome === 'Venta cerrada') {
        NFC_BOX_EXPANDED = true;
        showToast('¡Venta registrada!');
        launchConfetti();
      } else if (outcome === 'Volver otro día') {
        showToast('Seguimiento agendado');
      } else {
        showToast('Marcado como No interesa');
      }
      render();
    }

    async function saveFichaNotes(leadId) {
      const lead = findLeadById(leadId) || {};
      const notes = document.getElementById('ficha-notes').value;
      const res = await fetch('/api/leads/update', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          lead_id: leadId,
          commercial_status: lead.commercial_status || 'Pendiente de revisar',
          notes
        })
      });
      const data = await res.json();
      if (data.state) STATE = data.state;
      showToast('✓ Nota guardada en Supabase');
      render();
    }

    /* =====================================================================
       VISITAS Y PERFIL
       ===================================================================== */
    function renderVisitas() {
      const s = STATE.stats || {};
      const inters = (STATE.recent_interactions || []).slice(0, 15).map(it => `
        <div class="stop-card" onclick="openLeadDetail(${it.lead_id})">
          <div class="stop-num-circle" style="background:${it.outcome === 'Vendido' ? '#22C55E' : 'var(--surface-3)'};color:#fff;">✓</div>
          <div style="flex:1;">
            <div style="display:flex;justify-content:space-between;">
              <strong>${escapeHtml(it.lead_name || 'Local #' + it.lead_id)}</strong>
              <span class="mint-pill" style="font-size:11px;">${escapeHtml(it.outcome)}</span>
            </div>
            <div style="font-size:12px;color:var(--muted);margin-top:4px;">${escapeHtml(it.created_at)} · ${escapeHtml(it.note || 'Visita presencial')}</div>
          </div>
        </div>
      `).join('');

      return `
        <div class="top-nav-row">
          <h1 class="screen-title">Tus números y visitas</h1>
          <button class="btn-white-pill" style="padding:8px 12px;font-size:12px;" onclick="undoLastVisit()">Deshacer</button>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px;">
          <div class="card" style="margin:0;">
            <div style="font-size:12px;color:var(--muted);">Visitas realizadas</div>
            <div style="font-size:28px;font-weight:800;margin-top:4px;">${s.visited_count || 0}</div>
          </div>
          <div class="card" style="margin:0;">
            <div style="font-size:12px;color:var(--muted);">Ventas cerradas</div>
            <div style="font-size:28px;font-weight:800;color:#4ADE80;margin-top:4px;">${s.sales_count || 0}</div>
          </div>
        </div>

        ${inters || '<div class="card">Aún no has registrado visitas hoy. Abre cualquier local y pulsa Vendido, Volver otro día o No interesa.</div>'}
      `;
    }

    function renderPerfil() {
      const cfg = STATE.settings || {};
      const u = STATE.api_usage || {};
      const gOk = STATE.google_Configured;
      const sbOk = STATE.supabase_configured;

      return `
        <div class="top-nav-row">
          <h1 class="screen-title">Perfil y Ajustes</h1>
        </div>

        <div class="card">
          <div class="card-title">Estado de Nube y APIs</div>
          <div style="display:flex;flex-wrap:wrap;gap:8px;">
            <span class="${sbOk ? 'mint-pill' : 'rose-pill'}">
              ${sbOk ? '✓ Base de datos Supabase (Facenia) conectada' : 'Supabase pendiente en variables de entorno'}
            </span>
            <span class="${gOk ? 'mint-pill' : 'rose-pill'}">
              ${gOk ? '✓ GOOGLE_MAPS_API_KEY activa' : 'GOOGLE_MAPS_API_KEY pendiente'}
            </span>
          </div>

          <label>Fuente de búsqueda predeterminada</label>
          <select id="cfg-provider">
            <option value="google_places" ${cfg.default_provider === 'google_places' ? 'selected' : ''}>Google Places API (New) · Principal</option>
            <option value="osm" ${cfg.default_provider === 'osm' ? 'selected' : ''}>OpenStreetMap · Gratuita</option>
          </select>

          <label>Máximo de resultados por ruta (5–40)</label>
          <input id="cfg-max-res" type="number" min="5" max="40" value="${cfg.places_max_results || 20}" />

          <label>Máximo de llamadas API por búsqueda (1–5)</label>
          <input id="cfg-max-req" type="number" min="1" max="5" value="${cfg.places_max_requests || 3}" />

          <button class="btn-gold-cta" style="margin-top:12px;padding:13px;font-size:14px;" onclick="saveSettings()">Guardar configuración</button>
        </div>

        <div class="card">
          <div class="card-title">Consumo interno Google Places API (New)</div>
          <div style="font-size:13px;color:var(--muted);">
            Peticiones enviadas: <strong style="color:#fff;">${u.total_requests || 0}</strong>
            (Nearby: ${u.nearby_search || 0} · Text: ${u.text_search || 0} · Details: ${u.place_details || 0})
          </div>
          <button class="circle-btn" style="width:100%;border-radius:12px;margin-top:10px;font-size:12px;font-weight:700;" onclick="resetApiCounter()">Reiniciar contador</button>
        </div>

        <div class="card">
          <div class="card-title">Exportar a Notion / Excel</div>
          <div style="display:flex;gap:8px;">
            <a class="btn-white-pill" style="flex:1;padding:12px;" href="/api/export/csv" download>CSV Notion</a>
            <a class="btn-gold-cta" style="flex:1;padding:12px;font-size:14px;" href="/api/export/excel" download>Excel (.xlsx)</a>
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

    function useGpsLocation() {
      if (!navigator.geolocation) {
        showToast('GPS no disponible en este navegador');
        return;
      }
      navigator.geolocation.getCurrentPosition(pos => {
        ROUTE_FORM.start_lat = pos.coords.latitude;
        ROUTE_FORM.start_lon = pos.coords.longitude;
        ROUTE_FORM.start_label = 'Mi ubicación actual (GPS)';
        showToast('✓ Ubicación GPS fijada');
        render();
      }, err => {
        showToast('No se pudo obtener GPS: ' + err.message);
      }, { enableHighAccuracy: true, timeout: 8000 });
    }

    async function geocodeStreet() {
      const inp = document.getElementById('geo-input');
      const q = inp ? inp.value.trim() : '';
      if (!q) return;
      const res = await fetch('/api/geocode?q=' + encodeURIComponent(q));
      const data = await res.json();
      if (!res.ok || data.error) {
        showToast(data.error || 'Dirección no encontrada');
        return;
      }
      ROUTE_FORM.start_lat = data.lat;
      ROUTE_FORM.start_lon = data.lon;
      ROUTE_FORM.start_label = data.label.split(',')[0];
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

    async function undoLastVisit() {
      const res = await fetch('/api/visits/undo', { method: 'POST' });
      const data = await res.json();
      if (data.state) STATE = data.state;
      showToast('Último registro deshecho');
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
        showToast(data.error || 'Error al consultar Google Places');
        return;
      }
      STATE = data.state;
      showToast('✓ Detalles actualizados');
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
      showToast('✓ Ajustes guardados');
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
      showToast('Contador reiniciado');
      render();
    }

    loadState();
  </script>
</body>
</html>"""
