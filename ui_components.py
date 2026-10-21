"""
Componentes interactivos Custom Components v2 (CCv2) para FACENIA Lead Finder:
1. Geolocalización real del navegador (navigator.geolocation).
2. Mapa interactivo Leaflet con paradas numeradas (#1..#N), punto de salida y trazado peatonal real (OSRM).
3. Barra de copiado al portapapeles (navigator.clipboard) y compartido móvil nativo (navigator.share).
4. Barra de navegación inferior fija estilo aplicación móvil (Inicio, Mapa, Negocios, Visitas, Perfil) con iconos SVG Lucide.
"""

from collections.abc import Callable
from typing import Any, Dict, List, Optional
import streamlit as st


# --------------------------------------------------------------------------
# 1. Componente CCv2: Botón de Geolocalización Real (Browser GPS)
# --------------------------------------------------------------------------
_GEO_HTML = """
<div class="geo-wrap">
  <button id="geo-btn" type="button" class="geo-btn">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3"></circle>
      <path d="M12 2v3m0 14v3M2 12h3m14 0h3"></path>
      <circle cx="12" cy="12" r="8"></circle>
    </svg>
    <span id="geo-label">Usar mi ubicación actual (GPS)</span>
  </button>
  <div id="geo-status" class="geo-status" role="status" aria-live="polite"></div>
</div>
"""

_GEO_CSS = """
.geo-wrap {
  font-family: Inter, system-ui, -apple-system, sans-serif;
  width: 100%;
}
.geo-btn {
  width: 100%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 12px 16px;
  border-radius: 10px;
  border: 1px solid #F5B82E;
  background: rgba(245, 184, 46, 0.12);
  color: #FACC15;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease, transform 0.1s ease;
}
.geo-btn:hover {
  background: rgba(245, 184, 46, 0.22);
}
.geo-btn:active {
  transform: scale(0.99);
}
.geo-btn:focus-visible {
  outline: 2px solid #FACC15;
  outline-offset: 2px;
}
.geo-status {
  margin-top: 6px;
  font-size: 12.5px;
  color: #9CA3AF;
  min-height: 18px;
}
.geo-status.ok {
  color: #22C55E;
}
.geo-status.err {
  color: #F87171;
}
"""

_GEO_JS = """
export default function (component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component;
  const btn = parentElement.querySelector("#geo-btn");
  const statusEl = parentElement.querySelector("#geo-status");
  const labelEl = parentElement.querySelector("#geo-label");
  if (!btn || !statusEl || !labelEl) return;

  if (data && data.currentLabel) {
    statusEl.textContent = "Punto activo: " + data.currentLabel;
    statusEl.className = "geo-status ok";
  }

  btn.onclick = () => {
    if (!navigator.geolocation) {
      const msg = "Tu navegador no soporta geolocalización. Introduce la calle o zona manualmente.";
      statusEl.textContent = msg;
      statusEl.className = "geo-status err";
      setStateValue("error", msg);
      return;
    }
    labelEl.textContent = "Obteniendo coordenadas GPS...";
    statusEl.textContent = "Solicitando permiso de ubicación al navegador...";
    statusEl.className = "geo-status";

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const payload = {
          lat: Number(pos.coords.latitude.toFixed(6)),
          lon: Number(pos.coords.longitude.toFixed(6)),
          accuracy: Math.round(pos.coords.accuracy || 0),
          ts: Date.now()
        };
        labelEl.textContent = "Ubicación GPS actualizada";
        statusEl.textContent = "Coordenadas recibidas (" + payload.lat + ", " + payload.lon + ")";
        statusEl.className = "geo-status ok";
        setStateValue("error", "");
        setTriggerValue("coords", payload);
      },
      (err) => {
        labelEl.textContent = "Usar mi ubicación actual (GPS)";
        let msg = "No se pudo obtener el GPS. Usa el buscador manual de dirección o barrio.";
        if (err && err.code === 1) {
          msg = "Permiso de ubicación denegado en el navegador. Introduce la dirección o barrio manualmente abajo.";
        }
        statusEl.textContent = msg;
        statusEl.className = "geo-status err";
        setStateValue("error", msg);
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
    );
  };
}
"""

_GEO_COMPONENT = st.components.v2.component(
    "facenia_geolocation_button",
    html=_GEO_HTML,
    css=_GEO_CSS,
    js=_GEO_JS,
)


