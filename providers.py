"""
Arquitectura modular de proveedores de datos para FACENIA Lead Finder.

Implementa:
- OSMOverpassProvider: Proveedor principal, gratuito y abierto (Nominatim + Overpass API).
- DemoProvider: Proveedor de pruebas offline.
- GooglePlacesProvider: Proveedor comercial opcional (desactivado por defecto).
"""

from abc import ABC, abstractmethod
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import requests

from config import (
    GOOGLE_PLACES_API_KEY,
    HTTP_TIMEOUT,
    HTTP_USER_AGENT,
    MAX_ELEMENTS_PER_SECTOR,
    MAX_RADIUS_KM,
    NOMINATIM_REVERSE_URL,
    NOMINATIM_URL,
    OVERPASS_ENDPOINTS,
    OVERPASS_QUERY_TIMEOUT,
    PAUSE_BETWEEN_QUERIES,
    PLACES_MAX_REQUESTS_PER_SEARCH,
    PLACES_MAX_RESULTS_PER_SEARCH,
    PROVIDER_DEMO,
    PROVIDER_GOOGLE,
    PROVIDER_OSM,
    SECTORS,
    WEB_INSUFFICIENT,
    WEB_NONE,
    WEB_SOCIAL,
    get_google_maps_api_key,
    resolve_sector,
    sector_info,
)
from database import get_geo_cache, get_lead, save_lead, set_geo_cache
from demo_data import DEMO_LEADS_DATA
from scoring import calculate_lead_score
from verification import classify_web_status


