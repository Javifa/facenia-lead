"""
Servicio de planificación de rutas a pie y enrutamiento peatonal real para FACENIA.

Principios de honestidad técnica:
- Utiliza el servidor público de enrutamiento peatonal OSRM de FOSSGIS
  (https://routing.openstreetmap.de/routed-foot) para obtener trazados por calles
  peatonales, distancias reales de tramo y tiempos estimados a pie.
- Optimiza el orden de las visitas desde el punto de salida mediante vecino más
  cercano + mejora local 2-opt.
- Si el servicio externo de rutas no responde, NUNCA disfraza la distancia en línea
  recta como una ruta peatonal: informa claramente de la limitación, conserva el
  orden geográfico óptimo y ofrece navegación directa en Google Maps.
"""

import math
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus
import requests

from config import HTTP_USER_AGENT, ROUTING_BASE_URL, ROUTING_TIMEOUT


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en metros en línea recta (fórmula de Haversine) entre dos coordenadas."""
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def _build_distance_matrix(points: List[Tuple[float, float]], osrm_matrix: Optional[List[List[float]]] = None) -> List[List[float]]:
    n = len(points)
    if osrm_matrix and len(osrm_matrix) == n and all(len(row) == n for row in osrm_matrix):
        cleaned: List[List[float]] = []
        for i in range(n):
            row_vals: List[float] = []
            for j in range(n):
                val = osrm_matrix[i][j]
                if val is None:
                    val = haversine_m(points[i][0], points[i][1], points[j][0], points[j][1])
                row_vals.append(float(val))
            cleaned.append(row_vals)
        return cleaned

    matrix: List[List[float]] = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_m(points[i][0], points[i][1], points[j][0], points[j][1])
            matrix[i][j] = d
            matrix[j][i] = d
    return matrix


def optimize_stop_order(
    start_lat: float,
    start_lon: float,
    stops: List[Dict[str, Any]],
    osrm_matrix: Optional[List[List[float]]] = None,
) -> List[Dict[str, Any]]:
    """
    Ordena las paradas de la ruta empezando en (start_lat, start_lon) (índice 0)
    para minimizar el recorrido a pie mediante Vecino Más Cercano + 2-opt.
    """
    if len(stops) <= 1:
        return list(stops)

    points: List[Tuple[float, float]] = [(float(start_lat), float(start_lon))]
    for s in stops:
        points.append((float(s["lat"]), float(s["lon"])))

    dist = _build_distance_matrix(points, osrm_matrix=osrm_matrix)
    n_stops = len(stops)
    unvisited = set(range(1, n_stops + 1))
    order: List[int] = []
    curr = 0

    while unvisited:
        nxt = min(unvisited, key=lambda idx: dist[curr][idx])
        order.append(nxt)
        unvisited.remove(nxt)
        curr = nxt

    # Mejora 2-opt para caminos abiertos con origen fijo en 0
    def path_cost(seq: List[int]) -> float:
        total = dist[0][seq[0]]
        for idx in range(len(seq) - 1):
            total += dist[seq[idx]][seq[idx + 1]]
        return total

    improved = True
    iterations = 0
    while improved and iterations < 40:
        improved = False
        iterations += 1
        base_cost = path_cost(order)
        for i in range(len(order) - 1):
            for k in range(i + 1, len(order)):
                candidate = order[:i] + list(reversed(order[i : k + 1])) + order[k + 1 :]
                cand_cost = path_cost(candidate)
                if cand_cost + 0.5 < base_cost:
                    order = candidate
                    base_cost = cand_cost
                    improved = True

    return [stops[idx - 1] for idx in order]


def fetch_osrm_table(points: List[Tuple[float, float]]) -> Optional[List[List[float]]]:
    """
    Consulta la matriz de distancias peatonales reales en OSRM (perfil a pie).
    points es una lista de (lat, lon).
    """
    if len(points) < 2 or len(points) > 45:
        return None

    coords_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    url = f"{ROUTING_BASE_URL.rstrip('/')}/table/v1/driving/{coords_str}"
    params = {"annotations": "distance"}
    headers = {"User-Agent": HTTP_USER_AGENT}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=ROUTING_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok" and "distances" in data:
                return data["distances"]
    except Exception:
        pass
    return None


def fetch_osrm_foot_route(points: List[Tuple[float, float]]) -> Optional[Dict[str, Any]]:
    """
    Obtiene el trazado peatonal real (geometría GeoJSON, distancia y duración por tramo)
    para la secuencia ordenada de puntos [(lat, lon), ...].
    """
    if len(points) < 2:
        return None

    coords_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    url = f"{ROUTING_BASE_URL.rstrip('/')}/route/v1/driving/{coords_str}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }
    headers = {"User-Agent": HTTP_USER_AGENT}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=ROUTING_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            routes = data.get("routes") or []
            if data.get("code") == "Ok" and routes:
                r0 = routes[0]
                raw_coords = (r0.get("geometry") or {}).get("coordinates") or []
                # Convertir de [lon, lat] (GeoJSON) a [lat, lon] para Leaflet
                latlon_geometry = [[float(c[1]), float(c[0])] for c in raw_coords if len(c) >= 2]
                legs = r0.get("legs") or []
                return {
                    "total_distance_m": float(r0.get("distance", 0.0)),
                    "total_duration_s": float(r0.get("duration", 0.0)),
                    "geometry": latlon_geometry,
                    "legs": [
                        {
                            "distance_m": float(leg.get("distance", 0.0)),
                            "duration_s": float(leg.get("duration", 0.0)),
                        }
                        for leg in legs
                    ],
                }
    except Exception:
        pass
    return None


def plan_walking_route(
    start_lat: float,
    start_lon: float,
    candidates: List[Dict[str, Any]],
    max_stops: int = 15,
    use_external_routing: bool = True,
) -> Dict[str, Any]:
    """
    Selecciona los mejores candidatos por puntuación de oportunidad (y cercanía en caso de empate),
    optimiza el orden de visita a pie y calcula la ruta peatonal real con OSRM.
    """
    valid_candidates = [
        c for c in candidates if c.get("lat") is not None and c.get("lon") is not None
    ]

    for c in valid_candidates:
        c["origin_straight_m"] = round(
            haversine_m(start_lat, start_lon, float(c["lat"]), float(c["lon"])), 1
        )

    # Priorizar mayor oportunidad comercial y menor distancia al punto de salida
    sorted_by_priority = sorted(
        valid_candidates,
        key=lambda x: (-int(x.get("score", 0)), float(x.get("origin_straight_m", 999999.0))),
    )
    selected = sorted_by_priority[: max(1, int(max_stops))]

    if not selected:
        return {
            "stops": [],
            "routing_source": "Sin paradas",
            "routing_note": "No se encontraron negocios con coordenadas válidas en este radio.",
            "total_distance_m": None,
            "total_duration_s": None,
            "geometry": [],
            "has_real_route": False,
        }

    initial_points = [(float(start_lat), float(start_lon))] + [
        (float(s["lat"]), float(s["lon"])) for s in selected
    ]
    osrm_matrix = fetch_osrm_table(initial_points) if use_external_routing else None
    ordered_stops = optimize_stop_order(start_lat, start_lon, selected, osrm_matrix=osrm_matrix)

    ordered_points = [(float(start_lat), float(start_lon))] + [
        (float(s["lat"]), float(s["lon"])) for s in ordered_stops
    ]
    osrm_route = fetch_osrm_foot_route(ordered_points) if use_external_routing else None

    prev_lat, prev_lon = float(start_lat), float(start_lon)
    enriched_stops: List[Dict[str, Any]] = []

    for idx, stop in enumerate(ordered_stops):
        s_copy = dict(stop)
        s_copy["position"] = idx + 1
        s_lat, s_lon = float(s_copy["lat"]), float(s_copy["lon"])
        straight_m = round(haversine_m(prev_lat, prev_lon, s_lat, s_lon), 1)
        s_copy["straight_distance_m"] = straight_m

        if osrm_route and idx < len(osrm_route["legs"]):
            leg = osrm_route["legs"][idx]
            s_copy["leg_distance_m"] = round(leg["distance_m"], 1)
            s_copy["leg_duration_s"] = round(leg["duration_s"], 1)
        else:
            s_copy["leg_distance_m"] = None
            s_copy["leg_duration_s"] = None

        enriched_stops.append(s_copy)
        prev_lat, prev_lon = s_lat, s_lon

    if osrm_route:
        return {
            "stops": enriched_stops,
            "routing_source": "OSRM Foot (OpenStreetMap)",
            "routing_note": "Ruta peatonal calculada sobre calles y pasos peatonales de OpenStreetMap.",
            "total_distance_m": round(osrm_route["total_distance_m"], 1),
            "total_duration_s": round(osrm_route["total_duration_s"], 1),
            "geometry": osrm_route["geometry"],
            "has_real_route": True,
        }

    return {
        "stops": enriched_stops,
        "routing_source": "Ordenación por proximidad (OSRM no disponible)",
        "routing_note": (
            "El servicio externo de trazado peatonal no está disponible en este momento. "
            "Se muestra el orden óptimo de visitas y la distancia en línea recta como referencia; "
            "usa el botón de Google Maps para navegar calle a calle."
        ),
        "total_distance_m": None,
        "total_duration_s": None,
        "geometry": [],
        "has_real_route": False,
    }


def format_distance(meters: Optional[float]) -> str:
    """Formatea una distancia en metros de manera clara ('350 m' o '1,4 km')."""
    if meters is None:
        return "—"
    m = float(meters)
    if m < 1000:
        return f"{int(round(m))} m"
    return f"{m / 1000.0:.1f} km".replace(".", ",")


def format_duration(seconds: Optional[float]) -> str:
    """Formatea una duración en segundos a minutos u horas ('14 min' o '1 h 10 min')."""
    if seconds is None:
        return "—"
    mins = max(1, int(round(float(seconds) / 60.0)))
    if mins < 60:
        return f"{mins} min"
    hours = mins // 60
    rem = mins % 60
    return f"{hours} h {rem} min" if rem else f"{hours} h"


def build_google_maps_place_url(lead: Dict[str, Any]) -> str:
    """
    Construye un enlace válido para abrir la ubicación o búsqueda del negocio en Google Maps
    y lanzar las indicaciones a pie desde el móvil.
    """
    name = (lead.get("name") or "").strip()
    address = (lead.get("address") or "").strip()
    city = (lead.get("city") or "Málaga").strip()
    lat = lead.get("lat")
    lon = lead.get("lon")

    query_parts = [p for p in [name, address, city] if p]
    query_str = ", ".join(query_parts)

    if query_str:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query_str)}"
    if lat is not None and lon is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    return "https://www.google.com/maps"


def build_google_maps_directions_to_lead(
    lead: Dict[str, Any],
    origin_lat: Optional[float] = None,
    origin_lon: Optional[float] = None,
) -> str:
    """Construye un enlace de indicaciones a pie en Google Maps hacia un negocio."""
    lat = lead.get("lat")
    lon = lead.get("lon")
    name = (lead.get("name") or "").strip()
    address = (lead.get("address") or "").strip()
    city = (lead.get("city") or "Málaga").strip()

    if lat is not None and lon is not None:
        dest = f"{lat},{lon}"
    else:
        dest = quote_plus(", ".join([p for p in [name, address, city] if p]))

    url = f"https://www.google.com/maps/dir/?api=1&destination={dest}&travelmode=walking"
    if origin_lat is not None and origin_lon is not None:
        url += f"&origin={origin_lat},{origin_lon}"
    return url


def build_google_maps_walking_route_url(
    start_lat: float,
    start_lon: float,
    stops: List[Dict[str, Any]],
) -> str:
    """
    Construye un enlace de ruta completa a pie en Google Maps con punto de salida y paradas.
    (Google Maps admite hasta 9 waypoints intermedios en URLs estándar).
    """
    valid = [s for s in stops if s.get("lat") is not None and s.get("lon") is not None]
    if not valid:
        return f"https://www.google.com/maps/search/?api=1&query={start_lat},{start_lon}"

    # Tomar hasta las primeras 10 paradas para cumplir el límite de URL de Google Maps
    capped = valid[:10]
    dest = capped[-1]
    dest_str = f"{dest['lat']},{dest['lon']}"
    url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={start_lat},{start_lon}"
        f"&destination={dest_str}"
        f"&travelmode=walking"
    )
    if len(capped) > 1:
        waypoints = "|".join(f"{s['lat']},{s['lon']}" for s in capped[:-1])
        url += f"&waypoints={quote_plus(waypoints)}"
    return url
