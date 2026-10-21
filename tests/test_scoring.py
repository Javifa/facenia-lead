"""
Pruebas para el algoritmo de scoring comercial de FACENIA Lead Finder.
"""

from scoring import calculate_lead_score
from config import WEB_STATUS_NO_WEB, WEB_STATUS_FOUND, WEB_STATUS_SOCIAL, PRIORITY_HIGH, PRIORITY_LOW


def test_high_priority_lead_without_web():
    """Un negocio sin web, con teléfono, dirección y sector prioritario debe tener prioridad Alta."""
    lead = {
        "web_status": WEB_STATUS_NO_WEB,
        "phone": "+34 952 00 11 22",
        "address": "Calle Larios 5",
        "sector": "Reformas y construcción"
    }

    score, priority, reasons = calculate_lead_score(lead)
    assert score >= 70
    assert priority == PRIORITY_HIGH
    assert len(reasons) >= 4


def test_social_media_web_opportunity():
    """Un negocio cuya web es un perfil de red social en un sector prioritario debe recibir puntuación relevante."""
    lead = {
        "web_status": WEB_STATUS_SOCIAL,
        "phone": "+34 952 11 22 33",
        "address": "Calle Granada 12",
        "sector": "Inmobiliarias"
    }

    score, priority, reasons = calculate_lead_score(lead)
    assert score >= 70
    assert priority == PRIORITY_HIGH


def test_low_priority_with_existing_web_and_no_phone():
    """Un negocio con web propia existente y sin teléfono debe tener prioridad baja."""
    lead = {
        "web_status": WEB_STATUS_FOUND,
        "phone": "",
        "address": "",
        "sector": "Comercios"
    }

    score, priority, reasons = calculate_lead_score(lead)
    assert score < 45
    assert priority == PRIORITY_LOW
