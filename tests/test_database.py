"""
Pruebas automatizadas para la capa de base de datos, deduplicación,
búsquedas favoritas, caché geográfico y preferencias de usuario.
"""

import os
import pytest
from database import (
    init_db,
    save_or_update_lead,
    save_selected_leads_batch,
    get_leads,
    get_stats,
    update_lead_commercial,
    delete_lead,
    set_search_results_temp,
    get_search_results_temp,
    save_search_query,
    get_saved_searches,
    delete_saved_search,
    get_setting,
    set_setting,
    get_geo_cache,
    set_geo_cache,
    backup_database,
    restore_database
)
from config import COMMERCIAL_STATUS_CONTACTED, COMMERCIAL_STATUS_PENDING_REVIEW, WEB_STATUS_NO_WEB


@pytest.fixture
def temp_db(tmp_path):
    """Crea una base de datos temporal aislada para cada test."""
    db_file = str(tmp_path / "test_leads.db")
    init_db(db_file)
    return db_file


def test_insert_and_retrieve_lead(temp_db):
    lead_data = {
        "source": "OpenStreetMap",
        "source_id": "osm_node_1001",
        "name": "Reformas Rápidas SL",
        "sector": "Reformas y construcción",
        "address": "Calle Mayor 10, Málaga",
        "city": "Málaga",
        "postal_code": "29001",
        "phone": "952112233",
        "website": "",
        "web_status": WEB_STATUS_NO_WEB,
        "score": 85,
        "priority": "Alta"
    }

    lead_id, is_new = save_or_update_lead(lead_data, db_path=temp_db)
    assert is_new is True
    assert lead_id > 0

    leads = get_leads(db_path=temp_db)
    assert len(leads) == 1
    assert leads[0]["name"] == "Reformas Rápidas SL"
    assert leads[0]["commercial_status"] == COMMERCIAL_STATUS_PENDING_REVIEW


def test_deduplication_preserves_commercial_notes(temp_db):
    """
    Verifica que al volver a guardar o actualizar un prospecto existente:
    - Se actualicen campos de la fuente (teléfono, etc.)
    - Se PRESERVEN las notas, próximo paso y estado comercial del usuario
    """
    lead_data = {
        "source": "OpenStreetMap",
        "source_id": "osm_node_2002",
        "name": "Clima Málaga",
        "sector": "Empresas de climatización",
        "phone": "951000000",
        "web_status": WEB_STATUS_NO_WEB,
        "score": 75,
        "priority": "Alta"
    }

    lead_id, is_new = save_or_update_lead(lead_data, db_path=temp_db)
    assert is_new is True

    # El usuario edita el estado comercial y añade una nota
    update_lead_commercial(
        lead_id,
        COMMERCIAL_STATUS_CONTACTED,
        notes="Hablado con el dueño, enviar propuesta",
        next_action="Llamar el jueves",
        db_path=temp_db
    )

    # Simular una nueva consulta que actualiza el teléfono
    updated_from_source = {
        "source": "OpenStreetMap",
        "source_id": "osm_node_2002",
        "name": "Clima Málaga",
        "sector": "Empresas de climatización",
        "phone": "951999999",  # Teléfono cambiado
        "web_status": WEB_STATUS_NO_WEB,
        "score": 80,
        "priority": "Alta"
    }

    lead_id_2, is_new_2 = save_or_update_lead(updated_from_source, db_path=temp_db)
    assert is_new_2 is False
    assert lead_id_2 == lead_id

    # Comprobar que las notas y el estado comercial NO se han borrado
    leads = get_leads(db_path=temp_db)
    assert len(leads) == 1
    saved = leads[0]
    assert saved["phone"] == "951999999"  # Actualizado
    assert saved["commercial_status"] == COMMERCIAL_STATUS_CONTACTED  # PRESERVADO
    assert saved["notes"] == "Hablado con el dueño, enviar propuesta"  # PRESERVADO
    assert saved["next_action"] == "Llamar el jueves"  # PRESERVADO


def test_batch_saving_and_deletion(temp_db):
    batch = [
        {"source_id": "s1", "name": "Empresa Uno", "sector": "Inmobiliarias", "web_status": WEB_STATUS_NO_WEB},
        {"source_id": "s2", "name": "Empresa Dos", "sector": "Talleres mecánicos", "web_status": "Web encontrada"}
    ]
    new_c, upd_c = save_selected_leads_batch(batch, db_path=temp_db)
    assert new_c == 2
    assert upd_c == 0

    leads = get_leads(db_path=temp_db)
    assert len(leads) == 2

    # Eliminar uno
    delete_lead(leads[0]["id"], db_path=temp_db)
    assert len(get_leads(db_path=temp_db)) == 1


def test_temporary_search_results(temp_db):
    temp_list = [
        {"name": "Temp 1", "sector": "Fontaneros y electricistas", "score": 90, "web_status": WEB_STATUS_NO_WEB},
        {"name": "Temp 2", "sector": "Reformas y construcción", "score": 70, "web_status": "Web encontrada"}
    ]
    set_search_results_temp(temp_list, db_path=temp_db)
    retrieved = get_search_results_temp(db_path=temp_db)
    assert len(retrieved) == 2
    assert retrieved[0]["name"] == "Temp 1"


def test_saved_searches(temp_db):
    search_id = save_search_query(
        name="Málaga Reformas",
        city="Málaga",
        province="Málaga",
        country="España",
        radius_km=15.0,
        sectors=["Reformas y construcción"],
        db_path=temp_db
    )
    assert search_id > 0

    saved_list = get_saved_searches(db_path=temp_db)
    assert len(saved_list) == 1
    assert saved_list[0]["name"] == "Málaga Reformas"
    assert "Reformas y construcción" in saved_list[0]["sectors"]

    delete_saved_search(search_id, db_path=temp_db)
    assert len(get_saved_searches(db_path=temp_db)) == 0


def test_app_settings_and_geo_cache(temp_db):
    # Preferencias
    set_setting("theme", "dark", db_path=temp_db)
    assert get_setting("theme", db_path=temp_db) == "dark"

    # Geo Cache
    set_geo_cache("malaga, espana", 36.7213, -4.4214, "[]", "Málaga, España", db_path=temp_db)
    cached = get_geo_cache("malaga, espana", db_path=temp_db)
    assert cached is not None
    assert round(cached["lat"], 2) == 36.72


def test_backup_and_restore(temp_db, tmp_path):
    save_or_update_lead({
        "source_id": "p_backup",
        "name": "Negocio Para Backup",
        "sector": "Comercios locales",
        "web_status": WEB_STATUS_NO_WEB
    }, db_path=temp_db)

    backup_file = str(tmp_path / "backup_test.db")
    backup_database(destination_path=backup_file, db_path=temp_db)
    assert os.path.exists(backup_file)

    restored_db = str(tmp_path / "restored.db")
    restore_database(source_path=backup_file, db_path=restored_db)

    leads = get_leads(db_path=restored_db)
    assert len(leads) == 1
    assert leads[0]["name"] == "Negocio Para Backup"