class BaseProvider(ABC):
    """Clase base para todos los proveedores de datos de prospección."""

    def __init__(self, provider_id: str, name: str, is_free: bool = True, requires_api_key: bool = False):
        self.provider_id = provider_id
        self.name = name
        self.is_free = is_free
        self.requires_api_key = requires_api_key

    @abstractmethod
    def search(
        self,
        city: str,
        province: str,
        country: str,
        radius_km: float,
        sectors: List[str],
        target_per_sector: int = 25,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> List[Dict[str, Any]]:
        """Ejecuta la búsqueda y retorna una lista de prospectos normalizados."""
        pass

    @abstractmethod
    def validate_connection(self) -> Tuple[bool, str]:
        """Comprueba la disponibilidad o credenciales del servicio."""
        pass


class OSMOverpassProvider(BaseProvider):
    """
    Proveedor principal 100% gratuito y abierto basado en OpenStreetMap.
    Utiliza Nominatim para geocodificar la ubicación y Overpass API para consultar negocios.
    Respeta la política de uso de OSM con caché local, User-Agent corporativo y reintentos.
    """

    def __init__(self):
        super().__init__(
            provider_id="osm",
            name=PROVIDER_OSM,
            is_free=True,
            requires_api_key=False
        )

    def geocode_location(self, city: str, province: str = "", country: str = "España") -> Optional[Tuple[float, float, str]]:
        """
        Geocodifica una ubicación usando Nominatim con caché en SQLite.
        Retorna (latitud, longitud, display_name) o None si no se encuentra.
        """
        parts = [p.strip() for p in (city, province, country) if p and p.strip()]
        query_key = ", ".join(parts)

        # 1. Comprobar caché local permanente
        cached = get_geo_cache(query_key)
        if cached:
            return float(cached["lat"]), float(cached["lon"]), cached.get("display_name", query_key)

        # 2. Petición respetuosa a Nominatim
        params = {
            "q": query_key,
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        }
        headers = {
            "User-Agent": HTTP_USER_AGENT
        }

        try:
            time.sleep(1.0)
            resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    first = data[0]
                    lat = float(first["lat"])
                    lon = float(first["lon"])
                    display_name = first.get("display_name", query_key)
                    bbox_json = json.dumps(first.get("boundingbox", []))

                    set_geo_cache(query_key, lat, lon, bbox_json, display_name)
                    return lat, lon, display_name
        except Exception:
            pass

        return None

    def search_locations(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Busca ubicaciones candidatas (calles, barrios, municipios) en Nominatim
        para que el usuario pueda elegir y confirmar el punto de salida exacto.
        """
        q_clean = (query or "").strip()
        if not q_clean:
            return []

        cache_key = f"candidates::{q_clean.lower()}::{limit}"
        cached = get_geo_cache(cache_key)
        if cached and cached.get("bounding_box_json"):
            try:
                parsed = json.loads(cached["bounding_box_json"])
                if isinstance(parsed, list) and parsed:
                    return parsed
            except Exception:
                pass

        # Si no menciona provincia/país, priorizar España sin excluir búsquedas explícitas
        search_q = q_clean if "españa" in q_clean.lower() else f"{q_clean}, España"
        params = {
            "q": search_q,
            "format": "json",
            "limit": max(1, min(limit, 8)),
            "addressdetails": 1,
        }
        headers = {"User-Agent": HTTP_USER_AGENT}

        candidates: List[Dict[str, Any]] = []
        try:
            time.sleep(0.8)
            resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=12)
            if resp.status_code == 200:
                items = resp.json() or []
                for item in items:
                    lat = float(item["lat"])
                    lon = float(item["lon"])
                    display = item.get("display_name", q_clean)
                    addr = item.get("address") or {}
                    city = (
                        addr.get("city")
                        or addr.get("town")
                        or addr.get("village")
                        or addr.get("municipality")
                        or "Málaga"
                    )
                    suburb = addr.get("suburb") or addr.get("neighbourhood") or addr.get("quarter") or ""
                    road = addr.get("road") or addr.get("pedestrian") or addr.get("square") or ""
                    house_number = addr.get("house_number") or ""
                    short_parts = [p for p in [f"{road} {house_number}".strip(), suburb, city] if p]
                    short_label = ", ".join(dict.fromkeys(short_parts)) if short_parts else display.split(",")[0]
                    candidates.append(
                        {
                            "label": short_label,
                            "display_name": display,
                            "lat": lat,
                            "lon": lon,
                            "city": city,
                        }
                    )
                if candidates:
                    first = candidates[0]
                    set_geo_cache(
                        cache_key,
                        first["lat"],
                        first["lon"],
                        json.dumps(candidates, ensure_ascii=False),
                        first["display_name"],
                    )
        except Exception:
            pass

        return candidates

    def reverse_geocode(self, lat: float, lon: float) -> str:
        """
        Obtiene una etiqueta legible para unas coordenadas GPS usando Nominatim Reverse.
        """
        cache_key = f"rev::{lat:.5f},{lon:.5f}"
        cached = get_geo_cache(cache_key)
        if cached and cached.get("display_name"):
            return cached["display_name"]

        params = {
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
            "format": "json",
            "zoom": 18,
            "addressdetails": 1,
        }
        headers = {"User-Agent": HTTP_USER_AGENT}
        try:
            resp = requests.get(NOMINATIM_REVERSE_URL, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json() or {}
                addr = data.get("address") or {}
                road = addr.get("road") or addr.get("pedestrian") or addr.get("square") or ""
                num = addr.get("house_number") or ""
                suburb = addr.get("suburb") or addr.get("neighbourhood") or ""
                city = addr.get("city") or addr.get("town") or addr.get("village") or ""
                parts = [p for p in [f"{road} {num}".strip(), suburb, city] if p]
                label = ", ".join(dict.fromkeys(parts)) or data.get("display_name") or f"{lat:.5f}, {lon:.5f}"
                set_geo_cache(cache_key, lat, lon, "[]", label)
                return label
        except Exception:
            pass

        return f"Ubicación GPS ({lat:.4f}, {lon:.4f})"

    def _query_overpass_endpoint(self, overpass_query: str) -> Optional[Dict[str, Any]]:
        """Ejecuta la consulta Overpass QL con tolerancia a fallos y rotación de servidores espejo."""
        headers = {
            "User-Agent": HTTP_USER_AGENT
        }

        for endpoint in OVERPASS_ENDPOINTS:
            try:
                resp = requests.post(
                    endpoint,
                    data={"data": overpass_query},
                    headers=headers,
                    timeout=HTTP_TIMEOUT
                )
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code in (429, 504):
                    continue
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                continue

        return None

    def _build_overpass_query(
        self,
        tags: List[Tuple[str, List[str]]],
        radius_meters: int,
        lat: float,
        lon: float,
        limit: int
    ) -> str:
        """Construye un bloque Overpass QL eficiente para los tags especificados."""
        statements = []
        for key, values in tags:
            if not values:
                continue
            if len(values) == 1:
                val = values[0]
                statements.append(f'node["{key}"="{val}"](around:{radius_meters},{lat},{lon});')
                statements.append(f'way["{key}"="{val}"](around:{radius_meters},{lat},{lon});')
            else:
                regex_val = "^(" + "|".join(values) + ")$"
                statements.append(f'node["{key}"~"{regex_val}"](around:{radius_meters},{lat},{lon});')
                statements.append(f'way["{key}"~"{regex_val}"](around:{radius_meters},{lat},{lon});')

        body = "\n  ".join(statements)
        return f"""[out:json][timeout:{OVERPASS_QUERY_TIMEOUT}];
(
  {body}
);
out center {limit};"""

    def _match_category_for_tags(
        self,
        tags_dict: Dict[str, Any],
        active_categories: List[str],
        default_category: str = "Otros negocios locales",
    ) -> str:
        """Determina a cuál de las categorías seleccionadas pertenece un elemento OSM."""
        for cat_name in active_categories:
            resolved = resolve_sector(cat_name) or cat_name
            info = SECTORS.get(resolved, {})
            for key, values in info.get("tags", []):
                val = tags_dict.get(key)
                if val and val in values:
                    return resolved
        # Comprobar contra todas las categorías conocidas si alguna coincide
        for cat_name, info in SECTORS.items():
            for key, values in info.get("tags", []):
                val = tags_dict.get(key)
                if val and val in values:
                    return cat_name
        return resolve_sector(default_category) or default_category

    def _parse_osm_element(
        self,
        elem: Dict[str, Any],
        sector_name: str,
        default_city: str = "Málaga",
    ) -> Optional[Dict[str, Any]]:
        """Normaliza un elemento de Overpass API en un diccionario de negocio."""
        elem_id = elem.get("id")
        elem_type = elem.get("type", "node")
        source_id = f"osm_{elem_type}_{elem_id}"

        tags_dict = elem.get("tags", {})
        name = (
            tags_dict.get("name")
            or tags_dict.get("brand")
            or tags_dict.get("operator")
            or ""
        ).strip()

        if not name:
            return None

        elem_lat = elem.get("lat")
        elem_lon = elem.get("lon")
        if elem_lat is None and "center" in elem and isinstance(elem["center"], dict):
            elem_lat = elem["center"].get("lat")
            elem_lon = elem["center"].get("lon")

        if elem_lat is None or elem_lon is None:
            return None

        street = tags_dict.get("addr:street", "").strip()
        housenumber = tags_dict.get("addr:housenumber", "").strip()
        full_address = f"{street} {housenumber}".strip() if street else (tags_dict.get("addr:full", "").strip())
        elem_city = (
            tags_dict.get("addr:city")
            or tags_dict.get("addr:suburb")
            or tags_dict.get("addr:town")
            or default_city
        ).strip()
        postal_code = tags_dict.get("addr:postcode", "").strip()

        phone = (
            tags_dict.get("contact:phone")
            or tags_dict.get("phone")
            or tags_dict.get("contact:mobile")
            or tags_dict.get("mobile")
            or ""
        ).strip()

        email = (
            tags_dict.get("contact:email")
            or tags_dict.get("email")
            or ""
        ).strip()

        website = (
            tags_dict.get("contact:website")
            or tags_dict.get("website")
            or tags_dict.get("url")
            or ""
        ).strip()

        social_url = (
            tags_dict.get("contact:instagram")
            or tags_dict.get("instagram")
            or tags_dict.get("contact:facebook")
            or tags_dict.get("facebook")
            or tags_dict.get("contact:linkedin")
            or ""
        ).strip()

        opening_hours = tags_dict.get("opening_hours", "").strip()
        brand = (tags_dict.get("brand") or "").strip()
        is_chain = int(bool(tags_dict.get("brand:wikidata") or tags_dict.get("brand:wikipedia") or brand))

        web_status, verif_detail = classify_web_status(website, social_url=social_url)
        if web_status == WEB_SOCIAL and not social_url and website:
            social_url = website
            website = ""

        # Si no hay web, red social, teléfono, email, dirección ni horario, marcar como información insuficiente
        if (
            web_status == WEB_NONE
            and not phone
            and not email
            and not full_address
            and not opening_hours
        ):
            web_status = WEB_INSUFFICIENT
            verif_detail = "Solo consta nombre y coordenadas en OpenStreetMap; verificar datos en visita presencial."

        map_url = f"https://www.openstreetmap.org/{elem_type}/{elem_id}"

        resolved_sector = resolve_sector(sector_name) or sector_name

        lead_dict: Dict[str, Any] = {
            "source": "OpenStreetMap",
            "source_id": source_id,
            "name": name,
            "sector": resolved_sector,
            "address": full_address,
            "city": elem_city,
            "postal_code": postal_code,
            "phone": phone,
            "email": email,
            "website": website,
            "social_url": social_url,
            "opening_hours": opening_hours,
            "map_url": map_url,
            "lat": float(elem_lat),
            "lon": float(elem_lon),
            "brand": brand,
            "is_chain": is_chain,
            "web_status": web_status,
            "verification_status": verif_detail,
            "data_source": "OpenStreetMap (Overpass API)",
        }

        score, priority, reasons = calculate_lead_score(lead_dict)
        lead_dict["score"] = score
        lead_dict["priority"] = priority
        lead_dict["scoring_reasons"] = reasons
        return lead_dict

    def search_around(
        self,
        lat: float,
        lon: float,
        radius_m: int,
        sectors: List[str],
        city_hint: str = "Málaga",
        max_results: int = 200,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Busca negocios reales en un radio exacto en metros alrededor de (lat, lon).
        Para radios peatonales (<= 2500 m) combina las etiquetas de las categorías seleccionadas
        en una única consulta Overpass QL rápida y respetuosa con el servidor público.
        """
        resolved_sectors: List[str] = []
        for s in sectors:
            r = resolve_sector(s) or s
            if r in SECTORS and r not in resolved_sectors:
                resolved_sectors.append(r)

        if not resolved_sectors:
            resolved_sectors = list(SECTORS.keys())[:3]

        radius_meters = max(100, min(int(radius_m), int(MAX_RADIUS_KM * 1000)))

        # Agrupar tags por clave OSM para optimizar la consulta combinada
        merged_tags_map: Dict[str, List[str]] = {}
        for sec_name in resolved_sectors:
            info = SECTORS.get(sec_name, {})
            for key, values in info.get("tags", []):
                bucket = merged_tags_map.setdefault(key, [])
                for v in values:
                    if v not in bucket:
                        bucket.append(v)

        merged_tags: List[Tuple[str, List[str]]] = list(merged_tags_map.items())
        results: List[Dict[str, Any]] = []
        seen_source_ids = set()

        if progress_callback:
            progress_callback(0.15, f"Consultando OpenStreetMap en un radio de {radius_meters} m...")

        query_limit = min(MAX_ELEMENTS_PER_SECTOR, max(max_results * 3, 120))
        overpass_query = self._build_overpass_query(merged_tags, radius_meters, lat, lon, query_limit)
        data = self._query_overpass_endpoint(overpass_query)

        if data and "elements" in data:
            elements = data.get("elements", [])
            if progress_callback:
                progress_callback(0.65, f"Analizando {len(elements)} registros encontrados en la zona...")

            for elem in elements:
                elem_id = elem.get("id")
                elem_type = elem.get("type", "node")
                source_id = f"osm_{elem_type}_{elem_id}"
                if source_id in seen_source_ids:
                    continue

                tags_dict = elem.get("tags", {})
                matched_sector = self._match_category_for_tags(
                    tags_dict, resolved_sectors, default_category=resolved_sectors[0]
                )
                lead_dict = self._parse_osm_element(elem, matched_sector, default_city=city_hint)
                if not lead_dict:
                    continue

                seen_source_ids.add(source_id)
                results.append(lead_dict)
                if len(results) >= max_results:
                    break
        else:
            # Fallback por categoría si la consulta combinada dio timeout
            total_sectors = max(1, len(resolved_sectors))
            per_sec_target = max(10, int(max_results / total_sectors) + 5)
            for idx, sec_name in enumerate(resolved_sectors):
                if progress_callback:
                    pct = 0.2 + (0.7 * (idx / total_sectors))
                    progress_callback(pct, f"Consultando categoría: {sec_name}...")
                tags = SECTORS.get(sec_name, {}).get("tags", [])
                if not tags:
                    continue
                q = self._build_overpass_query(tags, radius_meters, lat, lon, per_sec_target * 2)
                sec_data = self._query_overpass_endpoint(q)
                if not sec_data or "elements" not in sec_data:
                    time.sleep(PAUSE_BETWEEN_QUERIES)
                    continue
                for elem in sec_data.get("elements", []):
                    source_id = f"osm_{elem.get('type', 'node')}_{elem.get('id')}"
                    if source_id in seen_source_ids:
                        continue
                    lead_dict = self._parse_osm_element(elem, sec_name, default_city=city_hint)
                    if not lead_dict:
                        continue
                    seen_source_ids.add(source_id)
                    results.append(lead_dict)
                time.sleep(PAUSE_BETWEEN_QUERIES)

        if progress_callback:
            progress_callback(1.0, f"Búsqueda completada: {len(results)} negocios localizados.")

        return results

    def search(
        self,
        city: str,
        province: str,
        country: str,
        radius_km: float,
        sectors: List[str],
        target_per_sector: int = 25,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        seen_source_ids = set()

        if progress_callback:
            progress_callback(0.05, f"Geocodificando {city}...")

        coords = self.geocode_location(city, province, country)
        if not coords:
            coords = self.geocode_location(city, "", country)

        if not coords:
            raise ValueError(f"No se pudieron obtener las coordenadas para: {city}, {province}. Revisa la ortografía.")

        lat, lon, _ = coords
        radius_meters = int(min(radius_km, MAX_RADIUS_KM) * 1000)
        total_sectors = max(1, len(sectors))

        for idx, sector_name in enumerate(sectors):
            if progress_callback:
                pct = 0.1 + (0.85 * (idx / total_sectors))
                progress_callback(pct, f"Consultando OpenStreetMap: {sector_name}...")

            info = sector_info(sector_name)
            resolved_name = resolve_sector(sector_name) or sector_name
            if not info:
                for k, v in SECTORS.items():
                    if sector_name.lower() in k.lower() or k.lower() in sector_name.lower():
                        info = v
                        resolved_name = k
                        break
            if not info:
                continue

            tags = info.get("tags", [])
            if not tags:
                continue

            query_limit = min(MAX_ELEMENTS_PER_SECTOR, max(target_per_sector * 2, 40))
            overpass_query = self._build_overpass_query(tags, radius_meters, lat, lon, query_limit)

            data = self._query_overpass_endpoint(overpass_query)
            if not data or "elements" not in data:
                time.sleep(PAUSE_BETWEEN_QUERIES)
                continue

            elements = data.get("elements", [])
            sector_collected = 0

            for elem in elements:
                if sector_collected >= target_per_sector:
                    break

                elem_id = elem.get("id")
                elem_type = elem.get("type", "node")
                source_id = f"osm_{elem_type}_{elem_id}"

                if source_id in seen_source_ids:
                    continue

                lead_dict = self._parse_osm_element(elem, resolved_name, default_city=city)
                if not lead_dict:
                    continue

                seen_source_ids.add(source_id)
                results.append(lead_dict)
                sector_collected += 1

            time.sleep(PAUSE_BETWEEN_QUERIES)

        if progress_callback:
            progress_callback(1.0, f"Búsqueda finalizada: {len(results)} negocios encontrados.")

        return results

    def validate_connection(self) -> Tuple[bool, str]:
        """Verifica la conectividad básica con OpenStreetMap Overpass."""
        test_query = "[out:json][timeout:5]; node(around:100,36.7213,-4.4214); out count;"
        data = self._query_overpass_endpoint(test_query)
        if data is not None:
            return True, "Conexión operativa con OpenStreetMap y Overpass API."
        return False, "Los servidores de OpenStreetMap Overpass no responden en este momento."


class DemoProvider(BaseProvider):
    """Proveedor para pruebas inmediatas offline utilizando datos de muestra de Málaga."""

    def __init__(self):
        super().__init__(
            provider_id="demo",
            name=PROVIDER_DEMO,
            is_free=True,
            requires_api_key=False
        )

    def search(
        self,
        city: str,
        province: str,
        country: str,
        radius_km: float,
        sectors: List[str],
        target_per_sector: int = 25,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> List[Dict[str, Any]]:
        if progress_callback:
            progress_callback(0.3, "Cargando catálogo local de demostración...")

        resolved_filter = {resolve_sector(s) or s for s in (sectors or [])}
        results: List[Dict[str, Any]] = []
        for raw in DEMO_LEADS_DATA:
            lead_sector = resolve_sector(raw.get("sector", "")) or raw.get("sector", "")
            matches = (
                not sectors
                or lead_sector in resolved_filter
                or any(
                    s.lower() in lead_sector.lower() or lead_sector.lower() in s.lower()
                    for s in sectors
                )
            )
            if matches:
                item = dict(raw)
                item["sector"] = lead_sector
                item["source"] = "Demostración"
                item["source_id"] = raw.get("place_id", f"demo_{len(results)}")
                item["map_url"] = raw.get("google_maps_url", "")
                item["social_url"] = ""
                item["email"] = ""
                item["opening_hours"] = ""
                item["is_demo"] = 1

                web_status, verif_detail = classify_web_status(item.get("website", ""))
                item["web_status"] = web_status
                item["verification_status"] = verif_detail

                score, priority, reasons = calculate_lead_score(item)
                item["score"] = score
                item["priority"] = priority
                item["scoring_reasons"] = reasons

                results.append(item)

        time.sleep(0.2)
        if progress_callback:
            progress_callback(1.0, f"Demostración lista: {len(results)} prospectos disponibles.")

        return results

    def validate_connection(self) -> Tuple[bool, str]:
        return True, "Catálogo de demostración local disponible sin conexión."


class GooglePlacesProvider(BaseProvider):
    """
    Proveedor oficial de Google Places API (New) v1.
    Soporta:
    - Nearby Search (New) por coordenadas, radio y tipos de establecimiento (Tabla A).
    - Text Search (New) para categorías específicas o búsquedas por texto.
    - Place Details (New) bajo demanda al abrir la ficha de un negocio individual.
    """

    def __init__(self, api_key: Optional[str] = None, db_path: Optional[str] = None):
        super().__init__(
            provider_id="google_places",
            name=PROVIDER_GOOGLE,
            is_free=False,
            requires_api_key=True,
        )
        self._explicit_api_key = api_key
        self.db_path = db_path

    @property
    def api_key(self) -> str:
        if self._explicit_api_key is not None:
            return self._explicit_api_key.strip()
        return get_google_maps_api_key()

    @staticmethod
    def _match_category_for_place(
        raw_place: Dict[str, Any],
        active_categories: List[str],
        default_category: str = "Otros negocios locales",
    ) -> str:
        """Asigna la categoría comercial de BuscaLeads más acorde a los `types` de Google Places."""
        place_types = set(raw_place.get("types") or [])
        primary = (raw_place.get("primaryType") or "").strip()
        if primary:
            place_types.add(primary)

        for cat_name in active_categories:
            resolved = resolve_sector(cat_name) or cat_name
            info = SECTORS.get(resolved, {})
            g_types = set(info.get("google_types") or [])
            if place_types & g_types:
                return resolved

        for cat_name, info in SECTORS.items():
            g_types = set(info.get("google_types") or [])
            if place_types & g_types:
                return cat_name

        return resolve_sector(default_category) or default_category

    @staticmethod
    def _enrich_and_score_place(
        raw_place: Dict[str, Any],
        sector_name: str,
        default_city: str = "Málaga",
    ) -> Optional[Dict[str, Any]]:
        """Normaliza, clasifica el estado web con rigor y calcula la prioridad comercial (0–100)."""
        from places_client import PlacesClient

        resolved_sector = resolve_sector(sector_name) or sector_name
        parsed = PlacesClient.parse_place_record(raw_place, resolved_sector, default_city=default_city)
        if not parsed.get("name"):
            return None

        # Excluir negocios cerrados permanentemente si Google Places lo indica
        if parsed.get("business_status") == "CLOSED_PERMANENTLY":
            return None

        website_raw = (parsed.get("website") or "").strip()
        web_status, verif_detail = classify_web_status(website_raw)
        if web_status == WEB_SOCIAL and website_raw:
            parsed["social_url"] = website_raw
            parsed["website"] = ""
        elif web_status == WEB_NONE:
            verif_detail = "Sin web identificada en Google Places; pendiente de confirmar con el negocio."

        parsed["web_status"] = web_status
        parsed["verification_status"] = verif_detail

        score, priority, reasons = calculate_lead_score(parsed)
        parsed["score"] = score
        parsed["priority"] = priority
        parsed["scoring_reasons"] = reasons
        return parsed

    def search_around(
        self,
        lat: float,
        lon: float,
        radius_m: int,
        sectors: List[str],
        city_hint: str = "Málaga",
        max_results: int = PLACES_MAX_RESULTS_PER_SEARCH,
        max_requests: int = PLACES_MAX_REQUESTS_PER_SEARCH,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Busca negocios alrededor de (lat, lon) en `radius_m` metros utilizando Google Places API (New)
        con control estricto de consumo:
        1. Agrupa los tipos `includedTypes` compatibles con Nearby Search (New) en una sola llamada.
        2. Para categorías que requieren Text Search (New), ejecuta consultas controladas sin superar `max_requests`.
        3. Deduplica por `place_id` oficial de Google Places.
        """
        from places_client import PlacesAPIError, PlacesClient

        key = self.api_key
        if not key:
            raise PlacesAPIError(
                "No se ha configurado GOOGLE_MAPS_API_KEY. Configura tu clave en el archivo .env "
                "o selecciona explícitamente OpenStreetMap como proveedor.",
                error_type="missing_key",
            )

        client = PlacesClient(api_key=key, db_path=self.db_path)

        resolved_sectors: List[str] = []
        for s in sectors:
            r = resolve_sector(s) or s
            if r in SECTORS and r not in resolved_sectors:
                resolved_sectors.append(r)
        if not resolved_sectors:
            resolved_sectors = list(SECTORS.keys())[:3]

        clamped_radius = max(100, min(int(radius_m), int(MAX_RADIUS_KM * 1000)))
        clamped_max_results = max(5, min(int(max_results), 60))
        clamped_max_requests = max(1, min(int(max_requests), 10))

        nearby_types: List[str] = []
        text_search_sectors: List[str] = []

        for sec_name in resolved_sectors:
            info = SECTORS.get(sec_name, {})
            if info.get("use_text_search"):
                text_search_sectors.append(sec_name)
            for gt in info.get("google_types") or []:
                if gt not in nearby_types:
                    nearby_types.append(gt)

        results: List[Dict[str, Any]] = []
        seen_place_ids = set()
        requests_made = 0

        # 1. Petición Nearby Search (New) combinada para todos los tipos seleccionados
        if nearby_types and requests_made < clamped_max_requests:
            if progress_callback:
                progress_callback(0.20, f"Consultando Google Places Nearby Search (New) en {clamped_radius} m...")

            nearby_resp = client.search_nearby(
                lat=float(lat),
                lon=float(lon),
                radius_meters=float(clamped_radius),
                included_types=nearby_types[:50],
                max_results=min(20, clamped_max_results),
            )
            requests_made += 1

            for raw in nearby_resp.get("places", []):
                pid = (raw.get("id") or "").strip()
                if pid and pid in seen_place_ids:
                    continue
                matched_sec = self._match_category_for_place(
                    raw, resolved_sectors, default_category=resolved_sectors[0]
                )
                lead_dict = self._enrich_and_score_place(raw, matched_sec, default_city=city_hint)
                if not lead_dict:
                    continue
                if pid:
                    seen_place_ids.add(pid)
                results.append(lead_dict)
                if len(results) >= clamped_max_results:
                    break

        # 2. Peticiones Text Search (New) para categorías especializadas (o si faltan candidatos dentro del presupuesto)
        remaining_sectors = list(text_search_sectors)
        if len(results) < min(10, clamped_max_results) and requests_made < clamped_max_requests:
            for s in resolved_sectors:
                if s not in remaining_sectors:
                    remaining_sectors.append(s)

        for sec_name in remaining_sectors:
            if len(results) >= clamped_max_results or requests_made >= clamped_max_requests:
                break

            info = SECTORS.get(sec_name, {})
            query_text = info.get("google_text_query") or sec_name
            if progress_callback:
                pct = min(0.85, 0.35 + 0.45 * (requests_made / clamped_max_requests))
                progress_callback(pct, f"Consultando Google Places Text Search (New): {sec_name}...")

            needed = max(5, min(20, clamped_max_results - len(results)))
            text_resp = client.search_places_text(
                query=f"{query_text} en {city_hint}",
                location_bias_coords=(float(lat), float(lon)),
                radius_meters=float(clamped_radius),
                max_results=needed,
            )
            requests_made += 1

            for raw in text_resp.get("places", []):
                pid = (raw.get("id") or "").strip()
                if pid and pid in seen_place_ids:
                    continue
                lead_dict = self._enrich_and_score_place(raw, sec_name, default_city=city_hint)
                if not lead_dict:
                    continue
                if pid:
                    seen_place_ids.add(pid)
                results.append(lead_dict)
                if len(results) >= clamped_max_results:
                    break

        if progress_callback:
            progress_callback(
                1.0,
                f"Google Places (New): {len(results)} negocios localizados ({requests_made} peticiones realizadas).",
            )

        return results

    def fetch_place_details_on_demand(
        self,
        lead_id: int,
        place_id: str,
        sector_name: str = "Comercios",
        default_city: str = "Málaga",
        db_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Consulta Place Details (New) bajo demanda únicamente para un negocio concreto
        al abrir su ficha comercial, actualiza los campos de la fuente en SQLite sin
        sobrescribir notas ni estados del usuario y devuelve el registro actualizado.
        """
        from places_client import PlacesClient

        target_db = db_path or self.db_path
        client = PlacesClient(api_key=self.api_key, db_path=target_db)
        raw_details = client.get_place_details(place_id)
        if not raw_details:
            stored = get_lead(lead_id, db_path=target_db)
            return stored or {}

        enriched = self._enrich_and_score_place(raw_details, sector_name, default_city=default_city)
        if enriched:
            save_lead(enriched, db_path=target_db)

        updated = get_lead(lead_id, db_path=target_db)
        return updated or enriched or {}

    def search(
        self,
        city: str,
        province: str,
        country: str,
        radius_km: float,
        sectors: List[str],
        target_per_sector: int = 20,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Búsqueda por localidad y texto usando Text Search (New) con deduplicación por Place ID."""
        from places_client import PlacesAPIError, PlacesClient

        key = self.api_key
        if not key:
            raise PlacesAPIError(
                "Google Places requiere configurar GOOGLE_MAPS_API_KEY en .env o en las variables de entorno.",
                error_type="missing_key",
            )

        client = PlacesClient(api_key=key, db_path=self.db_path)
        location_str = f"{city}, {province}, {country}".strip(", ")
        results: List[Dict[str, Any]] = []
        seen_place_ids = set()

        total_sectors = max(1, len(sectors))
        for idx, sector_name in enumerate(sectors):
            if progress_callback:
                pct = 0.1 + (0.85 * (idx / total_sectors))
                progress_callback(pct, f"Consultando Google Places: {sector_name}...")

            resolved_name = resolve_sector(sector_name) or sector_name
            info = SECTORS.get(resolved_name, {})
            q_label = info.get("google_text_query") or resolved_name
            query = f"{q_label} en {location_str}"
            response = client.search_places_text(
                query=query,
                radius_meters=radius_km * 1000,
                max_results=min(20, target_per_sector),
            )

            for raw in response.get("places", []):
                pid = (raw.get("id") or "").strip()
                if pid and pid in seen_place_ids:
                    continue
                lead_dict = self._enrich_and_score_place(raw, resolved_name, default_city=city or "Málaga")
                if not lead_dict:
                    continue
                if pid:
                    seen_place_ids.add(pid)
                results.append(lead_dict)

        return results

    def validate_connection(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "No se ha configurado GOOGLE_MAPS_API_KEY en el entorno."
        try:
            from places_client import PlacesClient

            client = PlacesClient(api_key=self.api_key, db_path=self.db_path)
            return client.validate_api_key()
        except Exception as e:
            return False, f"Error al verificar Google Places API (New): {e}"


class ProviderRegistry:
    """Registro y fábrica de proveedores."""

    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {
            "osm": OSMOverpassProvider(),
            "demo": DemoProvider(),
            "google_places": GooglePlacesProvider()
        }

    def get(self, provider_id: str) -> BaseProvider:
        return self._providers.get(provider_id, self._providers["osm"])

    def list_all(self) -> List[BaseProvider]:
        return list(self._providers.values())


provider_registry = ProviderRegistry()
