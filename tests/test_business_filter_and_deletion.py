import pytest
from business_filter import is_large_business_or_chain
from database import init_db, save_lead, get_lead, delete_lead, delete_all_leads, get_leads

def test_is_large_business_or_chain():
    # Large chains / franchises
    is_large, reason = is_large_business_or_chain("McDonald's Plaza Mayor", ["restaurant"])
    assert is_large is True
    assert "Mcdonald" in reason or "Cadena" in reason

    is_large, _ = is_large_business_or_chain("Mercadona Supermercado", ["grocery_store"])
    assert is_large is True

    is_large, _ = is_large_business_or_chain("Zara Larios", ["clothing_store"])
    assert is_large is True

    is_large, _ = is_large_business_or_chain("Banco Santander Sucursal", ["bank"])
    assert is_large is True

    is_large, _ = is_large_business_or_chain("Burger King Vialia", ["fast_food_restaurant"])
    assert is_large is True

    is_large, _ = is_large_business_or_chain("Carrefour Express", ["store"])
    assert is_large is True

    is_large, _ = is_large_business_or_chain("Repsol Gasolinera", ["gas_station"])
    assert is_large is True

    # Generic excluded types even if name doesn't match
    is_large, reason = is_large_business_or_chain("Despensa La Esquina", ["supermarket"])
    assert is_large is True
    assert "Tipo no objetivo" in reason or "supermarket" in reason

    is_large, _ = is_large_business_or_chain("Cajero 24h", ["atm"])
    assert is_large is True

    is_large, _ = is_large_business_or_chain("Estación de Servicio Sol", ["gas_station"])
    assert is_large is True

    # Genuine local small businesses (SMBs) target for FACENIA
    is_large, _ = is_large_business_or_chain("Cafetería El Rincón de Paco", ["cafe"])
    assert is_large is False

    is_large, _ = is_large_business_or_chain("Peluquería Carmen Morales", ["hair_care", "beauty_salon"])
    assert is_large is False

    is_large, _ = is_large_business_or_chain("Taller Mecánico Hermanos Ruiz", ["car_repair"])
    assert is_large is False

    is_large, _ = is_large_business_or_chain("Clínica Dental Ramos", ["dentist"])
    assert is_large is False

    is_large, _ = is_large_business_or_chain("Restaurante Los Mellizos del Soho", ["restaurant"])
    assert is_large is False


def test_delete_lead_database(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_leads.db")
    monkeypatch.setattr("database.DATABASE_PATH", test_db)
    init_db(test_db)

    lead_sample = {
        "name": "Bar de Barrio Para Borrar",
        "sector": "hosteleria",
        "address": "Calle Falsa 123",
        "lat": 36.72,
        "lon": -4.42,
        "web_status": "Sin web indicada",
    }

    lead_id, is_new = save_lead(lead_sample, db_path=test_db)
    assert is_new is True
    assert lead_id > 0

    lead = get_lead(lead_id, db_path=test_db)
    assert lead is not None
    assert lead["name"] == "Bar de Barrio Para Borrar"

    deleted = delete_lead(lead_id, db_path=test_db)
    assert deleted is True
    assert get_lead(lead_id, db_path=test_db) is None


def test_delete_all_leads_database(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_leads.db")
    monkeypatch.setattr("database.DATABASE_PATH", test_db)
    init_db(test_db)

    save_lead({"name": "Local 1", "sector": "hosteleria", "lat": 36.72, "lon": -4.42}, db_path=test_db)
    save_lead({"name": "Local 2", "sector": "comercios", "lat": 36.73, "lon": -4.43}, db_path=test_db)
    assert len(get_leads(db_path=test_db)) >= 2

    count = delete_all_leads(db_path=test_db)
    assert count >= 2
    assert len(get_leads(db_path=test_db)) == 0
