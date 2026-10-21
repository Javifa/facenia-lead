"""
Pruebas automatizadas para las funcionalidades del rediseño comercial de BuscaLeads:
- Enrutamiento peatonal y ordenación de visitas (Haversine + 2-opt + fallback honesto)
- Recomendaciones FACENIA, argumentos rotables y generador de enlaces de reseñas / NFC
- Persistencia de rutas, resultados de visita (Vendido, Volver otro día, No interesa)
- Exclusión de descartados, acción Deshacer (undo) y Estadísticas Comerciales reales
"""

import pytest

from config import (
    OUTCOME_NOT_INTERESTED,
    OUTCOME_RETURN,
    OUTCOME_SOLD,
    WEB_NONE,
    WEB_SOCIAL,
)
from database import (
    get_discarded_keys,
    get_lead,
    get_route,
    get_stats,
    init_db,
    record_visit_outcome,
    save_lead,
    save_route,
    undo_interaction,
    update_lead_review_nfc,
)
from pitch_service import (
    generate_commercial_recommendation,
    generate_sales_pitch,
    prepare_review_link_info,
)
from routing_service import (
    build_google_maps_directions_to_lead,
    build_google_maps_walking_route_url,
    haversine_m,
    plan_walking_route,
)


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_prospeccion.db")
    init_db(db_file)
    return db_file


def test_empty_stats_start_at_zero(temp_db):
    """Al principio, sin datos reales, todas las métricas comerciales deben ser 0."""
    stats = get_stats(db_path=temp_db)
    assert stats["found_total"] == 0
    assert stats["visited_count"] == 0
    assert stats["contacted_count"] == 0
    assert stats["followup_pending"] == 0
    assert stats["sales_count"] == 0
    assert stats["sales_amount_total"] == 0.0
    assert stats["conversion_rate"] == 0.0
    assert stats["by_category"] == []


def test_haversine_and_route_planning_honest_fallback():
    """Verifica el cálculo geográfico y que no se inventen paradas ni rutas cuando OSRM no se usa."""
    start_lat, start_lon = 36.7213, -4.4214
    d = haversine_m(start_lat, start_lon, 36.7220, -4.4214)
    assert 70 <= d <= 85

    candidates = [
        {"id": 1, "lead_id": 1, "name": "Peluquería Sol", "sector": "Belleza y peluquerías", "lat": 36.7250, "lon": -4.4250, "score": 85},
        {"id": 2, "lead_id": 2, "name": "Cafetería Centro", "sector": "Cafeterías y panaderías", "lat": 36.7218, "lon": -4.4218, "score": 90},
        {"id": 3, "lead_id": 3, "name": "Taller Sur", "sector": "Talleres", "lat": 36.7230, "lon": -4.4230, "score": 80},
    ]

    # Pedimos 10 paradas pero solo hay 3 candidatos reales: debe devolver exactamente 3
    plan = plan_walking_route(start_lat, start_lon, candidates, max_stops=10, use_external_routing=False)
    stops = plan["stops"]
    assert len(stops) == 3
    assert stops[0]["name"] == "Cafetería Centro"  # Más cercano al punto de salida
    assert stops[0]["position"] == 1
    assert stops[1]["position"] == 2
    assert stops[2]["position"] == 3
    assert plan["has_real_route"] is False
    assert plan["total_distance_m"] is None

    gmaps_route = build_google_maps_walking_route_url(start_lat, start_lon, stops)
    assert "travelmode=walking" in gmaps_route
    assert "36.7213,-4.4214" in gmaps_route

    gmaps_dir = build_google_maps_directions_to_lead(stops[0])
    assert "travelmode=walking" in gmaps_dir


def test_pitch_and_nfc_review_link_veracity():
    """Comprueba recomendaciones, rotación de argumentos y ausencia de enlaces falsos placeid=demo."""
    lead = {
        "id": 10,
        "name": "Salón Aura",
        "sector": "Belleza y peluquerías",
        "address": "Calle Granada 15",
        "city": "Málaga",
        "web_status": WEB_NONE,
        "phone": "952123456",
    }

    rec = generate_commercial_recommendation(lead)
    assert rec["is_nfc_fit"] is True
    assert "Tarjeta NFC de reseñas" in rec["services"]
    assert len(rec["bullets"]) >= 2

    p0 = generate_sales_pitch(lead, variant=0)
    p1 = generate_sales_pitch(lead, variant=1)
    assert p0["text"] != p1["text"]
    assert "Salón Aura" in p0["text"]

    # Sin Place ID de Google: devuelve enlace de búsqueda pública y marca is_direct_review_link=False
    rev_default = prepare_review_link_info(lead)
    assert rev_default["is_direct_review_link"] is False
    assert "placeid=demo" not in rev_default["review_url"]
    assert "google.com/maps/search" in rev_default["review_url"]

    # Con un Place ID real de Google (ChIJ...): genera enlace directo writereview
    rev_real = prepare_review_link_info(lead, custom_input="ChIJN1t_tDeuEmsRUsoyG83frY4")
    assert rev_real["is_direct_review_link"] is True
    assert "writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4" in rev_real["review_url"]


