"""
Servicio unificado de búsqueda y planificación de rutas de prospección para FACENIA Lead Finder.

Coordina:
1. Búsqueda geográfica por coordenadas y radio en metros (prospección a pie) usando el proveedor
   configurado: Google Places API (New) como fuente principal u OpenStreetMap como alternativa.
2. Control de consumo: límite de resultados y peticiones por búsqueda, y prevención de llamadas
   repetidas por clics accidentales o re-renderizados.
3. Normalización, deduplicación por Place ID / source_id y exclusión de negocios descartados.
4. Persistencia en SQLite conservando siempre notas, estados comerciales y verificaciones manuales.
5. Planificación de ruta peatonal optimizada (OSRM Foot / Haversine) y almacenamiento en `routes`.
"""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from config import (
    NO_WEB_STATUSES,
    PLACES_MAX_REQUESTS_PER_SEARCH,
    PLACES_MAX_RESULTS_PER_SEARCH,
    PLACES_MIN_SEARCH_INTERVAL_SECONDS,
    ROUTE_MAX_STOPS,
    WEB_NONE,
    WEB_SOCIAL,
    resolve_sector,
)
from database import (
    find_saved_ids,
    generate_dedup_hash,
    get_discarded_keys,
    get_lead,
    get_route,
    record_search,
    save_lead,
    save_route,
)
from providers import (
    BaseProvider,
    GooglePlacesProvider,
    OSMOverpassProvider,
    provider_registry,
)
from routing_service import haversine_m, plan_walking_route

# Guardia en memoria para evitar peticiones duplicadas por doble clic accidental
_LAST_ROUTE_REQUEST: Dict[str, Any] = {
    "signature": "",
    "timestamp": 0.0,
    "route_id": None,
}


def reset_duplicate_request_guard() -> None:
    """Limpia la guardia de peticiones repetidas (útil en tests automatizados)."""
    _LAST_ROUTE_REQUEST["signature"] = ""
    _LAST_ROUTE_REQUEST["timestamp"] = 0.0
    _LAST_ROUTE_REQUEST["route_id"] = None


