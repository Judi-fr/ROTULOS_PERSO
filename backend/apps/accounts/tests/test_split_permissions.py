"""Permisos partidos de otros más gruesos (migración 0029): cada uno se puede
sacar por separado sin perder el permiso del que salió, y quien tenía el viejo
recibió el nuevo."""

from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.models import GroupRolePermission, RolePermission
from apps.accounts.permissions_map import user_has_permission
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order

from .test_team_permissions import add_employee, bearer, make_user
from apps.accounts.tests.team_helpers import new_business

DESIGN = {"destinatario": {"left": 10, "top": 10}}


def take_away(group_name, key):
    """Le saca al rol ``group_name`` el permiso ``key`` (lo que haría el admin en roles.html)."""
    GroupRolePermission.objects.filter(group=Group.objects.get(name=group_name), permission__key=key).delete()


class SeededTests(APITestCase):
    def test_whoever_had_the_old_permission_has_the_new_one(self):
        merchant = make_user("m@example.com")
        new_business(merchant)
        for key in ("orders.tracking_import", "orders.manifest", "labels.template_edit", "labels.template_delete"):
            with self.subTest(key=key):
                self.assertTrue(user_has_permission(merchant, key))
        for key in ("users.delete", "users.export", "audit.export", "reports.view"):
            with self.subTest(key=key):
                self.assertFalse(user_has_permission(merchant, key))

    def test_admin_has_all_of_them(self):
        admin = make_user("a@example.com", group="admin")
        for key in (
            "orders.tracking_import", "orders.manifest", "labels.template_edit", "labels.template_delete",
            "users.delete", "users.export", "audit.export", "reports.view",
        ):
            with self.subTest(key=key):
                self.assertTrue(user_has_permission(admin, key))


class OrderPermissionTests(APITestCase):
    def setUp(self):
        self.merchant = make_user("m@example.com")
        self.merchant_biz = new_business(self.merchant)
        address = Address.objects.create(user=self.merchant_biz, street="Calle", number="1", city="CABA")
        self.order = Order.objects.create(user=self.merchant_biz, address=address)

    def post(self, url, body, **extra):
        return self.client.post(url, body, **extra, **bearer(self.merchant))

    def preview(self):
        upload = SimpleUploadedFile("t.csv", b"pedido;seguimiento\n1;ABC\n", content_type="text/csv")
        return self.post("/api/v1/orders/tracking-import/preview/", {"file": upload}, format="multipart")

    def test_tracking_import_has_its_own_permission(self):
        self.assertNotEqual(self.preview().status_code, 403)
        take_away("subscriber", "orders.tracking_import")
        self.assertEqual(self.preview().status_code, 403)
        confirm = self.post("/api/v1/orders/tracking-import/confirm/", {"status": "dispatched", "rows": []}, format="json")
        self.assertEqual(confirm.status_code, 403)
        # Despachar sigue andando: es otro permiso.
        self.assertTrue(user_has_permission(self.merchant, "orders.dispatch"))

    def test_manifest_has_its_own_permission(self):
        url = "/api/v1/orders/manifest/"
        self.assertNotEqual(self.post(url, {"order_ids": [self.order.id]}, format="json").status_code, 403)
        take_away("subscriber", "orders.manifest")
        self.assertEqual(self.post(url, {"order_ids": [self.order.id]}, format="json").status_code, 403)

    def test_team_access_dispatch_covers_both(self):
        without, _ = add_employee(self.merchant, "e@example.com", accesses=["orders.view"])
        self.assertFalse(user_has_permission(without, "orders.tracking_import"))
        self.assertFalse(user_has_permission(without, "orders.manifest"))
        with_it, _ = add_employee(self.merchant, "d@example.com", accesses=["orders.view", "orders.dispatch"])
        self.assertTrue(user_has_permission(with_it, "orders.tracking_import"))
        self.assertTrue(user_has_permission(with_it, "orders.manifest"))


class DeleteBatchesAccessTests(APITestCase):
    def test_printing_no_longer_includes_deleting_batches(self):
        owner = make_user("o@example.com")
        new_business(owner)
        printer, _ = add_employee(owner, "p@example.com", accesses=["orders.view", "labels.print"])
        self.assertTrue(user_has_permission(printer, "labels.batch"))
        self.assertFalse(user_has_permission(printer, "documents.delete"))
        both, _ = add_employee(owner, "b@example.com", accesses=["orders.view", "labels.print", "documents.delete"])
        self.assertTrue(user_has_permission(both, "documents.delete"))

    def test_new_depot_profile_prints_but_does_not_delete(self):
        owner = make_user("o@example.com")
        new_business(owner)
        self.assertNotIn("documents.delete", teams.team_for(owner).profiles.get(name="Depósito").accesses)
        self.assertIn("documents.delete", teams.team_for(owner).profiles.get(name="Todo").accesses)


