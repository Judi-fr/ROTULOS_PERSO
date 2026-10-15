"""Planillas para descargar (CSV y Excel), compartidas por las exportaciones.

Lo usan la exportación de pedidos (``apps.orders.bulk``) y la de auditoría
(``apps.audit.views``). Dos reglas que valen para cualquier planilla que salga
de la app:

- **CSV con ``;`` y BOM**: es lo que Excel en español abre bien con doble
  clic (con ``,`` y sin BOM mete todo en una columna y rompe los acentos).
- **Nada se lee como fórmula**: los datos los escribe un tercero (el comprador,
  el nombre de una tienda, un email), así que un valor que empieza con
  ``= + - @`` se neutraliza (inyección de CSV) y en el .xlsx todo texto se
  fuerza a texto.
"""

from __future__ import annotations

import csv
import io
import re

from openpyxl import Workbook

# Un valor que empieza con estos caracteres una planilla lo toma como
# fórmula. Un teléfono "+54 11 ..." es solo números y no se toca, para no
# ensuciarlo con la comilla.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_HARMLESS_NUMBER = re.compile(r"^[+-]?[\d\s().-]+$")


def safe_cell(value):
    if not isinstance(value, str):
        return value
    if value.startswith(_FORMULA_PREFIXES) and not _HARMLESS_NUMBER.match(value):
        return "'" + value
    return value


def csv_bytes(columns, rows):
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([safe_cell(value) for value in row])
    return buffer.getvalue().encode("utf-8")


def xlsx_bytes(columns, rows, title):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.append(columns)
    for row in rows:
        sheet.append([safe_cell(value) for value in row])
    # Todo texto queda como texto: openpyxl escribe como fórmula cualquier
    # cadena que empiece con "=", aun después de safe_cell.
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    sheet.freeze_panes = "A2"
    for column_cells in sheet.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells[:200])
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 8), 50)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