def test_route_persistence_visit_outcomes_undo_and_stats(temp_db):
    """
    Prueba integral de Etapa 2, Etapa 4 y Etapa 5:
    - Guardar negocios y ruta
    - Registrar Vendido (con servicio e importe), Volver otro día y No interesa
    - Comprobar estadísticas reales y exclusión de descartados
    - Deshacer la última acción y verificar restauración de estado
    """
    l1_id, _ = save_lead(
        {
            "source_id": "osm_node_9001",
            "name": "Clínica Dental Alameda",
            "sector": "Salud y servicios",
            "address": "Alameda Principal 12",
            "city": "Málaga",
            "lat": 36.7180,
            "lon": -4.4220,
            "web_status": WEB_NONE,
            "phone": "952001122",
        },
        db_path=temp_db,
    )
    l2_id, _ = save_lead(
        {
            "source_id": "osm_node_9002",
            "name": "Asador El Puerto",
            "sector": "Restauración",
            "address": "Calle Larios 4",
            "city": "Málaga",
            "lat": 36.7200,
            "lon": -4.4215,
            "web_status": WEB_SOCIAL,
            "phone": "952003344",
        },
        db_path=temp_db,
    )
    l3_id, _ = save_lead(
        {
            "source_id": "osm_node_9003",
            "name": "Bazar Central",
            "sector": "Comercios",
            "address": "Calle Nueva 8",
            "city": "Málaga",
            "lat": 36.7210,
            "lon": -4.4230,
            "web_status": WEB_NONE,
        },
        db_path=temp_db,
    )

    route_id = save_route(
        {
            "start_label": "Plaza de la Constitución",
            "start_lat": 36.7213,
            "start_lon": -4.4214,
            "radius_m": 500,
            "requested_stops": 5,
            "found_count": 3,
            "categories": ["Salud y servicios", "Restauración", "Comercios"],
            "routing_source": "OSRM Foot (OpenStreetMap)",
            "total_distance_m": 620.0,
            "total_duration_s": 450.0,
            "geometry": [[36.7213, -4.4214], [36.7200, -4.4215], [36.7180, -4.4220]],
        },
        [
            {"position": 1, "lead_id": l2_id, "leg_distance_m": 180.0, "leg_duration_s": 130.0, "straight_distance_m": 150.0},
            {"position": 2, "lead_id": l1_id, "leg_distance_m": 240.0, "leg_duration_s": 175.0, "straight_distance_m": 200.0},
            {"position": 3, "lead_id": l3_id, "leg_distance_m": 200.0, "leg_duration_s": 145.0, "straight_distance_m": 160.0},
        ],
        db_path=temp_db,
    )

    loaded_route = get_route(route_id, db_path=temp_db)
    assert loaded_route is not None
    assert len(loaded_route["stops"]) == 3
    assert loaded_route["stops"][0]["name"] == "Asador El Puerto"

    # Configurar NFC en l2
    updated_l2 = update_lead_review_nfc(
        lead_id=l2_id,
        review_url="https://g.page/r/asador-puerto/review",
        review_url_status="Verificado manualmente",
        nfc_status="Entregada",
        rating=4.6,
        user_ratings_total=128,
        reviews_source="Verificado manualmente",
        db_path=temp_db,
    )
    assert updated_l2["nfc_status"] == "Entregada"
    assert updated_l2["rating"] == 4.6

    # 1) Registrar VENTA en l1 (Página web + reservas por 650 €)
    res_sold = record_visit_outcome(
        lead_id=l1_id,
        outcome=OUTCOME_SOLD,
        route_id=route_id,
        note="Cerrado acuerdo con la doctora",
        sale_service="Reservas y citas online",
        sale_amount=650.0,
        db_path=temp_db,
    )
    assert res_sold["status_after"] == "Cliente conseguido"
    assert res_sold["lead"]["sale_amount"] == 650.0
    assert res_sold["lead"]["sale_service"] == "Reservas y citas online"

    # 2) Registrar VOLVER OTRO DÍA en l2
    res_ret = record_visit_outcome(
        lead_id=l2_id,
        outcome=OUTCOME_RETURN,
        route_id=route_id,
        note="Pasar el jueves a las 11:00",
        followup_date="2026-10-25",
        db_path=temp_db,
    )
    assert res_ret["status_after"] == "Seguimiento pendiente"
    assert res_ret["lead"]["next_followup_date"] == "2026-10-25"

    # 3) Registrar NO INTERESA en l3
    res_disc = record_visit_outcome(
        lead_id=l3_id,
        outcome=OUTCOME_NOT_INTERESTED,
        route_id=route_id,
        discard_reason="No necesita servicios digitales ahora",
        db_path=temp_db,
    )
    assert res_disc["status_after"] == "Descartado"

    # Verificar que l3 aparece en claves descartadas para no reaparecer en búsquedas
    disc_sids, _ = get_discarded_keys(db_path=temp_db)
    assert "osm_node_9003" in disc_sids

    # Verificar estadísticas comerciales reales
    stats = get_stats(db_path=temp_db)
    assert stats["found_total"] == 3
    assert stats["visited_count"] == 3
    assert stats["sales_count"] == 1
    assert stats["sales_amount_total"] == 650.0
    assert stats["followup_pending"] == 1
    assert stats["discarded_count"] == 1
    assert stats["conversion_rate"] == round((1 / 3) * 100.0, 1)

    # 4) Probar DESHACER la última acción (el descarte de l3)
    undo_res = undo_interaction(db_path=temp_db)
    assert undo_res is not None
    restored_l3 = get_lead(l3_id, db_path=temp_db)
    assert restored_l3["commercial_status"] == "Pendiente de visitar"
    disc_sids_after, _ = get_discarded_keys(db_path=temp_db)
    assert "osm_node_9003" not in disc_sids_after
