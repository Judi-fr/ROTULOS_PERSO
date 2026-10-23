"""Lo que encontró la revisión final de los comercios con equipo: cada test
reproduce un hueco (fuga entre tiendas o comercios, o algo que se rompió)."""

import csv
import io
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.models import TeamInvitation
from apps.audit.models import AuditLog
from apps.documents.models import Document
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order

from .test_store_scope import DESIGN, store
from .test_team_permissions import add_employee, bearer, make_owner, make_user


def csv_file(rows):
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";").writerows(rows)
    return SimpleUploadedFile("seguimientos.csv", buffer.getvalue().encode("utf-8"), content_type="text/csv")


class ReviewFixesTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.t1 = store(self.owner, "1")
        self.t2 = store(self.owner, "2")
        address = Address.objects.create(user=self.owner, recipient_name="Compradora T2", street="Calle", number="1", city="CABA")
        self.o1 = Order.objects.create(user=self.owner, address=address, store_connection=self.t1, external_id="1", external_number="1001")
        self.o2 = Order.objects.create(user=self.owner, address=address, store_connection=self.t2, external_id="2", external_number="2002")
        self.template = LabelTemplate.objects.create(owner=self.owner, name="Privada", width_cm=10, height_cm=15, design=DESIGN)
        self.limited, member = add_employee(self.owner, "limitado@example.com", profile="Todo")
        member.all_stores = False
        member.save()
        member.stores.set([self.t1])

    def test_render_refuses_order_of_other_store(self):
        response = self.client.post(
            "/api/v1/labels/render/", {"template_id": self.template.id, "order_id": self.o2.id}, format="json", **bearer(self.limited)
        )
        self.assertEqual(response.status_code, 404)

    def test_tracking_preview_does_not_match_other_store_orders(self):
        upload = csv_file([["Nro de pedido", "Número de seguimiento"], ["2002", "AR1"], ["1001", "AR2"]])
        response = self.client.post(
            "/api/v1/orders/tracking-import/preview/", {"file": upload}, format="multipart", **bearer(self.limited)
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["state"] for row in response.data["rows"]], ["not_found", "ok"])
        self.assertNotIn("Compradora T2", str(response.data["rows"][0]))

    def test_employee_can_attach_label_to_business_order(self):
        everything, _ = add_employee(self.owner, "todo@example.com", profile="Todo")
        payload = {"name": "R", "width_cm": "10", "height_cm": "15", "design": DESIGN, "order": self.o1.id}
        response = self.client.post("/api/v1/labels/labels/", payload, format="json", **bearer(everything))
        self.assertEqual(response.status_code, 201, response.data)
        payload["order"] = self.o2.id
        response = self.client.post("/api/v1/labels/labels/", payload, format="json", **bearer(self.limited))
        self.assertEqual(response.status_code, 400)

    def test_employee_sets_business_private_template_as_default(self):
        response = self.client.patch(
            "/api/v1/auth/me/account/", {"default_template": self.template.id}, format="json", **bearer(self.limited)
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_limited_employee_cannot_write_rates_on_other_store(self):
        body = {"connection": self.t2.id, "option_code": "std", "option_name": "Std", "postal_code_from": "1000",
                "postal_code_to": "1999", "price": "100"}
        response = self.client.post("/api/v1/integrations/shipping-rates/", body, format="json", **bearer(self.limited))
        self.assertEqual(response.status_code, 400)
        body["connection"] = self.t1.id
        response = self.client.post("/api/v1/integrations/shipping-rates/", body, format="json", **bearer(self.limited))
        self.assertEqual(response.status_code, 201, response.data)

    def test_legacy_document_is_hidden_from_limited_employee(self):
        legacy = Document.objects.create(user=self.owner, name="Viejo", status=Document.Status.READY, store_ids=None)
        ids = {item["id"] for item in self.client.get("/api/v1/documents/", **bearer(self.limited)).data["results"]}
        self.assertNotIn(legacy.id, ids)
        ids = {item["id"] for item in self.client.get("/api/v1/documents/", **bearer(self.owner)).data["results"]}
        self.assertIn(legacy.id, ids)

    def test_manager_cannot_touch_a_more_powerful_member(self):
        manager, _ = add_employee(self.owner, "m@example.com", accesses=["team", "orders.view"])
        _, strong = add_employee(self.owner, "fuerte@example.com", profile="Todo")
        url = f"/api/v1/team/members/{strong.id}/"
        body = {"profile": None, "accesses": ["orders.view"]}
        self.assertEqual(self.client.patch(url, body, format="json", **bearer(manager)).status_code, 403)
        self.assertEqual(self.client.delete(url, **bearer(manager)).status_code, 403)
        self.assertEqual(self.client.patch(url, body, format="json", **bearer(self.owner)).status_code, 200)

    def test_profile_used_by_a_pending_invitation_cannot_be_deleted(self):
        atencion = teams.team_for(self.owner).profiles.get(name="Atención")
        self.client.post("/api/v1/team/invitations/", {"email": "x@example.com", "profile": atencion.id}, format="json", **bearer(self.owner))
        response = self.client.delete(f"/api/v1/team/profiles/{atencion.id}/", **bearer(self.owner))
        self.assertEqual(response.status_code, 400)

    def test_activity_hides_what_a_member_did_before_joining(self):
        other_owner = make_user("otro-owner@example.com")
        mover, _ = add_employee(other_owner, "mover@example.com", profile="Todo")
        old = AuditLog.objects.create(actor=mover, actor_email=mover.email, category="orders", action="order.ship", target_repr="#A")
        AuditLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.client.post("/api/v1/team/invitations/", {"email": "mover@example.com", "profile": None, "accesses": ["orders.view"]},
                         format="json", **bearer(self.owner))
        invitation = TeamInvitation.objects.get(email="mover@example.com")
        self.assertEqual(self.client.post(f"/api/v1/team/my-invitations/{invitation.id}/accept/", **bearer(mover)).status_code, 200)
        AuditLog.objects.create(actor=mover, actor_email=mover.email, category="orders", action="order.ship", target_repr="#B")
        rows = [row["target_repr"] for row in self.client.get("/api/v1/team/activity/", **bearer(self.owner)).data["results"]
                if row["actor_email"] == "mover@example.com"]
        # Su ingreso a este comercio y lo que hizo después; nada de antes.
        self.assertEqual(rows, ["#B", "Comercio de owner@example.com"])

    def test_an_old_designer_group_still_imports_as_a_merchant(self):
        # "designer" ya no es un rol (migración 0030): un Group viejo con ese
        # nombre cuenta como comerciante, que importa.
        designer = make_owner("designer@example.com", group="designer")
        response = self.client.post("/api/v1/integrations/empretienda/import/preview/", {}, format="multipart", **bearer(designer))
        self.assertNotEqual(response.status_code, 403)


class PermanentDeleteTests(APITestCase):
    def setUp(self):
        self.admin = make_user("admin@example.com", group="admin")
        self.admin.is_staff = True
        self.admin.save()

    def test_permanent_delete_of_a_solo_user_works(self):
        user = make_owner("solo@example.com")
        data_user = teams.data_owner(user)
        response = self.client.delete(f"/api/v1/users/{user.id}/?permanent=true", **bearer(self.admin))
        self.assertEqual(response.status_code, 204, getattr(response, "data", None))
        self.assertFalse(type(user).objects.filter(pk=user.pk).exists())
        # Su comercio sin empleados se va con él, con su usuario de datos.
        self.assertFalse(type(user).objects.filter(pk=data_user.pk).exists())

    def test_permanent_delete_refuses_an_owner_with_employees(self):
        owner = make_user("owner@example.com")
        add_employee(owner, "e@example.com", profile="Todo")
        response = self.client.delete(f"/api/v1/users/{owner.id}/?permanent=true", **bearer(self.admin))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(type(owner).objects.filter(pk=owner.pk).exists())
