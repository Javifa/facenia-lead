"""
Pruebas para el clasificador de sitios web y redes sociales.
"""

from verification import classify_web_status, extract_domain
from config import WEB_STATUS_NO_WEB, WEB_STATUS_FOUND, WEB_STATUS_SOCIAL, WEB_STATUS_MANUAL_REVIEW


def test_extract_domain():
    assert extract_domain("https://www.ejemplo.es/contacto") == "ejemplo.es"
    assert extract_domain("http://mi-empresa.com") == "mi-empresa.com"
    assert extract_domain("www.clinica.com") == "clinica.com"


def test_classify_empty_url():
    status, reason = classify_web_status("")
    assert status == WEB_STATUS_NO_WEB

    status_none, _ = classify_web_status(None)
    assert status_none == WEB_STATUS_NO_WEB


def test_classify_social_media_url():
    status_fb, reason_fb = classify_web_status("https://www.facebook.com/talleresgonzalez")
    assert status_fb == WEB_STATUS_SOCIAL
    assert "facebook" in reason_fb.lower()

    status_ig, reason_ig = classify_web_status("https://instagram.com/peluqueria_malaga")
    assert status_ig == WEB_STATUS_SOCIAL
    assert "instagram" in reason_ig.lower()


def test_classify_directory_url():
    status, reason = classify_web_status("https://www.paginasamarillas.es/f/malaga/reformas_123.html")
    assert status == WEB_STATUS_MANUAL_REVIEW
    assert "directorio" in reason.lower()


def test_classify_standard_website():
    status, reason = classify_web_status("https://www.reformasgarcia.es")
    assert status == WEB_STATUS_FOUND
    assert "propia" in reason.lower()
