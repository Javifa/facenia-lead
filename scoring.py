"""
Puntuación de oportunidad comercial (0-100).

Es un indicador interno para ordenar visitas, NO una probabilidad de venta ni una
predicción estadística. Solo usa señales presentes en los datos y explica cada punto.
"""

from typing import Any, Dict, List, Tuple

from config import (PRIORITY_THRESHOLDS, SCORING_WEIGHTS, WEB_CONFIRMED_NONE, WEB_FOUND,
                    WEB_INSUFFICIENT, WEB_NONE, WEB_REVIEW, WEB_SOCIAL, sector_info)


def calculate_lead_score(lead: Dict[str, Any]) -> Tuple[int, str, List[str]]:
    """Devuelve (puntuación, prioridad, motivos legibles)."""
    w = SCORING_WEIGHTS
    score = 0
    reasons: List[str] = []

    web = lead.get("web_status", "")
    if web in (WEB_NONE, WEB_CONFIRMED_NONE):
        score += w["no_web"]
        reasons.append(f"+{w['no_web']} sin web identificada en las fuentes")
    elif web == WEB_SOCIAL:
        score += w["social_only"]
        reasons.append(f"+{w['social_only']} solo perfil en redes sociales")
    elif web == WEB_REVIEW:
        score += w["web_review"]
        reasons.append(f"+{w['web_review']} web de directorio o pendiente de verificar")
    elif web == WEB_INSUFFICIENT:
        score += w["insufficient"]
        reasons.append(f"+{w['insufficient']} información insuficiente (verificar en persona)")

    if str(lead.get("phone") or "").strip():
        score += w["phone"]
        reasons.append(f"+{w['phone']} teléfono público")
    if str(lead.get("email") or "").strip():
        score += w["email"]
        reasons.append(f"+{w['email']} email público")
    if str(lead.get("address") or "").strip():
        score += w["address"]
        reasons.append(f"+{w['address']} dirección conocida")
    if str(lead.get("opening_hours") or "").strip():
        score += w["opening_hours"]
        reasons.append(f"+{w['opening_hours']} horario publicado")

    info = sector_info(lead.get("sector"))
    fit = int(info.get("fit", 0))
    if fit:
        score += fit
        reasons.append(f"+{fit} encaje de la categoría con servicios de FACENIA")
    if info.get("nfc"):
        score += w["nfc_fit"]
        reasons.append(f"+{w['nfc_fit']} atención presencial: encaje con tarjeta NFC de reseñas")

    if lead.get("is_chain"):
        score += w["chain_penalty"]
        reasons.append(f"{w['chain_penalty']} marca o cadena (decisiones centralizadas)")

    score = max(0, min(100, score))
    if score >= PRIORITY_THRESHOLDS["Alta"]:
        priority = "Alta"
    elif score >= PRIORITY_THRESHOLDS["Media"]:
        priority = "Media"
    else:
        priority = "Baja"
    return score, priority, reasons
