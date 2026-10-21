"""El lector de planillas corta antes de cargar en memoria un archivo con
muchísimas filas (un .xlsx comprime muy bien: 5 MB pueden ser millones)."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from openpyxl import Workbook

from apps.orders.import_parsing import ImportFileError, parse_tabular_file


def xlsx_with_rows(count):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet()
    sheet.append(["pedido", "seguimiento"])
    for number in range(count):
        sheet.append([number, f"AR{number}"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile("planilla.xlsx", buffer.getvalue())


@override_settings(IMPORT_PARSE_MAX_ROWS=100)
class ParseLimitTests(SimpleTestCase):
    def test_a_huge_sheet_is_refused_while_reading(self):
        with self.assertRaises(ImportFileError) as raised:
            parse_tabular_file(xlsx_with_rows(101), "planilla.xlsx")
        self.assertIn("100", str(raised.exception))

    def test_a_huge_csv_is_refused_too(self):
        content = "pedido;seguimiento\n" + "".join(f"{n};AR{n}\n" for n in range(101))
        with self.assertRaises(ImportFileError):
            parse_tabular_file(SimpleUploadedFile("planilla.csv", content.encode()), "planilla.csv")

    def test_up_to_the_limit_is_read(self):
        headers, rows = parse_tabular_file(xlsx_with_rows(100), "planilla.xlsx")
        self.assertEqual((headers, len(rows)), (["pedido", "seguimiento"], 100))
