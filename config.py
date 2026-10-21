"""
Configuración central de FACENIA Lead Finder.

Define rutas, parámetros de las fuentes abiertas (OpenStreetMap), catálogo de
categorías con etiquetas OSM estructuradas, estados comerciales, enrutamiento
peatonal y pesos de puntuación.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "facenia_leads.db"))
BACKUPS_DIR = BASE_DIR / "backups"
BACKUPS_DIR.mkdir(exist_ok=True)
WEB_DIR = BASE_DIR / "web"

# --------------------------------------------------------------------------
# Fuentes abiertas: OpenStreetMap
# Políticas: https://operations.osmfoundation.org/policies/nominatim/
#            https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances
# --------------------------------------------------------------------------
SOURCE_NAME = "OpenStreetMap"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# Identificación obligatoria según la política de uso de Nominatim/Overpass
HTTP_USER_AGENT = os.getenv(
    "OSM_USER_AGENT", "FACENIA-Lead-Finder/4.0 (prospeccion comercial local)"
)
OVERPASS_QUERY_TIMEOUT = 12      # segundos que se piden al servidor
HTTP_TIMEOUT = 15                # segundos de espera en cliente
PAUSE_BETWEEN_QUERIES = 1.0      # pausa de cortesía entre consultas
MAX_RADIUS_KM = 50               # radios mayores saturan los servidores públicos
MAX_ELEMENTS_PER_SECTOR = 400    # tope de elementos solicitados por sector

# --------------------------------------------------------------------------
# Enrutamiento peatonal (OSRM, instancia pública de FOSSGIS con perfil a pie)
# Uso razonable: https://routing.openstreetmap.de/about.html
# --------------------------------------------------------------------------
ROUTING_BASE_URL = os.getenv("ROUTING_BASE_URL", "https://routing.openstreetmap.de/routed-foot")
ROUTING_TIMEOUT = 20
ROUTE_RADIUS_OPTIONS_M = [250, 500, 750, 1000, 1500, 2000]
ROUTE_MIN_STOPS = 5
ROUTE_MAX_STOPS = 30
DEFAULT_START = {"label": "Málaga centro (Plaza de la Constitución)", "lat": 36.7213, "lon": -4.4214}

# --------------------------------------------------------------------------
# Estados de web
# --------------------------------------------------------------------------
WEB_NONE = "Sin web indicada"          # la fuente no aporta URL
WEB_FOUND = "Web encontrada"           # URL propia en la fuente o verificada
WEB_SOCIAL = "Solo redes sociales"     # solo perfil de Facebook/Instagram...
WEB_REVIEW = "Revisión manual"         # URL de directorio, caída o dudosa
WEB_CONFIRMED_NONE = "Sin web (verificado)"  # verificado manualmente por el usuario
WEB_INSUFFICIENT = "Información insuficiente"  # sin web, teléfono, email ni dirección

WEB_STATUSES = [WEB_NONE, WEB_SOCIAL, WEB_REVIEW, WEB_FOUND, WEB_CONFIRMED_NONE, WEB_INSUFFICIENT]
NO_WEB_STATUSES = [WEB_NONE, WEB_SOCIAL, WEB_CONFIRMED_NONE]

# Etiquetas legibles para la interfaz (no se guardan en base de datos)
WEB_LABELS = {
    WEB_NONE: "Sin web identificada en Google Places",
    WEB_FOUND: "Web encontrada",
    WEB_SOCIAL: "Solo redes sociales",
    WEB_REVIEW: "Pendiente de verificar",
    WEB_CONFIRMED_NONE: "Verificación manual completada (sin web)",
    WEB_INSUFFICIENT: "Información insuficiente",
}


def get_web_label(web_status: str, source: str = "") -> str:
    """
    Devuelve la etiqueta exacta del estado web según la fuente del negocio,
    distinguiendo claramente entre 'Sin web identificada en Google Places',
    'Web encontrada', 'Pendiente de verificar' y 'Verificación manual completada'.
    """
    if web_status == WEB_NONE:
        if source and "openstreetmap" in source.lower():
            return "Sin web identificada en OpenStreetMap"
        return "Sin web identificada en Google Places"
    return WEB_LABELS.get(web_status, web_status)

# --------------------------------------------------------------------------
# Estados comerciales y resultados de visita
# --------------------------------------------------------------------------
COMMERCIAL_STATUSES = [
    "Pendiente de visitar",
    "Visitado",
    "Contactado",
    "Seguimiento pendiente",
    "Cliente conseguido",
    "Descartado",
]
DEFAULT_COMMERCIAL_STATUS = COMMERCIAL_STATUSES[0]
FOLLOWUP_STATUSES = ["Contactado", "Seguimiento pendiente"]
CLOSED_STATUSES = ["Cliente conseguido", "Descartado"]

# Valores heredados de versiones anteriores -> valores actuales
LEGACY_COMMERCIAL_STATUS = {
    "Pendiente de revisar": "Pendiente de visitar",
    "Pendiente de contactar": "Pendiente de visitar",
    "Ha respondido": "Contactado",
    "Seguimiento": "Seguimiento pendiente",
    "No interesado": "Descartado",
    "No contactar": "Descartado",
    "Cliente": "Cliente conseguido",
}

# Resultados rápidos tras una visita -> estado comercial resultante
OUTCOME_SOLD = "Vendido"
OUTCOME_RETURN = "Volver otro día"
OUTCOME_NOT_INTERESTED = "No interesa"
OUTCOME_VISITED = "Visitado"
OUTCOME_CONTACTED = "Contactado"
OUTCOME_TO_STATUS = {
    OUTCOME_SOLD: "Cliente conseguido",
    OUTCOME_RETURN: "Seguimiento pendiente",
    OUTCOME_NOT_INTERESTED: "Descartado",
    OUTCOME_VISITED: "Visitado",
    OUTCOME_CONTACTED: "Contactado",
}

PRIORITIES = ["Alta", "Media", "Baja"]

# Servicios de FACENIA
FACENIA_SERVICES = [
    "Página web",
    "Tienda online",
    "Reservas y citas online",
    "Gestión de reseñas",
    "Tarjeta NFC de reseñas",
    "Software a medida",
    "Automatización de tareas",
    "Inteligencia artificial",
    "Integraciones entre herramientas",
    "Digitalización de procesos",
]

# --------------------------------------------------------------------------
# Puntuación de oportunidad (0-100). Indicador interno de prioridad, NO una
# probabilidad de venta. Solo usa señales observables en los datos.
# --------------------------------------------------------------------------
SCORING_WEIGHTS = {
    "no_web": 40,            # la fuente no indica web
    "social_only": 35,       # solo redes sociales
    "web_review": 20,        # web dudosa / directorio
    "insufficient": 15,      # datos insuficientes: posible oportunidad, sin confirmar
    "web_found": 0,
    "phone": 10,             # se puede contactar
    "email": 5,
    "address": 5,            # se puede visitar
    "opening_hours": 5,
    "nfc_fit": 10,           # atención presencial al público
    "chain_penalty": -25,    # marca/cadena: decisiones centralizadas
}
# El encaje de cada categoría con los servicios de FACENIA (0-20) se define en SECTORS["fit"].
PRIORITY_THRESHOLDS = {"Alta": 70, "Media": 45}

# --------------------------------------------------------------------------
# Categorías: cada entrada es una lista de (clave OSM, [valores]).
# fit: encaje con servicios de FACENIA (0-20). nfc: atención presencial.
# services: propuestas posibles a valorar (no necesidades comprobadas).
# --------------------------------------------------------------------------
SectorTags = List[Tuple[str, List[str]]]

SECTORS: Dict[str, Dict] = {
    "Restauración": {
        "tags": [("amenity", ["restaurant", "bar", "pub", "fast_food", "ice_cream", "biergarten"])],
        "google_types": ["restaurant", "bar"],
        "google_text_query": "Restaurantes y bares",
        "fit": 15, "nfc": True, "priority": False,
        "services": ["Página web", "Reservas y citas online", "Gestión de reseñas", "Tarjeta NFC de reseñas"],
    },
    "Cafeterías y panaderías": {
        "tags": [("amenity", ["cafe"]), ("shop", ["bakery", "pastry", "confectionery"])],
        "google_types": ["cafe", "bakery"],
        "google_text_query": "Cafeterías y panaderías",
        "fit": 12, "nfc": True, "priority": False,
        "services": ["Página web", "Gestión de reseñas", "Tarjeta NFC de reseñas"],
    },
    "Belleza y peluquerías": {
        "tags": [("shop", ["hairdresser", "beauty", "cosmetics", "massage", "tattoo"])],
        "google_types": ["hair_care", "beauty_salon", "spa"],
        "google_text_query": "Peluquerías y centros de estética",
        "fit": 18, "nfc": True, "priority": True,
        "services": ["Reservas y citas online", "Página web", "Gestión de reseñas", "Tarjeta NFC de reseñas"],
    },
    "Salud y servicios": {
        "tags": [("amenity", ["dentist", "clinic", "doctors", "veterinary"]),
                 ("healthcare", ["physiotherapist", "podiatrist", "psychotherapist",
                                 "optometrist", "speech_therapist", "dentist"]),
                 ("shop", ["optician"])],
        "google_types": ["dentist", "doctor", "physiotherapist", "veterinary_care"],
        "google_text_query": "Clínicas dentales fisioterapia y centros médicos",
        "fit": 18, "nfc": True, "priority": True,
        "services": ["Reservas y citas online", "Página web", "Automatización de tareas", "Gestión de reseñas"],
    },
    "Comercios": {
        "tags": [("shop", ["clothes", "shoes", "florist", "butcher", "greengrocer", "hardware",
                           "furniture", "jewelry", "gift", "pet", "bicycle", "electronics", "books",
                           "toys", "sports", "stationery", "mobile_phone", "deli", "wine",
                           "houseware", "fabric", "interior_decoration"])],
        "google_types": [
            "clothing_store", "shoe_store", "jewelry_store", "florist",
            "book_store", "pet_store", "furniture_store", "electronics_store",
            "hardware_store", "home_goods_store",
        ],
        "google_text_query": "Tiendas y comercios locales",
        "fit": 10, "nfc": True, "priority": False,
        "services": ["Tienda online", "Página web", "Tarjeta NFC de reseñas", "Gestión de reseñas"],
    },
    "Inmobiliarias": {
        "tags": [("office", ["estate_agent"]), ("shop", ["estate_agent"])],
        "google_types": ["real_estate_agency"],
        "google_text_query": "Agencias inmobiliarias",
        "fit": 20, "nfc": False, "priority": True,
        "services": ["Página web", "Software a medida", "Automatización de tareas", "Integraciones entre herramientas"],
    },
    "Talleres": {
        "tags": [("shop", ["car_repair", "motorcycle_repair", "tyres", "car_parts"])],
        "google_types": ["car_repair"],
        "google_text_query": "Talleres mecánicos y de chapa y pintura",
        "fit": 15, "nfc": True, "priority": False,
        "services": ["Reservas y citas online", "Página web", "Software a medida", "Gestión de reseñas"],
    },
    "Reformas y construcción": {
        "tags": [("craft", ["builder", "carpenter", "painter", "tiler", "plasterer", "stonemason",
                            "floorer", "roofer", "glaziery"]),
                 ("office", ["construction_company"])],
        "google_types": ["painter", "roofing_contractor"],
        "google_text_query": "Empresas de reformas y construcción",
        "use_text_search": True,
        "fit": 20, "nfc": False, "priority": True,
        "services": ["Página web", "Software a medida", "Digitalización de procesos", "Automatización de tareas"],
    },
    "Instalaciones y mantenimiento": {
        "tags": [("craft", ["plumber", "electrician", "hvac", "heating_engineer", "locksmith",
                            "metal_construction", "window_construction", "insulation"]),
                 ("shop", ["air_conditioning"])],
        "google_types": ["plumber", "electrician", "locksmith"],
        "google_text_query": "Fontanería electricidad y climatización",
        "fit": 20, "nfc": False, "priority": True,
        "services": ["Página web", "Digitalización de procesos", "Software a medida", "Automatización de tareas"],
    },
    "Academias y formación": {
        "tags": [("amenity", ["language_school", "driving_school", "music_school",
                              "dancing_school", "training", "prep_school"]),
                 ("office", ["educational_institution"])],
        "google_types": ["school"],
        "google_text_query": "Academias de idiomas formación y autoescuelas",
        "use_text_search": True,
        "fit": 18, "nfc": False, "priority": True,
        "services": ["Página web", "Reservas y citas online", "Automatización de tareas", "Inteligencia artificial"],
    },
    "Consultorías y despachos": {
        "tags": [("office", ["accountant", "tax_advisor", "lawyer", "notary", "insurance",
                             "financial_advisor", "consulting", "architect", "engineer", "it",
                             "graphic_design", "advertising_agency"])],
        "google_types": ["lawyer", "accounting", "insurance_agency"],
        "google_text_query": "Asesoría gestoría despacho de abogados y consultoría",
        "fit": 18, "nfc": False, "priority": True,
        "services": ["Automatización de tareas", "Inteligencia artificial", "Integraciones entre herramientas", "Página web"],
    },
    "Distribución y logística": {
        "tags": [("office", ["logistics", "courier", "moving_company"]),
                 ("shop", ["wholesale", "storage_rental"])],
        "google_types": ["moving_company", "storage"],
        "google_text_query": "Empresas de logística distribución y mudanzas",
        "use_text_search": True,
        "fit": 16, "nfc": False, "priority": True,
        "services": ["Software a medida", "Integraciones entre herramientas", "Digitalización de procesos", "Automatización de tareas"],
    },
    "Otros negocios locales": {
        "tags": [("shop", ["laundry", "dry_cleaning", "copyshop", "travel_agency", "tailor",
                           "shoe_repair", "funeral_directors", "photo"]),
                 ("craft", ["cleaning", "photographer"]),
                 ("amenity", ["car_wash"])],
        "google_types": ["laundry", "travel_agency", "car_wash"],
        "google_text_query": "Comercios y servicios locales",
        "fit": 10, "nfc": True, "priority": False,
        "services": ["Página web", "Gestión de reseñas", "Tarjeta NFC de reseñas"],
    },
}

SECTOR_NAMES = list(SECTORS.keys())
DEFAULT_SECTORS = ["Restauración", "Belleza y peluquerías", "Comercios"]

# Nombres de categorías de versiones anteriores -> categoría actual
LEGACY_SECTOR_ALIASES = {
    "Fontanería y electricidad": "Instalaciones y mantenimiento",
    "Climatización": "Instalaciones y mantenimiento",
    "Fontaneros y electricistas": "Instalaciones y mantenimiento",
    "Empresas de climatización": "Instalaciones y mantenimiento",
    "Academias": "Academias y formación",
    "Academias y centros de formación": "Academias y formación",
    "Peluquerías y estética": "Belleza y peluquerías",
    "Peluquerías y centros de estética": "Belleza y peluquerías",
    "Clínicas": "Salud y servicios",
    "Clínicas y centros sanitarios": "Salud y servicios",
    "Gestorías y asesorías": "Consultorías y despachos",
    "Gestorías y consultorías": "Consultorías y despachos",
    "Servicios profesionales": "Consultorías y despachos",
    "Empresas de servicios profesionales": "Consultorías y despachos",
    "Transporte y logística": "Distribución y logística",
    "Limpieza": "Otros negocios locales",
    "Empresas de limpieza": "Otros negocios locales",
    "Restaurantes y bares": "Restauración",
    "Talleres mecánicos": "Talleres",
    "Comercios locales": "Comercios",
}


def resolve_sector(name: Optional[str]) -> Optional[str]:
    """Devuelve el nombre de categoría actual para un nombre actual o heredado."""
    if not name:
        return None
    if name in SECTORS:
        return name
    return LEGACY_SECTOR_ALIASES.get(name)


def sector_info(name: Optional[str]) -> Dict:
    resolved = resolve_sector(name)
    return SECTORS.get(resolved, {}) if resolved else {}


# --------------------------------------------------------------------------
# Proveedores y configuración de Google Places API (New)
# --------------------------------------------------------------------------
PROVIDER_OSM = "OpenStreetMap"
PROVIDER_DEMO = "Demostración"
PROVIDER_GOOGLE = "Google Places"

DEFAULT_DATA_PROVIDER = os.getenv("DEFAULT_DATA_PROVIDER", "google_places").strip().lower()
if DEFAULT_DATA_PROVIDER not in ("google_places", "osm"):
    DEFAULT_DATA_PROVIDER = "google_places"


def _clean_env_api_key(raw_val: str) -> str:
    val = (raw_val or "").strip()
    if not val:
        return ""
    low = val.lower()
    if "aqui" in low or "your_api_key" in low or "tu_api_key" in low or "placeholder" in low:
        return ""
    return val


def get_google_maps_api_key() -> str:
    """
    Obtiene la clave de Google Maps Platform desde el entorno del servidor.
    Prioriza `GOOGLE_MAPS_API_KEY` y mantiene compatibilidad con `GOOGLE_PLACES_API_KEY`.
    Nunca expone la clave en logs ni en el frontend.
    """
    primary = _clean_env_api_key(os.getenv("GOOGLE_MAPS_API_KEY", ""))
    if primary:
        return primary
    return _clean_env_api_key(os.getenv("GOOGLE_PLACES_API_KEY", ""))


def is_google_maps_configured() -> bool:
    return bool(get_google_maps_api_key())


GOOGLE_MAPS_API_KEY = get_google_maps_api_key()
GOOGLE_PLACES_API_KEY = GOOGLE_MAPS_API_KEY

# Endpoints oficiales de Google Places API (New) v1
PLACES_SEARCH_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
PLACES_SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL_TEMPLATE = "https://places.googleapis.com/v1/places/{place_id}"

# FieldMask para búsquedas (Nearby Search New y Text Search New)
# Solo solicita los campos indispensables para identificar, geolocalizar, clasificar web y puntuar.
# Nunca se utiliza '*' en producción.
PLACES_SEARCH_FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.location,"
    "places.types,"
    "places.primaryType,"
    "places.primaryTypeDisplayName,"
    "places.businessStatus,"
    "places.websiteUri,"
    "places.nationalPhoneNumber,"
    "places.rating,"
    "places.userRatingCount,"
    "places.googleMapsUri,"
    "places.googleMapsLinks"
)
PLACES_FIELD_MASK = PLACES_SEARCH_FIELD_MASK

# FieldMask para Place Details (New) bajo demanda al abrir la ficha de un negocio concreto
PLACES_DETAILS_FIELD_MASK = (
    "id,"
    "displayName,"
    "formattedAddress,"
    "location,"
    "types,"
    "primaryType,"
    "primaryTypeDisplayName,"
    "businessStatus,"
    "websiteUri,"
    "nationalPhoneNumber,"
    "internationalPhoneNumber,"
    "rating,"
    "userRatingCount,"
    "regularOpeningHours,"
    "googleMapsUri,"
    "googleMapsLinks"
)

# Límites de control de consumo por búsqueda
PLACES_MAX_RESULTS_PER_SEARCH = max(5, min(int(os.getenv("PLACES_MAX_RESULTS_PER_SEARCH", "20") or 20), 60))
PLACES_MAX_REQUESTS_PER_SEARCH = max(1, min(int(os.getenv("PLACES_MAX_REQUESTS_PER_SEARCH", "3") or 3), 10))
PLACES_MIN_SEARCH_INTERVAL_SECONDS = 2.0

WEB_STATUS_NO_WEB = WEB_NONE
WEB_STATUS_FOUND = WEB_FOUND
WEB_STATUS_SOCIAL = WEB_SOCIAL
WEB_STATUS_MANUAL_REVIEW = WEB_REVIEW
WEB_STATUS_PENDING = WEB_REVIEW

COMMERCIAL_STATUS_PENDING_REVIEW = "Pendiente de visitar"
COMMERCIAL_STATUS_PENDING_CONTACT = "Pendiente de visitar"
COMMERCIAL_STATUS_VISITED = "Visitado"
COMMERCIAL_STATUS_CONTACTED = "Contactado"
COMMERCIAL_STATUS_INTERESTED = "Contactado"
COMMERCIAL_STATUS_FOLLOWUP = "Seguimiento pendiente"
COMMERCIAL_STATUS_CLIENT = "Cliente conseguido"
COMMERCIAL_STATUS_NOT_INTERESTED = "Descartado"

PRIORITY_HIGH = "Alta"
PRIORITY_MEDIUM = "Media"
PRIORITY_LOW = "Baja"

SECTORS_CATALOG = SECTORS
NOMINATIM_SEARCH_URL = NOMINATIM_URL
OSM_USER_AGENT = HTTP_USER_AGENT
OSM_TIMEOUT_SECONDS = OVERPASS_QUERY_TIMEOUT