def generate_prospecting_route(
    start_lat: float,
    start_lon: float,
    start_label: str,
    radius_m: int,
    categories: List[str],
    requested_stops: int = 15,
    city_hint: str = "Málaga",
    provider_id: str = "osm",
    max_results_limit: Optional[int] = None,
    max_requests_limit: Optional[int] = None,
    api_key: Optional[str] = None,
    exclude_discarded: bool = True,
    exclude_existing_clients: bool = True,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ejecuta el flujo completo de creación de ruta comercial a pie:
    1. Consulta el proveedor elegido (`google_places` u `osm`) sin cambiar silenciosamente de fuente si falla.
    2. Filtra estrictamente por distancia <= radius_m y elimina duplicados por Place ID (`source_id`) y hash.
    3. Excluye negocios marcados previamente como 'Descartado' ('No interesa').
    4. Guarda/actualiza cada negocio en SQLite sin sobrescribir datos comerciales propios del usuario.
    5. Selecciona los mejores candidatos hasta `requested_stops` (sin inventar negocios si hay menos).
    6. Calcula la ruta a pie óptima (OSRM Foot) y la persiste en la base de datos.
    """
    if not categories:
        raise ValueError("Selecciona al menos una categoría de negocio para generar la ruta.")

    req_stops = max(1, min(int(requested_stops), ROUTE_MAX_STOPS))
    rad_m = max(100, int(radius_m))
    prov_clean = (provider_id or "osm").strip().lower()

    # Prevención de peticiones repetidas por doble clic o re-renderizado accidental
    sig = f"{prov_clean}|{float(start_lat):.4f}|{float(start_lon):.4f}|{rad_m}|{req_stops}|{','.join(sorted(categories))}|{db_path or ''}"
    now_ts = time.time()
    if (
        _LAST_ROUTE_REQUEST["signature"] == sig
        and (now_ts - float(_LAST_ROUTE_REQUEST["timestamp"])) < PLACES_MIN_SEARCH_INTERVAL_SECONDS
        and _LAST_ROUTE_REQUEST["route_id"] is not None
    ):
        cached_route = get_route(int(_LAST_ROUTE_REQUEST["route_id"]), db_path=db_path)
        if cached_route:
            return cached_route

    if prov_clean == "google_places":
        gp_provider = GooglePlacesProvider(api_key=api_key, db_path=db_path)
        effective_max_results = (
            int(max_results_limit)
            if max_results_limit is not None
            else max(req_stops, PLACES_MAX_RESULTS_PER_SEARCH)
        )
        effective_max_requests = (
            int(max_requests_limit)
            if max_requests_limit is not None
            else PLACES_MAX_REQUESTS_PER_SEARCH
        )
        # Si Google Places falla (clave ausente/inválida, cuota 429 o red), lanza PlacesAPIError
        # sin cambiar silenciosamente a OpenStreetMap ni simular resultados.
        raw_leads = gp_provider.search_around(
            lat=float(start_lat),
            lon=float(start_lon),
            radius_m=rad_m,
            sectors=categories,
            city_hint=city_hint,
            max_results=effective_max_results,
            max_requests=effective_max_requests,
            progress_callback=progress_callback,
        )
        provider_display_name = gp_provider.name
    elif prov_clean == "osm":
        osm_provider = OSMOverpassProvider()
        raw_leads = osm_provider.search_around(
            lat=float(start_lat),
            lon=float(start_lon),
            radius_m=rad_m,
            sectors=categories,
            city_hint=city_hint,
            max_results=max(req_stops * 5, 100),
            progress_callback=progress_callback,
        )
        provider_display_name = osm_provider.name
    else:
        raise ValueError(f"Proveedor de datos no reconocido: {provider_id}")

    discarded_sids, discarded_hashes = (
        get_discarded_keys(db_path=db_path) if exclude_discarded else (set(), set())
    )

    seen_hashes = set()
    seen_sids = set()
    valid_candidates: List[Dict[str, Any]] = []
    excluded_count = 0

    for item in raw_leads:
        lat = item.get("lat")
        lon = item.get("lon")
        if lat is None or lon is None:
            continue

        # Comprobación estricta del radio geográfico solicitado
        dist_origin = haversine_m(float(start_lat), float(start_lon), float(lat), float(lon))
        if dist_origin > rad_m * 1.05:
            continue

        sid = (item.get("source_id") or item.get("place_id") or "").strip()
        dedup = generate_dedup_hash(item.get("name", ""), item.get("address"), item.get("phone"))

        if (sid and sid in seen_sids) or dedup in seen_hashes:
            continue

        if exclude_discarded and ((sid and sid in discarded_sids) or dedup in discarded_hashes):
            excluded_count += 1
            continue

        if sid:
            seen_sids.add(sid)
        seen_hashes.add(dedup)
        valid_candidates.append(item)

    if progress_callback:
        progress_callback(0.75, f"Guardando y priorizando {len(valid_candidates)} negocios reales...")

    hydrated_candidates: List[Dict[str, Any]] = []
    new_count = 0
    duplicate_count = 0

    for cand in valid_candidates:
        lead_id, is_new = save_lead(cand, db_path=db_path)
        if is_new:
            new_count += 1
        else:
            duplicate_count += 1
        stored = get_lead(lead_id, db_path=db_path)
        if not stored:
            continue
        if exclude_discarded and stored.get("commercial_status") == "Descartado":
            excluded_count += 1
            continue
        if exclude_existing_clients and stored.get("commercial_status") == "Cliente conseguido":
            excluded_count += 1
            continue
        stored["lead_id"] = lead_id
        hydrated_candidates.append(stored)

    if progress_callback:
        progress_callback(0.88, "Calculando orden óptimo de visitas y ruta peatonal...")

    route_plan = plan_walking_route(
        start_lat=float(start_lat),
        start_lon=float(start_lon),
        candidates=hydrated_candidates,
        max_stops=req_stops,
    )

    sector_counts: Dict[str, int] = {}
    for c in hydrated_candidates:
        s = resolve_sector(c.get("sector")) or c.get("sector") or "Otros"
        sector_counts[s] = sector_counts.get(s, 0) + 1

    report = {
        "source": provider_display_name,
        "location_query": start_label,
        "location_resolved": start_label,
        "lat": float(start_lat),
        "lon": float(start_lon),
        "radius_km": round(rad_m / 1000.0, 2),
        "sectors": categories,
        "status": "Completada",
        "total": len(hydrated_candidates),
        "new_count": new_count,
        "duplicate_count": duplicate_count,
        "errors": [],
        "sector_report": [{"sector": k, "count": v} for k, v in sector_counts.items()],
    }
    search_id = record_search(report, hydrated_candidates, db_path=db_path)

    route_meta = {
        "search_id": search_id,
        "start_label": start_label,
        "start_lat": float(start_lat),
        "start_lon": float(start_lon),
        "radius_m": rad_m,
        "requested_stops": req_stops,
        "found_count": len(hydrated_candidates),
        "excluded_count": excluded_count,
        "categories": categories,
        "routing_source": route_plan["routing_source"],
        "routing_note": route_plan["routing_note"],
        "total_distance_m": route_plan["total_distance_m"],
        "total_duration_s": route_plan["total_duration_s"],
        "geometry": route_plan["geometry"],
    }

    route_id = save_route(route_meta, route_plan["stops"], db_path=db_path)

    _LAST_ROUTE_REQUEST["signature"] = sig
    _LAST_ROUTE_REQUEST["timestamp"] = time.time()
    _LAST_ROUTE_REQUEST["route_id"] = route_id

    if progress_callback:
        progress_callback(1.0, "Ruta comercial generada correctamente.")

    saved_route = get_route(route_id, db_path=db_path)
    return saved_route or {**route_meta, "id": route_id, "stops": route_plan["stops"]}


def run_business_search(
    city: str,
    province: str,
    country: str = "España",
    radius_km: float = 25.0,
    selected_sectors: Optional[List[str]] = None,
    target_per_sector: int = 25,
    provider_id: str = "osm",
    filter_no_web_only: bool = False,
    filter_has_phone_only: bool = False,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ejecuta una búsqueda de negocios a través del proveedor seleccionado,
    compara con el CRM para detectar si ya están guardados y persiste el historial.
    """
    sectors = selected_sectors or []
    if not sectors:
        raise ValueError("Debes seleccionar al menos un sector comercial para buscar.")

    city_clean = city.strip()
    if not city_clean:
        raise ValueError("Debes especificar al menos la ciudad para la búsqueda.")

    provider: BaseProvider = provider_registry.get(provider_id)

    raw_results = provider.search(
        city=city_clean,
        province=province.strip(),
        country=country.strip(),
        radius_km=radius_km,
        sectors=sectors,
        target_per_sector=target_per_sector,
        progress_callback=progress_callback,
    )

    filtered_results: List[Dict[str, Any]] = []
    for lead in raw_results:
        if filter_no_web_only:
            status = lead.get("web_status", "")
            if status not in NO_WEB_STATUSES and status not in (WEB_NONE, WEB_SOCIAL):
                continue
        if filter_has_phone_only and not str(lead.get("phone", "")).strip():
            continue
        filtered_results.append(lead)

    saved_ids_map = find_saved_ids(filtered_results, db_path=db_path)
    duplicate_count = 0
    for lead in filtered_results:
        sid = lead.get("source_id", "")
        if sid and sid in saved_ids_map:
            lead["saved_in_crm"] = True
            lead["crm_lead_id"] = saved_ids_map[sid]
            duplicate_count += 1
        else:
            lead["saved_in_crm"] = False
            lead["crm_lead_id"] = None

    new_count = len(filtered_results) - duplicate_count

    sector_counts: Dict[str, int] = {}
    for r in filtered_results:
        s = r.get("sector", "Otros")
        sector_counts[s] = sector_counts.get(s, 0) + 1
    sector_report = [{"sector": k, "count": v} for k, v in sector_counts.items()]

    location_query = f"{city_clean}, {province.strip()}, {country.strip()}".strip(", ")
    report = {
        "source": provider.name,
        "location_query": location_query,
        "location_resolved": location_query,
        "lat": filtered_results[0].get("lat") if filtered_results else None,
        "lon": filtered_results[0].get("lon") if filtered_results else None,
        "radius_km": radius_km,
        "sectors": sectors,
        "status": "Completada",
        "total": len(filtered_results),
        "new_count": new_count,
        "duplicate_count": duplicate_count,
        "errors": [],
        "sector_report": sector_report,
    }

    search_id = record_search(report, filtered_results, db_path=db_path)

    return {
        "search_id": search_id,
        "report": report,
        "leads": filtered_results,
        "total_found": len(filtered_results),
        "provider_name": provider.name,
        "location": location_query,
    }


def record_search_history(
    location: str,
    radius_km: float,
    sectors: List[str],
    total_found: int,
    new_leads: int,
    duplicates: int,
    api_mode: str = "OpenStreetMap",
    db_path: Optional[str] = None,
) -> int:
    """Función de compatibilidad con versiones previas."""
    rep = {
        "source": api_mode,
        "location_query": location,
        "location_resolved": location,
        "radius_km": radius_km,
        "sectors": sectors,
        "status": "Completada",
        "total": total_found,
        "new_count": new_leads,
        "duplicate_count": duplicates,
        "errors": [],
        "sector_report": [],
    }
    return record_search(rep, [], db_path=db_path)
