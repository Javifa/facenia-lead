"""
Módulo de exportación a Excel y CSV optimizado para Notion y CRM de FACENIA.

Genera libros de Excel multi-pestaña con estilos profesionales sobrios y archivos CSV
codificados en UTF-8 con BOM para importación directa en Notion sin corrupción de caracteres.
Sin emojis ni adornos visuales superfluos.
"""

import csv
import io
from typing import Any, Dict, List, Tuple
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from config import (
    FOLLOWUP_STATUSES,
    NO_WEB_STATUSES,
    PRIORITY_HIGH,
    WEB_CONFIRMED_NONE,
    WEB_NONE,
    WEB_REVIEW,
    WEB_SOCIAL,
)

COLUMN_EXPORT_MAPPING = [
    ("name", "Nombre Comercial"),
    ("sector", "Sector"),
    ("commercial_status", "Estado Comercial"),
    ("priority_val", "Prioridad"),
    ("score", "Puntuacion Comercial (0-100)"),
    ("web_status_val", "Estado de la Web"),
    ("website", "Pagina Web"),
    ("social_url", "Redes Sociales"),
    ("phone", "Telefono Comercial"),
    ("email", "Correo Electronico"),
    ("address", "Direccion"),
    ("city", "Ciudad"),
    ("postal_code", "Codigo Postal"),
    ("opening_hours", "Horario"),
    ("next_action", "Proxima Accion"),
    ("next_followup_date", "Fecha de Seguimiento"),
    ("last_contact_date", "Ultimo Contacto"),
    ("sale_service", "Servicio Vendido"),
    ("sale_amount", "Importe Venta (EUR)"),
    ("sale_date", "Fecha de Venta"),
    ("nfc_status", "Estado Tarjeta NFC"),
    ("review_url", "Enlace de Resenas"),
    ("notes", "Notas Comerciales"),
    ("data_source", "Fuente de Datos"),
    ("verification_status", "Detalle de Verificacion"),
    ("map_url", "Enlace al Mapa"),
    ("created_at", "Fecha de Registro"),
]


def prepare_export_rows(leads: List[Dict[str, Any]]) -> Tuple[List[str], List[List[Any]]]:
    """Convierte una lista de prospectos en cabeceras y filas estructuradas."""
    headers = [col_label for _, col_label in COLUMN_EXPORT_MAPPING]
    if not leads:
        return headers, []

    rows: List[List[Any]] = []
    for lead in leads:
        row: List[Any] = []
        web_val = lead.get("web_effective") or lead.get("web_status") or WEB_NONE
        prio_val = lead.get("priority_effective") or lead.get("priority") or "Baja"
        map_url = lead.get("map_url") or lead.get("google_maps_url") or ""

        for key, _ in COLUMN_EXPORT_MAPPING:
            if key == "web_status_val":
                row.append(web_val)
            elif key == "priority_val":
                row.append(prio_val)
            elif key == "map_url":
                row.append(map_url)
            else:
                row.append(lead.get(key, ""))
        rows.append(row)

    return headers, rows


def export_leads_to_csv_bytes(leads: List[Dict[str, Any]]) -> bytes:
    """
    Genera un archivo CSV codificado en UTF-8 con BOM ('utf-8-sig').
    Garantiza compatibilidad nativa con Notion, CRM y Microsoft Excel.
    """
    headers, rows = prepare_export_rows(leads)
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode("utf-8-sig")


def _apply_sheet_formatting(ws, title: str):
    """Aplica formato visual sobrio y corporativo a las hojas de Excel."""
    header_fill = PatternFill(start_color="0F2A4A", end_color="0F2A4A", fill_type="solid")
    header_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
    data_font = Font(name="Segoe UI", size=9)
    border_thin = Side(style="thin", color="CBD5E1")
    cell_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = cell_border

    ws.row_dimensions[1].height = 24

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = data_font
            cell.border = cell_border
            if isinstance(cell.value, (int, float)):
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    if ws.max_column > 0 and ws.max_row > 1:
        max_col_letter = get_column_letter(ws.max_column)
        ws.auto_filter.ref = f"A1:{max_col_letter}{ws.max_row}"

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        adjusted_width = max(12, min(max_len + 3, 40))
        ws.column_dimensions[col_letter].width = adjusted_width


def export_leads_to_excel_bytes(all_leads: List[Dict[str, Any]]) -> bytes:
    """
    Genera un libro de Excel (.xlsx) estructurado en 4 hojas de trabajo:
    1. 'Todos los Prospectos'
    2. 'Sin Web Indicada'
    3. 'Prioridad Alta'
    4. 'Seguimiento Comercial'
    """
    wb = Workbook()
    ws_all = wb.active
    ws_all.title = "Todos los Prospectos"

    df_all = prepare_export_rows(all_leads)

    # Filtrar sin web
    no_web_leads = [
        item for item in all_leads
        if (item.get("web_effective") or item.get("web_status")) in (
            WEB_NONE, WEB_SOCIAL, WEB_CONFIRMED_NONE, "Sin web indicada", "Solo redes sociales"
        )
    ]
    df_no_web = prepare_export_rows(no_web_leads)

    # Filtrar prioridad alta
    high_priority_leads = [
        item for item in all_leads
        if (item.get("priority_effective") or item.get("priority")) == PRIORITY_HIGH
    ]
    df_high = prepare_export_rows(high_priority_leads)

    # Filtrar pendientes de verificar
    pending_leads = [
        item for item in all_leads
        if (item.get("web_effective") or item.get("web_status")) in (WEB_REVIEW, "Revisión manual", "Pendiente de verificar")
    ]
    df_pending = prepare_export_rows(pending_leads)

    # Filtrar seguimiento comercial
    followup_leads = [
        item for item in all_leads
        if item.get("commercial_status") in FOLLOWUP_STATUSES or item.get("next_followup_date")
    ]
    df_followup = prepare_export_rows(followup_leads)

    def populate_sheet(ws, table_data, sheet_name):
        headers, rows = table_data
        ws.title = sheet_name
        ws.append(headers)
        for row in rows:
            ws.append(row)
        _apply_sheet_formatting(ws, sheet_name)

    populate_sheet(ws_all, df_all, "Todos los Prospectos")

    ws_no_web = wb.create_sheet(title="Sin Web Indicada")
    populate_sheet(ws_no_web, df_no_web, "Sin Web Indicada")

    ws_high = wb.create_sheet(title="Prioridad Alta")
    populate_sheet(ws_high, df_high, "Prioridad Alta")

    ws_pending = wb.create_sheet(title="Pendientes de Verificar")
    populate_sheet(ws_pending, df_pending, "Pendientes de Verificar")

    ws_followup = wb.create_sheet(title="Seguimiento Comercial")
    populate_sheet(ws_followup, df_followup, "Seguimiento Comercial")

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()
