"""Tests del parseo de rangos de fecha compartido.

El comportamiento de punta a punta (que filtrar un listado por fechas
devuelva lo que tiene que devolver) ya lo cubren los tests de orders,
audit, documents y labels, que son los que consumen esto. Acá se fija el
contrato de la función en sí, que antes no lo tenía nadie porque estaba
repetida en siete lugares.
"""

from datetime import date

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.common.date_filters import date_range_q, parse_date_param, parse_date_range


class ParseDateParamTests(SimpleTestCase):
    def test_fecha_valida(self):
        self.assertEqual(parse_date_param("2026-09-24", "date_from"), date(2026, 9, 24))

    def test_vacio_o_ausente_es_none(self):
        for valor in ("", "   ", None):
            with self.subTest(valor=valor):
                self.assertIsNone(parse_date_param(valor, "date_from"))

    def test_espacios_alrededor_no_molestan(self):
        self.assertEqual(parse_date_param("  2026-09-24 ", "date_from"), date(2026, 9, 24))

    def test_formato_invalido_es_400_y_nombra_el_parametro(self):
        for valor in ("24/09/2026", "2026-13-01", "ayer", "2026-09-31"):
            with self.subTest(valor=valor):
                with self.assertRaises(ValidationError) as caso:
                    parse_date_param(valor, "date_to")
                self.assertIn("date_to", caso.exception.detail)

    def test_un_valor_que_no_es_texto_no_revienta(self):
        """Los filtros del lote llegan en un body JSON, donde el cliente
        puede mandar cualquier cosa: tiene que ser un 400, no un 500."""
        with self.assertRaises(ValidationError):
            parse_date_param(20260924, "date_from")


class ParseDateRangeTests(SimpleTestCase):
    def test_devuelve_las_dos_fechas(self):
        desde, hasta = parse_date_range({"date_from": "2026-09-01", "date_to": "2026-09-30"})
        self.assertEqual((desde, hasta), (date(2026, 9, 1), date(2026, 9, 30)))

    def test_sin_parametros_devuelve_none(self):
        self.assertEqual(parse_date_range({}), (None, None))

    def test_rango_al_reves_se_rechaza(self):
        with self.assertRaises(ValidationError) as caso:
            parse_date_range({"date_from": "2026-09-30", "date_to": "2026-09-01"})
        self.assertIn("date_to", caso.exception.detail)

    def test_mismo_dia_en_los_dos_extremos_es_valido(self):
        desde, hasta = parse_date_range({"date_from": "2026-09-24", "date_to": "2026-09-24"})
        self.assertEqual(desde, hasta)

    def test_nombres_de_parametro_propios(self):
        desde, hasta = parse_date_range(
            {"date_joined_from": "2026-01-01", "date_joined_to": "2026-02-01"},
            "date_joined_from",
            "date_joined_to",
        )
        self.assertEqual((desde, hasta), (date(2026, 1, 1), date(2026, 2, 1)))


class DateRangeQTests(SimpleTestCase):
    @staticmethod
    def _condiciones(q):
        return dict(q.children)

    def test_sin_parametros_es_un_q_vacio(self):
        """Filtrar con esto no tiene que recortar nada: así el llamador no
        necesita preguntar si vinieron las fechas."""
        self.assertEqual(len(date_range_q({}).children), 0)

    def test_compara_por_fecha_para_incluir_el_dia_entero(self):
        """``__date__lte`` y no ``__lte``: con el segundo, 'hasta el 24'
        dejaría afuera todo lo del 24 salvo la medianoche exacta."""
        condiciones = self._condiciones(date_range_q({"date_to": "2026-09-24"}))
        self.assertEqual(condiciones, {"created_at__date__lte": date(2026, 9, 24)})

    def test_solo_desde(self):
        condiciones = self._condiciones(date_range_q({"date_from": "2026-09-01"}))
        self.assertEqual(condiciones, {"created_at__date__gte": date(2026, 9, 1)})

    def test_los_dos_extremos(self):
        condiciones = self._condiciones(
            date_range_q({"date_from": "2026-09-01", "date_to": "2026-09-30"})
        )
        self.assertEqual(
            condiciones,
            {
                "created_at__date__gte": date(2026, 9, 1),
                "created_at__date__lte": date(2026, 9, 30),
            },
        )

    def test_campo_propio(self):
        """El panel de usuarios filtra por date_joined y por last_login, no
        por created_at."""
        condiciones = self._condiciones(
            date_range_q(
                {"date_joined_from": "2026-01-01"},
                "date_joined",
                "date_joined_from",
                "date_joined_to",
            )
        )
        self.assertEqual(condiciones, {"date_joined__date__gte": date(2026, 1, 1)})
