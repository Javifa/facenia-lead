"""
Clasificación de presencia web y verificación HTTP opcional.

Regla clave: que una fuente no indique web NO demuestra que el negocio no la tenga.
Por eso el estado por defecto es «Sin web indicada», nunca «sin web».
"""

from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

from config import HTTP_USER_AGENT, WEB_FOUND, WEB_NONE, WEB_REVIEW, WEB_SOCIAL

SOCIAL_DOMAINS = {
    "facebook.com", "fb.com", "instagram.com", "tiktok.com", "twitter.com",
    "x.com", "linkedin.com", "youtube.com", "wa.me", "whatsapp.com", "linktr.ee",
}
DIRECTORY_DOMAINS = {
    "paginasamarillas.es", "qdq.com", "tripadvisor.es", "tripadvisor.com",
    "thefork.es", "eltenedor.es", "habitissimo.es", "cylex.es", "infoisinfo.es",
    "google.com", "goo.gl", "maps.app.goo.gl", "business.site", "doctoralia.es",
}


def normalize_url(url: Optional[str]) -> str:
    """Devuelve la URL con esquema, o cadena vacía."""
    if not url or not str(url).strip():
        return ""
    url = str(url).strip().split(";")[0].strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def extract_domain(url: Optional[str]) -> str:
    """Dominio en minúsculas sin «www.»."""
    url = normalize_url(url)
    if not url:
        return ""
    try:
        domain = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return domain[4:] if domain.startswith("www.") else domain


def _matches(domain: str, candidates: set) -> Optional[str]:
    for cand in candidates:
        if domain == cand or domain.endswith("." + cand):
            return cand
    return None


def classify_web_status(website: Optional[str], social_url: Optional[str] = None) -> Tuple[str, str]:
    """
    Clasifica la presencia web a partir de lo que indica la fuente.
    Devuelve (estado, explicación legible).
    """
    domain = extract_domain(website)
    if not domain:
        if social_url:
            return WEB_SOCIAL, "La fuente solo indica un perfil en redes sociales."
        return WEB_NONE, "La fuente no indica página web. No está confirmado que no exista."

    social = _matches(domain, SOCIAL_DOMAINS)
    if social:
        return WEB_SOCIAL, f"La URL indicada es un perfil de {social}, no una web propia."

    directory = _matches(domain, DIRECTORY_DOMAINS)
    if directory:
        return WEB_REVIEW, f"La URL indicada pertenece a un directorio ({directory})."

    return WEB_FOUND, f"La fuente indica una web propia ({domain})."


def check_website(url: str, timeout: int = 6) -> Dict[str, str]:
    """
    Comprobación HTTP ligera de una web indicada. Un timeout, un error SSL o un
    bloqueo anti-bots no se interpretan como «sin web»: se marcan para revisión.
    """
    url = normalize_url(url)
    if not url:
        return {"status": WEB_NONE, "detail": "Sin URL que comprobar."}

    headers = {"User-Agent": HTTP_USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout,
                            allow_redirects=True, stream=True)
        code = resp.status_code
        resp.close()
    except requests.exceptions.SSLError:
        return {"status": WEB_REVIEW, "detail": "La web existe pero tiene un problema de certificado SSL."}
    except requests.exceptions.Timeout:
        return {"status": WEB_REVIEW, "detail": "La web no respondió a tiempo. Puede ser temporal."}
    except requests.exceptions.ConnectionError:
        return {"status": WEB_REVIEW, "detail": "No se pudo conectar (DNS o servidor caído). Revisar manualmente."}
    except requests.exceptions.RequestException as exc:
        return {"status": WEB_REVIEW, "detail": f"Error al comprobar: {type(exc).__name__}."}

    if code < 400 or code in (401, 403, 429):
        return {"status": WEB_FOUND, "detail": f"La web responde (HTTP {code})."}
    return {"status": WEB_REVIEW, "detail": f"La web devuelve HTTP {code}. Revisar manualmente."}
