"""
FACENIA Lead Finder — BuscaLeads (Aplicación Móvil de Prospección Comercial y Rutas a Pie)
Diseñada mobile-first con barra de navegación inferior fija (Inicio, Mapa, Negocios, Visitas, Perfil),
flujo dedicado de creación de ruta, mapa interactivo a pie (Leaflet + OSRM), fichas de negocio con
argumentario comercial y generador de enlaces NFC, registro táctil de visitas y perfil/ajustes.
"""

import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
import pandas as pd
import streamlit as st

from config import (
    BACKUPS_DIR,
    COMMERCIAL_STATUSES,
    DATABASE_PATH,
    DEFAULT_COMMERCIAL_STATUS,
    DEFAULT_DATA_PROVIDER,
    DEFAULT_SECTORS,
    DEFAULT_START,
    FACENIA_SERVICES,
    OUTCOME_NOT_INTERESTED,
    OUTCOME_RETURN,
    OUTCOME_SOLD,
    PLACES_MAX_REQUESTS_PER_SEARCH,
    PLACES_MAX_RESULTS_PER_SEARCH,
    ROUTE_MAX_STOPS,
    ROUTE_MIN_STOPS,
    ROUTE_RADIUS_OPTIONS_M,
    SECTOR_NAMES,
    SECTORS,
    WEB_CONFIRMED_NONE,
    WEB_FOUND,
    WEB_INSUFFICIENT,
    WEB_LABELS,
    WEB_NONE,
    WEB_REVIEW,
    WEB_SOCIAL,
    WEB_STATUSES,
    get_web_label,
    is_google_maps_configured,
)
from database import (
    backup_database,
    get_api_usage_stats,
    get_lead,
    get_leads,
    get_recent_interactions,
    get_route,
    get_setting,
    get_stats,
    init_db,
    list_routes,
    record_visit_outcome,
    reset_api_usage_stats,
    set_manual_web_status,
    set_setting,
    undo_interaction,
    update_lead_commercial,
    update_lead_review_nfc,
    update_route_progress,
)
from export_service import export_leads_to_csv_bytes, export_leads_to_excel_bytes
from pitch_service import (
    NFC_STATUS_OPTIONS,
    REVIEW_URL_STATUS_OPTIONS,
    generate_commercial_recommendation,
    generate_sales_pitch,
    prepare_review_link_info,
)
from places_client import PlacesAPIError
from providers import GooglePlacesProvider, OSMOverpassProvider
from routing_service import (
    build_google_maps_directions_to_lead,
    build_google_maps_place_url,
    build_google_maps_walking_route_url,
    format_distance,
    format_duration,
    haversine_m,
)
from search_service import generate_prospecting_route
from ui_components import (
    bottom_nav_bar,
    copy_and_share_bar,
    geolocation_button,
    route_map_component,
)
from verification import check_website

# --------------------------------------------------------------------------
# 1. Configuración de página e inicialización de base de datos
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="BuscaLeads · FACENIA",
    page_icon=":material/route:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

init_db()

# --------------------------------------------------------------------------
# 2. Estilos CSS Mobile-First (App Shell + Cards + Padding para Bottom Nav)
# --------------------------------------------------------------------------
st.html(
    """
<style>
  /* Ocultar decoración superior innecesaria de Streamlit para aspecto de app nativa */
  header[data-testid="stHeader"] {
    background: transparent !important;
    height: 1.5rem !important;
  }
  div[data-testid="stDecoration"] {
    display: none !important;
  }

  /* Contenedor principal centrado tipo móvil/tablet y con espacio inferior para la barra fija */
  .block-container {
    padding-top: 0.75rem !important;
    padding-bottom: 115px !important;
    max-width: 680px !important;
    margin: 0 auto !important;
  }
  @media (max-width: 640px) {
    .block-container {
      padding-left: 0.75rem !important;
      padding-right: 0.75rem !important;
      padding-top: 0.5rem !important;
      padding-bottom: 115px !important;
    }
  }

  /* Botones principales dorados (#F5B82E) con alto contraste */
  button[kind="primary"] {
    background-color: #F5B82E !important;
    color: #0B0D11 !important;
    border: 1px solid #F5B82E !important;
    font-weight: 700 !important;
    min-height: 48px !important;
    border-radius: 12px !important;
    box-shadow: 0 4px 14px rgba(245, 184, 46, 0.18) !important;
  }
  button[kind="primary"] p,
  button[kind="primary"] span {
    color: #0B0D11 !important;
    font-weight: 700 !important;
  }
  button[kind="primary"]:hover {
    background-color: #FACC15 !important;
    border-color: #FACC15 !important;
  }
  button[kind="secondary"] {
    min-height: 44px !important;
    border-radius: 11px !important;
    font-weight: 600 !important;
    border-color: #262D3B !important;
    background-color: #151922 !important;
  }
  button[kind="secondary"]:hover {
    border-color: #F5B82E !important;
  }

  /* Botón específico de VENTA (verde esmeralda) */
  .st-key-btn_outcome_sold button,
  .st-key-btn_quick_sold button {
    background-color: #22C55E !important;
    color: #052E16 !important;
    border: 1px solid #22C55E !important;
    font-weight: 700 !important;
    min-height: 50px !important;
  }
  .st-key-btn_outcome_sold button p,
  .st-key-btn_quick_sold button p,
  .st-key-btn_outcome_sold button span,
  .st-key-btn_quick_sold button span {
    color: #052E16 !important;
    font-weight: 700 !important;
  }

  /* Botón específico de VOLVER OTRO DÍA (ámbar) */
  .st-key-btn_outcome_return button {
    border-color: #F59E0B !important;
    color: #FBBF24 !important;
    min-height: 50px !important;
  }
  /* Botón específico de NO INTERESA (rojo suave) */
  .st-key-btn_outcome_discard button {
    border-color: #EF4444 !important;
    color: #F87171 !important;
    min-height: 50px !important;
  }
  /* Botón específico de PENDIENTE */
  .st-key-btn_outcome_pending button {
    border-color: #64748B !important;
    color: #CBD5E1 !important;
    min-height: 50px !important;
  }

  /* Cabecera móvil compacta */
  .app-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 12px 14px;
    background: #141821;
    border: 1px solid #262D3B;
    border-radius: 14px;
    margin-bottom: 12px;
  }
  .app-brand-group {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .app-brand-badge {
    background: #F5B82E;
    color: #0B0D11;
    font-weight: 800;
    font-size: 11.5px;
    padding: 4px 8px;
    border-radius: 7px;
    letter-spacing: 0.04em;
  }
  .app-brand-title {
    font-size: 15.5px;
    font-weight: 700;
    color: #F9FAFB;
    line-height: 1.2;
  }
  .app-brand-sub {
    font-size: 11.5px;
    color: #94A3B8;
    line-height: 1.2;
  }

  /* Grid 2x2 de KPIs en Inicio */
  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
    margin-bottom: 14px;
  }
  .kpi-card {
    background: #141821;
    border: 1px solid #262D3B;
    border-radius: 14px;
    padding: 13px 14px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 6px;
  }
  .kpi-card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: #94A3B8;
    font-size: 12px;
    font-weight: 600;
  }
  .kpi-icon {
    width: 18px;
    height: 18px;
    display: inline-block;
    background-repeat: no-repeat;
    background-position: center;
    background-size: contain;
    flex-shrink: 0;
  }
  .kpi-icon-store {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='%23F5B82E' stroke-width='2.1' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z'/%3E%3Cpath d='M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2'/%3E%3Cpath d='M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2'/%3E%3Cpath d='M10 6h4'/%3E%3Cpath d='M10 10h4'/%3E%3Cpath d='M10 14h4'/%3E%3Cpath d='M10 18h4'/%3E%3C/svg%3E");
  }
  .kpi-icon-pin {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='%2338BDF8' stroke-width='2.1' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0'/%3E%3Ccircle cx='12' cy='10' r='3'/%3E%3C/svg%3E");
  }
  .kpi-icon-clock {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='%23F59E0B' stroke-width='2.1' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='10'/%3E%3Cpolyline points='12 6 12 12 16 14'/%3E%3C/svg%3E");
  }
  .kpi-icon-check {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='%2322C55E' stroke-width='2.1' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M22 11.08V12a10 10 0 1 1-5.93-9.14'/%3E%3Cpolyline points='22 4 12 14.01 9 11.01'/%3E%3C/svg%3E");
  }
  .kpi-val {
    font-size: 24px;
    font-weight: 800;
    color: #F9FAFB;
    line-height: 1.1;
    letter-spacing: -0.02em;
  }
  .kpi-val.gold {
    color: #FACC15;
  }
  .kpi-val.green {
    color: #22C55E;
  }
  .kpi-foot {
    font-size: 11.5px;
    color: #64748B;
  }

  /* Tarjetas móviles personalizadas */
  .m-card {
    background: #141821;
    border: 1px solid #262D3B;
    border-radius: 14px;
    padding: 14px 16px;
    margin-bottom: 10px;
  }
  .m-card-active {
    background: linear-gradient(180deg, rgba(245, 184, 46, 0.09) 0%, rgba(20, 24, 33, 0.98) 100%);
    border: 1px solid rgba(245, 184, 46, 0.45);
    border-radius: 14px;
    padding: 15px 16px;
    margin-bottom: 12px;
  }
  .m-section-title {
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #94A3B8;
    margin: 14px 0 8px 2px;
  }

  /* Píldoras de puntuación y estado */
  .score-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 10px;
    border-radius: 999px;
    font-weight: 700;
    font-size: 12px;
    white-space: nowrap;
  }
  .score-high {
    background: rgba(245, 184, 46, 0.16);
    color: #FACC15;
    border: 1px solid rgba(245, 184, 46, 0.45);
  }
  .score-med {
    background: rgba(56, 189, 248, 0.14);
    color: #38BDF8;
    border: 1px solid rgba(56, 189, 248, 0.35);
  }
  .score-low {
    background: rgba(156, 163, 175, 0.14);
    color: #9CA3AF;
    border: 1px solid rgba(156, 163, 175, 0.3);
  }
  .stop-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 26px;
    height: 26px;
    padding: 0 7px;
    border-radius: 8px;
    background: #F5B82E;
    color: #0B0D11;
    font-weight: 800;
    font-size: 12.5px;
    margin-right: 6px;
  }
</style>
"""
)

# --------------------------------------------------------------------------
# 3. Estado de sesión y preferencias persistentes
# --------------------------------------------------------------------------
NAV_TABS = ["inicio", "mapa", "negocios", "visitas", "perfil"]

PRESET_ZONES = {
    "Centro Histórico (Plaza Constitución)": (36.7213, -4.4214),
    "Soho / Alameda Principal": (36.7165, -4.4252),
    "Teatinos (Av. Plutarco)": (36.7192, -4.4748),
    "Carretera de Cádiz / Huelin": (36.7018, -4.4394),
    "El Palo / Pedregalejo": (36.7211, -4.3608),
    "Ciudad Jardín": (36.7425, -4.4197),
}

