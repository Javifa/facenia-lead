# FACENIA Lead Finder — Buscador de Clientes Potenciales

Herramienta profesional de prospección comercial para FACENIA, diseñada para localizar negocios locales sin página web propia o con oportunidades de digitalización, automatización y desarrollo de software a medida.

La aplicación utiliza **OpenStreetMap (Overpass API y Nominatim)** como fuente de datos principal, permitiendo realizar consultas gratuitas e ilimitadas sin depender de Google Places API ni de servicios con facturación por uso.

---

## 1. Características Principales

- **Búsqueda 100% gratuita y abierta**: Integración nativa con OpenStreetMap y Overpass API. No requiere tarjetas de crédito, claves de pago ni cuentas de facturación en Google Cloud.
- **Arquitectura modular de proveedores**:
  - `OpenStreetMap` (Proveedor principal activo por defecto).
  - `Demostración local` (Catálogo offline de muestra para pruebas inmediatas).
  - `Google Places API` (Proveedor comercial opcional, desactivado por defecto).
- **Interfaz profesional sin emojis**: Diseño sobrio de software empresarial (SaaS), tipografía clara, bordes sutiles y paleta corporativa neutra.
- **Modo oscuro y modo claro**: Modo oscuro configurado como predeterminado para largas jornadas de prospección, con conmutador instantáneo y persistencia de preferencia en base de datos.
- **Flujo de cualificación en dos fases**:
  1. *Exploración*: Búsqueda geográfica y sectorial rápida en el Buscador.
  2. *Cualificación y guardado*: Revisión de resultados en tabla interactiva y guardado manual o por lote únicamente de los negocios de verdadero interés comercial.
- **Detección rigurosa de presencia web**:
  - `Sin web indicada`: Negocios sin URL en la fuente consultada (prioridad comercial para desarrollo web).
  - `Web encontrada`: Sitios web corporativos propios detectados (oportunidad para software, IA o integraciones).
  - `Revisión manual`: Empresas que utilizan perfiles de redes sociales (Instagram, Facebook) como única "web", o dominios con incidencias técnicas.
  - `Pendiente de verificar`: Casos que requieren confirmación adicional.
- **Persistencia local en SQLite con deduplicación**:
  - Base de datos local indexada por identificador de origen (`source_id`) y hash normalizado de deduplicación.
  - **Preservación total del trabajo comercial**: Al consultar de nuevo una zona, las notas comerciales, observaciones y estados de seguimiento se conservan intactos.
- **Búsquedas favoritas**: Guardado de configuraciones habituales (ciudad, radio, sectores) para relanzarlas con un solo clic.
- **Exportación optimizada para Notion CRM y Excel**:
  - Archivos CSV en codificación `UTF-8 con BOM` (`utf-8-sig`) compatibles con Notion.
  - Libros de Excel (.xlsx) organizados en 4 hojas de trabajo especializadas (*Todos los Prospectos*, *Sin Web Indicada*, *Prioridad Alta*, *Pendientes de Verificar*).
- **Caché y respeto a las fuentes**:
  - Almacenamiento local de coordenadas geográficas en SQLite (`geo_cache`) para evitar consultas redundantes a Nominatim.
  - Cabecera `User-Agent` identificativa conforme a las directrices de la Fundación OpenStreetMap.
  - Tolerancia a fallos con rotación automática de servidores espejo de Overpass.

---

## 2. Estructura del Proyecto

