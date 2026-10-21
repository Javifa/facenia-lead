"""
Pruebas automatizadas para la arquitectura modular de proveedores de datos
y la integración de Google Places API (New) con respuestas simuladas (mocks).
No realiza llamadas reales de pago a Google Cloud.
"""

from unittest.mock import MagicMock, patch
import pytest

import config
from database import (
    get_api_usage_stats,
    get_lead,
    increment_api_usage,
    init_db,
    reset_api_usage_stats,
    save_lead,
    update_lead_commercial,
)
from places_client import PlacesAPIError, PlacesClient
from providers import (
    DemoProvider,
    GooglePlacesProvider,
    OSMOverpassProvider,
    provider_registry,
)
from search_service import (
    generate_prospecting_route,
    reset_duplicate_request_guard,
)


def test_provider_registry():
    osm = provider_registry.get("osm")
    assert isinstance(osm, OSMOverpassProvider)
    assert osm.is_free is True
    assert osm.requires_api_key is False

    gp = provider_registry.get("google_places")
    assert isinstance(gp, GooglePlacesProvider)
    assert gp.requires_api_key is True

    demo = provider_registry.get("demo")
    assert isinstance(demo, DemoProvider)
    assert demo.is_free is True

    providers = provider_registry.list_all()
    assert len(providers) >= 3


def test_demo_provider_search():
    demo = DemoProvider()
    results = demo.search(
        city="Málaga",
        province="Málaga",
        country="España",
        radius_km=25.0,
        sectors=["Reformas y construcción", "Empresas de climatización"],
        target_per_sector=10,
    )

    assert len(results) > 0
    first = results[0]
    assert "name" in first
    assert "sector" in first
    assert "web_status" in first
    assert "score" in first
    assert "priority" in first
    assert first["source"] == "Demostración"


def test_places_client_rejects_empty_key_and_wildcard_field_mask():
    client = PlacesClient(api_key="")
    with pytest.raises(PlacesAPIError) as exc_info:
        client.search_nearby(lat=36.7213, lon=-4.4214, radius_meters=500)
    assert "GOOGLE_MAPS_API_KEY" in str(exc_info.value)

    client_with_key = PlacesClient(api_key="test-fake-key-no-network")
    with pytest.raises(ValueError) as exc_wc:
        client_with_key._get_headers("*")
    assert "X-Goog-FieldMask" in str(exc_wc.value)


def test_parse_place_record_full_and_missing_fields():
    raw_full = {
        "id": "places/ChIJ_test_place_123",
        "displayName": {"text": "Clínica Dental Alameda"},
        "formattedAddress": "Alameda Principal 14, Distrito Centro, 29005 Málaga, España",
        "location": {"latitude": 36.7185, "longitude": -4.4220},
        "primaryType": "dentist",
        "types": ["dentist", "health", "point_of_interest"],
        "businessStatus": "OPERATIONAL",
        "nationalPhoneNumber": "952 11 22 33",
        "websiteUri": "",
        "googleMapsUri": "https://maps.google.com/?cid=123456",
        "googleMapsLinks": {
            "placeUri": "https://maps.google.com/?cid=123456",
            "writeAReviewUri": "https://search.google.com/local/writereview?placeid=ChIJ_test_place_123",
        },
        "rating": 4.6,
        "userRatingCount": 48,
        "regularOpeningHours": {
            "weekdayDescriptions": [
                "lunes: 9:00–20:00",
                "martes: 9:00–20:00",
            ]
        },
    }

    parsed = PlacesClient.parse_place_record(
        raw_full,
        sector_name="Clínicas dentales",
        default_city="Málaga",
    )

    assert parsed["place_id"] == "ChIJ_test_place_123"
    assert parsed["source_id"] == "ChIJ_test_place_123"
    assert parsed["name"] == "Clínica Dental Alameda"
    assert parsed["lat"] == 36.7185
    assert parsed["lon"] == -4.4220
    assert parsed["primary_type"] == "dentist"
    assert "dentist" in parsed["types"]
    assert parsed["phone"] == "952 11 22 33"
    assert parsed["website"] == ""
    assert parsed["rating"] == 4.6
    assert parsed["user_ratings_total"] == 48
    assert "writereview" in parsed["review_url"]
    assert "lunes: 9:00–20:00" in parsed["opening_hours"]
    assert parsed["source"] == "Google Places"
    assert parsed["data_source"] == "Google Places API (New)"

    # Minimal record with missing fields -> None / "" (never invented)
    raw_minimal = {
        "id": "ChIJ_minimal_999",
        "displayName": {"text": "Taller Sin Datos"},
    }
    parsed_min = PlacesClient.parse_place_record(
        raw_minimal,
        sector_name="Talleres mecánicos",
        default_city="Málaga",
    )
    assert parsed_min["place_id"] == "ChIJ_minimal_999"
    assert parsed_min["phone"] == ""
    assert parsed_min["website"] == ""
    assert parsed_min["rating"] is None
    assert parsed_min["user_ratings_total"] is None
    assert parsed_min["lat"] is None
    assert parsed_min["lon"] is None
    assert (
        config.get_web_label(config.WEB_NONE, parsed_min["source"])
        == "Sin web identificada en Google Places"
    )


