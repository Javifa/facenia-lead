"""
Cliente oficial HTTP para Google Places API (New) v1.

Soporta:
1. Nearby Search (New): `POST https://places.googleapis.com/v1/places:searchNearby`
2. Text Search (New):   `POST https://places.googleapis.com/v1/places:searchText`
3. Place Details (New): `GET  https://places.googleapis.com/v1/places/{place_id}`

Cumple estrictamente:
- Uso obligatorio de `X-Goog-FieldMask` (prohibido `*` en producción).
- Clave de servidor leída desde `GOOGLE_MAPS_API_KEY` (nunca expuesta en logs ni errores).
- Registro en el contador interno de peticiones de BuscaLeads (`increment_api_usage`).
- Tratamiento explícito de errores de autenticación (401/403), cuota (429), red y campos ausentes.
"""

import time
from typing import Any, Dict, List, Optional, Tuple
import requests

from config import (
    PLACES_DETAILS_FIELD_MASK,
    PLACES_DETAILS_URL_TEMPLATE,
    PLACES_SEARCH_FIELD_MASK,
    PLACES_SEARCH_NEARBY_URL,
    PLACES_SEARCH_TEXT_URL,
    get_google_maps_api_key,
)
from database import increment_api_usage


class PlacesAPIError(Exception):
    """Excepción para errores de configuración, cuota o red en Google Places API (New)."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_type: str = "api_error",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.details = details or {}


class PlacesClient:
    """Cliente desacoplado y con control de costes para Google Places API (New)."""

    def __init__(self, api_key: Optional[str] = None, db_path: Optional[str] = None):
        resolved_key = (api_key if api_key is not None else get_google_maps_api_key()).strip()
        self.api_key = resolved_key
        self.db_path = db_path

    def _require_key(self) -> None:
        if not self.api_key:
            raise PlacesAPIError(
                "No se ha configurado la variable de entorno GOOGLE_MAPS_API_KEY. "
                "Añádela en tu archivo .env local o en las variables de entorno del servidor.",
                status_code=None,
                error_type="missing_key",
            )

    def _get_headers(self, field_mask: str) -> Dict[str, str]:
        """Genera los encabezados HTTP obligatorios de Places API (New) verificando FieldMask."""
        self._require_key()
        clean_mask = (field_mask or "").strip()
        if not clean_mask or "*" in clean_mask:
            raise ValueError(
                "X-Goog-FieldMask es obligatorio y no puede utilizar el comodín '*' en producción."
            )
        return {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": clean_mask,
        }

    def _handle_http_error(self, response: requests.Response) -> None:
        """Traduce códigos de estado HTTP de Google Places API (New) en excepciones claras sin filtrar secretos."""
        status = response.status_code
        err_json: Dict[str, Any] = {}
        err_msg = ""
        try:
            err_json = response.json()
            err_msg = err_json.get("error", {}).get("message", "")
        except Exception:
            err_msg = (response.text or "")[:200]

        if status in (401, 403):
            raise PlacesAPIError(
                f"Error de autorización en Google Places API (New) (HTTP {status}): {err_msg or 'Acceso denegado'}. "
                "Comprueba que GOOGLE_MAPS_API_KEY sea válida y que 'Places API (New)' esté habilitada en Google Cloud Console.",
                status_code=status,
                error_type="auth_error",
                details=err_json,
            )
        if status == 429:
            raise PlacesAPIError(
                "Límite de cuota o tasa de solicitudes alcanzado en Google Places API (New) (HTTP 429). "
                "Espera unos segundos o revisa el presupuesto de tu cuenta en Google Cloud.",
                status_code=429,
                error_type="quota_exceeded",
                details=err_json,
            )
        if status == 400:
            raise PlacesAPIError(
                f"Solicitud rechazada por Google Places API (New) (HTTP 400): {err_msg or 'Parámetros no válidos'}.",
                status_code=400,
                error_type="invalid_request",
                details=err_json,
            )
        if status >= 500:
            raise PlacesAPIError(
                f"Error temporal en los servidores de Google Places API (HTTP {status}).",
                status_code=status,
                error_type="server_error",
                details=err_json,
            )
        raise PlacesAPIError(
            f"Error en Google Places API (New) (HTTP {status}): {err_msg or 'Respuesta inesperada'}.",
            status_code=status,
            error_type="api_error",
            details=err_json,
        )

    def validate_api_key(self) -> Tuple[bool, str]:
        """
        Valida si la clave de API es válida y tiene habilitada 'Places API (New)'
        solicitando únicamente `places.id` con `maxResultCount=1`.
        """
        if not self.api_key:
            return (
                False,
                "No se ha configurado GOOGLE_MAPS_API_KEY en el archivo .env ni en el entorno del servidor.",
            )

        headers = self._get_headers("places.id")
        payload = {
            "textQuery": "Málaga",
            "languageCode": "es",
            "maxResultCount": 1,
        }

        try:
            increment_api_usage("validate_key", count=1, db_path=self.db_path)
            response = requests.post(
                PLACES_SEARCH_TEXT_URL,
                json=payload,
                headers=headers,
                timeout=8,
            )
            if response.status_code == 200:
                return True, "Clave de Google Places API (New) válida y operativa."
            self._handle_http_error(response)
            return False, "Error desconocido al validar la clave."
        except PlacesAPIError as e:
            return False, str(e)
        except requests.exceptions.Timeout:
            return False, "Tiempo de espera agotado al conectar con Google Places API (New)."
        except requests.exceptions.ConnectionError:
            return False, "Error de conexión de red al contactar con Google Maps Platform."
        except Exception as e:
            return False, f"No se pudo validar la clave: {str(e)}"

    def search_nearby(
        self,
        lat: float,
        lon: float,
        radius_meters: float,
        included_types: Optional[List[str]] = None,
        max_results: int = 20,
        rank_preference: str = "POPULARITY",
        field_mask: Optional[str] = None,
        max_retries: int = 1,
    ) -> Dict[str, Any]:
        """
        Ejecuta Nearby Search (New) (`POST /v1/places:searchNearby`) por coordenadas,
        radio circular estricto (`locationRestriction`) y tipos de establecimiento (`includedTypes`).
        """
        headers = self._get_headers(field_mask or PLACES_SEARCH_FIELD_MASK)
        clamped_radius = max(50.0, min(float(radius_meters), 50000.0))
        clamped_max = max(1, min(int(max_results), 20))

        payload: Dict[str, Any] = {
            "languageCode": "es",
            "maxResultCount": clamped_max,
            "rankPreference": rank_preference if rank_preference in ("POPULARITY", "DISTANCE") else "POPULARITY",
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": float(lat),
                        "longitude": float(lon),
                    },
                    "radius": clamped_radius,
                }
            },
        }
        if included_types:
            # Nearby Search (New) admite hasta 50 tipos de la Tabla A
            unique_types = list(dict.fromkeys([t.strip() for t in included_types if t and t.strip()]))[:50]
            if unique_types:
                payload["includedTypes"] = unique_types

        for attempt in range(max_retries + 1):
            try:
                increment_api_usage("nearby_search", count=1, db_path=self.db_path)
                response = requests.post(
                    PLACES_SEARCH_NEARBY_URL,
                    json=payload,
                    headers=headers,
                    timeout=12,
                )
                if response.status_code == 200:
                    data = response.json() if response.content else {}
                    return {"places": data.get("places", [])}

                if response.status_code >= 500 and attempt < max_retries:
                    time.sleep(1.0)
                    continue

                self._handle_http_error(response)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as net_err:
                if attempt < max_retries:
                    time.sleep(1.0)
                    continue
                raise PlacesAPIError(
                    f"Error de red o tiempo de espera agotado en Nearby Search (New): {net_err}",
                    error_type="network_error",
                )

        return {"places": []}

    def search_places_text(
        self,
        query: str,
        location_bias_coords: Optional[Tuple[float, float]] = None,
        radius_meters: Optional[float] = None,
        included_type: Optional[str] = None,
        page_token: Optional[str] = None,
        max_results: int = 20,
        field_mask: Optional[str] = None,
        max_retries: int = 1,
    ) -> Dict[str, Any]:
        """
        Ejecuta Text Search (New) (`POST /v1/places:searchText`) cuando la categoría
        o consulta por texto lo requiere, aplicando sesgo circular (`locationBias`).
        """
        headers = self._get_headers(field_mask or PLACES_SEARCH_FIELD_MASK)
        clamped_max = max(1, min(int(max_results), 20))

        payload: Dict[str, Any] = {
            "textQuery": (query or "").strip(),
            "languageCode": "es",
            "maxResultCount": clamped_max,
        }
        if page_token:
            payload["pageToken"] = page_token
        if included_type:
            payload["includedType"] = included_type

        if location_bias_coords and radius_meters:
            lat, lng = location_bias_coords
            payload["locationBias"] = {
                "circle": {
                    "center": {
                        "latitude": float(lat),
                        "longitude": float(lng),
                    },
                    "radius": max(50.0, min(float(radius_meters), 50000.0)),
                }
            }

        for attempt in range(max_retries + 1):
            try:
                increment_api_usage("text_search", count=1, db_path=self.db_path)
                response = requests.post(
                    PLACES_SEARCH_TEXT_URL,
                    json=payload,
                    headers=headers,
                    timeout=12,
                )
                if response.status_code == 200:
                    data = response.json() if response.content else {}
                    return {
                        "places": data.get("places", []),
                        "nextPageToken": data.get("nextPageToken"),
                    }

                if response.status_code >= 500 and attempt < max_retries:
                    time.sleep(1.0)
                    continue

                self._handle_http_error(response)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as net_err:
                if attempt < max_retries:
                    time.sleep(1.0)
                    continue
                raise PlacesAPIError(
                    f"Error de red o tiempo de espera agotado en Text Search (New): {net_err}",
                    error_type="network_error",
                )

        return {"places": [], "nextPageToken": None}

    def get_place_details(
        self,
        place_id: str,
        field_mask: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ejecuta Place Details (New) (`GET /v1/places/{place_id}`) bajo demanda únicamente
        para un negocio seleccionado cuando el usuario abre o amplía su ficha comercial.
        """
        clean_id = (place_id or "").strip()
        if clean_id.startswith("places/"):
            clean_id = clean_id.split("places/", 1)[1]
        if not clean_id or clean_id.startswith("osm_") or clean_id.startswith("demo_"):
            raise PlacesAPIError(
                "El negocio seleccionado no procede de Google Places o carece de un Place ID válido.",
                error_type="invalid_place_id",
            )

        headers = self._get_headers(field_mask or PLACES_DETAILS_FIELD_MASK)
        url = PLACES_DETAILS_URL_TEMPLATE.format(place_id=clean_id)

        try:
            increment_api_usage("place_details", count=1, db_path=self.db_path)
            response = requests.get(
                url,
                params={"languageCode": "es"},
                headers=headers,
                timeout=10,
            )
            if response.status_code == 200:
                return response.json() if response.content else {}
            self._handle_http_error(response)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as net_err:
            raise PlacesAPIError(
                f"Error de red al recuperar detalles del negocio en Google Places: {net_err}",
                error_type="network_error",
            )
        return {}

    @staticmethod
    def parse_place_record(
        raw_place: Dict[str, Any],
        sector_name: str,
        default_city: str = "Málaga",
    ) -> Dict[str, Any]:
        """
        Normaliza un objeto `Place` de Google Places API (New) al esquema de BuscaLeads.
        No promete ni inventa campos ausentes: si un dato no viene en la respuesta,
        se deja vacío o `None` para mostrarse como no disponible.
        """
        place_id = (raw_place.get("id") or "").strip()
        if place_id.startswith("places/"):
            place_id = place_id.split("places/", 1)[1]

        display_name_obj = raw_place.get("displayName")
        if isinstance(display_name_obj, dict):
            name = (display_name_obj.get("text") or "").strip()
        else:
            name = str(display_name_obj or "").strip()

        address = (
            raw_place.get("formattedAddress")
            or raw_place.get("shortFormattedAddress")
            or ""
        ).strip()

        city = ""
        postal_code = ""
        for comp in raw_place.get("addressComponents") or []:
            if not isinstance(comp, dict):
                continue
            types = comp.get("types") or []
            long_name = (comp.get("longText") or comp.get("shortText") or "").strip()
            if "locality" in types and long_name:
                city = long_name
            elif "postal_code" in types and long_name:
                postal_code = long_name
            elif "administrative_area_level_2" in types and not city and long_name:
                city = long_name

        if not city:
            city = default_city

        loc = raw_place.get("location") or {}
        lat_raw = loc.get("latitude") if isinstance(loc, dict) else None
        lon_raw = loc.get("longitude") if isinstance(loc, dict) else None
        lat = float(lat_raw) if lat_raw is not None else None
        lon = float(lon_raw) if lon_raw is not None else None

        phone = (
            raw_place.get("nationalPhoneNumber")
            or raw_place.get("internationalPhoneNumber")
            or ""
        ).strip()
        website = (raw_place.get("websiteUri") or "").strip()

        # Enlaces de Google Maps y enlace directo de reseñas si está disponible
        gmaps_links = raw_place.get("googleMapsLinks") or {}
        if not isinstance(gmaps_links, dict):
            gmaps_links = {}

        google_maps_url = (
            raw_place.get("googleMapsUri")
            or gmaps_links.get("placeUri")
            or ""
        ).strip()

        write_review_uri = (
            gmaps_links.get("writeAReviewUri")
            or gmaps_links.get("reviewsUri")
            or ""
        ).strip()

        if write_review_uri:
            review_url = write_review_uri
            review_url_status = "Verificado (Google Places API)"
        elif place_id:
            review_url = f"https://search.google.com/local/writereview?placeid={place_id}"
            review_url_status = "Verificado (Place ID oficial)"
        else:
            review_url = ""
            review_url_status = ""

        # Valoración y reseñas: solo si están presentes en la respuesta (nunca inventar 0.0 si es None)
        rating_raw = raw_place.get("rating")
        rating: Optional[float] = float(rating_raw) if rating_raw is not None else None

        ratings_count_raw = raw_place.get("userRatingCount")
        user_ratings_total: Optional[int] = (
            int(ratings_count_raw) if ratings_count_raw is not None else None
        )

        reviews_source = (
            "Google Places API (New)"
            if (rating is not None or user_ratings_total is not None)
            else ""
        )

        # Horarios (regularOpeningHours.weekdayDescriptions)
        opening_hours = ""
        reg_hours = raw_place.get("regularOpeningHours") or raw_place.get("currentOpeningHours") or {}
        if isinstance(reg_hours, dict):
            weekday_desc = reg_hours.get("weekdayDescriptions") or []
            if isinstance(weekday_desc, list) and weekday_desc:
                opening_hours = " · ".join(str(line).strip() for line in weekday_desc if str(line).strip())

        primary_type_obj = raw_place.get("primaryTypeDisplayName")
        if isinstance(primary_type_obj, dict):
            primary_type_label = (primary_type_obj.get("text") or "").strip()
        else:
            primary_type_label = (raw_place.get("primaryType") or "").strip()

        place_types = raw_place.get("types") or []
        if not isinstance(place_types, list):
            place_types = []

        business_status = (raw_place.get("businessStatus") or "").strip()

        return {
            "place_id": place_id,
            "source_id": place_id,
            "source": "Google Places",
            "data_source": "Google Places API (New)",
            "name": name,
            "sector": sector_name,
            "primary_type": primary_type_label,
            "types": place_types,
            "business_status": business_status,
            "address": address,
            "city": city,
            "postal_code": postal_code,
            "phone": phone,
            "email": "",
            "website": website,
            "social_url": "",
            "opening_hours": opening_hours,
            "google_maps_url": google_maps_url,
            "map_url": google_maps_url,
            "lat": lat,
            "lon": lon,
            "rating": rating,
            "user_ratings_total": user_ratings_total,
            "reviews_source": reviews_source,
            "review_url": review_url,
            "review_url_status": review_url_status,
        }