saved_default_zone = get_setting("default_zone_label", DEFAULT_START["label"])
saved_default_lat = float(get_setting("default_zone_lat", str(DEFAULT_START["lat"])) or DEFAULT_START["lat"])
saved_default_lon = float(get_setting("default_zone_lon", str(DEFAULT_START["lon"])) or DEFAULT_START["lon"])
saved_default_stops = int(get_setting("default_stops", "12") or 12)
saved_default_radius = int(get_setting("default_radius_m", "500") or 500)
saved_default_provider = get_setting("default_provider", DEFAULT_DATA_PROVIDER)
if saved_default_provider not in ("google_places", "osm"):
    saved_default_provider = DEFAULT_DATA_PROVIDER
saved_max_results = int(get_setting("places_max_results", str(PLACES_MAX_RESULTS_PER_SEARCH)) or PLACES_MAX_RESULTS_PER_SEARCH)
saved_max_requests = int(get_setting("places_max_requests", str(PLACES_MAX_REQUESTS_PER_SEARCH)) or PLACES_MAX_REQUESTS_PER_SEARCH)

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

if not st.session_state.authenticated:
    st.markdown(
        """
        <div style="text-align: center; margin-top: 1.5rem; margin-bottom: 1.5rem;">
            <div style="display:inline-block; padding: 14px 18px; background: rgba(226, 179, 37, 0.15); border-radius: 20px; border: 1.5px solid rgba(226, 179, 37, 0.4);">
                <span style="font-size: 28px;">🔒</span>
            </div>
            <div style="font-size: 11px; font-weight: 800; color: #E2B325; letter-spacing: 0.12em; text-transform: uppercase; margin-top: 12px;">FACENIA · ACCESO EXCLUSIVO</div>
            <h1 style="margin: 4px 0 6px 0; font-size: 26px; font-weight: 800;">BuscaLeads</h1>
            <p style="color: #9CA3AF; font-size: 13px; margin: 0 auto; max-width: 320px;">Introduce tus credenciales para acceder al sistema.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.form("login_form", clear_on_submit=False):
        u = st.text_input("Usuario", value="admin123", key="login_username_input")
        p = st.text_input("Contraseña", type="password", key="login_password_input")
        submitted = st.form_submit_button("Iniciar sesión →", use_container_width=True, type="primary")
        if submitted:
            from auth_service import authenticate
            ok, user_info, err = authenticate(u, p, DATABASE_PATH)
            if ok:
                st.session_state.authenticated = True
                st.session_state.auth_user = user_info
                st.rerun()
            else:
                st.error(err or "Usuario o contraseña incorrectos.")
    st.markdown(
        """
        <div style="text-align: center; margin-top: 1.5rem; font-size: 11px; color: #9CA3AF;">
            🔒 <strong>Acceso protegido</strong> · Las altas y bajas se gestionan exclusivamente desde la base de datos de FACENIA.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

if "nav_tab" not in st.session_state:
    st.session_state.nav_tab = "inicio"
if "sub_screen" not in st.session_state:
    st.session_state.sub_screen = None  # None | "crear_ruta" | "ficha_negocio"
if "active_provider" not in st.session_state:
    st.session_state.active_provider = saved_default_provider
if "places_max_results" not in st.session_state:
    st.session_state.places_max_results = saved_max_results
if "places_max_requests" not in st.session_state:
    st.session_state.places_max_requests = saved_max_requests
if "start_label" not in st.session_state:
    st.session_state.start_label = saved_default_zone
if "start_lat" not in st.session_state:
    st.session_state.start_lat = saved_default_lat
if "start_lon" not in st.session_state:
    st.session_state.start_lon = saved_default_lon
if "location_candidates" not in st.session_state:
    st.session_state.location_candidates = []
if "requested_stops" not in st.session_state:
    st.session_state.requested_stops = saved_default_stops
if "radius_m" not in st.session_state:
    st.session_state.radius_m = saved_default_radius if saved_default_radius in ROUTE_RADIUS_OPTIONS_M else 500
if "selected_categories" not in st.session_state:
    st.session_state.selected_categories = list(DEFAULT_SECTORS)
if "active_route_id" not in st.session_state:
    latest_route = get_route(None)
    st.session_state.active_route_id = latest_route["id"] if latest_route else None
if "active_lead_id" not in st.session_state:
    latest_route = get_route(st.session_state.active_route_id) if st.session_state.active_route_id else None
    if latest_route and latest_route.get("stops"):
        idx = min(int(latest_route.get("current_index", 0)), len(latest_route["stops"]) - 1)
        st.session_state.active_lead_id = latest_route["stops"][max(0, idx)]["lead_id"]
    else:
        all_existing = get_leads()
        st.session_state.active_lead_id = all_existing[0]["id"] if all_existing else None
if "pitch_variants" not in st.session_state:
    st.session_state.pitch_variants = {}
if "last_processed_gps_ts" not in st.session_state:
    st.session_state.last_processed_gps_ts = 0
if "outcome_mode" not in st.session_state:
    st.session_state.outcome_mode = None
if "flash_message" not in st.session_state:
    st.session_state.flash_message = None


def _open_lead_ficha(lead_id: int, stop_index: Optional[int] = None) -> None:
    st.session_state.active_lead_id = int(lead_id)
    if stop_index is not None and st.session_state.active_route_id:
        update_route_progress(st.session_state.active_route_id, int(stop_index))
    st.session_state.nav_tab = "negocios"
    st.session_state.sub_screen = "ficha_negocio"


def _open_lead_visit(lead_id: int, stop_index: Optional[int] = None) -> None:
    st.session_state.active_lead_id = int(lead_id)
    if stop_index is not None and st.session_state.active_route_id:
        update_route_progress(st.session_state.active_route_id, int(stop_index))
    st.session_state.nav_tab = "visitas"
    st.session_state.sub_screen = None


def _format_radius_label(m: int) -> str:
    if m < 1000:
        return f"{m} m"
    km = m / 1000.0
    return f"{int(km)} km" if km.is_integer() else f"{km:.1f} km".replace(".", ",")


def _web_badge_color(web_status: str) -> str:
    if web_status in (WEB_NONE, WEB_CONFIRMED_NONE):
        return "orange"
    if web_status == WEB_SOCIAL:
        return "yellow"
    if web_status == WEB_FOUND:
        return "green"
    if web_status == WEB_REVIEW:
        return "blue"
    return "gray"


def _status_badge_color(comm_status: str) -> str:
    if comm_status == "Cliente conseguido":
        return "green"
    if comm_status in ("Seguimiento pendiente", "Contactado"):
        return "orange"
    if comm_status == "Visitado":
        return "blue"
    if comm_status == "Descartado":
        return "red"
    return "gray"


# Cargar ruta activa si existe
active_route = get_route(st.session_state.active_route_id) if st.session_state.active_route_id else get_route(None)
if active_route and st.session_state.active_route_id is None:
    st.session_state.active_route_id = active_route["id"]

# Mostrar mensaje flash si existe
if st.session_state.flash_message:
    st.success(st.session_state.flash_message, icon=":material/check_circle:")
    st.session_state.flash_message = None


# ==========================================================================
# PANTALLA 1: INICIO (RESUMEN COMERCIAL + RUTA ACTIVA + FLUJO "CREAR RUTA")
# ==========================================================================
if st.session_state.nav_tab == "inicio":
    # ----------------------------------------------------------------------
    # SUB-PANTALLA DEDICADA: CREAR RUTA
    # ----------------------------------------------------------------------
    if st.session_state.sub_screen == "crear_ruta":
        c_back, c_title = st.columns([1.2, 2.8])
        if c_back.button("Volver a Inicio", icon=":material/arrow_back:", width="stretch", key="btn_back_from_create"):
            st.session_state.sub_screen = None
            st.rerun()
        c_title.markdown("### Crear ruta comercial a pie")

        # Tarjeta 0: Selección de fuente de datos (Google Places API New principal / OpenStreetMap alternativa)
        with st.container(border=True):
            st.markdown("**:material/cloud_sync: Fuente de datos para la búsqueda**")
            provider_options = {
                "google_places": "Google Places API (New) · Principal",
                "osm": "OpenStreetMap · Alternativa abierta",
            }
            chosen_prov = st.segmented_control(
                "Proveedor de datos",
                options=list(provider_options.keys()),
                default=st.session_state.active_provider if st.session_state.active_provider in provider_options else "google_places",
                format_func=lambda k: provider_options[k],
                label_visibility="collapsed",
                width="stretch",
                key="seg_provider_create_route",
            )
            if chosen_prov and chosen_prov != st.session_state.active_provider:
                st.session_state.active_provider = chosen_prov
                st.rerun()

            if st.session_state.active_provider == "google_places":
                if is_google_maps_configured():
                    st.markdown(
                        ":green-badge[:material/verified_user: GOOGLE_MAPS_API_KEY configurada en servidor] · "
                        f"Límite por consulta: **máx. {st.session_state.places_max_results} resultados** "
                        f"y **máx. {st.session_state.places_max_requests} peticiones API**."
                    )
                else:
                    st.markdown(
                        ":orange-badge[:material/key_off: GOOGLE_MAPS_API_KEY pendiente de configurar en .env] · "
                        "Configura tu clave en `.env` para lanzar búsquedas en Google Places, o selecciona OpenStreetMap arriba."
                    )
            else:
                st.caption("Usando OpenStreetMap (Overpass API) como fuente abierta gratuita.")

        # Tarjeta 1: Punto de salida (GPS + Zonas rápidas + Buscador de dirección)
        with st.container(border=True):
            st.markdown("**:material/my_location: 1. Punto de salida**")
            st.caption(f"Ubicación seleccionada: **{st.session_state.start_label}**")

            geo_res = geolocation_button(
                current_label=f"{st.session_state.start_label} ({st.session_state.start_lat:.4f}, {st.session_state.start_lon:.4f})",
                key="geo_btn_create_route",
            )
            if geo_res and getattr(geo_res, "coords", None):
                coords_payload = geo_res.coords
                ts = coords_payload.get("ts", 0)
                if ts and ts != st.session_state.last_processed_gps_ts:
                    st.session_state.last_processed_gps_ts = ts
                    lat_gps = float(coords_payload["lat"])
                    lon_gps = float(coords_payload["lon"])
                    osm = OSMOverpassProvider()
                    resolved_lbl = osm.reverse_geocode(lat_gps, lon_gps)
                    st.session_state.start_lat = lat_gps
                    st.session_state.start_lon = lon_gps
                    st.session_state.start_label = resolved_lbl
                    st.session_state.flash_message = f"Ubicación GPS fijada: {resolved_lbl}"
                    st.rerun()

            st.caption("Zonas rápidas de Málaga:")
            preset_choice = st.pills(
                "Zonas frecuentes de Málaga",
                options=list(PRESET_ZONES.keys()),
                selection_mode="single",
                default=st.session_state.start_label if st.session_state.start_label in PRESET_ZONES else None,
                label_visibility="collapsed",
                key="pills_preset_zone_create",
            )
            if preset_choice and preset_choice in PRESET_ZONES:
                p_lat, p_lon = PRESET_ZONES[preset_choice]
                if (st.session_state.start_lat, st.session_state.start_lon) != (p_lat, p_lon):
                    st.session_state.start_label = preset_choice
                    st.session_state.start_lat = p_lat
                    st.session_state.start_lon = p_lon
                    st.rerun()

            col_q, col_btn_q = st.columns([2.6, 1.1])
            address_query = col_q.text_input(
                "Buscar calle, dirección o barrio",
                placeholder="Ej. Calle Larios, Teatinos, Huelin...",
                label_visibility="collapsed",
                key="input_manual_location_query",
            )
            if col_btn_q.button("Buscar", icon=":material/search:", width="stretch", key="btn_search_zone"):
                if address_query.strip():
                    osm = OSMOverpassProvider()
                    cands = osm.search_locations(address_query.strip(), limit=5)
                    st.session_state.location_candidates = cands
                    if cands:
                        first = cands[0]
                        st.session_state.start_label = first["label"]
                        st.session_state.start_lat = float(first["lat"])
                        st.session_state.start_lon = float(first["lon"])
                    else:
                        st.warning("No se encontró esa dirección en OpenStreetMap. Prueba con calle y municipio.")

            if st.session_state.location_candidates:
                cand_options = [
                    f"{c['label']} ({c['lat']:.4f}, {c['lon']:.4f})"
                    for c in st.session_state.location_candidates
                ]
                chosen_cand = st.selectbox(
                    "Resultados encontrados",
                    options=cand_options,
                    index=0,
                    key="select_location_candidate",
                )
                if chosen_cand:
                    c_idx = cand_options.index(chosen_cand)
                    c_obj = st.session_state.location_candidates[c_idx]
                    st.session_state.start_label = c_obj["label"]
                    st.session_state.start_lat = float(c_obj["lat"])
                    st.session_state.start_lon = float(c_obj["lon"])

        # Tarjeta 2: Cantidad de negocios y Radio a pie
        with st.container(border=True):
            st.markdown("**:material/tune: 2. Paradas y radio de búsqueda a pie**")

            st.caption("Cantidad de negocios a visitar (entre 5 y 30):")
            b_minus, b_val, b_plus = st.columns([1, 2, 1])
            if b_minus.button("− 1", key="btn_stops_dec", width="stretch"):
                st.session_state.requested_stops = max(ROUTE_MIN_STOPS, int(st.session_state.requested_stops) - 1)
                st.rerun()
            b_val.markdown(
                f"<div style='text-align:center;font-size:20px;font-weight:800;color:#FACC15;padding-top:6px;'>"
                f"{st.session_state.requested_stops} negocios</div>",
                unsafe_allow_html=True,
            )
            if b_plus.button("+ 1", key="btn_stops_inc", width="stretch"):
                st.session_state.requested_stops = min(ROUTE_MAX_STOPS, int(st.session_state.requested_stops) + 1)
                st.rerun()

            quick_stops = st.segmented_control(
                "Cantidad rápida",
                options=[5, 10, 12, 15, 20, 30],
                default=st.session_state.requested_stops if st.session_state.requested_stops in [5, 10, 12, 15, 20, 30] else None,
                format_func=lambda x: f"{x}",
                label_visibility="collapsed",
                width="stretch",
            )
            if quick_stops and int(quick_stops) != int(st.session_state.requested_stops):
                st.session_state.requested_stops = int(quick_stops)
                st.rerun()

            st.caption("Radio máximo caminando desde el punto de salida:")
            chosen_radius = st.segmented_control(
                "Radio geográfico",
                options=ROUTE_RADIUS_OPTIONS_M,
                default=st.session_state.radius_m if st.session_state.radius_m in ROUTE_RADIUS_OPTIONS_M else 500,
                format_func=_format_radius_label,
                label_visibility="collapsed",
                width="stretch",
            )
            if chosen_radius and int(chosen_radius) != int(st.session_state.radius_m):
                st.session_state.radius_m = int(chosen_radius)
                st.rerun()

        # Tarjeta 3: Categorías de negocio
        with st.container(border=True):
            st.markdown("**:material/category: 3. Categorías de negocio**")
            c_btn_all, c_btn_prio, c_btn_nfc = st.columns(3)
            if c_btn_all.button("Todas (13)", key="btn_cat_all", width="stretch"):
                st.session_state.selected_categories = list(SECTOR_NAMES)
                st.rerun()
            if c_btn_prio.button("Alta prioridad", key="btn_cat_prio", width="stretch"):
                st.session_state.selected_categories = [k for k, v in SECTORS.items() if v.get("priority")]
                st.rerun()
            if c_btn_nfc.button("Ideales NFC", key="btn_cat_nfc", width="stretch"):
                st.session_state.selected_categories = [k for k, v in SECTORS.items() if v.get("nfc")]
                st.rerun()

            cat_filter_text = st.text_input(
                "Filtrar categorías",
                placeholder="Filtrar categorías (ej. belleza, talleres, salud, reformas...)",
                label_visibility="collapsed",
                key="input_cat_filter",
            )
            visible_categories = [
                c for c in SECTOR_NAMES if not cat_filter_text.strip() or cat_filter_text.strip().lower() in c.lower()
            ]
            for sel in st.session_state.selected_categories:
                if sel in SECTOR_NAMES and sel not in visible_categories:
                    visible_categories.append(sel)

            picked_cats = st.pills(
                "Categorías comerciales",
                options=visible_categories,
                selection_mode="multi",
                default=[c for c in st.session_state.selected_categories if c in visible_categories],
                label_visibility="collapsed",
                key="pills_categories_create",
            )
            if picked_cats is not None:
                st.session_state.selected_categories = list(picked_cats)

            st.caption(f"{len(st.session_state.selected_categories)} categorías seleccionadas.")

        # Botón principal de generación de ruta
        n_target = int(st.session_state.requested_stops)
        if st.button(
            f"Generar ruta ({n_target} negocios)",
            type="primary",
            icon=":material/directions_walk:",
            width="stretch",
            key="btn_generate_route_main",
        ):
            if not st.session_state.selected_categories:
                st.error("Selecciona al menos una categoría de negocio antes de generar la ruta.", icon=":material/error:")
            else:
                progress = st.progress(0.0)
                status_box = st.empty()

                def _on_progress(pct: float, msg: str) -> None:
                    progress.progress(min(1.0, max(0.0, float(pct))))
                    status_box.caption(msg)

                try:
                    route_obj = generate_prospecting_route(
                        start_lat=float(st.session_state.start_lat),
                        start_lon=float(st.session_state.start_lon),
                        start_label=st.session_state.start_label,
                        radius_m=int(st.session_state.radius_m),
                        categories=list(st.session_state.selected_categories),
                        requested_stops=n_target,
                        provider_id=st.session_state.active_provider,
                        max_results_limit=int(st.session_state.places_max_results),
                        max_requests_limit=int(st.session_state.places_max_requests),
                        progress_callback=_on_progress,
                    )
                    progress.empty()
                    status_box.empty()
                    st.session_state.active_route_id = route_obj["id"]
                    stops = route_obj.get("stops") or []
                    if stops:
                        st.session_state.active_lead_id = stops[0]["lead_id"]
                        prov_lbl = "Google Places (New)" if st.session_state.active_provider == "google_places" else "OpenStreetMap"
                        st.session_state.flash_message = (
                            f"Ruta #{route_obj['id']} generada con {prov_lbl}: {len(stops)} paradas "
                            f"(de {route_obj.get('found_count', len(stops))} negocios reales encontrados en {_format_radius_label(int(st.session_state.radius_m))})."
                        )
                        st.session_state.sub_screen = None
                        st.session_state.nav_tab = "mapa"
                        st.rerun()
                    else:
                        st.warning(
                            f"No se encontraron negocios dentro de {_format_radius_label(int(st.session_state.radius_m))} "
                            "para esas categorías. Prueba a ampliar el radio a 1 km o seleccionar más categorías.",
                            icon=":material/info:",
                        )
                except PlacesAPIError as p_err:
                    progress.empty()
                    status_box.empty()
                    st.error(
                        f"**No se pudo consultar Google Places API (New):** {p_err}\n\n"
                        "Por transparencia y control de costes, **no se han simulado datos ni se ha cambiado silenciosamente de proveedor**. "
                        "Configura o revisa `GOOGLE_MAPS_API_KEY` en tu entorno, o selecciona manualmente **OpenStreetMap** en el selector superior.",
                        icon=":material/error:",
                    )
                except Exception as exc:
                    progress.empty()
                    status_box.empty()
                    st.error(f"No se pudo completar la consulta: {exc}", icon=":material/error:")

    # ----------------------------------------------------------------------
    # PANTALLA PRINCIPAL DE INICIO (DASHBOARD MÓVIL)
    # ----------------------------------------------------------------------
    else:
        stats = get_stats()
        found_total = int(stats.get("found_total", 0))
        visited_cnt = int(stats.get("visited_count", 0))
        followup_cnt = int(stats.get("followup_pending", 0))
        sales_cnt = int(stats.get("sales_count", 0))
        conv_rate = float(stats.get("conversion_rate", 0.0))
        no_web_cnt = int(stats.get("no_web", 0))

        # Cabecera de marca
        st.html(
            f"""
            <div class="app-topbar">
              <div class="app-brand-group">
                <span class="app-brand-badge">FACENIA</span>
                <div>
                  <div class="app-brand-title">BuscaLeads</div>
                  <div class="app-brand-sub">Prospección comercial en calle · Málaga</div>
                </div>
              </div>
              <span class="score-pill score-high">{no_web_cnt} sin web</span>
            </div>
            """
        )

        # Cuadrícula 2x2 de KPIs reales
        st.html(
            f"""
            <div class="kpi-grid">
              <div class="kpi-card">
                <div class="kpi-card-top">
                  <span>Negocios en cartera</span>
                  <span class="kpi-icon kpi-icon-store" aria-hidden="true"></span>
                </div>
                <div class="kpi-val">{found_total}</div>
                <div class="kpi-foot">{no_web_cnt} sin web identificada</div>
              </div>

              <div class="kpi-card">
                <div class="kpi-card-top">
                  <span>Visitas realizadas</span>
                  <span class="kpi-icon kpi-icon-pin" aria-hidden="true"></span>
                </div>
                <div class="kpi-val">{visited_cnt}</div>
                <div class="kpi-foot">Puerta fría y presencial</div>
              </div>

              <div class="kpi-card">
                <div class="kpi-card-top">
                  <span>Seguimientos</span>
                  <span class="kpi-icon kpi-icon-clock" aria-hidden="true"></span>
                </div>
                <div class="kpi-val gold">{followup_cnt}</div>
                <div class="kpi-foot">Volver otro día / pendientes</div>
              </div>

              <div class="kpi-card">
                <div class="kpi-card-top">
                  <span>Ventas cerradas</span>
                  <span class="kpi-icon kpi-icon-check" aria-hidden="true"></span>
                </div>
                <div class="kpi-val green">{sales_cnt}</div>
                <div class="kpi-foot">Conversión: {conv_rate:.1f}%</div>
              </div>
            </div>
            """
        )

        # Botón principal destacado: Crear ruta
        if st.button(
            "Crear ruta comercial a pie",
            type="primary",
            icon=":material/add_road:",
            width="stretch",
            key="btn_home_open_create_route",
        ):
            st.session_state.sub_screen = "crear_ruta"
            st.rerun()

        # Tarjeta de Ruta Activa (si existe) o Estado Vacío
        if active_route and active_route.get("stops"):
            stops_list = active_route["stops"]
            done_cnt = sum(
                1
                for s in stops_list
                if s.get("commercial_status") and s.get("commercial_status") != DEFAULT_COMMERCIAL_STATUS
            )
            pct_done = int(round((done_cnt / len(stops_list)) * 100)) if stops_list else 0
            dist_lbl = format_distance(active_route.get("total_distance_m")) if active_route.get("geometry") else "Tramos directos"
            dur_lbl = format_duration(active_route.get("total_duration_s")) if active_route.get("geometry") else ""

            st.html(
                f"""
                <div class="m-section-title">Ruta activa en curso</div>
                <div class="m-card-active">
                  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                    <div>
                      <div style="font-size:15px;font-weight:700;color:#F9FAFB;">
                        Ruta #{active_route['id']} · {active_route.get('start_label', 'Málaga')}
                      </div>
                      <div style="font-size:12.5px;color:#94A3B8;margin-top:3px;">
                        {len(stops_list)} paradas · Radio {_format_radius_label(int(active_route.get('radius_m', 500)))} · {dist_lbl} {('· ' + dur_lbl) if dur_lbl else ''}
                      </div>
                    </div>
                    <span class="score-pill score-high">{done_cnt}/{len(stops_list)} visitadas ({pct_done}%)</span>
                  </div>
                </div>
                """
            )
            ha1, ha2 = st.columns(2)
            if ha1.button("Continuar en Mapa", icon=":material/map:", width="stretch", key="btn_home_go_map"):
                st.session_state.nav_tab = "mapa"
                st.session_state.sub_screen = None
                st.rerun()
            if ha2.button("Registrar visitas", icon=":material/fact_check:", width="stretch", key="btn_home_go_visits"):
                st.session_state.nav_tab = "visitas"
                st.session_state.sub_screen = None
                st.rerun()
        else:
            st.html(
                """
                <div class="m-card" style="text-align:center;padding:22px 16px;margin-top:10px;">
                  <div style="font-size:15px;font-weight:700;color:#F9FAFB;margin-bottom:4px;">
                    Sin ruta activa seleccionada
                  </div>
                  <div style="font-size:13px;color:#94A3B8;">
                    Pulsa «Crear ruta comercial a pie» arriba para buscar negocios reales en tu zona y ordenar tus visitas.
                  </div>
                </div>
                """
            )

        # Rutas recientes para retomar con un toque
        recent_routes = list_routes(limit=4)
        if recent_routes:
            st.html('<div class="m-section-title">Rutas recientes</div>')
            for r_item in recent_routes:
                with st.container(border=True):
                    rc1, rc2 = st.columns([2.8, 1.2])
                    rc1.markdown(
                        f"**Ruta #{r_item['id']} · {r_item.get('start_label', 'Málaga')}**"
                    )
                    rc1.caption(
                        f"{r_item.get('found_count', 0)} encontrados · Radio {_format_radius_label(int(r_item.get('radius_m') or 500))} · "
                        f"{(r_item.get('created_at') or '')[:16].replace('T', ' ')}"
                    )
                    if rc2.button("Retomar", key=f"btn_home_load_route_{r_item['id']}", width="stretch"):
                        st.session_state.active_route_id = r_item["id"]
                        loaded = get_route(r_item["id"])
                        if loaded and loaded.get("stops"):
                            st.session_state.active_lead_id = loaded["stops"][0]["lead_id"]
                        st.session_state.nav_tab = "mapa"
                        st.session_state.sub_screen = None
                        st.rerun()


# ==========================================================================
# PANTALLA 2: MAPA (RUTA A PIE INTERACTIVA + SECUENCIA DE PARADAS)
# ==========================================================================
elif st.session_state.nav_tab == "mapa":
    if not active_route or not active_route.get("stops"):
        st.html(
            """
            <div class="m-card" style="text-align:center;padding:26px 16px;">
              <div style="font-size:16px;font-weight:700;color:#F9FAFB;margin-bottom:6px;">
                No hay ninguna ruta activa en el mapa
              </div>
              <div style="font-size:13px;color:#94A3B8;">
                Configura tu punto de salida en Málaga y genera un recorrido a pie para visualizar las paradas numeradas.
              </div>
            </div>
            """
        )
        if st.button("Crear ruta ahora", type="primary", icon=":material/add_road:", width="stretch", key="btn_map_empty_create"):
            st.session_state.nav_tab = "inicio"
            st.session_state.sub_screen = "crear_ruta"
            st.rerun()
    else:
        stops = active_route["stops"]
        req_n = active_route.get("requested_stops", len(stops))
        found_n = active_route.get("found_count", len(stops))
        has_real_geom = bool(active_route.get("geometry"))
        dist_str = format_distance(active_route.get("total_distance_m")) if has_real_geom else "Por tramo"
        dur_str = format_duration(active_route.get("total_duration_s")) if has_real_geom else "Variable"

        # Resumen compacto de la ruta en la parte superior del mapa
        st.html(
            f"""
            <div class="app-topbar">
              <div>
                <div class="app-brand-title">Ruta #{active_route['id']} · {active_route.get('start_label', 'Málaga')}</div>
                <div class="app-brand-sub">
                  {len(stops)} paradas (de {req_n} pedidas) · {dist_str} a pie · {dur_str} · {found_n} reales en radio
                </div>
              </div>
              <span class="score-pill score-high">{_format_radius_label(int(active_route.get('radius_m', 500)))}</span>
            </div>
            """
        )

        # Botones de acción rápida sobre el mapa
        gmaps_full_url = build_google_maps_walking_route_url(
            float(active_route["start_lat"]),
            float(active_route["start_lon"]),
            stops,
        )
        ma1, ma2, ma3 = st.columns(3)
        ma1.link_button(
            "Google Maps",
            url=gmaps_full_url,
            icon=":material/directions_walk:",
            width="stretch",
        )
        if ma2.button("Ver negocios", icon=":material/format_list_bulleted:", width="stretch", key="btn_map_to_list"):
            st.session_state.nav_tab = "negocios"
            st.session_state.sub_screen = None
            st.rerun()
        if ma3.button("Nueva ruta", icon=":material/tune:", width="stretch", key="btn_map_new_route"):
            st.session_state.nav_tab = "inicio"
            st.session_state.sub_screen = "crear_ruta"
            st.rerun()

        # Mapa interactivo Leaflet + OSRM
        map_res = route_map_component(
            start_lat=float(active_route["start_lat"]),
            start_lon=float(active_route["start_lon"]),
            start_label=active_route.get("start_label", "Salida"),
            stops=stops,
            geometry=active_route.get("geometry") or [],
            key=f"map_route_{active_route['id']}",
        )
        if map_res and getattr(map_res, "selected_lead_id", None):
            clicked_id = int(map_res.selected_lead_id)
            _open_lead_ficha(clicked_id)
            st.rerun()

        # Lista compacta de paradas en orden peatonal
        st.html('<div class="m-section-title">Orden de paradas a pie</div>')
        for idx, stop in enumerate(stops):
            pos = stop.get("position", idx + 1)
            lead_id = int(stop.get("lead_id") or stop.get("id"))
            web_eff = stop.get("web_effective") or stop.get("web_status") or WEB_NONE
            comm_st = stop.get("commercial_status") or DEFAULT_COMMERCIAL_STATUS
            leg_m = stop.get("leg_distance_m")
            leg_s = stop.get("leg_duration_s")
            straight_m = stop.get("straight_distance_m")
            leg_text = (
                f"{format_distance(leg_m)} ({format_duration(leg_s)})"
                if leg_m is not None
                else f"{format_distance(straight_m)}"
            )

            with st.container(border=True):
                src_lbl = stop.get("source") or "OpenStreetMap"
                web_lbl = get_web_label(web_eff, src_lbl)
                st.markdown(
                    f"<span class='stop-badge'>#{pos}</span> **{stop.get('name', 'Negocio')}** "
                    f":{_web_badge_color(web_eff)}-badge[{web_lbl}] "
                    f":{_status_badge_color(comm_st)}-badge[{comm_st}]",
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"{stop.get('sector', '')} · {stop.get('address') or stop.get('city') or 'Málaga'} · "
                    f"**A pie: {leg_text}** · Prioridad **{stop.get('score', 0)}/100**"
                )
                sb1, sb2, sb3 = st.columns(3)
                sb1.link_button(
                    "Ir ahora",
                    url=build_google_maps_directions_to_lead(stop),
                    icon=":material/navigation:",
                    width="stretch",
                )
                if sb2.button(
                    "Ver ficha",
                    key=f"btn_map_ficha_{lead_id}_{pos}",
                    type="primary",
                    icon=":material/assignment_ind:",
                    width="stretch",
                ):
                    _open_lead_ficha(lead_id, stop_index=idx)
                    st.rerun()
                if sb3.button(
                    "Visitar",
                    key=f"btn_map_visit_{lead_id}_{pos}",
                    icon=":material/fact_check:",
                    width="stretch",
                ):
                    _open_lead_visit(lead_id, stop_index=idx)
                    st.rerun()


# ==========================================================================
# PANTALLA 3: NEGOCIOS (LISTA COMPACTA + SUB-PANTALLA FICHA DEL NEGOCIO)
# ==========================================================================
elif st.session_state.nav_tab == "negocios":
    # ----------------------------------------------------------------------
    # SUB-PANTALLA DEDICADA: FICHA COMPLETA DEL NEGOCIO Y NFC
    # ----------------------------------------------------------------------
    if st.session_state.sub_screen == "ficha_negocio":
        route_stops = active_route.get("stops", []) if active_route else []
        stop_ids = [int(s.get("lead_id") or s.get("id")) for s in route_stops] if route_stops else []

        top_back, top_prev, top_next = st.columns([1.6, 1.1, 1.1])
        if top_back.button("Volver a Negocios", icon=":material/arrow_back:", width="stretch", key="btn_back_to_negocios"):
            st.session_state.sub_screen = None
            st.rerun()

        if stop_ids and st.session_state.active_lead_id in stop_ids:
            curr_idx = stop_ids.index(int(st.session_state.active_lead_id))
            if top_prev.button("Anterior", disabled=curr_idx <= 0, icon=":material/chevron_left:", width="stretch", key="btn_ficha_prev"):
                _open_lead_ficha(stop_ids[curr_idx - 1], stop_index=curr_idx - 1)
                st.rerun()
            if top_next.button("Siguiente", disabled=curr_idx >= len(stop_ids) - 1, icon=":material/chevron_right:", width="stretch", key="btn_ficha_next"):
                _open_lead_ficha(stop_ids[curr_idx + 1], stop_index=curr_idx + 1)
                st.rerun()

        lead = get_lead(int(st.session_state.active_lead_id)) if st.session_state.active_lead_id else None
        if not lead:
            st.info("Selecciona un negocio de la lista para ver su ficha completa.")
        else:
            lead_id = int(lead["id"])
            web_eff = lead.get("web_effective") or lead.get("web_status") or WEB_NONE
            comm_st = lead.get("commercial_status") or DEFAULT_COMMERCIAL_STATUS
            score = int(lead.get("score", 0))
            prio = lead.get("priority_effective") or lead.get("priority") or "Baja"

            # Cabecera de la ficha: distinción clara entre Datos de la Fuente y Gestión Propia
            src_name = lead.get("source") or "OpenStreetMap"
            src_id = lead.get("place_id") or lead.get("source_id") or "—"
            web_lbl_ficha = get_web_label(web_eff, src_name)
            is_google_lead = "google" in src_name.lower() or str(src_id).startswith("ChIJ")

            with st.container(border=True):
                st.subheader(lead.get("name", "Negocio"))
                st.markdown(
                    f"**{lead.get('sector', '—')}** · "
                    f":{_status_badge_color(comm_st)}-badge[{comm_st}] "
                    f":{_web_badge_color(web_eff)}-badge[{web_lbl_ficha}] "
                    f":yellow-badge[Prioridad {score}/100 ({prio})]"
                )
                st.caption(
                    f"**Fuente de datos:** {lead.get('data_source') or src_name} · "
                    f"**ID de lugar:** `{src_id}`"
                )
                st.markdown(
                    f"**:material/location_on: Dirección ({src_name}):** {lead.get('address') or 'Desconocida / No disponible'} "
                    f"({lead.get('city') or 'Málaga'} {lead.get('postal_code') or ''})"
                )
                st.markdown(
                    f"**:material/schedule: Horario ({src_name}):** {lead.get('opening_hours') or 'Desconocido (puedes cargarlo bajo demanda o anotarlo)'}"
                )

                phone_str = (lead.get("phone") or "").strip()
                phone_clean_href = phone_str.replace(" ", "").replace("-", "")
                if phone_str:
                    st.markdown(f"**:material/call: Teléfono ({src_name}):** [{phone_str}](tel:{phone_clean_href})")
                else:
                    st.markdown(f"**:material/call: Teléfono ({src_name}):** Desconocido / No disponible")

                if lead.get("website"):
                    w_raw = lead["website"].strip()
                    w_href = w_raw if w_raw.lower().startswith(("http://", "https://")) else f"https://{w_raw}"
                    st.markdown(f"**:material/language: Web ({src_name}):** [{w_raw}]({w_href})")
                elif lead.get("social_url"):
                    s_raw = lead["social_url"].strip()
                    s_href = s_raw if s_raw.lower().startswith(("http://", "https://")) else f"https://{s_raw.lstrip('@')}"
                    st.markdown(f"**:material/share: Red social ({src_name}):** [{s_raw}]({s_href})")
                else:
                    st.markdown(f"**:material/language: Web ({src_name}):** {web_lbl_ficha}")

                rating_v = lead.get("rating")
                ratings_n = lead.get("user_ratings_total")
                if rating_v is not None and ratings_n is not None:
                    st.markdown(
                        f"**:material/star: Reseñas ({lead.get('reviews_source') or src_name}):** {rating_v:.1f} / 5 ({ratings_n} reseñas)"
                    )
                else:
                    st.markdown("**:material/star: Reseñas:** Sin datos de reseñas registrados todavía")

                if lead.get("notes"):
                    st.markdown(f"**:material/sticky_note_2: Notas propias del usuario (CRM):** {lead.get('notes')}")

                act1, act2 = st.columns(2)
                act1.link_button(
                    "Abrir en Google Maps",
                    url=lead.get("map_url") if (is_google_lead and lead.get("map_url")) else build_google_maps_directions_to_lead(lead),
                    icon=":material/map:",
                    width="stretch",
                )
                if act2.button(
                    "Registrar visita / venta",
                    type="primary",
                    icon=":material/fact_check:",
                    width="stretch",
                    key=f"btn_ficha_to_visit_{lead_id}",
                ):
                    _open_lead_visit(lead_id)
                    st.rerun()

                # Carga de Place Details (New) bajo demanda (solo 1 petición para este negocio)
                if is_google_lead and src_id and not str(src_id).startswith(("osm_", "demo_")):
                    if st.button(
                        "Ampliar / actualizar detalles desde Google Places (1 petición)",
                        icon=":material/cloud_download:",
                        width="stretch",
                        key=f"btn_fetch_place_details_{lead_id}",
                    ):
                        try:
                            gp_prov = GooglePlacesProvider()
                            gp_prov.fetch_place_details_on_demand(
                                lead_id=lead_id,
                                place_id=str(src_id),
                                sector_name=lead.get("sector") or "Comercios",
                                default_city=lead.get("city") or "Málaga",
                            )
                            st.session_state.flash_message = "Detalles del negocio actualizados desde Google Places API (New)."
                            st.rerun()
                        except PlacesAPIError as det_err:
                            st.error(f"No se pudieron obtener los detalles en Google Places: {det_err}", icon=":material/error:")

            # Recomendación FACENIA + Argumento de venta en persona
            rec = generate_commercial_recommendation(lead)
            variant_idx = int(st.session_state.pitch_variants.get(lead_id, 0))
            pitch_data = generate_sales_pitch(lead, variant=variant_idx)

            with st.container(border=True):
                st.markdown("**:material/lightbulb: Qué ofrecer de FACENIA**")
                st.markdown(f"**{rec['headline']}**")
                pills_html = " ".join(f":yellow-badge[{srv}]" for srv in rec["services"])
                st.markdown(pills_html)
                for b_item in rec["bullets"]:
                    st.markdown(f"- {b_item}")

            with st.container(border=True):
                p_head1, p_head2 = st.columns([2.4, 1.3])
                p_head1.markdown(f"**:material/record_voice_over: Argumento en puerta** · *{pitch_data['title']}*")
                if p_head2.button(
                    "Otro enfoque",
                    icon=":material/autorenew:",
                    width="stretch",
                    key=f"btn_rotate_pitch_{lead_id}",
                ):
                    st.session_state.pitch_variants[lead_id] = (variant_idx + 1) % pitch_data["variant_count"]
                    st.rerun()

                st.info(pitch_data["text"], icon=":material/chat:")
                copy_and_share_bar(
                    text=pitch_data["text"].strip("«»"),
                    copy_label="Copiar argumento",
                    enable_share=True,
                    share_label="Compartir",
                    share_title=f"Argumento FACENIA - {lead.get('name', '')}",
                    key=f"copy_pitch_{lead_id}_{variant_idx}",
                )

            # Generador de enlace de reseñas y tarjeta NFC
            with st.container(border=True):
                st.markdown("**:material/contactless: Enlace de reseñas y Tarjeta NFC**")
                rev_info = prepare_review_link_info(lead)
                badge_col = "green" if rev_info["is_direct_review_link"] else "orange"
                st.markdown(f"Estado: :{badge_col}-badge[{rev_info['status_label']}]")
                st.caption(rev_info["instructions"])
                st.code(rev_info["review_url"], language=None)

                c_g1, c_g2 = st.columns(2)
                c_g1.link_button(
                    "Buscar ficha en Google",
                    url=rev_info["public_search_url"],
                    icon=":material/travel_explore:",
                    width="stretch",
                )
                c_g2.link_button(
                    "Probar enlace actual",
                    url=rev_info["review_url"],
                    icon=":material/open_in_new:",
                    width="stretch",
                )

                copy_and_share_bar(
                    text=rev_info["review_url"],
                    copy_label="Copiar enlace NFC",
                    enable_share=True,
                    share_label="Compartir enlace",
                    share_title=f"Reseñas · {lead.get('name', '')}",
                    share_url=rev_info["review_url"],
                    key=f"copy_nfc_url_{lead_id}",
                )

                with st.expander("Configurar Place ID / URL verificada, tarjeta NFC y reseñas", expanded=False):
                    input_rev_url = st.text_input(
                        "Pegar enlace verificado de Google Maps o Place ID real (ej. ChIJ...)",
                        value=lead.get("review_url") or "",
                        key=f"input_rev_url_{lead_id}",
                    )
                    nc1, nc2 = st.columns(2)
                    current_nfc = lead.get("nfc_status") or "No preparada"
                    nfc_idx = NFC_STATUS_OPTIONS.index(current_nfc) if current_nfc in NFC_STATUS_OPTIONS else 0
                    input_nfc_status = nc1.selectbox(
                        "Estado tarjeta NFC",
                        options=NFC_STATUS_OPTIONS,
                        index=nfc_idx,
                        key=f"sel_nfc_status_{lead_id}",
                    )
                    input_verif_state = nc2.selectbox(
                        "Verificación del enlace",
                        options=REVIEW_URL_STATUS_OPTIONS,
                        index=(
                            REVIEW_URL_STATUS_OPTIONS.index(lead["review_url_status"])
                            if lead.get("review_url_status") in REVIEW_URL_STATUS_OPTIONS
                            else 0
                        ),
                        key=f"sel_rev_status_{lead_id}",
                    )
                    rc1, rc2 = st.columns(2)
                    input_rating = rc1.number_input(
                        "Valoración observada (0-5)",
                        min_value=0.0,
                        max_value=5.0,
                        value=float(lead["rating"]) if lead.get("rating") is not None else 0.0,
                        step=0.1,
                        key=f"num_rating_{lead_id}",
                    )
                    input_reviews_count = rc2.number_input(
                        "Nº de reseñas",
                        min_value=0,
                        max_value=50000,
                        value=int(lead["user_ratings_total"]) if lead.get("user_ratings_total") is not None else 0,
                        step=1,
                        key=f"num_reviews_{lead_id}",
                    )
                    if st.button("Guardar NFC y reseñas", type="primary", icon=":material/save:", width="stretch", key=f"btn_save_nfc_{lead_id}"):
                        if "placeid=demo" in input_rev_url.lower():
                            st.error("No se permiten identificadores ficticios (placeid=demo).")
                        else:
                            prepared = prepare_review_link_info(lead, custom_input=input_rev_url.strip())
                            final_url = prepared["review_url"] if input_rev_url.strip() else ""
                            update_lead_review_nfc(
                                lead_id=lead_id,
                                review_url=final_url,
                                review_url_status=input_verif_state if final_url else "",
                                nfc_status=input_nfc_status,
                                rating=float(input_rating) if input_rating > 0 else "",
                                user_ratings_total=int(input_reviews_count) if input_rating > 0 or input_reviews_count > 0 else "",
                                reviews_source="Verificado manualmente" if (input_rating > 0 or input_reviews_count > 0) else "",
                            )
                            st.session_state.flash_message = "Datos de NFC y reseñas actualizados."
                            st.rerun()

            # Desglose de puntuación y verificación de presencia web
            with st.expander("Ver desglose de prioridad (0–100) y verificar web", expanded=False, icon=":material/verified:"):
                st.markdown(f"**Prioridad comercial: {score}/100 ({prio})**")
                for r_line in lead.get("scoring_reasons") or []:
                    st.markdown(f"- {r_line}")

                v_url = st.text_input(
                    "URL de web (si la descubres en la visita)",
                    value=lead.get("website") or "",
                    placeholder="https://...",
                    key=f"input_web_url_{lead_id}",
                )
                vc1, vc2 = st.columns(2)
                if vc1.button("Comprobar HTTP", width="stretch", key=f"btn_http_{lead_id}"):
                    if v_url.strip():
                        res_check = check_website(v_url.strip())
                        set_manual_web_status(
                            lead_id,
                            status=res_check["status"],
                            website=v_url.strip(),
                            detail=res_check["detail"],
                        )
                        st.session_state.flash_message = f"Verificación HTTP: {res_check['detail']}"
                        st.rerun()
                if vc2.button("Confirmar: Sin web", width="stretch", key=f"btn_confirm_noweb_{lead_id}"):
                    set_manual_web_status(
                        lead_id,
                        status=WEB_CONFIRMED_NONE,
                        website="",
                        detail="Verificado en persona: el negocio confirma que no tiene web.",
                    )
                    st.session_state.flash_message = "Marcado como «Sin web (verificado por ti)»."
                    st.rerun()

    # ----------------------------------------------------------------------
    # VISTA PRINCIPAL DE NEGOCIOS (TARJETAS MÓVILES COMPACTAS)
    # ----------------------------------------------------------------------
    else:
        route_stops = active_route.get("stops", []) if active_route else []
        scope_options = ["Ruta activa", "Toda la cartera (CRM)"] if route_stops else ["Toda la cartera (CRM)"]

        st.html(
            """
            <div class="app-topbar">
              <div>
                <div class="app-brand-title">Negocios y Oportunidades</div>
                <div class="app-brand-sub">Priorizados por potencial comercial para FACENIA</div>
              </div>
            </div>
            """
        )

        selected_scope = st.segmented_control(
            "Ámbito",
            options=scope_options,
            default=scope_options[0],
            label_visibility="collapsed",
            width="stretch",
        )

        if selected_scope == "Ruta activa" and route_stops:
            base_leads = list(route_stops)
        else:
            base_leads = get_leads()
            for i, item in enumerate(base_leads):
                item.setdefault("position", i + 1)
                if item.get("lat") is not None and item.get("lon") is not None:
                    item["straight_distance_m"] = round(
                        haversine_m(
                            float(st.session_state.start_lat),
                            float(st.session_state.start_lon),
                            float(item["lat"]),
                            float(item["lon"]),
                        ),
                        1,
                    )

        search_name_q = st.text_input(
            "Buscar negocio por nombre, calle o categoría",
            placeholder="Buscar por nombre, calle, categoría o teléfono...",
            label_visibility="collapsed",
            key="input_negocios_search",
        )

        with st.expander("Ordenar y filtrar negocios", expanded=False, icon=":material/filter_list:"):
            sort_options = [
                "Mayor oportunidad comercial",
                "Orden de visita en ruta",
                "Menor distancia",
                "Valoración",
                "Número de reseñas",
            ]
            sort_by = st.selectbox("Ordenar por", options=sort_options, index=0)
            available_cats = sorted(list({l.get("sector", "") for l in base_leads if l.get("sector")}))
            filter_cats = st.multiselect("Categoría", options=available_cats, default=[], placeholder="Todas")
            filter_comm = st.multiselect("Estado comercial", options=COMMERCIAL_STATUSES, default=[], placeholder="Todos")
            digital_filter_opts = [
                "Todos",
                "Sin web identificada o solo redes",
                "Sin web identificada en fuentes",
                "Solo redes sociales",
                "Web encontrada",
            ]
            filter_digital = st.selectbox("Presencia digital", options=digital_filter_opts, index=0)

        # Aplicar filtros
        filtered_leads: List[Dict[str, Any]] = []
        for item in base_leads:
            web_eff = item.get("web_effective") or item.get("web_status") or WEB_NONE
            comm_st = item.get("commercial_status") or DEFAULT_COMMERCIAL_STATUS

            if filter_cats and item.get("sector") not in filter_cats:
                continue
            if filter_comm and comm_st not in filter_comm:
                continue
            if filter_digital == "Sin web identificada o solo redes" and web_eff not in (WEB_NONE, WEB_SOCIAL, WEB_CONFIRMED_NONE):
                continue
            elif filter_digital == "Sin web identificada en fuentes" and web_eff not in (WEB_NONE, WEB_CONFIRMED_NONE):
                continue
            elif filter_digital == "Solo redes sociales" and web_eff != WEB_SOCIAL:
                continue
            elif filter_digital == "Web encontrada" and web_eff != WEB_FOUND:
                continue

            if search_name_q.strip():
                q_low = search_name_q.strip().lower()
                haystack = f"{item.get('name', '')} {item.get('address', '')} {item.get('phone', '')} {item.get('sector', '')}".lower()
                if q_low not in haystack:
                    continue

            filtered_leads.append(item)

        # Aplicar ordenación
        if sort_by == "Mayor oportunidad comercial":
            filtered_leads.sort(key=lambda x: (-int(x.get("score", 0)), int(x.get("position", 999))))
        elif sort_by == "Orden de visita en ruta":
            filtered_leads.sort(key=lambda x: int(x.get("position", 999)))
        elif sort_by == "Menor distancia":
            filtered_leads.sort(
                key=lambda x: float(
                    x.get("leg_distance_m")
                    if x.get("leg_distance_m") is not None
                    else (x.get("straight_distance_m") or 999999.0)
                )
            )
        elif sort_by == "Valoración":
            filtered_leads.sort(key=lambda x: float(x.get("rating") or -1.0), reverse=True)
        elif sort_by == "Número de reseñas":
            filtered_leads.sort(key=lambda x: int(x.get("user_ratings_total") or -1), reverse=True)

        st.caption(f"Mostrando **{len(filtered_leads)}** de **{len(base_leads)}** negocios.")

        if not filtered_leads:
            st.info("No hay negocios que coincidan con los filtros actuales.", icon=":material/search_off:")
        else:
            for item in filtered_leads:
                lead_id = int(item.get("lead_id") or item.get("id"))
                pos = item.get("position", "—")
                score = int(item.get("score", 0))
                web_eff = item.get("web_effective") or item.get("web_status") or WEB_NONE
                comm_st = item.get("commercial_status") or DEFAULT_COMMERCIAL_STATUS
                rec = generate_commercial_recommendation(item)

                dist_val = item.get("leg_distance_m")
                dist_txt = (
                    f"{format_distance(dist_val)} a pie"
                    if dist_val is not None
                    else (
                        f"{format_distance(item.get('straight_distance_m'))}"
                        if item.get("straight_distance_m") is not None
                        else ""
                    )
                )
                score_cls = "score-high" if score >= 70 else ("score-med" if score >= 45 else "score-low")

                with st.container(border=True):
                    st.html(
                        f"""
                        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                          <div>
                            <span class="stop-badge">#{pos}</span>
                            <span style="font-size:15px;font-weight:700;color:#F9FAFB;">{item.get('name', 'Negocio')}</span>
                          </div>
                          <span class="score-pill {score_cls}">{score}/100</span>
                        </div>
                        """
                    )
                    src_item = item.get("source") or "OpenStreetMap"
                    web_lbl_item = get_web_label(web_eff, src_item)
                    st.markdown(
                        f"**{item.get('sector', '')}** · "
                        f":{_web_badge_color(web_eff)}-badge[{web_lbl_item}] "
                        f":{_status_badge_color(comm_st)}-badge[{comm_st}]"
                    )
                    rating_item = item.get("rating")
                    reviews_item = item.get("user_ratings_total")
                    rev_short = (
                        f" · {float(rating_item):.1f}/5 ({int(reviews_item)} reseñas)"
                        if rating_item is not None and reviews_item is not None
                        else ""
                    )
                    st.caption(
                        f"{item.get('address') or 'Dirección no detallada en fuente'} ({item.get('city', 'Málaga')})"
                        + (f" · **{dist_txt}**" if dist_txt else "")
                        + rev_short
                        + f" · Fuente: {src_item} · Sugerido: **{', '.join(rec['services'][:2])}**"
                    )

                    b_ficha, b_maps, b_visit = st.columns(3)
                    if b_ficha.button(
                        "Abrir ficha",
                        key=f"btn_list_ficha_{lead_id}_{pos}",
                        type="primary",
                        icon=":material/assignment:",
                        width="stretch",
                    ):
                        _open_lead_ficha(lead_id)
                        st.rerun()
                    b_maps.link_button(
                        "Mapa",
                        url=build_google_maps_directions_to_lead(item),
                        icon=":material/directions_walk:",
                        width="stretch",
                    )
                    if b_visit.button(
                        "Visitar",
                        key=f"btn_list_visit_{lead_id}_{pos}",
                        icon=":material/fact_check:",
                        width="stretch",
                    ):
                        _open_lead_visit(lead_id)
                        st.rerun()


# ==========================================================================
# PANTALLA 4: VISITAS (REGISTRO TÁCTIL DE RESULTADOS + AVANCE DE RUTA)
# ==========================================================================
elif st.session_state.nav_tab == "visitas":
    route_stops = active_route.get("stops", []) if active_route else []
    if route_stops:
        stop_ids = [int(s.get("lead_id") or s.get("id")) for s in route_stops]
        if st.session_state.active_lead_id not in stop_ids:
            st.session_state.active_lead_id = stop_ids[0]
        curr_idx = stop_ids.index(int(st.session_state.active_lead_id))
        done_cnt = sum(
            1
            for s in route_stops
            if s.get("commercial_status") and s.get("commercial_status") != DEFAULT_COMMERCIAL_STATUS
        )
        pct_val = min(1.0, max(0.0, done_cnt / len(route_stops)))

        st.html(
            f"""
            <div class="app-topbar">
              <div>
                <div class="app-brand-title">Registro de Visitas en Ruta</div>
                <div class="app-brand-sub">Progreso: {done_cnt} de {len(route_stops)} paradas gestionadas</div>
              </div>
              <span class="score-pill score-high">Parada #{curr_idx + 1}/{len(route_stops)}</span>
            </div>
            """
        )
        st.progress(pct_val)

        # Selector rápido de parada
        vp1, vp2, vp3 = st.columns([1, 2.5, 1])
        if vp1.button("Anterior", disabled=curr_idx <= 0, icon=":material/chevron_left:", width="stretch", key="btn_vis_prev"):
            _open_lead_visit(stop_ids[curr_idx - 1], stop_index=curr_idx - 1)
            st.session_state.outcome_mode = None
            st.rerun()

        stop_labels = [
            f"#{s.get('position', i + 1)} · {s.get('name', '')} ({s.get('commercial_status', 'Pendiente')})"
            for i, s in enumerate(route_stops)
        ]
        chosen_stop_lbl = vp2.selectbox(
            "Seleccionar parada",
            options=stop_labels,
            index=curr_idx,
            label_visibility="collapsed",
            key="sel_visit_stop_dropdown",
        )
        if chosen_stop_lbl:
            new_idx = stop_labels.index(chosen_stop_lbl)
            if new_idx != curr_idx:
                _open_lead_visit(stop_ids[new_idx], stop_index=new_idx)
                st.session_state.outcome_mode = None
                st.rerun()

        if vp3.button("Siguiente", disabled=curr_idx >= len(stop_ids) - 1, icon=":material/chevron_right:", width="stretch", key="btn_vis_next"):
            _open_lead_visit(stop_ids[curr_idx + 1], stop_index=curr_idx + 1)
            st.session_state.outcome_mode = None
            st.rerun()
    else:
        stop_ids = []
        curr_idx = 0
        all_crm = get_leads()
        if all_crm:
            crm_map = {f"{l['name']} ({l.get('sector', '')})": l["id"] for l in all_crm}
            chosen_crm = st.selectbox("Seleccionar negocio de la cartera", options=list(crm_map.keys()))
            if chosen_crm:
                st.session_state.active_lead_id = crm_map[chosen_crm]

    lead = get_lead(int(st.session_state.active_lead_id)) if st.session_state.active_lead_id else None

    if not lead:
        st.info("No hay negocios seleccionados para registrar visitas. Crea una ruta desde Inicio.")
    else:
        lead_id = int(lead["id"])
        comm_st = lead.get("commercial_status") or DEFAULT_COMMERCIAL_STATUS
        web_eff = lead.get("web_effective") or lead.get("web_status") or WEB_NONE

        # Tarjeta resumen del negocio actual
        with st.container(border=True):
            c_info, c_ficha_btn = st.columns([2.6, 1.3])
            with c_info:
                st.subheader(lead.get("name", "Negocio"))
                st.markdown(
                    f"**{lead.get('sector', '')}** · "
                    f":{_status_badge_color(comm_st)}-badge[{comm_st}] "
                    f":{_web_badge_color(web_eff)}-badge[{WEB_LABELS.get(web_eff, web_eff)}]"
                )
                st.caption(f"{lead.get('address') or lead.get('city') or 'Málaga'} · Tel: {lead.get('phone') or 'No indicado'}")
            with c_ficha_btn:
                if st.button("Ver ficha / NFC", icon=":material/assignment:", width="stretch", key=f"btn_vis_open_ficha_{lead_id}"):
                    _open_lead_ficha(lead_id)
                    st.rerun()

            st.markdown("**¿Qué resultado has obtenido en la visita?**")
            b1, b2 = st.columns(2)
            b3, b4 = st.columns(2)

            if b1.button("Vendido", icon=":material/check_circle:", width="stretch", key="btn_outcome_sold"):
                st.session_state.outcome_mode = OUTCOME_SOLD
            if b2.button("Volver otro día", icon=":material/event_repeat:", width="stretch", key="btn_outcome_return"):
                st.session_state.outcome_mode = OUTCOME_RETURN
            if b3.button("No interesa", icon=":material/cancel:", width="stretch", key="btn_outcome_discard"):
                st.session_state.outcome_mode = OUTCOME_NOT_INTERESTED
            if b4.button("Pendiente / Nota", icon=":material/schedule:", width="stretch", key="btn_outcome_pending"):
                st.session_state.outcome_mode = "PENDIENTE_NOTA"

            active_mode = st.session_state.outcome_mode or OUTCOME_SOLD

            # Formulario contextual según estado táctil elegido
            if active_mode == OUTCOME_SOLD:
                with st.container(border=True):
                    st.markdown("**:green[:material/workspace_premium: Confirmar venta cerrada]**")
                    sc1, sc2 = st.columns(2)
                    default_srv = lead.get("sale_service") or FACENIA_SERVICES[0]
                    srv_idx = FACENIA_SERVICES.index(default_srv) if default_srv in FACENIA_SERVICES else 0
                    sold_service = sc1.selectbox(
                        "Servicio vendido",
                        options=FACENIA_SERVICES,
                        index=srv_idx,
                        key=f"sold_srv_{lead_id}",
                    )
                    sold_amount_str = sc2.text_input(
                        "Importe en € (opcional)",
                        value=str(lead["sale_amount"]) if lead.get("sale_amount") is not None else "",
                        placeholder="Ej. 450",
                        key=f"sold_amt_{lead_id}",
                    )
                    sold_note = st.text_area(
                        "Notas del acuerdo / entregables",
                        placeholder="Ej. Entregar expositor NFC y boceto web el martes...",
                        height=75,
                        key=f"sold_note_{lead_id}",
                    )
                    if st.button("Guardar venta y avanzar", icon=":material/check:", width="stretch", key="btn_quick_sold"):
                        parsed_amount: Optional[float] = None
                        if sold_amount_str.strip():
                            try:
                                parsed_amount = float(sold_amount_str.strip().replace(",", "."))
                            except ValueError:
                                st.error("Introduce un importe numérico válido (ej. 350 o 49.90).")
                                st.stop()
                        record_visit_outcome(
                            lead_id=lead_id,
                            outcome=OUTCOME_SOLD,
                            route_id=st.session_state.active_route_id,
                            note=sold_note,
                            sale_service=sold_service,
                            sale_amount=parsed_amount,
                        )
                        if stop_ids and curr_idx < len(stop_ids) - 1:
                            _open_lead_visit(stop_ids[curr_idx + 1], stop_index=curr_idx + 1)
                        st.session_state.flash_message = f"Venta registrada en {lead.get('name')}."
                        st.rerun()

            elif active_mode == OUTCOME_RETURN:
                with st.container(border=True):
                    st.markdown("**:orange[:material/event_repeat: Programar «Volver otro día»]**")
                    rc1, rc2 = st.columns(2)
                    followup_dt = rc1.date_input(
                        "Fecha de seguimiento",
                        value=date.today() + timedelta(days=2),
                        key=f"ret_date_{lead_id}",
                    )
                    next_act = rc2.text_input(
                        "Próxima acción",
                        value=lead.get("next_action") or "Pasar cuando esté el responsable",
                        key=f"ret_act_{lead_id}",
                    )
                    ret_note = st.text_area(
                        "Nota de la visita",
                        placeholder="Ej. El encargado está de 10:00 a 13:30...",
                        height=75,
                        key=f"ret_note_{lead_id}",
                    )
                    if st.button("Guardar seguimiento y avanzar", type="primary", icon=":material/event_available:", width="stretch", key=f"btn_save_return_{lead_id}"):
                        record_visit_outcome(
                            lead_id=lead_id,
                            outcome=OUTCOME_RETURN,
                            route_id=st.session_state.active_route_id,
                            note=ret_note,
                            next_action=next_act,
                            followup_date=followup_dt.isoformat() if followup_dt else "",
                        )
                        if stop_ids and curr_idx < len(stop_ids) - 1:
                            _open_lead_visit(stop_ids[curr_idx + 1], stop_index=curr_idx + 1)
                        st.session_state.flash_message = f"Seguimiento programado para {lead.get('name')}."
                        st.rerun()

            elif active_mode == OUTCOME_NOT_INTERESTED:
                with st.container(border=True):
                    st.markdown("**:red[:material/block: Confirmar «No interesa» (Descartar)]**")
                    st.caption("No volverá a sugerirse automáticamente en nuevas rutas por esta zona.")
                    discard_reasons = [
                        "No necesita servicios digitales ahora",
                        "Acaba de renovar su web / sistemas",
                        "Franquicia o decisiones centralizadas",
                        "Negocio cerrado o traspasado",
                        "Otro motivo",
                    ]
                    chosen_reason = st.selectbox("Motivo", options=discard_reasons, key=f"disc_reason_{lead_id}")
                    disc_note = st.text_input("Nota adicional (opcional)", key=f"disc_note_{lead_id}")
                    if st.button("Confirmar descarte y avanzar", type="secondary", icon=":material/person_off:", width="stretch", key=f"btn_save_discard_{lead_id}"):
                        record_visit_outcome(
                            lead_id=lead_id,
                            outcome=OUTCOME_NOT_INTERESTED,
                            route_id=st.session_state.active_route_id,
                            note=disc_note,
                            discard_reason=chosen_reason,
                        )
                        if stop_ids and curr_idx < len(stop_ids) - 1:
                            _open_lead_visit(stop_ids[curr_idx + 1], stop_index=curr_idx + 1)
                        st.session_state.flash_message = f"«{lead.get('name')}» descartado."
                        st.rerun()

            else:
                with st.container(border=True):
                    st.markdown("**:material/edit_note: Dejar como Pendiente / Actualizar nota**")
                    new_st = st.selectbox(
                        "Estado comercial",
                        options=COMMERCIAL_STATUSES,
                        index=COMMERCIAL_STATUSES.index(comm_st) if comm_st in COMMERCIAL_STATUSES else 0,
                        key=f"direct_st_{lead_id}",
                    )
                    full_notes_val = st.text_area(
                        "Notas del negocio",
                        value=lead.get("notes") or "",
                        height=80,
                        key=f"full_notes_{lead_id}",
                    )
                    if st.button("Guardar estado y nota", type="primary", icon=":material/save:", width="stretch", key=f"btn_update_direct_{lead_id}"):
                        update_lead_commercial(
                            lead_id=lead_id,
                            commercial_status=new_st,
                            notes=full_notes_val,
                            next_action=lead.get("next_action") or "",
                            manual_priority=lead.get("manual_priority") or "",
                            last_contact_date=lead.get("last_contact_date") or "",
                            next_followup_date=lead.get("next_followup_date") or "",
                        )
                        st.session_state.flash_message = f"Estado de {lead.get('name')} guardado ({new_st})."
                        st.rerun()

        # Deshacer última acción
        recent_inter = get_recent_interactions(limit=5)
        active_inter = [i for i in recent_inter if not i.get("undone")]
        with st.container(border=True):
            u1, u2 = st.columns([2.3, 1.4])
            with u1:
                if active_inter:
                    last_i = active_inter[0]
                    st.caption(
                        f"Última acción: **{last_i.get('lead_name')}** → `{last_i.get('status_after')}`"
                    )
                else:
                    st.caption("Sin acciones recientes pendientes de deshacer.")
            with u2:
                if st.button(
                    "Deshacer última acción",
                    disabled=len(active_inter) == 0,
                    icon=":material/undo:",
                    width="stretch",
                    key="btn_undo_last_action",
                ):
                    undone_res = undo_interaction()
                    if undone_res and undone_res.get("lead"):
                        restored = undone_res["lead"]
                        st.session_state.flash_message = (
                            f"Deshecho: «{restored.get('name')}» vuelve a «{restored.get('commercial_status')}»."
                        )
                        st.rerun()


# ==========================================================================
# PANTALLA 5: PERFIL (PREFERENCIAS, FUENTES DE DATOS, MÉTRICAS Y EXPORTACIÓN)
# ==========================================================================
elif st.session_state.nav_tab == "perfil":
    st.html(
        """
        <div class="app-topbar">
          <div class="app-brand-group">
            <span class="app-brand-badge">FACENIA</span>
            <div>
              <div class="app-brand-title">Perfil y Configuración</div>
              <div class="app-brand-sub">Preferencias por defecto, fuentes, estadísticas y copias</div>
            </div>
          </div>
        </div>
        """
    )

    # 0. Sesión activa
    with st.container(border=True):
        st.markdown("**:material/account_circle: Sesión activa**")
        user_name = (st.session_state.auth_user or {}).get("username", "admin123")
        u_col1, u_col2 = st.columns([2, 1])
        u_col1.caption(f"Usuario autenticado: **{user_name}** · Rol: **Administrador** (Base de datos)")
        if u_col2.button("Cerrar sesión", icon=":material/logout:", key="btn_logout_user", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.auth_user = None
            st.rerun()

    # 1. Preferencias de búsqueda por defecto
    with st.container(border=True):
        st.markdown("**:material/settings: Preferencias de búsqueda por defecto**")
        st.caption("Se aplican al iniciar la aplicación para agilizar tus salidas comerciales en Málaga.")

        zone_names = list(PRESET_ZONES.keys())
        curr_def_zone = get_setting("default_zone_label", DEFAULT_START["label"])
        z_idx = zone_names.index(curr_def_zone) if curr_def_zone in zone_names else 0
        pref_zone = st.selectbox("Zona habitual de salida en Málaga", options=zone_names, index=z_idx, key="pref_zone_sel")

        pc1, pc2 = st.columns(2)
        pref_stops = pc1.number_input(
            "Negocios por ruta (5–30)",
            min_value=ROUTE_MIN_STOPS,
            max_value=ROUTE_MAX_STOPS,
            value=int(get_setting("default_stops", "12") or 12),
            step=1,
            key="pref_stops_num",
        )
        curr_rad = int(get_setting("default_radius_m", "500") or 500)
        rad_idx = ROUTE_RADIUS_OPTIONS_M.index(curr_rad) if curr_rad in ROUTE_RADIUS_OPTIONS_M else 1
        pref_radius = pc2.selectbox(
            "Radio a pie habitual",
            options=ROUTE_RADIUS_OPTIONS_M,
            index=rad_idx,
            format_func=_format_radius_label,
            key="pref_rad_sel",
        )

        if st.button("Guardar preferencias por defecto", type="primary", icon=":material/save:", width="stretch", key="btn_save_prefs"):
            z_lat, z_lon = PRESET_ZONES[pref_zone]
            set_setting("default_zone_label", pref_zone)
            set_setting("default_zone_lat", str(z_lat))
            set_setting("default_zone_lon", str(z_lon))
            set_setting("default_stops", str(int(pref_stops)))
            set_setting("default_radius_m", str(int(pref_radius)))
            st.session_state.start_label = pref_zone
            st.session_state.start_lat = z_lat
            st.session_state.start_lon = z_lon
            st.session_state.requested_stops = int(pref_stops)
            st.session_state.radius_m = int(pref_radius)
            st.session_state.flash_message = "Preferencias predeterminadas guardadas en SQLite."
            st.rerun()

    # 2. Fuentes de datos, Control de consumo y Contador interno de peticiones (Google Places API New)
    with st.container(border=True):
        st.markdown("**:material/cloud_done: Fuentes de datos y control de consumo (Google Places API New)**")
        has_gmaps_key = is_google_maps_configured()
        if has_gmaps_key:
            st.markdown(
                "- **Google Places API (New) — Fuente principal:** :green-badge[GOOGLE_MAPS_API_KEY configurada en servidor]"
            )
        else:
            st.markdown(
                "- **Google Places API (New) — Fuente principal:** :orange-badge[Pendiente de configurar GOOGLE_MAPS_API_KEY en .env]"
            )
        st.markdown(
            "- **OpenStreetMap (Overpass API + Nominatim):** :green-badge[Alternativa gratuita disponible]"
        )
        st.markdown(
            "- **Rutas a pie en mapa (OSRM Foot + enlaces externos Google Maps):** :green-badge[Activa sin coste de Routes API]"
        )
        st.caption(
            "Por seguridad, `GOOGLE_MAPS_API_KEY` se lee exclusivamente en el servidor y nunca se muestra ni se expone en el cliente."
        )

        prov_pref_map = {
            "google_places": "Google Places API (New) · Principal",
            "osm": "OpenStreetMap · Alternativa abierta",
        }
        curr_prov_pref = get_setting("default_provider", DEFAULT_DATA_PROVIDER)
        if curr_prov_pref not in prov_pref_map:
            curr_prov_pref = DEFAULT_DATA_PROVIDER
        chosen_default_prov = st.selectbox(
            "Proveedor de búsqueda predeterminado",
            options=list(prov_pref_map.keys()),
            index=list(prov_pref_map.keys()).index(curr_prov_pref),
            format_func=lambda k: prov_pref_map[k],
            key="sel_profile_default_provider",
        )

        lim1, lim2 = st.columns(2)
        new_max_res = lim1.number_input(
            "Máx. resultados por búsqueda (5–40)",
            min_value=5,
            max_value=40,
            value=int(st.session_state.places_max_results),
            step=5,
            key="num_profile_max_results",
        )
        new_max_req = lim2.number_input(
            "Máx. peticiones API por ruta (1–5)",
            min_value=1,
            max_value=5,
            value=int(st.session_state.places_max_requests),
            step=1,
            key="num_profile_max_requests",
        )

        if st.button("Guardar proveedor y límites de consumo", icon=":material/tune:", width="stretch", key="btn_save_places_limits"):
            set_setting("default_provider", chosen_default_prov)
            set_setting("places_max_results", str(int(new_max_res)))
            set_setting("places_max_requests", str(int(new_max_req)))
            st.session_state.active_provider = chosen_default_prov
            st.session_state.places_max_results = int(new_max_res)
            st.session_state.places_max_requests = int(new_max_req)
            st.session_state.flash_message = "Proveedor y límites de control de consumo actualizados."
            st.rerun()

        # Contador interno de peticiones realizadas por la aplicación
        usage = get_api_usage_stats()
        st.markdown("**:material/counter_1: Contador interno de peticiones a Google Places API (New)**")
        st.caption(
            f"**Total peticiones registradas por la app:** `{usage.get('total_requests', 0)}` "
            f"(Nearby Search: `{usage.get('nearby_search', 0)}` · "
            f"Text Search: `{usage.get('text_search', 0)}` · "
            f"Place Details bajo demanda: `{usage.get('place_details', 0)}` · "
            f"Validaciones: `{usage.get('validate_key', 0)}`)"
            + (f" · Última: `{usage.get('last_request_at')}`" if usage.get("last_request_at") else "")
        )
        st.caption(
            "Nota: Este contador registra únicamente el número de llamadas HTTP enviadas desde BuscaLeads. "
            "No representa un coste monetario exacto; consulta Google Cloud Billing para facturación real."
        )
        if st.button("Reiniciar contador interno de peticiones", icon=":material/restart_alt:", width="stretch", key="btn_reset_api_counter"):
            reset_api_usage_stats()
            st.session_state.flash_message = "Contador interno de peticiones reiniciado a 0."
            st.rerun()

    # 3. Estadísticas comerciales completas
    stats = get_stats()
    with st.container(border=True):
        st.markdown("**:material/bar_chart: Rendimiento comercial por categoría**")
        amt_total = float(stats.get("sales_amount_total", 0.0))
        amt_cnt = int(stats.get("sales_with_amount_count", 0))
        amt_fmt = f"{amt_total:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".") if amt_cnt > 0 else "0,00 €"
        st.caption(
            f"Total cartera: **{stats.get('found_total', 0)}** · "
            f"Visitados: **{stats.get('visited_count', 0)}** · "
            f"Ventas: **{stats.get('sales_count', 0)}** ({amt_fmt}) · "
            f"Conversión: **{stats.get('conversion_rate', 0.0):.1f}%**"
        )
        by_cat = stats.get("by_category") or []
        if by_cat:
            cat_df = pd.DataFrame(
                [
                    {
                        "Categoría": r.get("sector") or "Otros",
                        "Encontrados": int(r.get("found") or 0),
                        "Visitados": int(r.get("visited") or 0),
                        "Seguimiento": int(r.get("followup") or 0),
                        "Ventas": int(r.get("sold") or 0),
                        "Importe (€)": round(float(r.get("amount") or 0.0), 2),
                    }
                    for r in by_cat
                ]
            )
            st.dataframe(cat_df, hide_index=True, width="stretch")
        else:
            st.caption("Aún no hay registros comerciales en la base de datos.")

    # 4. Exportación a Notion (CSV) / Excel y Copias de seguridad SQLite
    all_leads_export = get_leads()
    with st.container(border=True):
        st.markdown("**:material/download: Exportar cartera y copias de seguridad**")
        st.caption(f"Negocios reales en base de datos listos para exportar: **{len(all_leads_export)}**.")
        ex1, ex2 = st.columns(2)
        if all_leads_export:
            ex1.download_button(
                "Descargar Excel (.xlsx)",
                data=export_leads_to_excel_bytes(all_leads_export),
                file_name=f"FACENIA_Prospeccion_{datetime.now():%Y%m%d_%H%M}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/table_view:",
                width="stretch",
            )
            ex2.download_button(
                "Descargar CSV (Notion)",
                data=export_leads_to_csv_bytes(all_leads_export),
                file_name=f"FACENIA_Notion_{datetime.now():%Y%m%d_%H%M}.csv",
                mime="text/csv",
                icon=":material/description:",
                width="stretch",
            )

        bk1, bk2 = st.columns(2)
        if bk1.button("Crear copia SQLite", icon=":material/backup:", width="stretch", key="btn_profile_backup"):
            dest_file = backup_database(label="manual")
            st.success(f"Copia creada: {dest_file}")

        backups_list = sorted(list(BACKUPS_DIR.glob("*.db")), reverse=True)
        if backups_list:
            latest_bk = backups_list[0]
            with open(latest_bk, "rb") as f_bk:
                bk2.download_button(
                    "Descargar copia (.db)",
                    data=f_bk.read(),
                    file_name=latest_bk.name,
                    mime="application/x-sqlite3",
                    icon=":material/database:",
                    width="stretch",
                )


# ==========================================================================
# BARRA DE NAVEGACIÓN INFERIOR FIJA (SIEMPRE VISIBLE EN TODAS LAS PANTALLAS)
# ==========================================================================
nav_event = bottom_nav_bar(active_tab=st.session_state.nav_tab, key="facenia_bottom_nav_fixed")
if nav_event and getattr(nav_event, "selected_tab", None):
    chosen_tab = str(nav_event.selected_tab)
    if chosen_tab in NAV_TABS:
        if chosen_tab != st.session_state.nav_tab or st.session_state.sub_screen is not None:
            st.session_state.nav_tab = chosen_tab
            st.session_state.sub_screen = None
            st.rerun()