```
BuscaLeads/
│
├── app.py                      # Interfaz gráfica completa en Streamlit (modo oscuro/claro, sin emojis)
├── config.py                   # Catálogo de 15 sectores con etiquetas OSM, scoring y configuración
├── database.py                 # Persistencia SQLite (leads, resultados temporales, favoritos, ajustes)
├── providers.py                # Arquitectura modular de proveedores (OSM, Demo, Google Places)
├── search_service.py           # Servicio unificado de prospección y filtrado comercial
├── scoring.py                  # Algoritmo de puntuación comercial (0 a 100) y prioridad
├── verification.py             # Clasificador de dominios, redes sociales y verificación HTTP
├── export_service.py           # Generador de Excel (.xlsx) de 4 pestañas y CSV para Notion
├── demo_data.py                # Catálogo de muestra para pruebas offline
├── places_client.py            # Cliente de Google Places API (conservado como opcional comercial)
│
├── tests/                      # Suite de pruebas automatizadas con pytest
│   ├── test_database.py        # Pruebas de persistencia, deduplicación, caché y favoritos
│   ├── test_providers.py       # Pruebas de la arquitectura modular y proveedores
│   ├── test_scoring.py         # Pruebas del cálculo de puntuación comercial
│   ├── test_verification.py    # Pruebas de detección web y redes sociales
│   └── test_export.py          # Pruebas de exportación a Excel y CSV Notion
│
├── iniciar_facenia_leads.bat   # Lanzador directo para Windows 11
├── requirements.txt            # Dependencias fijadas y verificadas
├── .env.example                # Plantilla de variables de entorno
├── .gitignore                  # Protección de base de datos local y temporales
└── README.md                   # Esta documentación
```

---

## 3. Instalación y Ejecución en Windows 11

### Método A: Doble Clic (Recomendado)
Haz doble clic sobre el archivo:
```
iniciar_facenia_leads.bat
```
El script configura el entorno virtual, instala dependencias si es necesario y abre la aplicación en tu navegador web en `http://localhost:8501`.

### Método B: Ejecución desde PowerShell
```powershell
cd "c:\Users\tacky\OneDrive\Escritorio\Proyectos\BuscaLeads"
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

---

## 4. Guía de Uso del Buscador

1. **Seleccionar ubicación**: Introduce la ciudad (por defecto: Málaga), provincia y país.
2. **Definir el radio**: Establece el alcance geográfico en kilómetros (ej: 10, 25 o 50 km).
3. **Elegir sectores**: Selecciona uno o varios de los 15 sectores comerciales disponibles (Reformas, Climatización, Clínicas, Instalaciones, etc.).
4. **Filtros de cualificación (Opcional)**:
   - Marca *Filtrar solo sin web indicada* si deseas excluir directamente empresas con sitio web.
   - Marca *Filtrar solo con teléfono disponible* para priorizar negocios contactables de inmediato.
5. **Ejecutar búsqueda**: Haz clic en *Ejecutar búsqueda*. La aplicación geocodificará la zona y consultará OpenStreetMap mediante Overpass API.
6. **Guardar en Prospectos**:
   - Revisa la tabla en la sección **Resultados**.
   - Selecciona con las casillas de verificación las filas de interés comercial y pulsa *Guardar seleccionados como prospectos permanentes* (o pulsa *Guardar todos en Prospectos*).
7. **Gestión y Seguimiento**: En la sección **Seguimiento comercial**, actualiza el estado de contacto (*Pendiente de contactar, Contactado, Ha respondido, etc.*) y planifica el próximo paso.
8. **Exportar a Notion**: En la sección **Prospectos guardados**, descarga el CSV listo para importar en tu base de datos de Notion.

---

## 5. Proveedor Comercial de Google Places (Opcional)

Si en el futuro deseas utilizar la API oficial de Google Places New de forma voluntaria:
1. Accede a Google Cloud Platform y obtén una clave con la API *Places API (New)* activada.
2. Añade tu clave en el archivo `.env`:
   ```ini
   GOOGLE_PLACES_API_KEY=tu_clave_de_google
   ```
3. En el Buscador, selecciona el proveedor *Google Places (Opcional)* en el menú desplegable.
*(Si no dispones de esta clave, OpenStreetMap funciona de forma 100% independiente y gratuita).*

---

## 6. Pruebas Automatizadas

La suite completa de pruebas unitarias puede ejecutarse en cualquier momento con:
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```
Se verifican 18 pruebas unitarias que cubren:
- Persistencia, actualización y borrado en SQLite.
- Deduplicación y preservación estricta de notas de prospección.
- Búsquedas favoritas y caché de geocodificación.
- Proveedores de búsqueda y fallback.
- Algoritmo de puntuación comercial (0-100).
- Detección de dominios propios frente a redes sociales y directorios.
- Estructura multi-pestaña de Excel y compatibilidad UTF-8 BOM con Notion.