class TemplatePermissionTests(APITestCase):
    URL = "/api/v1/labels/templates/"

    def setUp(self):
        self.merchant = make_user("m@example.com")
        self.merchant_biz = new_business(self.merchant)
        self.template = LabelTemplate.objects.create(
            owner=self.merchant_biz, name="Mía", width_cm=10, height_cm=15, design=DESIGN
        )

    def call(self, method, url, body=None):
        return getattr(self.client, method)(url, body, format="json", **bearer(self.merchant))

    def test_edit_and_delete_are_separate_from_create(self):
        detail = f"{self.URL}{self.template.id}/"
        take_away("subscriber", "labels.template_edit")
        take_away("subscriber", "labels.template_delete")
        self.assertEqual(self.call("patch", detail, {"name": "Otra"}).status_code, 403)
        self.assertEqual(self.call("post", f"{detail}versions/1/restore/").status_code, 403)
        self.assertEqual(self.call("delete", detail).status_code, 403)
        # Crear y duplicar siguen con "labels.template_create".
        self.assertEqual(self.call("post", f"{detail}duplicate/").status_code, 201)
        created = self.call("post", self.URL, {"name": "Nueva", "width_cm": "10", "height_cm": "15", "design": DESIGN})
        self.assertEqual(created.status_code, 201, created.data)

    def test_can_edit_and_can_delete_follow_the_permissions(self):
        detail = f"{self.URL}{self.template.id}/"
        data = self.call("get", detail).data
        self.assertTrue(data["can_edit"])
        self.assertTrue(data["can_delete"])
        take_away("subscriber", "labels.template_delete")
        data = self.call("get", detail).data
        self.assertTrue(data["can_edit"])
        self.assertFalse(data["can_delete"])


class AdminPermissionTests(APITestCase):
    """Sobre un rol de personal de la plataforma con lo necesario: el admin
    tiene todo siempre y no se le puede sacar nada (permissions_map)."""

    STAFF_PERMISSIONS = (
        "users.view", "users.deactivate", "users.delete", "users.export",
        "audit.view", "audit.export", "reports.view", "orders.view_all", "support.view_all",
    )

    def setUp(self):
        group = Group.objects.create(name="soporte")
        for permission in RolePermission.objects.filter(key__in=self.STAFF_PERMISSIONS):
            GroupRolePermission.objects.create(group=group, permission=permission)
        self.admin = make_user("staff@example.com", group="soporte")
        self.target = make_user("u@example.com")
        new_business(self.target)

    def get(self, url):
        return self.client.get(url, **bearer(self.admin))

    def test_permanent_delete_needs_users_delete_but_deactivating_does_not(self):
        take_away("soporte", "users.delete")
        url = f"/api/v1/users/{self.target.id}/"
        self.assertEqual(self.client.delete(f"{url}?permanent=true", **bearer(self.admin)).status_code, 403)
        self.assertIn(self.client.delete(url, **bearer(self.admin)).status_code, (200, 204))

    def test_export_users_needs_users_export(self):
        self.assertEqual(self.get("/api/v1/users/export/").status_code, 200)
        take_away("soporte", "users.export")
        self.assertEqual(self.get("/api/v1/users/export/").status_code, 403)
        self.assertEqual(self.get("/api/v1/users/").status_code, 200)

    def test_audit_export_needs_audit_export(self):
        url = "/api/v1/audit/logs/export/?file_type=csv"
        self.assertEqual(self.get(url).status_code, 200)
        take_away("soporte", "audit.export")
        self.assertEqual(self.get(url).status_code, 403)
        self.assertEqual(self.get("/api/v1/audit/logs/").status_code, 200)

    def test_every_report_block_needs_reports_view(self):
        urls = (
            "/api/v1/users/metrics/",
            "/api/v1/orders/metrics/",
            "/api/v1/support-messages/metrics/",
            "/api/v1/audit/metrics/",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.get(url).status_code, 200)
        take_away("soporte", "reports.view")
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.get(url).status_code, 403)

    def test_reports_without_seeing_the_user_list(self):
        group = Group.objects.create(name="analista")
        GroupRolePermission.objects.create(group=group, permission=RolePermission.objects.get(key="reports.view"))
        analyst = make_user("an@example.com", group="analista")
        self.assertEqual(self.client.get("/api/v1/users/metrics/", **bearer(analyst)).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/users/", **bearer(analyst)).status_code, 403)
