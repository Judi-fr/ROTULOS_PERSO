"""``POST /api/v1/labels/preview/``: el render de un diseño sin guardar.

Existe para que el editor muestre lo que realmente se imprime en vez de su
propia interpretación en CSS (ver ``views.PreviewLabelView``)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from ..models import LabelTemplate

User = get_user_model()

URL = "/api/v1/labels/preview/"

DESIGN = {
    "remitente": {"left": 6, "top": 22, "text": "Remitente: {{remitente}}"},
    "destinatario": {"left": 6, "top": 38, "text": "{{destinatario}}", "bold": True},
    "border": {"margin": 2, "width": 1},
}


def make_user(email="disenador@example.com"):
    user = User.objects.create_user(username=email, email=email, password="Clave123!")
    group, _ = Group.objects.get_or_create(name="subscriber")
    user.groups.add(group)
    return user


class PreviewLabelTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(user=self.user)

    def test_devuelve_un_pdf_sin_guardar_nada(self):
        # La migración siembra la plantilla del sistema, así que se mide que
        # no aparezca ninguna NUEVA, no que no haya ninguna.
        antes = LabelTemplate.objects.count()

        response = self.client.post(
            URL, {"design": DESIGN, "width_cm": 10, "height_cm": 15}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data if hasattr(response, "data") else response.content[:200])
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        # No se crea ninguna plantilla: es una vista previa, no un guardado.
        self.assertEqual(LabelTemplate.objects.count(), antes)

    def test_no_hace_falta_un_pedido(self):
        # El editor previsualiza un diseño que todavía no tiene pedido ni
        # plantilla asociada; con datos de muestra alcanza.
        response = self.client.post(URL, {"design": DESIGN}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_sin_diseno_se_rechaza(self):
        response = self.client.post(URL, {"width_cm": 10}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("design", response.data)

    def test_un_diseno_que_no_es_objeto_se_rechaza(self):
        response = self.client.post(URL, {"design": "no soy un objeto"}, format="json")

        self.assertEqual(response.status_code, 400)

    def test_una_medida_fuera_de_rango_se_rechaza(self):
        for field, value in (("width_cm", 0), ("height_cm", 500)):
            with self.subTest(field=field):
                response = self.client.post(
                    URL, {"design": DESIGN, field: value}, format="json"
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)

    def test_una_medida_que_no_es_numero_se_rechaza(self):
        response = self.client.post(
            URL, {"design": DESIGN, "width_cm": "ancho"}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_sin_sesion_no_se_puede(self):
        self.client.force_authenticate(user=None)

        self.assertEqual(self.client.post(URL, {"design": DESIGN}, format="json").status_code, 401)

    def test_el_diseno_del_sistema_se_previsualiza_entero(self):
        # La plantilla sembrada trae decoraciones (border/lines/texts) y
        # estilos por campo: el render tiene que aceptarlas tal cual salen
        # del editor.
        template = LabelTemplate.objects.filter(owner__isnull=True).first()
        self.assertIsNotNone(template, "falta la plantilla de sistema sembrada")

        response = self.client.post(
            URL,
            {
                "design": template.design,
                "width_cm": template.width_cm,
                "height_cm": template.height_cm,
                "template_id": template.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
