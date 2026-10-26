"""Lo que el editor de rótulos necesita del backend para reglas, campos
calculados y bloqueo: la numeración de ``{{secuencia}}`` configurable
(``/labels/sequence/``) y la clave ``locked`` del diseño, que el render
ignora."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog

from ..label_rendering import apply_design_rules, render_label_pdf
from ..models import Label, LabelSequence
from ..zpl import render_label_zpl
from .test_labels import VALID_DESIGN, auth_headers_for
from apps.accounts.tests.team_helpers import new_business

User = get_user_model()


class LabelSequenceApiTests(APITestCase):
    URL = "/api/v1/labels/sequence/"

    def setUp(self):
        group, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="numeracion@example.com", email="numeracion@example.com", password="Clave123!"
        )
        self.user.groups.add(group)
        self.biz = new_business(self.user)
        self.headers = auth_headers_for(self.user)

    def test_sin_configurar_devuelve_los_valores_por_defecto(self):
        resp = self.client.get(self.URL, **self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, {"prefix": "", "padding": 6, "next": 1, "example": "000001"})
        self.assertFalse(LabelSequence.objects.exists(), "leer no crea la fila")

    def test_guardar_y_el_proximo_rotulo_lleva_ese_numero(self):
        resp = self.client.put(
            self.URL, {"prefix": " ENV-", "padding": 4, "next": 120}, format="json", **self.headers
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["example"], "ENV-0120")
        # El mismo contador que consume el render.
        self.assertEqual(LabelSequence.next_value(self.biz), "ENV-0120")
        self.assertEqual(LabelSequence.next_value(self.biz), "ENV-0121")
        self.assertEqual(self.client.get(self.URL, **self.headers).data["next"], 122)
        self.assertTrue(
            AuditLog.objects.filter(action="label.update", target_type="label_sequence").exists()
        )

    def test_valores_invalidos_400_y_no_cambia_nada(self):
        for payload in (
            {"prefix": "x" * 21, "padding": 4, "next": 1},
            {"prefix": "", "padding": 13, "next": 1},
            {"prefix": "", "padding": 4, "next": 0},
            {"prefix": "", "padding": "4", "next": 1},
            {"prefix": "", "padding": True, "next": 1},
        ):
            resp = self.client.put(self.URL, payload, format="json", **self.headers)
            self.assertEqual(resp.status_code, 400, payload)
        self.assertFalse(LabelSequence.objects.exists())

    def test_cada_usuario_tiene_su_numeracion(self):
        other = User.objects.create_user(username="otro@example.com", email="otro@example.com", password="Clave123!")
        other.groups.add(Group.objects.get(name="subscriber"))
        new_business(other)
        self.client.put(self.URL, {"prefix": "A-", "padding": 2, "next": 50}, format="json", **self.headers)
        self.assertEqual(self.client.get(self.URL, **auth_headers_for(other)).data["next"], 1)

    def test_requiere_sesion(self):
        self.assertEqual(self.client.get(self.URL).status_code, 401)


class LockedDesignTests(APITestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="bloqueo@example.com", email="bloqueo@example.com", password="Clave123!"
        )
        self.user.groups.add(group)
        self.biz = new_business(self.user)
        self.headers = auth_headers_for(self.user)

    def _create(self, design):
        return self.client.post(
            "/api/v1/labels/labels/",
            {"name": "Bloqueos", "client": "María", "width_cm": "10", "height_cm": "15", "design": design},
            format="json",
            **self.headers,
        )

    def test_se_guarda_la_lista_de_bloqueados(self):
        design = dict(
            VALID_DESIGN,
            locked=["logo", "qr", "destinatario"],
            lines=[{"top": 50, "locked": True}],
            texts=[{"text": "Frágil", "left": 10, "top": 70, "locked": True}],
        )
        resp = self._create(design)
        self.assertEqual(resp.status_code, 201, resp.data)
        saved = Label.objects.get(pk=resp.data["id"]).design
        self.assertEqual(saved["locked"], ["logo", "qr", "destinatario"])
        self.assertTrue(saved["lines"][0]["locked"])

    def test_campo_inexistente_o_formato_malo_400(self):
        self.assertEqual(self._create(dict(VALID_DESIGN, locked=["no_existe"])).status_code, 400)
        self.assertEqual(self._create(dict(VALID_DESIGN, locked="logo")).status_code, 400)
        self.assertEqual(
            self._create(dict(VALID_DESIGN, lines=[{"top": 50, "locked": "si"}])).status_code, 400
        )

    def test_una_regla_no_puede_apuntar_a_locked(self):
        design = dict(
            VALID_DESIGN,
            locked=["logo"],
            rules=[{"when": {"field": "cp", "op": "empty"}, "then": {"action": "hide", "target": "locked"}}],
        )
        self.assertEqual(self._create(design).status_code, 400)

    def test_el_render_lo_ignora(self):
        design = dict(VALID_DESIGN, locked=["logo", "remitente"])
        context = {"remitente": "Tienda", "destinatario": "María"}
        self.assertTrue(render_label_pdf(design, 10, 15, context=context).startswith(b"%PDF"))
        plain = render_label_zpl(VALID_DESIGN, 10, 15, context=context)
        self.assertEqual(render_label_zpl(design, 10, 15, context=context), plain)
        self.assertIn("locked", apply_design_rules(design, context, None))
