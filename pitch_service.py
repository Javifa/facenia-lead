"""
Servicio de inteligencia comercial, recomendación de servicios FACENIA,
argumentos de venta en persona y preparación honesta de enlaces de reseñas / tarjetas NFC.

Reglas de veracidad:
- Las recomendaciones se presentan como propuestas a explorar en la visita, nunca
  como carencias confirmadas si solo proceden de la ausencia de un dato en OSM.
- Los argumentos de venta son breves, naturales para hablar en persona y no inventan
  cifras, porcentajes ni problemas no verificados.
- El generador de enlaces de reseñas y NFC NUNCA construye URLs con placeid=demo ni
  identificadores ficticios.
"""

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

from config import (
    SECTORS,
    WEB_CONFIRMED_NONE,
    WEB_FOUND,
    WEB_INSUFFICIENT,
    WEB_LABELS,
    WEB_NONE,
    WEB_REVIEW,
    WEB_SOCIAL,
    get_web_label,
    resolve_sector,
    sector_info,
)

NFC_STATUS_OPTIONS = ["No preparada", "Preparada", "Entregada"]
REVIEW_URL_STATUS_OPTIONS = [
    "Búsqueda pública (pendiente de fijar ficha)",
    "Enlace pegado (pendiente de verificar)",
    "Verificado (Google Places API)",
    "Verificado (Place ID oficial)",
    "Verificado manualmente",
]

# Expresión regular para un Place ID real de Google (ej. ChIJ...)
GOOGLE_PLACE_ID_RE = re.compile(r"^(ChIJ[A-Za-z0-9_-]{10,120})$")