def test_google_places_search_around_deduplication_and_limits(tmp_path):
    db_file = str(tmp_path / "test_places.db")
    init_db(db_file)
    reset_api_usage_stats(db_file)

    duplicate_place_a = {
        "id": "ChIJ_dup_001",
        "displayName": {"text": "Restaurante El Puerto"},
        "formattedAddress": "Calle Larios 4, 29005 Málaga",
        "location": {"latitude": 36.7205, "longitude": -4.4210},
        "primaryType": "restaurant",
        "types": ["restaurant", "food"],
        "businessStatus": "OPERATIONAL",
        "nationalPhoneNumber": "952 00 11 22",
        "rating": 4.2,
        "userRatingCount": 18,
    }
    unique_place_b = {
        "id": "ChIJ_uniq_002",
        "displayName": {"text": "Clínica Dental Centro"},
        "formattedAddress": "Alameda Principal 8, 29005 Málaga",
        "location": {"latitude": 36.7190, "longitude": -4.4218},
        "primaryType": "dentist",
        "types": ["dentist", "health"],
        "businessStatus": "OPERATIONAL",
        "nationalPhoneNumber": "952 33 44 55",
        "websiteUri": "https://clinicadentalcentro.example.com",
        "rating": 4.8,
        "userRatingCount": 95,
    }

    with patch.object(
        PlacesClient,
        "search_nearby",
        return_value={"places": [duplicate_place_a, duplicate_place_a, unique_place_b]},
    ) as mock_nearby, patch.object(
        PlacesClient,
        "search_places_text",
        return_value={"places": [duplicate_place_a]},
    ) as mock_text:
        provider = GooglePlacesProvider(api_key="mock-api-key", db_path=db_file)
        results = provider.search_around(
            lat=36.7202,
            lon=-4.4215,
            radius_m=600,
            sectors=["Restaurantes y bares", "Clínicas dentales"],
            city_hint="Málaga",
            max_results=20,
            max_requests=2,
        )

        assert mock_nearby.call_count == 1
        assert mock_text.call_count <= 1
        assert len(results) == 2
        place_ids = [r["place_id"] for r in results]
        assert place_ids == ["ChIJ_dup_001", "ChIJ_uniq_002"]


def test_google_places_error_handling_no_silent_fallback(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_err.db")
    init_db(db_file)
    reset_duplicate_request_guard()

    # 1. Missing API key raises PlacesAPIError and does NOT fall back to OSM
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)

    with patch.object(OSMOverpassProvider, "search_around") as mock_osm:
        with pytest.raises(PlacesAPIError) as exc_missing:
            generate_prospecting_route(
                start_lat=36.7202,
                start_lon=-4.4215,
                start_label="Centro Histórico",
                radius_m=500,
                categories=["Restaurantes y bares"],
                requested_stops=8,
                provider_id="google_places",
                api_key="",
                db_path=db_file,
            )
        assert "GOOGLE_MAPS_API_KEY" in str(exc_missing.value)
        assert mock_osm.call_count == 0

    # 2. HTTP 429 Quota Exceeded raises PlacesAPIError with clear message
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.json.return_value = {"error": {"message": "Quota exceeded for quota metric"}}

    with patch("places_client.requests.post", return_value=mock_resp):
        client = PlacesClient(api_key="mock-key-429", db_path=db_file)
        with pytest.raises(PlacesAPIError) as exc_429:
            client.search_nearby(lat=36.7202, lon=-4.4215, radius_meters=500)
        assert exc_429.value.status_code == 429
        assert "429" in str(exc_429.value)