def geolocation_button(
    current_label: str = "",
    *,
    key: str = "geo_btn_main",
    on_coords_change: Optional[Callable[[], None]] = None,
):
    if on_coords_change is None:
        on_coords_change = lambda: None
    return _GEO_COMPONENT(
        data={"currentLabel": current_label},
        key=key,
        on_coords_change=on_coords_change,
        on_error_change=lambda: None,
    )


# --------------------------------------------------------------------------
# 2. Componente CCv2: Copiar al portapapeles y Compartir en móvil
# --------------------------------------------------------------------------
_COPY_HTML = """
<div class="copy-bar">
  <button id="btn-copy" type="button" class="action-btn primary">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
    </svg>
    <span id="copy-txt">Copiar</span>
  </button>
  <button id="btn-share" type="button" class="action-btn secondary" style="display:none;">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="18" cy="5" r="3"></circle>
      <circle cx="6" cy="12" r="3"></circle>
      <circle cx="18" cy="19" r="3"></circle>
      <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
      <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
    </svg>
    <span id="share-txt">Compartir</span>
  </button>
  <span id="copy-feedback" class="feedback" role="status" aria-live="polite"></span>
</div>
"""

_COPY_CSS = """
.copy-bar {
  font-family: Inter, system-ui, -apple-system, sans-serif;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 14px;
  border-radius: 9px;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  flex: 1 1 auto;
  min-height: 42px;
}
.action-btn.primary {
  background: #F5B82E;
  color: #0B0D11;
  border: 1px solid #F5B82E;
}
.action-btn.primary:hover {
  background: #FACC15;
}
.action-btn.secondary {
  background: #181D27;
  color: #E5E7EB;
  border: 1px solid #2E3646;
}
.action-btn.secondary:hover {
  border-color: #F5B82E;
  color: #FACC15;
}
.feedback {
  font-size: 12px;
  color: #22C55E;
  font-weight: 600;
  width: 100%;
}
"""

_COPY_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const copyBtn = parentElement.querySelector("#btn-copy");
  const shareBtn = parentElement.querySelector("#btn-share");
  const copyTxt = parentElement.querySelector("#copy-txt");
  const shareTxt = parentElement.querySelector("#share-txt");
  const feedback = parentElement.querySelector("#copy-feedback");
  if (!copyBtn || !shareBtn || !copyTxt || !feedback) return;

  const textToCopy = (data && data.text) || "";
  const copyLabel = (data && data.copyLabel) || "Copiar";
  const shareLabel = (data && data.shareLabel) || "Compartir desde móvil";
  const showShare = Boolean(data && data.enableShare);
  const shareTitle = (data && data.shareTitle) || "FACENIA";
  const shareUrl = (data && data.shareUrl) || "";

  copyTxt.textContent = copyLabel;
  shareTxt.textContent = shareLabel;
  shareBtn.style.display = showShare ? "inline-flex" : "none";

  copyBtn.onclick = async () => {
    try {
      await navigator.clipboard.writeText(textToCopy);
      feedback.textContent = "Copiado al portapapeles";
      setTriggerValue("copied", Date.now());
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = textToCopy;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      parentElement.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      parentElement.removeChild(ta);
      feedback.textContent = "Copiado al portapapeles";
      setTriggerValue("copied", Date.now());
    }
    setTimeout(() => { feedback.textContent = ""; }, 3000);
  };

  shareBtn.onclick = async () => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: shareTitle,
          text: textToCopy,
          url: shareUrl || undefined
        });
        feedback.textContent = "Compartido correctamente";
        return;
      } catch (err) {
        if (err && err.name === "AbortError") return;
      }
    }
    const waText = encodeURIComponent(shareUrl ? (textToCopy + " " + shareUrl).trim() : textToCopy);
    window.open("https://wa.me/?text=" + waText, "_blank", "noopener,noreferrer");
  };
}
"""

_COPY_SHARE_COMPONENT = st.components.v2.component(
    "facenia_copy_share_bar",
    html=_COPY_HTML,
    css=_COPY_CSS,
    js=_COPY_JS,
)


def copy_and_share_bar(
    text: str,
    copy_label: str = "Copiar",
    enable_share: bool = False,
    share_label: str = "Compartir en móvil",
    share_title: str = "FACENIA",
    share_url: str = "",
    *,
    key: str,
):
    return _COPY_SHARE_COMPONENT(
        data={
            "text": text,
            "copyLabel": copy_label,
            "enableShare": enable_share,
            "shareLabel": share_label,
            "shareTitle": shareTitle if (shareTitle := share_title) else "FACENIA",
            "shareUrl": share_url,
        },
        key=key,
        on_copied_change=lambda: None,
    )


# --------------------------------------------------------------------------
# 3. Componente CCv2: Mapa Interactivo de Ruta a Pie (Leaflet + OSRM)
# --------------------------------------------------------------------------
_MAP_HTML = """
<div class="map-shell">
  <div id="map-canvas" class="map-canvas" role="region" aria-label="Mapa interactivo de ruta comercial a pie"></div>
  <div id="map-Selected-bar" class="map-selected-bar">
    <span class="hint">Toca cualquier parada numerada en el mapa para ver su resumen o abrir su ficha comercial.</span>
  </div>
