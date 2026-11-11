from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings

from apps.common.mail import branded_html


class BrandedHtmlTests(SimpleTestCase):
    def test_carries_heading_text_button_and_footnote(self):
        html = branded_html(
            heading="Confirmá tu email",
            paragraphs=["Gracias por registrarte."],
            action=("Confirmar mi email", "https://app.example/verify-email.html?token=abc"),
            footnote="Si no fuiste vos, ignoralo.",
        )
        self.assertIn("Confirmá tu email", html)
        self.assertIn("Gracias por registrarte.", html)
        self.assertIn('href="https://app.example/verify-email.html?token=abc"', html)
        self.assertIn("Confirmar mi email", html)
        self.assertIn("Si no fuiste vos, ignoralo.", html)

    def test_third_party_text_is_escaped(self):
        html = branded_html(
            heading="<script>alert(1)</script>",
            paragraphs=["Tienda <b>Juan & Hijos</b>"],
            items=[("<img src=x onerror=alert(1)>", "detalle <i>x</i>")],
            action=("Ver", 'https://app.example/?a=1&b="x"'),
        )
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>Juan", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<i>x</i>", html)
        self.assertIn("Juan &amp; Hijos", html)
        self.assertIn("a=1&amp;b=&quot;x&quot;", html)

    def test_no_remote_images_nor_stylesheets(self):
        html = branded_html(heading="Hola", paragraphs=["Texto"], action=("Ver", "https://app.example/"))
        self.assertNotIn("<img", html)
        self.assertNotIn("<link", html)
        self.assertNotIn("<style", html)

    def test_item_without_detail(self):
        html = branded_html(heading="Avisos", items=[("La tienda dejó de responder", "")])
        self.assertIn("La tienda dejó de responder", html)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class VerificationMailTests(TestCase):
    def test_register_mail_has_text_and_html_with_the_same_link(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"email": "mail-html@rotulos.local", "password": "Clave-Segura-9", "name": "Mail Html"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        html, mimetype = message.alternatives[0]
        self.assertEqual(mimetype, "text/html")
        link = next(line for line in message.body.splitlines() if "token=" in line).strip()
        self.assertIn(f'href="{link}"', html)
        self.assertIn("Confirmar mi email", html)