def test_on_demand_place_details_preserves_user_crm_data(tmp_path):
    db_file = str(tmp_path / "test_details.db")
    init_db(db_file)
    reset_api_usage_stats(db_file)

    initial_lead = {
        "name": "Asesoría Fiscal Sur",
        "sector": "Asesorías y gestorías",
        "address": "Calle Córdoba 5, Málaga",
        "city": "Málaga",
        "postal_code": "29001",
        "phone": "",
        "website": "",
        "web_status": config.WEB_NONE,
        "rating": 4.1,
        "user_ratings_total": 12,
        "map_url": "https://maps.google.com/?cid=999",
        "source": "Google Places",
        "data_source": "Google Places API (New)",
        "source_id": "ChIJ_asesoria_777",
        "place_id": "ChIJ_asesoria_777",
        "lat": 36.7180,
        "lon": -4.4212,
        "score": 82,
        "priority": "Alta",
        "scoring_reasons": ["No dispone de página web propia"],
    }
    lead_id, is_new = save_lead(initial_lead, db_path=db_file)
    assert is_new is True

    # User records commercial visit notes & custom status
    update_lead_commercial(
        lead_id=lead_id,
        commercial_status="Visitado",
        notes="Hablé con la gerente María, interesada en presupuesto web.",
        next_action="Enviar propuesta el jueves",
        db_path=db_file,
    )

    mock_details_payload = {
        "id": "ChIJ_asesoria_777",
        "displayName": {"text": "Asesoría Fiscal Sur S.L."},
        "formattedAddress": "Calle Córdoba 5, 2ª Planta, 29001 Málaga, España",
        "location": {"latitude": 36.7180, "longitude": -4.4212},
        "primaryType": "accounting",
        "types": ["accounting", "finance"],
        "businessStatus": "OPERATIONAL",
        "nationalPhoneNumber": "952 99 88 77",
        "websiteUri": "",
        "googleMapsUri": "https://maps.google.com/?cid=999",
        "rating": 4.3,
        "userRatingCount": 19,
        "regularOpeningHours": {
            "weekdayDescriptions": ["lunes a viernes: 9:00–14:00"]
        },
    }

    with patch.object(
        PlacesClient,
        "get_place_details",
        return_value=mock_details_payload,
    ) as mock_details:
        gp = GooglePlacesProvider(api_key="mock-key", db_path=db_file)
        updated = gp.fetch_place_details_on_demand(
            lead_id=lead_id,
            place_id="ChIJ_asesoria_777",
            sector_name="Asesorías y gestorías",
            default_city="Málaga",
            db_path=db_file,
        )
        assert mock_details.call_count == 1
        assert updated["phone"] == "952 99 88 77"
        assert updated["user_ratings_total"] == 19
        assert "lunes a viernes: 9:00–14:00" in updated["opening_hours"]
        # User CRM fields MUST remain untouched
        assert updated["commercial_status"] == "Visitado"
        assert updated["notes"] == "Hablé con la gerente María, interesada en presupuesto web."
        assert updated["next_action"] == "Enviar propuesta el jueves"


def test_api_usage_counter(tmp_path):
    db_file = str(tmp_path / "test_counter.db")
    init_db(db_file)
    reset_api_usage_stats(db_file)

    stats0 = get_api_usage_stats(db_file)
    assert stats0["total_requests"] == 0

    increment_api_usage("nearby_search", count=1, db_path=db_file)
    increment_api_usage("text_search", count=2, db_path=db_file)
    increment_api_usage("place_details", count=1, db_path=db_file)

    stats1 = get_api_usage_stats(db_file)
    assert stats1["nearby_search"] == 1
    assert stats1["text_search"] == 2
    assert stats1["place_details"] == 1
    assert stats1["total_requests"] == 4
    assert stats1["last_request_at"] != ""