</div>
"""

_MAP_CSS = """
.map-shell {
  font-family: Inter, system-ui, -apple-system, sans-serif;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid #262D3B;
  background: #12161F;
}
.map-canvas {
  width: 100%;
  height: 390px;
  background: #0E121A;
}
.map-canvas .leaflet-tile-pane {
  filter: brightness(0.66) invert(1) contrast(2.5) hue-rotate(200deg) saturate(0.32) brightness(0.78);
}
.map-selected-bar {
  padding: 10px 14px;
  background: #141821;
  border-top: 1px solid #262D3B;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
  color: #E5E7EB;
}
.map-selected-bar .hint {
  color: #9CA3AF;
  font-size: 12.5px;
}
.stop-pin {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  font-weight: 700;
  font-size: 12.5px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.65);
  border: 2px solid #0B0D11;
  transition: transform 0.12s ease;
}
.stop-pin.pending {
  background: #F5B82E;
  color: #0B0D11;
}
.stop-pin.sold {
  background: #22C55E;
  color: #052E16;
}
.stop-pin.visited {
  background: #38BDF8;
  color: #082F49;
}
.stop-pin.discarded {
  background: #4B5563;
  color: #E5E7EB;
}
.start-pin {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #10B981;
  color: #052E16;
  font-weight: 800;
  font-size: 11px;
  border: 2px solid #ECFDF5;
  box-shadow: 0 2px 10px rgba(16,185,129,0.5);
}
.open-ficha-btn {
  padding: 8px 14px;
  border-radius: 8px;
  background: #F5B82E;
  color: #0B0D11;
  font-weight: 700;
  font-size: 12.5px;
  border: none;
  cursor: pointer;
}
.open-ficha-btn:hover {
  background: #FACC15;
}
"""

_MAP_JS = """
const instances = new WeakMap();

