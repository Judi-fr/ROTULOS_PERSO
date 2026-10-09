"""Imprimir al despachar (``printing.py``, ``POST shipments/print/``): un solo
PDF con nuestro rótulo y la etiqueta de Andreani de cada envío."""

import io
from unittest.mock import patch

from pypdf import PdfReader
from reportlab.pdfgen import canvas
from rest_framework.test import APITestCase

from apps.carriers.andreani import shipments as service
from apps.carriers.models import CarrierShipment
from apps.integrations.tests.helpers import auth_headers_for, make_user
from apps.labels.models import LabelTemplate

from .fake_andreani import AndreaniTestMixin
from .test_andreani import API, make_order


def andreani_label(number, **_kwargs):
    """La etiqueta de Andreani simulada como PDF de verdad (la de
    ``fake_andreani`` es un texto que ningún lector abre)."""
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    page.drawString(72, 720, f"ETIQUETA ANDREANI {number}")
    page.showPage()
    page.save()
    return buffer.getvalue()


class PrintTests(AndreaniTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        self.account = self.make_account(self.user)
        self.shipments = [
            service.create_shipment(None, self.account, make_order(self.user, recipient_name=name), "400006709")
            for name in ("María Gómez", "Juan Pérez")
        ]
        patcher = patch("apps.carriers.andreani.client.AndreaniClient.label", autospec=True, side_effect=lambda client, number, **kw: andreani_label(number))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _print(self, ids, user=None):
        return self.client.post(
            f"{API}/shipments/print/", {"shipment_ids": ids}, format="json", **auth_headers_for(user or self.user)
        )

    def pages(self, response):
        return [page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages]

    def test_por_cada_envio_va_el_rotulo_y_despues_la_etiqueta_de_andreani(self):
        self.assertTrue(LabelTemplate.objects.filter(is_public=True, is_active=True).exists())

        response = self._print([shipment.pk for shipment in self.shipments])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        pages = self.pages(response)
        self.assertEqual(len(pages), 4)
        first, second = self.shipments
        # El rótulo es el nuestro, con el destinatario y el número de Andreani.
        self.assertIn("María Gómez", pages[0])
        self.assertIn(f"ETIQUETA ANDREANI {first.group_number or first.tracking_number}", pages[1])
        self.assertIn("Juan Pérez", pages[2])
        self.assertIn(f"ETIQUETA ANDREANI {second.group_number or second.tracking_number}", pages[3])

    def test_sin_plantilla_sale_igual_la_etiqueta_de_andreani(self):
        LabelTemplate.objects.update(is_active=False)

        response = self._print([self.shipments[0].pk])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.pages(response)), 1)

    def test_un_envio_cancelado_o_de_otro_no_se_imprime(self):
        CarrierShipment.objects.filter(pk=self.shipments[0].pk).update(status=CarrierShipment.Status.CANCELLED)

        self.assertEqual(self._print([self.shipments[0].pk]).status_code, 400)
        self.assertEqual(self._print([self.shipments[1].pk], user=make_user("otro@example.com")).status_code, 400)

    def test_una_etiqueta_ilegible_de_andreani_avisa_en_vez_de_imprimir_a_medias(self):
        with patch("apps.carriers.andreani.client.AndreaniClient.label", return_value=b"no es un pdf"):
            response = self._print([shipment.pk for shipment in self.shipments])

        self.assertEqual(response.status_code, 502)
        self.assertIn("ilegible", response.data["detail"])