def test_vercel_wsgi_entrypoint_and_api(tmp_path, monkeypatch):
    import io
    import json
    import app as vercel_entry
    import vercel_web

    db_file = str(tmp_path / "test_vercel.db")
    monkeypatch.setattr(vercel_web, "DATABASE_PATH", db_file)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)

    assert callable(vercel_entry.app)
    assert callable(vercel_entry.application)

    # 1. GET / -> HTML SPA
    statuses = []
    headers_list = []

    def start_response(status, headers):
        statuses.append(status)
        headers_list.append(headers)

    body_chunks = vercel_entry.app(
        {"REQUEST_METHOD": "GET", "PATH_INFO": "/"},
        start_response,
    )
    html_str = b"".join(body_chunks).decode("utf-8")
    assert statuses[-1] == "200 OK"
    assert "BuscaLeads" in html_str
    assert "Google Places API (New)" in html_str

    # 1b. GET /app.py (Vercel rewrite behaviour) -> HTML SPA
    vercel_root_chunks = vercel_entry.app(
        {"REQUEST_METHOD": "GET", "PATH_INFO": "/app.py"},
        start_response,
    )
    assert statuses[-1] == "200 OK"
    assert "BuscaLeads" in b"".join(vercel_root_chunks).decode("utf-8")

    # 2. GET /api/state sin autenticación -> 401 Unauthorized
    unauth_chunks = vercel_entry.app(
        {"REQUEST_METHOD": "GET", "PATH_INFO": "/app.py", "QUERY_STRING": "__path=/api/state"},
        start_response,
    )
    assert statuses[-1] == "401 Unauthorized"

    # 2b. POST /api/auth/login con admin123 / admin123 -> 200 OK con token
    login_body = json.dumps({"username": "admin123", "password": "admin123"}).encode("utf-8")
    login_chunks = vercel_entry.app(
        {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/auth/login",
            "CONTENT_LENGTH": str(len(login_body)),
            "wsgi.input": io.BytesIO(login_body),
        },
        start_response,
    )
    assert statuses[-1] == "200 OK"
    login_data = json.loads(b"".join(login_chunks).decode("utf-8"))
    assert login_data["ok"] is True
    auth_token = login_data["token"]
    assert bool(auth_token)

    # 2c. GET /api/state con token de autorización -> 200 OK con datos completos
    state_chunks = vercel_entry.app(
        {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/app.py",
            "QUERY_STRING": "__path=/api/state",
            "HTTP_AUTHORIZATION": f"Bearer {auth_token}",
        },
        start_response,
    )
    state_data = json.loads(b"".join(state_chunks).decode("utf-8"))
    assert statuses[-1] == "200 OK"
    assert "stats" in state_data
    assert "catalog" in state_data
    assert "api_usage" in state_data

    # 3. POST /api/routes/generate con token pero sin key de Google -> 400 Bad Request
    payload_bytes = json.dumps(
        {
            "provider_id": "google_places",
            "start_lat": 36.7213,
            "start_lon": -4.4214,
            "start_label": "Centro Histórico",
            "radius_m": 500,
            "requested_stops": 10,
            "categories": ["Restauración"],
        }
    ).encode("utf-8")
    err_chunks = vercel_entry.app(
        {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/routes/generate",
            "CONTENT_LENGTH": str(len(payload_bytes)),
            "HTTP_AUTHORIZATION": f"Bearer {auth_token}",
            "wsgi.input": io.BytesIO(payload_bytes),
        },
        start_response,
    )
    err_data = json.loads(b"".join(err_chunks).decode("utf-8"))
    assert statuses[-1] == "400 Bad Request"
    assert err_data["ok"] is False
    assert "GOOGLE_MAPS_API_KEY" in err_data["error"]