function ensureLeaflet(rootNode) {
  return new Promise((resolve) => {
    const cssHref = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    if (rootNode && !rootNode.querySelector("link[data-leaflet]")) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = cssHref;
      link.setAttribute("data-leaflet", "1");
      rootNode.appendChild(link);
    }
    if (window.L) {
      resolve(window.L);
      return;
    }
    let script = document.querySelector("script[data-leaflet]");
    if (!script) {
      script = document.createElement("script");
      script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
      script.setAttribute("data-leaflet", "1");
      document.head.appendChild(script);
    }
    script.addEventListener("load", () => resolve(window.L));
    if (window.L) resolve(window.L);
  });
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const mapEl = parentElement.querySelector("#map-canvas");
  const barEl = parentElement.querySelector("#map-Selected-bar");
  if (!mapEl || !barEl) return;

  const rootNode = parentElement.getRootNode();

  ensureLeaflet(rootNode).then((L) => {
    if (!L) return;

    let state = instances.get(parentElement);
    if (!state) {
      const map = L.map(mapEl, { zoomControl: true, attributionControl: true });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors"
      }).addTo(map);
      const layerGroup = L.layerGroup().addTo(map);
      state = { map, layerGroup, lastSig: "" };
      instances.set(parentElement, state);
      setTimeout(() => map.invalidateSize(), 150);
    }

    const startLat = Number((data && data.startLat) || 36.7213);
    const startLon = Number((data && data.startLon) || -4.4214);
    const startLabel = (data && data.startLabel) || "Punto de salida";
    const stops = (data && data.stops) || [];
    const geometry = (data && data.geometry) || [];
    const sig = JSON.stringify({ startLat, startLon, stopsLen: stops.length, geomLen: geometry.length, statuses: stops.map(s => s.commercial_status) });

    if (state.lastSig === sig) {
      return;
    }
    state.lastSig = sig;
    state.layerGroup.clearLayers();

    const bounds = [];

    // 1. Trazado peatonal real (solo si procede de OSRM)
    if (Array.isArray(geometry) && geometry.length >= 2) {
      const poly = L.polyline(geometry, {
        color: "#F5B82E",
        weight: 4,
        opacity: 0.9
      });
      poly.addTo(state.layerGroup);
    }

    // 2. Marcador de punto de salida
    const startIcon = L.divIcon({
      className: "",
      html: '<div class="start-pin" title="Punto de salida">INICIO</div>',
      iconSize: [32, 32],
      iconAnchor: [16, 16]
    });
    const startMarker = L.marker([startLat, startLon], { icon: startIcon });
    startMarker.bindPopup("<strong>Punto de salida</strong><br/>" + startLabel);
    startMarker.addTo(state.layerGroup);
    bounds.push([startLat, startLon]);

    // 3. Marcadores numerados de cada negocio
    stops.forEach((s) => {
      if (s.lat == null || s.lon == null) return;
      const lat = Number(s.lat);
      const lon = Number(s.lon);
      bounds.push([lat, lon]);

      let pinClass = "pending";
      const st = (s.commercial_status || "").toLowerCase();
      if (st.includes("cliente")) pinClass = "sold";
      else if (st.includes("descartado")) pinClass = "discarded";
      else if (st.includes("visitado") || st.includes("contactado") || st.includes("seguimiento")) pinClass = "visited";

      const pos = s.position || 1;
      const icon = L.divIcon({
        className: "",
        html: '<div class="stop-pin ' + pinClass + '">#' + pos + '</div>',
        iconSize: [30, 30],
        iconAnchor: [15, 15]
      });

      const marker = L.marker([lat, lon], { icon });
      marker.on("click", () => {
        barEl.innerHTML = "";
        const infoDiv = document.createElement("div");
        infoDiv.innerHTML = "<strong>#" + pos + " · " + (s.name || "") + "</strong> <span style='color:#9CA3AF'>(" + (s.sector || "") + " · Prioridad " + (s.score || 0) + "/100)</span>";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "open-ficha-btn";
        btn.textContent = "Abrir ficha #" + pos;
        btn.onclick = () => {
          setTriggerValue("selected_lead_id", Number(s.lead_id || s.id));
        };
        barEl.appendChild(infoDiv);
        barEl.appendChild(btn);
      });
      marker.addTo(state.layerGroup);
    });

    if (bounds.length > 1) {
      state.map.fitBounds(bounds, { padding: [32, 32], maxZoom: 17 });
    } else {
      state.map.setView([startLat, startLon], 16);
    }
    setTimeout(() => state.map.invalidateSize(), 200);
  });
}
"""

_MAP_COMPONENT = st.components.v2.component(
    "facenia_route_map",
    html=_MAP_HTML,
    css=_MAP_CSS,
    js=_MAP_JS,
)


def route_map_component(
    start_lat: float,
    start_lon: float,
    start_label: str,
    stops: List[Dict[str, Any]],
    geometry: List[List[float]],
    *,
    key: str = "facenia_map_main",
):
    compact_stops = [
        {
            "id": s.get("id"),
            "lead_id": s.get("lead_id") or s.get("id"),
            "position": s.get("position", i + 1),
            "name": s.get("name", ""),
            "sector": s.get("sector", ""),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "score": s.get("score", 0),
            "commercial_status": s.get("commercial_status", ""),
        }
        for i, s in enumerate(stops)
    ]
    return _MAP_COMPONENT(
        data={
            "startLat": float(start_lat),
            "startLon": float(start_lon),
            "startLabel": start_label,
            "stops": compact_stops,
            "geometry": geometry or [],
        },
        key=key,
        on_selected_lead_id_change=lambda: None,
    )


# --------------------------------------------------------------------------
# 4. Componente CCv2: Barra de Navegación Inferior Fija (App Móvil)
# --------------------------------------------------------------------------
_BOTTOM_NAV_HTML = """
<nav class="facenia-bottom-nav" role="navigation" aria-label="Navegación principal">
  <div class="facenia-bottom-nav-inner">
    <button type="button" class="facenia-nav-item" data-tab="inicio" aria-label="Inicio">
      <span class="facenia-nav-icon" aria-hidden="true">
        <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
          <path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"></path>
          <path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
        </svg>
      </span>
      <span class="facenia-nav-label">Inicio</span>
    </button>

    <button type="button" class="facenia-nav-item" data-tab="mapa" aria-label="Mapa">
      <span class="facenia-nav-icon" aria-hidden="true">
        <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
          <path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"></path>
          <circle cx="12" cy="10" r="3"></circle>
        </svg>
      </span>
      <span class="facenia-nav-label">Mapa</span>
    </button>

    <button type="button" class="facenia-nav-item" data-tab="negocios" aria-label="Negocios">
      <span class="facenia-nav-icon" aria-hidden="true">
        <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
          <path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"></path>
          <path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"></path>
          <path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"></path>
          <path d="M10 6h4"></path>
          <path d="M10 10h4"></path>
          <path d="M10 14h4"></path>
          <path d="M10 18h4"></path>
        </svg>
      </span>
      <span class="facenia-nav-label">Negocios</span>
    </button>

    <button type="button" class="facenia-nav-item" data-tab="visitas" aria-label="Visitas">
      <span class="facenia-nav-icon" aria-hidden="true">
        <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
          <rect width="8" height="4" x="8" y="2" rx="1" ry="1"></rect>
          <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path>
          <path d="m9 14 2 2 4-4"></path>
        </svg>
      </span>
      <span class="facenia-nav-label">Visitas</span>
    </button>

    <button type="button" class="facenia-nav-item" data-tab="perfil" aria-label="Perfil">
      <span class="facenia-nav-icon" aria-hidden="true">
        <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
          <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"></path>
          <circle cx="12" cy="7" r="4"></circle>
        </svg>
      </span>
      <span class="facenia-nav-label">Perfil</span>
    </button>
  </div>