def generate_commercial_recommendation(lead: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analiza los datos observables del negocio y devuelve una recomendación comercial
    honesta con los servicios de FACENIA que mejor encajan y por qué.
    """
    sector = resolve_sector(lead.get("sector")) or lead.get("sector") or "Otros negocios locales"
    info = sector_info(sector)
    web_effective = lead.get("web_effective") or lead.get("web_status_manual") or lead.get("web_status") or WEB_NONE
    has_phone = bool(str(lead.get("phone") or "").strip())
    has_hours = bool(str(lead.get("opening_hours") or "").strip())
    rating = lead.get("rating")
    ratings_total = lead.get("user_ratings_total")
    is_nfc_sector = bool(info.get("nfc", False))

    services: List[str] = list(info.get("services") or ["Página web", "Digitalización de procesos"])
    bullets: List[str] = []

    if web_effective == WEB_CONFIRMED_NONE:
        headline = "Negocio verificado sin página web propia: oportunidad directa de presencia digital."
        bullets.append(
            "Proponer una página web clara y rápida en móvil con sus servicios, ubicación, horario y botón de contacto directo por WhatsApp o teléfono."
        )
        if "Página web" not in services:
            services.insert(0, "Página web")
    elif web_effective == WEB_NONE:
        src_name = "Google Places" if "google" in str(lead.get("source", "")).lower() else "las fuentes consultadas"
        headline = f"Sin web identificada en {src_name}: confirmar en persona cómo captan clientes."
        bullets.append(
            "Preguntar si disponen de web propia actual o si dependen únicamente del paso de calle y del boca a boca."
        )
        bullets.append(
            "Si no tienen web o está desactualizada, valorar una web corporativa o catálogo sencillo adaptado a móvil."
        )
    elif web_effective == WEB_SOCIAL:
        headline = "Presencia centrada en redes sociales: oportunidad para profesionalizar captación y conversión."
        bullets.append(
            "Revisar si centralizan consultas por Instagram/Facebook y si pierden clientes que buscan en Google."
        )
        bullets.append(
            "Valorar una web propia conectada con sus redes para dar confianza, posicionar en búsquedas locales y automatizar consultas."
        )
    elif web_effective == WEB_REVIEW:
        headline = "Enlace externo de directorio o web pendiente de revisión técnica."
        bullets.append(
            "Comprobar con el responsable si la URL que aparece en directorios les pertenece o si necesitan recuperar el control de su dominio."
        )
    elif web_effective == WEB_INSUFFICIENT:
        headline = "Datos públicos mínimos en el mapa: oportunidad de diagnóstico completo en la visita."
        bullets.append(
            "Aprovechar la visita a pie para identificar al responsable, tomar teléfono de contacto y conocer qué canales digitales utilizan hoy."
        )
    else:
        headline = "Negocio con web identificada: explorar mejora de procesos, conversión, IA o reseñas."
        bullets.append(
            "Más allá de la web básica, explorar si gestionan presupuestos, citas o atención al cliente de forma manual."
        )

    # Recomendaciones específicas según la naturaleza de la categoría
    if sector in ("Belleza y peluquerías", "Salud y servicios", "Talleres", "Academias y formación"):
        bullets.append(
            "Explorar cómo gestionan las citas y recordatorios (teléfono/papel/WhatsApp) para valorar un sistema de reservas online o recordatorios automatizados."
        )
    elif sector in ("Reformas y construcción", "Instalaciones y mantenimiento", "Inmobiliarias", "Consultorías y despachos", "Distribución y logística"):
        bullets.append(
            "Investigar si elaboran presupuestos, partes de trabajo o seguimiento de clientes a mano/Excel para ofrecer software a medida, integraciones o automatización con IA."
        )
    elif sector in ("Restauración", "Cafeterías y panaderías", "Comercios", "Otros negocios locales"):
        bullets.append(
            "Valorar herramientas de fidelización en mostrador, carta/catálogo digital o pedidos online según el tipo de clientela."
        )

    if is_nfc_sector:
        bullets.append(
            "Por tener atención presencial continua, encaja ofrecer una tarjeta o expositor NFC de FACENIA para que los clientes satisfechos dejen su reseña en segundos acercando el móvil."
        )
        if "Tarjeta NFC de reseñas" not in services:
            services.append("Tarjeta NFC de reseñas")

    if rating is not None and ratings_total is not None:
        bullets.append(
            f"Datos de reseñas registrados ({rating} sobre 5 con {ratings_total} reseñas): valorar estrategia activa de captación y respuesta a reseñas."
        )
    elif not has_hours or not has_phone:
        bullets.append(
            "En las fuentes abiertas faltan datos básicos de horario o teléfono; revisar su ficha pública con ellos en el mostrador es una buena entrada comercial."
        )

    return {
        "headline": headline,
        "services": services[:5],
        "bullets": bullets,
        "is_nfc_fit": is_nfc_sector,
        "web_label": get_web_label(web_effective, str(lead.get("source", ""))),
    }


def generate_sales_pitch(lead: Dict[str, Any], variant: int = 0) -> Dict[str, Any]:
    """
    Genera un argumento de venta breve, natural y honesto para usar en persona.
    Ofrece 4 variantes rotables para que el comercial elija el enfoque más cómodo.
    """
    name = (lead.get("name") or "vuestro negocio").strip()
    sector = resolve_sector(lead.get("sector")) or lead.get("sector") or "Otros negocios locales"
    info = sector_info(sector)
    web_effective = lead.get("web_effective") or lead.get("web_status_manual") or lead.get("web_status") or WEB_NONE
    is_nfc = bool(info.get("nfc", False))

    idx = int(variant) % 4

    # Contexto breve de entrada según el estado web observable
    if web_effective in (WEB_NONE, WEB_CONFIRMED_NONE, WEB_INSUFFICIENT):
        web_hook = "al preparar la ruta por la zona no nos aparecía una página web vinculada"
    elif web_effective == WEB_SOCIAL:
        web_hook = "he visto que os movéis sobre todo por redes sociales pero no aparecía web propia"
    elif web_effective == WEB_REVIEW:
        web_hook = "he visto que en el mapa salís con un enlace de directorio en lugar de una web propia"
    else:
        web_hook = "he visto que ya tenéis presencia online y quería conoceros en persona"

    if idx == 0:
        title = "Enfoque 1 · Consultivo y cercano (Recomendado)"
        if web_effective in (WEB_NONE, WEB_CONFIRMED_NONE, WEB_SOCIAL, WEB_INSUFFICIENT):
            pitch = (
                f"«Hola, buenos días. Soy de FACENIA, trabajamos con negocios de aquí de Málaga en temas de web y digitalización. "
                f"Pasaba por la calle porque {web_hook} de {name}, y quería preguntaros rápido si ya lo tenéis cubierto o si os vendría bien "
                f"tener una web sencilla donde la gente vea vuestros servicios y os contacte directo por WhatsApp.»"
            )
        else:
            pitch = (
                f"«Hola, buenos días. Soy de FACENIA, ayudamos a empresas de Málaga con desarrollo web, automatizaciones y gestión digital. "
                f"Como estaba visitando negocios de esta zona y {web_hook}, quería saludaros y preguntar si hay alguna tarea repetitiva "
                f"—reservas, presupuestos o captación de reseñas— que os gustaría agilizar.»"
            )
    elif idx == 1:
        title = "Enfoque 2 · Tarjeta NFC y reseñas en mostrador"
        if is_nfc:
            pitch = (
                f"«Hola, ¿qué tal? Soy de FACENIA. Estamos visitando comercios de la zona para enseñar las tarjetas y expositores NFC de reseñas. "
                f"Funcionan acercando el móvil del cliente al terminar el servicio: se abre directamente vuestra ficha para dejar la reseña en tres segundos, "
                f"sin que tengan que buscar {name} a mano. Si tienes un minuto te enseño en mi móvil cómo queda el enlace.»"
            )
        else:
            pitch = (
                f"«Hola, buenos días. Soy de FACENIA. Ayudamos a empresas de {sector.lower()} en Málaga a mejorar su visibilidad local y sus fichas públicas "
                f"para que cuando un cliente busque vuestro servicio os encuentre primero y vea referencias recientes. ¿Con quién podría comentarlo dos minutos?»"
            )
    elif idx == 2:
        title = "Enfoque 3 · Ahorro de tiempo, citas y procesos"
        if sector in ("Belleza y peluquerías", "Salud y servicios", "Talleres", "Academias y formación"):
            pitch = (
                f"«Hola, buenas. Soy de FACENIA, desarrollamos soluciones digitales aquí en Málaga. "
                f"Estamos hablando con negocios de {sector.lower()} de la zona para ver cómo gestionáis las citas y los avisos a clientes. "
                f"Si ahora os quita mucho tiempo atender el teléfono o el WhatsApp, montamos sistemas muy sencillos de reserva y recordatorio automático.»"
            )
        else:
            pitch = (
                f"«Hola, buenas. Soy de FACENIA, hacemos software a medida, web y automatización para negocios de Málaga. "
                f"Quería preguntaros si en {name} lleváis la gestión de presupuestos, clientes o pedidos con varias hojas sueltas "
                f"y si os interesaría centralizarlo en una herramienta rápida adaptada a vuestra forma de trabajar.»"
            )
    else:
        title = "Enfoque 4 · Rompehielos breve (30 segundos)"
        pitch = (
            f"«Hola, buenos días. Estoy haciendo una ruta por los negocios de esta calle de parte de FACENIA —hacemos páginas web, "
            f"automatización y tarjetas NFC para reseñas aquí en Málaga—. ¿Está el responsable de {name} para dejarle mi contacto "
            f"y hacerle una consulta rápida de un minuto?»"
        )

    return {
        "variant": idx,
        "variant_count": 4,
        "title": title,
        "text": pitch,
    }


def _is_valid_google_domain(url_str: str) -> bool:
    try:
        parsed = urlparse(url_str if "://" in url_str else f"https://{url_str}")
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        allowed = (
            "google.com",
            "google.es",
            "maps.app.goo.gl",
            "goo.gl",
            "g.page",
            "search.google.com",
        )
        return any(host == d or host.endswith("." + d) for d in allowed)
    except Exception:
        return False


def prepare_review_link_info(
    lead: Dict[str, Any],
    custom_input: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Prepara la información del enlace de reseñas y tarjeta NFC para un negocio,
    sin inventar nunca Place IDs ficticios (como placeid=demo).

    Retorna:
    - review_url: URL lista para usar/previsualizar (directa si hay Place ID o enlace pegado;
      si no, enlace de búsqueda pública en Google Maps para localizar la ficha real).
    - is_direct_review_link: True solo si procede de un Place ID real (ChIJ...) o un enlace
      fijado/verificado por el usuario.
    - status_label: estado transparente del enlace.
    - public_search_url: URL para abrir la búsqueda pública del negocio en Google Maps.
    - instructions: pasos claros para verificar o grabar la tarjeta NFC.
    """
    name = (lead.get("name") or "").strip()
    address = (lead.get("address") or "").strip()
    city = (lead.get("city") or "Málaga").strip()

    query_parts = [p for p in [name, address, city] if p]
    query_str = ", ".join(query_parts) or name or "Málaga"
    public_search_url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(query_str)}"

    # 1. Comprobar si el usuario acaba de introducir un Place ID o URL
    candidate = (custom_input if custom_input is not None else (lead.get("review_url") or "")).strip()

    # Comprobar también si el negocio tiene un place_id real de Google Places (ChIJ...)
    raw_place_id = (lead.get("place_id") or lead.get("source_id") or "").strip()
    if not candidate and GOOGLE_PLACE_ID_RE.match(raw_place_id):
        candidate = raw_place_id

    if candidate:
        # Si es un Place ID real de Google (ChIJ...)
        if GOOGLE_PLACE_ID_RE.match(candidate):
            direct_url = f"https://search.google.com/local/writereview?placeid={candidate}"
            return {
                "review_url": direct_url,
                "public_search_url": public_search_url,
                "is_direct_review_link": True,
                "status_label": lead.get("review_url_status") or "Enlace directo de reseña (Place ID real)",
                "source_type": "place_id",
                "instructions": (
                    "Enlace directo generado a partir de un Place ID oficial de Google. "
                    "Ábrelo en el navegador antes de programar la tarjeta NFC para confirmar que abre la ventana de reseñas del negocio."
                ),
            }

        # Si es una URL pegada por el usuario
        norm_url = candidate if candidate.lower().startswith(("http://", "https://")) else f"https://{candidate}"
        if "placeid=demo" not in norm_url.lower():
            is_google = _is_valid_google_domain(norm_url)
            status_lbl = lead.get("review_url_status") or (
                "Enlace de Google guardado (verificar en móvil)"
                if is_google
                else "Enlace personalizado guardado"
            )
            return {
                "review_url": norm_url,
                "public_search_url": public_search_url,
                "is_direct_review_link": True,
                "status_label": status_lbl,
                "source_type": "custom_url",
                "instructions": (
                    "Enlace configurado para este negocio. Pulsa «Abrir y comprobar enlace» para verificar "
                    "que dirige a la ficha pública correcta antes de codificar la tarjeta NFC."
                ),
            }

    # 2. Por defecto (fuente OpenStreetMap sin Place ID de Google):
    # No inventamos un placeid falso. Ofrecemos el enlace de búsqueda pública en Google Maps
    # y explicamos cómo fijar el enlace directo en 2 toques.
    return {
        "review_url": public_search_url,
        "public_search_url": public_search_url,
        "is_direct_review_link": False,
        "status_label": "Ficha pública por localizar (OpenStreetMap no incluye Place ID de Google)",
        "source_type": "public_search",
        "instructions": (
            "Las fuentes gratuitas (OpenStreetMap) no contienen el identificador interno de reseñas de Google. "
            "Para grabar una tarjeta NFC auténtica: 1) Pulsa «Buscar ficha en Google Maps», "
            "2) Copia el enlace de «Compartir» o de «Solicitar reseñas» de la ficha real del negocio y "
            "3) Pégalo en el campo inferior para dejarlo guardado y verificado."
        ),
    }