</nav>
"""

_BOTTOM_NAV_CSS = """
.facenia-bottom-nav {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 999990;
  background: rgba(14, 18, 26, 0.97);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-top: 1px solid #262D3B;
  padding-top: 6px;
  padding-bottom: max(8px, env(safe-area-inset-bottom, 0px));
  padding-left: 8px;
  padding-right: 8px;
  box-shadow: 0 -6px 24px rgba(0, 0, 0, 0.55);
  font-family: Inter, system-ui, -apple-system, sans-serif;
}
.facenia-bottom-nav-inner {
  max-width: 680px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 4px;
  align-items: center;
}
.facenia-nav-item {
  appearance: none;
  border: none;
  background: transparent;
  color: #94A3B8;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 6px 4px;
  border-radius: 12px;
  cursor: pointer;
  transition: color 0.15s ease, background 0.15s ease, transform 0.1s ease;
  position: relative;
  min-height: 50px;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
}
.facenia-nav-item:hover {
  color: #E2E8F0;
  background: rgba(255, 255, 255, 0.04);
}
.facenia-nav-item:active {
  transform: scale(0.96);
}
.facenia-nav-item:focus-visible {
  outline: 2px solid #F5B82E;
  outline-offset: -2px;
}
.facenia-nav-item.active {
  color: #F5B82E;
  background: rgba(245, 184, 46, 0.11);
}
.facenia-nav-item.active::before {
  content: "";
  position: absolute;
  top: 0;
  left: 28%;
  right: 28%;
  height: 2.5px;
  border-radius: 999px;
  background: #F5B82E;
}
.facenia-nav-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}
.facenia-nav-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.01em;
  line-height: 1.1;
}
.facenia-nav-item.active .facenia-nav-label {
  font-weight: 700;
  color: #FACC15;
}
"""

_BOTTOM_NAV_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const activeTab = (data && data.activeTab) || "inicio";
  const buttons = parentElement.querySelectorAll(".facenia-nav-item");
  if (!buttons || !buttons.length) return;

  buttons.forEach((btn) => {
    const tab = btn.getAttribute("data-tab");
    if (tab === activeTab) {
      btn.classList.add("active");
      btn.setAttribute("aria-current", "page");
    } else {
      btn.classList.remove("active");
      btn.removeAttribute("aria-current");
    }

    btn.onclick = () => {
      buttons.forEach((b) => {
        b.classList.remove("active");
        b.removeAttribute("aria-current");
      });
      btn.classList.add("active");
      btn.setAttribute("aria-current", "page");
      setTriggerValue("selected_tab", tab);
    };
  });
}
"""

_BOTTOM_NAV_COMPONENT = st.components.v2.component(
    "facenia_bottom_nav",
    html=_BOTTOM_NAV_HTML,
    css=_BOTTOM_NAV_CSS,
    js=_BOTTOM_NAV_JS,
    isolate_styles=False,
)


def bottom_nav_bar(active_tab: str, *, key: str = "facenia_bottom_nav_main"):
    return _BOTTOM_NAV_COMPONENT(
        data={"activeTab": active_tab},
        key=key,
        on_selected_tab_change=lambda: None,
    )

