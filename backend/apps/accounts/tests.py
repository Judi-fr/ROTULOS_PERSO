"""Tests de autenticación: rotación de refresh, revocación (logout),
throttling de login, normalización de email y validación de contraseña.

Cubren el "Bloque 1" de endurecimiento: no dependen del frontend ni de
cookies (eso es una fase posterior); ejercitan el contrato JSON actual.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()

STRONG_PASSWORD = "Rotulos2026"  # cumple composición + validators de Django


class AuthTestCase(APITestCase):
    """Base: limpia la caché antes de cada test para que el rate limit de
    login (que vive en la caché y se keyea por IP) no se filtre entre tests.
    """

    def setUp(self):
        cache.clear()
        super().setUp()


class RegisterTests(AuthTestCase):
    def test_register_crea_usuario_y_devuelve_tokens(self):
        resp = self.client.post(
            "/api/v1/auth/register/",
            {"email": "Nuevo@Example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)
        # El email se normaliza a minúsculas y se usa como username.
        user = User.objects.get(email="nuevo@example.com")
        self.assertEqual(user.username, "nuevo@example.com")

    def test_register_rechaza_password_comun(self):
        # "password" pasa la composición? no (sin mayúscula/numero) -> probamos
        # una que pase la composición pero sea común/numérica para los validators.
        resp = self.client.post(
            "/api/v1/auth/register/",
            {"email": "a@example.com", "password": "Password1"},
            format="json",
        )
        # "Password1" es una contraseña común -> los validators de Django la rechazan.
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_rechaza_sin_mayuscula_ni_numero(self):
        resp = self.client.post(
            "/api/v1/auth/register/",
            {"email": "b@example.com", "password": "solominusculas"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class LoginTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="login@example.com",
            email="login@example.com",
            password=STRONG_PASSWORD,
        )

    def test_login_normaliza_email(self):
        # Mayúsculas y espacios deben resolver a la misma cuenta.
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "  Login@Example.com ", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)

    def test_login_password_incorrecto_da_401(self):
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "malísima"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_devuelve_is_staff_en_payload(self):
        # El frontend usa user.is_staff del login para decidir si muestra el
        # CRUD de administración (ver isAdminMode en admingestion_test.js).
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["user"]["is_staff"])

    def test_login_admin_devuelve_is_staff_true(self):
        admin = User.objects.create_user(
            username="admin_me@example.com",
            email="admin_me@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "admin_me@example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["user"]["is_staff"])

    def test_login_throttling_bloquea_tras_varios_intentos(self):
        # El rate real de login es 5/min (settings). Tras agotar la cuota, el
        # siguiente intento debe devolver 429 en vez de seguir probando
        # contraseñas. No se usa override_settings porque ScopedRateThrottle
        # captura THROTTLE_RATES al importar la clase y no lo relee.
        codes = []
        for _ in range(7):
            resp = self.client.post(
                "/api/v1/auth/login/",
                {"email": "login@example.com", "password": "mal"},
                format="json",
            )
            codes.append(resp.status_code)
        # Los primeros intentos fallan por credenciales (401); en algún momento
        # se agota la cuota y aparece el 429.
        self.assertIn(status.HTTP_429_TOO_MANY_REQUESTS, codes)
        self.assertEqual(codes[-1], status.HTTP_429_TOO_MANY_REQUESTS)


class LoginLockoutTests(AuthTestCase):
    """Bloqueo de cuenta tras 3 intentos fallidos consecutivos (LoginLockout)."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="lockout@example.com",
            email="lockout@example.com",
            password=STRONG_PASSWORD,
        )

    def _login(self, password):
        return self.client.post(
            "/api/v1/auth/login/",
            {"email": "lockout@example.com", "password": password},
            format="json",
        )

    def test_tercer_intento_fallido_bloquea_la_cuenta(self):
        from .models import LoginLockout

        for _ in range(3):
            resp = self._login("mal")
            self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

        lockout = LoginLockout.objects.get(user=self.user)
        self.assertEqual(lockout.failed_attempts, 3)
        self.assertIsNotNone(lockout.locked_until)
        self.assertTrue(lockout.is_locked())

    def test_login_rechazado_mientras_esta_locked_aunque_la_password_sea_correcta(self):
        for _ in range(3):
            self._login("mal")

        resp = self._login(STRONG_PASSWORD)
        self.assertEqual(resp.status_code, status.HTTP_423_LOCKED)
        self.assertIn("bloqueada", resp.data["detail"].lower())

    def test_login_exitoso_resetea_el_contador(self):
        from .models import LoginLockout

        self._login("mal")
        self._login("mal")
        resp = self._login(STRONG_PASSWORD)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        lockout = LoginLockout.objects.get(user=self.user)
        self.assertEqual(lockout.failed_attempts, 0)
        self.assertIsNone(lockout.locked_until)

    def test_bloqueo_expira_automaticamente_pasada_la_hora(self):
        from django.utils import timezone

        from .models import LoginLockout

        for _ in range(3):
            self._login("mal")

        # Simula que ya pasó la hora de bloqueo (sin cron: se resuelve al
        # comparar contra locked_until en el próximo intento de login).
        lockout = LoginLockout.objects.get(user=self.user)
        lockout.locked_until = timezone.now() - timezone.timedelta(minutes=1)
        lockout.save(update_fields=["locked_until"])

        resp = self._login(STRONG_PASSWORD)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        lockout.refresh_from_db()
        self.assertEqual(lockout.failed_attempts, 0)
        self.assertIsNone(lockout.locked_until)


class RefreshRotationTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="rot@example.com",
            email="rot@example.com",
            password=STRONG_PASSWORD,
        )
        login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "rot@example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.refresh = login.data["refresh"]

    def test_refresh_rota_y_reuso_del_viejo_da_401(self):
        # Primer uso: válido, devuelve access + refresh NUEVO.
        first = self.client.post(
            "/api/v1/auth/refresh/", {"refresh": self.refresh}, format="json"
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertIn("refresh", first.data)
        self.assertNotEqual(first.data["refresh"], self.refresh)

        # Reusar el refresh viejo (ya rotado y blacklisteado) -> 401.
        reuse = self.client.post(
            "/api/v1/auth/refresh/", {"refresh": self.refresh}, format="json"
        )
        self.assertEqual(reuse.status_code, status.HTTP_401_UNAUTHORIZED)


class LogoutTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="out@example.com",
            email="out@example.com",
            password=STRONG_PASSWORD,
        )
        login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "out@example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.access = login.data["access"]
        self.refresh = login.data["refresh"]

    def test_logout_revoca_refresh(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        resp = self.client.post(
            "/api/v1/auth/logout/", {"refresh": self.refresh}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_205_RESET_CONTENT)

        # Tras el logout, el refresh revocado ya no sirve.
        self.client.credentials()  # limpia el header
        reuse = self.client.post(
            "/api/v1/auth/refresh/", {"refresh": self.refresh}, format="json"
        )
        self.assertEqual(reuse.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_requiere_autenticacion(self):
        # Sin header Authorization -> 401 (permiso IsAuthenticated por defecto).
        resp = self.client.post(
            "/api/v1/auth/logout/", {"refresh": self.refresh}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class ProfileTests(AuthTestCase):
    """Perfil del usuario autenticado (/api/v1/auth/me/)."""

    URL = "/api/v1/auth/me/"

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="me@example.com",
            email="me@example.com",
            password=STRONG_PASSWORD,
            first_name="Ana",
            last_name="Pérez",
        )

    def test_anonimo_recibe_401(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_devuelve_perfil_propio(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["email"], "me@example.com")
        self.assertEqual(resp.data["first_name"], "Ana")
        # Cuenta con login manual -> tiene contraseña usable.
        self.assertTrue(resp.data["has_usable_password"])

    def test_patch_edita_nombre_y_apellido(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.patch(
            self.URL,
            {"first_name": "Editado", "last_name": "Nuevo"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Editado")
        self.assertEqual(self.user.last_name, "Nuevo")

    def test_patch_no_cambia_email(self):
        # El email es read-only: mandarlo no lo modifica (queda la identidad).
        self.client.force_authenticate(user=self.user)
        resp = self.client.patch(
            self.URL,
            {"email": "otro@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "me@example.com")

    def test_patch_no_permite_auto_promocion_a_staff(self):
        # is_staff es read-only: un usuario no puede promoverse a admin solo.
        self.client.force_authenticate(user=self.user)
        resp = self.client.patch(self.URL, {"is_staff": True}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)

    def test_put_no_permitido(self):
        # Solo se exponen GET y PATCH (ver http_method_names).
        self.client.force_authenticate(user=self.user)
        resp = self.client.put(self.URL, {"first_name": "X"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_has_usable_password_false_para_cuenta_google(self):
        # Cuenta creada vía Google: sin contraseña propia.
        google_user = User.objects.create_user(
            username="google@example.com", email="google@example.com"
        )
        google_user.set_unusable_password()
        google_user.save(update_fields=["password"])

        self.client.force_authenticate(user=google_user)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["has_usable_password"])

    def test_patch_no_cambia_role_ni_is_staff(self):
        # "role" es un SerializerMethodField (solo lectura) e "is_staff" está
        # en read_only_fields: mandarlos en el PATCH no debe alterarlos, aunque
        # el usuario los mande a mano.
        self.client.force_authenticate(user=self.user)
        resp = self.client.patch(
            self.URL,
            {"role": "admin", "is_staff": True, "first_name": "Sigue"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.groups.filter(name="admin").exists())
        self.assertEqual(resp.data["role"], "subscriber")
        self.assertEqual(self.user.first_name, "Sigue")

    def test_patch_con_id_ajeno_modifica_al_usuario_del_token(self):
        # La identidad sale siempre de request.user: un id ajeno en el body
        # no debe redirigir la edición hacia otra cuenta.
        otro = User.objects.create_user(
            username="otro@example.com",
            email="otro@example.com",
            password=STRONG_PASSWORD,
            first_name="Intacto",
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.patch(
            self.URL,
            {"id": otro.id, "first_name": "Cambiado"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        otro.refresh_from_db()
        self.assertEqual(self.user.first_name, "Cambiado")
        self.assertEqual(self.user.id, resp.data["id"])
        # La otra cuenta no se tocó.
        self.assertEqual(otro.first_name, "Intacto")

    def test_patch_anonimo_recibe_401(self):
        resp = self.client.patch(self.URL, {"first_name": "X"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class ChangePasswordTests(AuthTestCase):
    """Cambio de contraseña del usuario autenticado (/api/v1/auth/me/change-password/)."""

    URL = "/api/v1/auth/me/change-password/"
    NEW_PASSWORD = "NuevaClave2026"

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="cambio@example.com",
            email="cambio@example.com",
            password=STRONG_PASSWORD,
        )

    def _payload(self, **overrides):
        payload = {
            "current_password": STRONG_PASSWORD,
            "new_password": self.NEW_PASSWORD,
            "confirm_password": self.NEW_PASSWORD,
        }
        payload.update(overrides)
        return payload

    def test_change_password_unauthenticated(self):
        # Sin JWT -> 401 (permiso IsAuthenticated por defecto).
        resp = self.client.post(self.URL, self._payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_change_password_wrong_current(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.URL,
            self._payload(current_password="Incorrecta1"),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("current_password", resp.data)

    def test_change_password_mismatch(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.URL,
            self._payload(confirm_password="OtraClave2026"),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("confirm_password", resp.data)

    def test_change_password_success(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(self.URL, self._payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["detail"], "Contraseña actualizada correctamente.")

        # La contraseña vieja ya no funciona; la nueva sí.
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.NEW_PASSWORD))
        self.assertFalse(self.user.check_password(STRONG_PASSWORD))

        # Puede loguearse con la nueva contraseña.
        login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "cambio@example.com", "password": self.NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertIn("access", login.data)

    def test_change_password_rechaza_debil(self):
        # La nueva contraseña debe pasar la composición del proyecto
        # (mínimo 6, mayúscula y número) y los validators de Django.
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.URL,
            self._payload(new_password="solominusculas", confirm_password="solominusculas"),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password", resp.data)


class PermissionsTests(AuthTestCase):
    def test_health_es_publico(self):
        resp = self.client.get("/api/v1/health/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class RolesTests(AuthTestCase):
    """Los roles canónicos existen y los legacy fueron eliminados."""

    def test_los_cuatro_roles_canonicos_existen(self):
        from django.contrib.auth.models import Group

        nombres = set(Group.objects.values_list("name", flat=True))
        self.assertTrue(
            {"admin", "designer", "operator", "subscriber"}.issubset(nombres)
        )

    def test_los_groups_legacy_fueron_eliminados(self):
        from django.contrib.auth.models import Group

        nombres = set(Group.objects.values_list("name", flat=True))
        self.assertFalse(
            {"administradores", "diseñadores", "operadores"}.intersection(nombres)
        )


class RolePermissionSystemTests(AuthTestCase):
    """Sistema de permisos por roles (Groups + RolePermission + GroupRolePermission).

    Cubre el esquema sembrado por la migración 0005_seed_role_permissions y
    los endpoints bajo /api/v1/auth/roles/ y /api/v1/auth/permissions/.
    """

    ADMIN = "admin"
    DESIGNER = "designer"
    OPERATOR = "operator"
    SUBSCRIBER = "subscriber"

    ALL_PERMISSIONS = {
        "users.view",
        "users.create",
        "users.edit",
        "users.deactivate",
        "users.reactivate",
        "users.unlock",
        "users.me.view",
        "users.me.edit",
        "users.me.change_password",
        "addresses.manage",
        "orders.view",
        "orders.create",
        "orders.cancel",
        "orders.create_manual",
        "orders.import",
        "orders.import_mappings",
        "support.create",
        "labels.view",
        "labels.create",
        "labels.edit",
        "labels.delete",
        "labels.render",
        "labels.batch",
        "labels.template_create",
        "documents.view",
        "documents.delete",
        # Plantillas por elementos / catálogo / archivos fuente / importación
        # (self-service; integradas desde backend_echu).
        "plantillas.view",
        "plantillas.edit",
        "variables.view",
        "documents.upload",
        "processing.import",
        # Exclusivos del admin (ver 0014_seed_admin_only_permissions,
        # 0015_seed_label_permissions, 0017_seed_document_permissions,
        # 0020_seed_order_ingestion_permissions y
        # 0021_seed_integrations_manage_permission).
        "audit.view",
        "orders.view_all",
        "support.view_all",
        "support.manage",
        "labels.view_all",
        "labels.manage_templates",
        "variables.manage",
        "documents.view_all",
        "orders.create_for_others",
        "integrations.manage",
    }

    # Self-service: perfil propio + direcciones/pedidos propios + soporte +
    # rótulos propios. Es lo que tienen designer/subscriber (ver
    # 0007_seed_order_permissions, 0012_seed_support_permission y
    # 0015_seed_label_permissions). operator tiene esto MÁS la carga
    # operativa de pedidos (ver OPERATOR_PERMISSIONS, 0020).
    ME_VIEW_PERMISSIONS = {
        "users.me.view",
        "users.me.edit",
        "users.me.change_password",
        "addresses.manage",
        "orders.view",
        "orders.create",
        "orders.cancel",
        "support.create",
        "labels.view",
        "labels.create",
        "labels.edit",
        "labels.delete",
        "labels.render",
        "labels.batch",
        "labels.template_create",
        "plantillas.view",
        "plantillas.edit",
        "variables.view",
        "documents.upload",
        "processing.import",
        "documents.view",
        "documents.delete",
    }

    # operator: ME_VIEW_PERMISSIONS + carga operativa de pedidos (alta
    # manual, importación, plantillas de mapeo) — ver 0020.
    OPERATOR_PERMISSIONS = ME_VIEW_PERMISSIONS | {
        "orders.create_manual",
        "orders.import",
        "orders.import_mappings",
    }

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group

        from .models import GroupRolePermission, RolePermission

        self.Group = Group
        self.GroupRolePermission = GroupRolePermission
        self.RolePermission = RolePermission

        # Los Groups los siembra la migración; forzamos su existencia por si
        # los tests corren con una base ya migrada.
        for name in (self.ADMIN, self.DESIGNER, self.OPERATOR, self.SUBSCRIBER):
            Group.objects.get_or_create(name=name)

        # Usuarios de prueba.
        self.admin_user = User.objects.create_user(
            username="perm_admin@example.com",
            email="perm_admin@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.designer_user = User.objects.create_user(
            username="perm_designer@example.com",
            email="perm_designer@example.com",
            password=STRONG_PASSWORD,
        )
        self.operator_user = User.objects.create_user(
            username="perm_operator@example.com",
            email="perm_operator@example.com",
            password=STRONG_PASSWORD,
        )
        self.subscriber_user = User.objects.create_user(
            username="perm_subscriber@example.com",
            email="perm_subscriber@example.com",
            password=STRONG_PASSWORD,
        )

        # Asignar los Groups (roles) a cada usuario.
        self.admin_user.groups.add(Group.objects.get(name=self.ADMIN))
        self.designer_user.groups.add(Group.objects.get(name=self.DESIGNER))
        self.operator_user.groups.add(Group.objects.get(name=self.OPERATOR))
        self.subscriber_user.groups.add(Group.objects.get(name=self.SUBSCRIBER))

    # --- Datos sembrados -----------------------------------------------------

    def test_los_cuatro_roles_existen(self):
        names = set(self.Group.objects.values_list("name", flat=True))
        self.assertTrue(
            {self.ADMIN, self.DESIGNER, self.OPERATOR, self.SUBSCRIBER}.issubset(names)
        )

    def test_los_ocho_permisos_existen(self):
        keys = set(self.RolePermission.objects.values_list("key", flat=True))
        self.assertEqual(keys, self.ALL_PERMISSIONS)

    def test_asignacion_inicial_correcta(self):
        admin_perm = set(
            self.GroupRolePermission.objects.filter(
                group__name=self.ADMIN
            ).values_list("permission__key", flat=True)
        )
        designer_perm = set(
            self.GroupRolePermission.objects.filter(
                group__name=self.DESIGNER
            ).values_list("permission__key", flat=True)
        )
        operator_perm = set(
            self.GroupRolePermission.objects.filter(
                group__name=self.OPERATOR
            ).values_list("permission__key", flat=True)
        )
        subscriber_perm = set(
            self.GroupRolePermission.objects.filter(
                group__name=self.SUBSCRIBER
            ).values_list("permission__key", flat=True)
        )

        self.assertEqual(admin_perm, self.ALL_PERMISSIONS)
        self.assertEqual(designer_perm, self.ME_VIEW_PERMISSIONS)
        self.assertEqual(operator_perm, self.OPERATOR_PERMISSIONS)
        self.assertEqual(subscriber_perm, self.ME_VIEW_PERMISSIONS)

    def test_re_ejecutar_migracion_no_duplica(self):
        # Simula re-ejecutar la data migration: llamar get_or_create de nuevo
        # no debe duplicar permisos ni asignaciones.
        from .models import RolePermission as RP

        for perm in RP.objects.all():
            RP.objects.get_or_create(
                key=perm.key,
                defaults={"name": perm.name, "category": perm.category},
            )
        for link in self.GroupRolePermission.objects.all():
            self.GroupRolePermission.objects.get_or_create(
                group=link.group, permission=link.permission
            )

        # El catálogo completo (RolePermission) es exactamente ALL_PERMISSIONS.
        self.assertEqual(self.RolePermission.objects.count(), len(self.ALL_PERMISSIONS))
        self.assertEqual(
            self.GroupRolePermission.objects.count(),
            # admin tiene el catálogo completo (ALL_PERMISSIONS); designer y
            # subscriber tienen ME_VIEW_PERMISSIONS cada uno; operator tiene
            # OPERATOR_PERMISSIONS (ME_VIEW_PERMISSIONS + carga operativa).
            len(self.ALL_PERMISSIONS)
            + 2 * len(self.ME_VIEW_PERMISSIONS)
            + len(self.OPERATOR_PERMISSIONS),
        )

    # --- Endpoints autenticados (admin) --------------------------------------

    def test_get_roles_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.get("/api/v1/auth/roles/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [r["name"] for r in resp.data]
        # Los cuatro básicos aparecen primero y en orden canónico; pueden
        # existir roles personalizados creados por otros tests después.
        self.assertEqual(
            names[:4],
            [self.ADMIN, self.DESIGNER, self.OPERATOR, self.SUBSCRIBER],
        )
        self.assertGreaterEqual(len(names), 4)

    def test_get_permissions_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.get("/api/v1/auth/permissions/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # El catálogo completo del endpoint es exactamente ALL_PERMISSIONS.
        self.assertEqual(len(resp.data), len(self.ALL_PERMISSIONS))
        keys = {p["key"] for p in resp.data}
        self.assertEqual(keys, self.ALL_PERMISSIONS)

    def test_get_role_permissions_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        admin_group = self.Group.objects.get(name=self.ADMIN)
        resp = self.client.get(f"/api/v1/auth/roles/{admin_group.id}/permissions/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["role"]["name"], self.ADMIN)
        self.assertEqual(
            {p["key"] for p in resp.data["permissions"]},
            self.ALL_PERMISSIONS,
        )

    def test_put_role_permissions_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        designer_group = self.Group.objects.get(name=self.DESIGNER)
        resp = self.client.put(
            f"/api/v1/auth/roles/{designer_group.id}/permissions/",
            {"permissions": ["users.view", "users.me.view"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {p["key"] for p in resp.data["permissions"]},
            {"users.view", "users.me.view"},
        )

    def test_put_permiso_inexistente_devuelve_400_y_no_modifica(self):
        self.client.force_authenticate(user=self.admin_user)
        designer_group = self.Group.objects.get(name=self.DESIGNER)
        resp = self.client.put(
            f"/api/v1/auth/roles/{designer_group.id}/permissions/",
            {"permissions": ["users.view", "no.existe"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        # La asignación original no se tocó.
        current = set(
            self.GroupRolePermission.objects.filter(
                group=designer_group
            ).values_list("permission__key", flat=True)
        )
        self.assertEqual(current, self.ME_VIEW_PERMISSIONS)

    def test_put_rol_inexistente_devuelve_404(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.put(
            "/api/v1/auth/roles/999999/permissions/",
            {"permissions": ["users.view"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_rol_admin_vacio_rechazado(self):
        """Protección: el rol admin no puede quedar sin permisos."""
        self.client.force_authenticate(user=self.admin_user)
        admin_group = self.Group.objects.get(name=self.ADMIN)
        resp = self.client.put(
            f"/api/v1/auth/roles/{admin_group.id}/permissions/",
            {"permissions": []},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        # Sigue con los permisos originales.
        current = set(
            self.GroupRolePermission.objects.filter(
                group=admin_group
            ).values_list("permission__key", flat=True)
        )
        self.assertEqual(current, self.ALL_PERMISSIONS)

    # --- No admins no pueden tocar -------------------------------------------

    def test_designer_no_puede_modificar_permisos(self):
        self.client.force_authenticate(user=self.designer_user)
        admin_group = self.Group.objects.get(name=self.ADMIN)
        resp = self.client.put(
            f"/api/v1/auth/roles/{admin_group.id}/permissions/",
            {"permissions": ["users.view"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_operator_no_puede_modificar_permisos(self):
        self.client.force_authenticate(user=self.operator_user)
        admin_group = self.Group.objects.get(name=self.ADMIN)
        resp = self.client.put(
            f"/api/v1/auth/roles/{admin_group.id}/permissions/",
            {"permissions": ["users.view"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_subscriber_no_puede_modificar_permisos(self):
        self.client.force_authenticate(user=self.subscriber_user)
        admin_group = self.Group.objects.get(name=self.ADMIN)
        resp = self.client.put(
            f"/api/v1/auth/roles/{admin_group.id}/permissions/",
            {"permissions": ["users.view"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_designer_no_puede_ver_catalogo(self):
        self.client.force_authenticate(user=self.designer_user)
        resp = self.client.get("/api/v1/auth/roles/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonimo_recibe_401(self):
        resp = self.client.get("/api/v1/auth/roles/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        resp = self.client.get("/api/v1/auth/permissions/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- Los permisos asignados gobiernan los endpoints --------------------

    def _remove_permission(self, role_name, permission_key):
        group = self.Group.objects.get(name=role_name)
        self.GroupRolePermission.objects.filter(
            group=group, permission__key=permission_key
        ).delete()

    def _restore_permission(self, role_name, permission_key):
        self.GroupRolePermission.objects.get_or_create(
            group=self.Group.objects.get(name=role_name),
            permission=self.RolePermission.objects.get(key=permission_key),
        )

    def _grant_permission(self, role_name, permission_key):
        self._restore_permission(role_name, permission_key)

    def test_designer_users_view_otorgar_quitar_y_restaurar(self):
        from .permissions_map import get_effective_role, user_has_permission

        self.client.force_authenticate(user=self.designer_user)
        self.assertEqual(get_effective_role(self.designer_user), self.DESIGNER)
        self.assertFalse(user_has_permission(self.designer_user, "users.view"))
        self.assertEqual(self.client.get("/api/v1/users/").status_code, status.HTTP_403_FORBIDDEN)

        self._grant_permission(self.DESIGNER, "users.view")
        self.assertTrue(user_has_permission(self.designer_user, "users.view"))
        self.assertEqual(self.client.get("/api/v1/users/").status_code, status.HTTP_200_OK)

        self._remove_permission(self.DESIGNER, "users.view")
        self.assertFalse(user_has_permission(self.designer_user, "users.view"))
        self.assertEqual(self.client.get("/api/v1/users/").status_code, status.HTTP_403_FORBIDDEN)

        self._grant_permission(self.DESIGNER, "users.view")
        self.assertTrue(user_has_permission(self.designer_user, "users.view"))
        self.assertEqual(self.client.get("/api/v1/users/").status_code, status.HTTP_200_OK)

    def test_designer_users_create_otorgar_quitar_y_restaurar(self):
        self.client.force_authenticate(user=self.designer_user)
        payload = {"email": "designer-create-1@example.com", "password": STRONG_PASSWORD}
        self.assertEqual(self.client.post("/api/v1/users/", payload, format="json").status_code, status.HTTP_403_FORBIDDEN)

        self._grant_permission(self.DESIGNER, "users.create")
        self.assertEqual(self.client.post("/api/v1/users/", payload, format="json").status_code, status.HTTP_201_CREATED)

        self._remove_permission(self.DESIGNER, "users.create")
        denied = {"email": "designer-create-2@example.com", "password": STRONG_PASSWORD}
        self.assertEqual(self.client.post("/api/v1/users/", denied, format="json").status_code, status.HTTP_403_FORBIDDEN)

        self._grant_permission(self.DESIGNER, "users.create")
        restored = {"email": "designer-create-3@example.com", "password": STRONG_PASSWORD}
        self.assertEqual(self.client.post("/api/v1/users/", restored, format="json").status_code, status.HTTP_201_CREATED)

    def test_designer_users_edit_otorgar_quitar_y_restaurar(self):
        self.client.force_authenticate(user=self.designer_user)
        url = f"/api/v1/users/{self.operator_user.id}/"
        self.assertEqual(self.client.patch(url, {"first_name": "No"}, format="json").status_code, status.HTTP_403_FORBIDDEN)

        self._grant_permission(self.DESIGNER, "users.edit")
        self.assertEqual(self.client.patch(url, {"first_name": "Sí"}, format="json").status_code, status.HTTP_200_OK)

        self._remove_permission(self.DESIGNER, "users.edit")
        self.assertEqual(self.client.patch(url, {"last_name": "No"}, format="json").status_code, status.HTTP_403_FORBIDDEN)

        self._grant_permission(self.DESIGNER, "users.edit")
        self.assertEqual(self.client.patch(url, {"last_name": "Sí"}, format="json").status_code, status.HTTP_200_OK)

    def test_designer_me_view_otorgar_quitar_y_restaurar(self):
        self.client.force_authenticate(user=self.designer_user)
        self._remove_permission(self.DESIGNER, "users.me.view")
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, status.HTTP_403_FORBIDDEN)
        self._grant_permission(self.DESIGNER, "users.me.view")
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, status.HTTP_200_OK)

    def test_designer_me_edit_otorgar_quitar_y_restaurar(self):
        self.client.force_authenticate(user=self.designer_user)
        self._remove_permission(self.DESIGNER, "users.me.edit")
        self.assertEqual(self.client.patch("/api/v1/auth/me/", {"first_name": "No"}, format="json").status_code, status.HTTP_403_FORBIDDEN)
        self._grant_permission(self.DESIGNER, "users.me.edit")
        self.assertEqual(self.client.patch("/api/v1/auth/me/", {"first_name": "Sí"}, format="json").status_code, status.HTTP_200_OK)

    def test_designer_me_change_password_otorgar_quitar_y_restaurar(self):
        self.client.force_authenticate(user=self.designer_user)
        payload = {
            "current_password": STRONG_PASSWORD,
            "new_password": "NuevaClave2026",
            "confirm_password": "NuevaClave2026",
        }
        self._remove_permission(self.DESIGNER, "users.me.change_password")
        self.assertEqual(self.client.post("/api/v1/auth/me/change-password/", payload, format="json").status_code, status.HTTP_403_FORBIDDEN)
        self._grant_permission(self.DESIGNER, "users.me.change_password")
        self.assertEqual(self.client.post("/api/v1/auth/me/change-password/", payload, format="json").status_code, status.HTTP_200_OK)


    def test_quitar_users_view_impide_listar_y_reotorgar_restaura(self):
        self.client.force_authenticate(user=self.admin_user)
        self._remove_permission(self.ADMIN, "users.view")
        self.assertEqual(
            self.client.get("/api/v1/users/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self._restore_permission(self.ADMIN, "users.view")
        self.assertEqual(
            self.client.get("/api/v1/users/").status_code,
            status.HTTP_200_OK,
        )

    def test_quitar_users_create_impide_crear_y_reotorgar_restaura(self):
        self.client.force_authenticate(user=self.admin_user)
        self._remove_permission(self.ADMIN, "users.create")
        payload = {
            "email": "permission-create@example.com",
            "password": STRONG_PASSWORD,
        }
        self.assertEqual(
            self.client.post("/api/v1/users/", payload, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self._restore_permission(self.ADMIN, "users.create")
        self.assertEqual(
            self.client.post("/api/v1/users/", payload, format="json").status_code,
            status.HTTP_201_CREATED,
        )

    def test_quitar_users_edit_impide_patch_y_reotorgar_restaura(self):
        self.client.force_authenticate(user=self.admin_user)
        self._remove_permission(self.ADMIN, "users.edit")
        url = f"/api/v1/users/{self.designer_user.id}/"
        self.assertEqual(
            self.client.patch(url, {"first_name": "Sin permiso"}, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self._restore_permission(self.ADMIN, "users.edit")
        self.assertEqual(
            self.client.patch(url, {"first_name": "Con permiso"}, format="json").status_code,
            status.HTTP_200_OK,
        )

    def test_quitar_users_me_view_impide_perfil_y_reotorgar_restaura(self):
        self.client.force_authenticate(user=self.designer_user)
        self._remove_permission(self.DESIGNER, "users.me.view")
        self.assertEqual(
            self.client.get("/api/v1/auth/me/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self._restore_permission(self.DESIGNER, "users.me.view")
        self.assertEqual(
            self.client.get("/api/v1/auth/me/").status_code,
            status.HTTP_200_OK,
        )

    def test_quitar_users_me_edit_impide_patch_y_reotorgar_restaura(self):
        self.client.force_authenticate(user=self.designer_user)
        self._remove_permission(self.DESIGNER, "users.me.edit")
        payload = {"first_name": "Sin permiso"}
        self.assertEqual(
            self.client.patch("/api/v1/auth/me/", payload, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self._restore_permission(self.DESIGNER, "users.me.edit")
        self.assertEqual(
            self.client.patch("/api/v1/auth/me/", payload, format="json").status_code,
            status.HTTP_200_OK,
        )

    def test_quitar_cambio_password_impide_y_reotorgar_restaura(self):
        self.client.force_authenticate(user=self.designer_user)
        self._remove_permission(self.DESIGNER, "users.me.change_password")
        payload = {
            "current_password": STRONG_PASSWORD,
            "new_password": "OtraClave2026",
            "confirm_password": "OtraClave2026",
        }
        self.assertEqual(
            self.client.post("/api/v1/auth/me/change-password/", payload, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self._restore_permission(self.DESIGNER, "users.me.change_password")
        self.assertEqual(
            self.client.post("/api/v1/auth/me/change-password/", payload, format="json").status_code,
            status.HTTP_200_OK,
        )

    # --- user_has_permission consulta la base --------------------------------

    def test_user_has_permission_desde_db(self):
        from .permissions_map import user_has_permission

        # El admin tiene todos los permisos en la DB.
        self.assertTrue(user_has_permission(self.admin_user, "users.view"))
        self.assertTrue(user_has_permission(self.admin_user, "users.me.edit"))

        # Designer/operator/subscriber: solo perfil propio.
        for u in (self.designer_user, self.operator_user, self.subscriber_user):
            self.assertFalse(user_has_permission(u, "users.view"))
            self.assertTrue(user_has_permission(u, "users.me.view"))

        # Permiso desconocido -> False.
        self.assertFalse(user_has_permission(self.admin_user, "no.existe"))


class CustomRolePermissionTests(AuthTestCase):
    """Roles PERSONALIZADOS (Groups fuera de admin/designer/operator/subscriber).

    Regresión del bug donde ``permissions_map.normalize_role`` colapsaba
    cualquier Group no reconocido a ``DEFAULT_ROLE`` ("subscriber"): un rol
    personalizado podía autenticarse (el login/rol no depende de esta
    función) pero ``user_has_permission``/``get_effective_role`` consultaban
    ``GroupRolePermission`` para el Group "subscriber" en lugar del Group
    real, así que sus permisos asignados nunca se aplicaban.

    Estos tests crean el rol vía el endpoint público (POST /roles/), igual
    que lo haría el frontend, para cubrir el flujo de punta a punta.
    """

    USERS_URL = "/api/v1/users/"
    ME_URL = "/api/v1/auth/me/"
    CHANGE_PASSWORD_URL = "/api/v1/auth/me/change-password/"
    ROLES_URL = "/api/v1/auth/roles/"

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group

        from .models import GroupRolePermission, RolePermission

        self.Group = Group
        self.GroupRolePermission = GroupRolePermission
        self.RolePermission = RolePermission

        for name in ("admin", "designer", "operator", "subscriber"):
            Group.objects.get_or_create(name=name)

        self.admin_user = User.objects.create_user(
            username="custom_admin@example.com",
            email="custom_admin@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.admin_user.groups.add(Group.objects.get(name="admin"))

    def _create_custom_role(self, name, permission_keys):
        """Crea el rol vía API (como el frontend) y le asigna permisos."""
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.post(self.ROLES_URL, {"name": name}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        role_id = resp.data["id"]
        resp = self.client.put(
            f"{self.ROLES_URL}{role_id}/permissions/",
            {"permissions": list(permission_keys)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.client.force_authenticate(user=None)
        return self.Group.objects.get(pk=role_id)

    def _create_user_with_group(self, email, group):
        user = User.objects.create_user(
            username=email, email=email, password=STRONG_PASSWORD
        )
        user.groups.add(group)
        return user

    # --- Caso A: solo users.view ---------------------------------------------

    def test_caso_a_rol_personalizado_solo_users_view(self):
        group = self._create_custom_role("supervisor_de_usuarios", ["users.view"])
        user = self._create_user_with_group("caso_a@example.com", group)

        from .permissions_map import get_effective_role, user_has_permission

        # El rol efectivo es el Group personalizado, no "subscriber".
        self.assertEqual(get_effective_role(user), "supervisor_de_usuarios")
        self.assertTrue(user_has_permission(user, "users.view"))

        # Login OK.
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "caso_a@example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["user"]["role"], "supervisor_de_usuarios")
        self.assertIn("users.view", resp.data["user"]["permissions"])
        self.assertNotIn("users.create", resp.data["user"]["permissions"])

        self.client.force_authenticate(user=user)
        self.assertEqual(self.client.get(self.USERS_URL).status_code, status.HTTP_200_OK)

        other = User.objects.create_user(
            username="target_a@example.com", email="target_a@example.com", password=STRONG_PASSWORD
        )
        self.assertEqual(
            self.client.patch(f"{self.USERS_URL}{other.id}/", {"first_name": "X"}, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                self.USERS_URL,
                {"email": "nuevo_a@example.com", "full_name": "N", "role": "subscriber", "status": "active", "password": STRONG_PASSWORD},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(self.client.delete(f"{self.USERS_URL}{other.id}/").status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.patch(f"{self.USERS_URL}{other.id}/", {"status": "active"}, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )

    # --- Caso B: users.view + users.edit --------------------------------------

    def test_caso_b_rol_personalizado_view_y_edit(self):
        group = self._create_custom_role("diseñador_avanzado", ["users.view", "users.edit"])
        user = self._create_user_with_group("caso_b@example.com", group)
        other = User.objects.create_user(
            username="target_b@example.com", email="target_b@example.com", password=STRONG_PASSWORD
        )

        self.client.force_authenticate(user=user)
        self.assertEqual(self.client.get(self.USERS_URL).status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.patch(f"{self.USERS_URL}{other.id}/", {"first_name": "Editado"}, format="json").status_code,
            status.HTTP_200_OK,
        )
        # Sin users.deactivate/reactivate/create: siguen prohibidas.
        self.assertEqual(
            self.client.patch(f"{self.USERS_URL}{other.id}/", {"status": "inactive"}, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(self.client.delete(f"{self.USERS_URL}{other.id}/").status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.post(
                self.USERS_URL,
                {"email": "nuevo_b@example.com", "full_name": "N", "role": "subscriber", "status": "active", "password": STRONG_PASSWORD},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    # --- Caso C: rol sin permisos administrativos -----------------------------

    def test_caso_c_rol_personalizado_sin_permisos_administrativos(self):
        group = self._create_custom_role("solo_perfil", ["users.me.view", "users.me.edit"])
        user = self._create_user_with_group("caso_c@example.com", group)

        self.client.force_authenticate(user=user)
        self.assertEqual(self.client.get(self.USERS_URL).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.post(
                self.USERS_URL,
                {"email": "nuevo_c@example.com", "full_name": "N", "role": "subscriber", "status": "active", "password": STRONG_PASSWORD},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        # Su propio perfil sigue accesible.
        self.assertEqual(self.client.get(self.ME_URL).status_code, status.HTTP_200_OK)

    # --- Caso D: cambiar permisos en DB cambia el comportamiento sin deploy --

    def test_caso_d_cambiar_permisos_en_db_cambia_comportamiento_en_caliente(self):
        group = self._create_custom_role("rol_dinamico", ["users.view"])
        user = self._create_user_with_group("caso_d@example.com", group)

        self.client.force_authenticate(user=user)
        self.assertEqual(self.client.get(self.USERS_URL).status_code, status.HTTP_200_OK)

        create_perm = self.RolePermission.objects.get(key="users.create")
        self.GroupRolePermission.objects.create(group=group, permission=create_perm)

        resp = self.client.post(
            self.USERS_URL,
            {"email": "nuevo_d@example.com", "full_name": "N", "role": "subscriber", "status": "active", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        self.GroupRolePermission.objects.filter(group=group, permission__key="users.view").delete()
        self.assertEqual(self.client.get(self.USERS_URL).status_code, status.HTTP_403_FORBIDDEN)

    # --- Caso E: pertenecer a un Group no lo vuelve admin ---------------------

    def test_caso_e_rol_personalizado_no_es_tratado_como_admin(self):
        group = self._create_custom_role("rol_no_admin", ["users.view", "users.edit"])
        user = self._create_user_with_group("caso_e@example.com", group)

        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

        self.client.force_authenticate(user=user)
        # No tiene users.deactivate/create -> prohibido pese a "tener un rol".
        other = User.objects.create_user(
            username="target_e@example.com", email="target_e@example.com", password=STRONG_PASSWORD
        )
        self.assertEqual(self.client.delete(f"{self.USERS_URL}{other.id}/").status_code, status.HTTP_403_FORBIDDEN)
        # Los endpoints de administración de roles siguen exigiendo IsAdminUser.
        self.assertEqual(self.client.get(self.ROLES_URL).status_code, status.HTTP_403_FORBIDDEN)

    # --- Caso F: permisos users.me.* independientes de los administrativos ---

    def test_caso_f_permisos_me_independientes_de_permisos_administrativos(self):
        group = self._create_custom_role(
            "rol_admin_sin_perfil",
            ["users.view", "users.create", "users.edit", "users.deactivate", "users.reactivate"],
        )
        user = self._create_user_with_group("caso_f@example.com", group)

        self.client.force_authenticate(user=user)
        # Tiene permisos administrativos completos, pero NADA de users.me.*.
        self.assertEqual(self.client.get(self.ME_URL).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.patch(self.ME_URL, {"first_name": "X"}, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                self.CHANGE_PASSWORD_URL,
                {"current_password": STRONG_PASSWORD, "new_password": "OtraClave2026", "confirm_password": "OtraClave2026"},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Al agregar solo users.me.view, únicamente esa acción se habilita.
        me_view_perm = self.RolePermission.objects.get(key="users.me.view")
        self.GroupRolePermission.objects.create(group=group, permission=me_view_perm)
        self.assertEqual(self.client.get(self.ME_URL).status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.patch(self.ME_URL, {"first_name": "X"}, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )


class UserAdminCrudTests(AuthTestCase):
    """CRUD de usuarios reservado a administradores (/api/v1/users/)."""

    URL = "/api/v1/users/"

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            username="admin@example.com",
            email="admin@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.normal = User.objects.create_user(
            username="user@example.com",
            email="user@example.com",
            password=STRONG_PASSWORD,
        )

    # --- Permisos -----------------------------------------------------------

    def test_anonimo_recibe_401(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_admin_recibe_403(self):
        self.client.force_authenticate(user=self.normal)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # --- CRUD feliz ---------------------------------------------------------

    def test_admin_lista_usuarios_paginado(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # UserAdminPagination -> {results, pagination, summary}
        self.assertIn("results", resp.data)
        self.assertIn("pagination", resp.data)
        self.assertEqual(resp.data["pagination"]["count"], 2)

    def test_admin_lista_incluye_summary(self):
        # El frontend pinta las tarjetas Total/Active/Inactive desde summary.
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        summary = resp.data["summary"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["active"], 2)
        self.assertEqual(summary["inactive"], 0)

    def test_admin_lista_incluye_campos_calculados(self):
        # El frontend usa display_name/role_key/status_key/can_reactivate.
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        user = next(u for u in resp.data["results"] if u["id"] == self.normal.id)
        self.assertEqual(user["display_name"], self.normal.email)
        self.assertEqual(user["role_key"], "subscriber")
        self.assertEqual(user["status_key"], "active")
        self.assertFalse(user["can_reactivate"])

    def test_admin_crea_usuario(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"email": "Nuevo@Example.com", "password": STRONG_PASSWORD,
             "first_name": "Ana"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # El email se normaliza y se usa como username.
        creado = User.objects.get(email="nuevo@example.com")
        self.assertEqual(creado.username, "nuevo@example.com")
        self.assertEqual(creado.first_name, "Ana")
        # La contraseña nunca se devuelve en la respuesta.
        self.assertNotIn("password", resp.data)

    def test_admin_crea_usuario_con_payload_del_panel(self):
        # El panel manda full_name/role/status en vez de first_name/groups/is_active.
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"email": "panel@example.com", "password": STRONG_PASSWORD,
             "full_name": "Panel User", "role": "designer", "status": "active"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        creado = User.objects.get(email="panel@example.com")
        self.assertEqual(creado.first_name, "Panel")
        self.assertEqual(creado.last_name, "User")
        self.assertTrue(creado.is_active)
        self.assertEqual(
            set(creado.groups.values_list("name", flat=True)),
            {"designer"},
        )

    def test_admin_edita_usuario(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f"{self.URL}{self.normal.id}/",
            {"first_name": "Editado"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.normal.refresh_from_db()
        self.assertEqual(self.normal.first_name, "Editado")

    def test_admin_elimina_usuario_hace_soft_delete(self):
        # "Eliminar" desactiva (soft delete): el registro permanece en la DB.
        self.client.force_authenticate(user=self.admin)
        resp = self.client.delete(f"{self.URL}{self.normal.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.normal.refresh_from_db()
        self.assertFalse(self.normal.is_active)

    def test_admin_reactiva_usuario(self):
        # La reactivación conserva el mismo ID y no crea un registro nuevo.
        self.normal.is_active = False
        self.normal.save(update_fields=["is_active"])
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f"{self.URL}{self.normal.id}/",
            {"status": "active"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.normal.refresh_from_db()
        self.assertTrue(self.normal.is_active)
        self.assertEqual(User.objects.filter(pk=self.normal.pk).count(), 1)

    def test_filtro_status_inactive(self):
        self.normal.is_active = False
        self.normal.save(update_fields=["is_active"])
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL, {"status": "inactive"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [u["id"] for u in resp.data["results"]]
        self.assertIn(self.normal.id, ids)
        self.assertNotIn(self.admin.id, ids)

    # --- Validaciones -------------------------------------------------------

    def test_crear_rechaza_email_duplicado(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"email": "user@example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_crear_rechaza_password_debil(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"email": "otro@example.com", "password": "minusculas"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # --- Roles (groups) -----------------------------------------------------

    def test_admin_asigna_roles_al_crear(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"email": "conrol@example.com", "password": STRONG_PASSWORD,
             "groups": ["designer", "operator"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        creado = User.objects.get(email="conrol@example.com")
        self.assertEqual(
            set(creado.groups.values_list("name", flat=True)),
            {"designer", "operator"},
        )
        # La respuesta devuelve los roles por nombre.
        self.assertEqual(set(resp.data["groups"]), {"designer", "operator"})

    def test_admin_edita_roles(self):
        self.client.force_authenticate(user=self.admin)
        self.normal.groups.add(Group.objects.get(name="operator"))
        # Reemplaza los roles por uno solo.
        resp = self.client.patch(
            f"{self.URL}{self.normal.id}/",
            {"groups": ["admin"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(self.normal.groups.values_list("name", flat=True)),
            {"admin"},
        )

    def test_crear_rechaza_rol_inexistente(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"email": "malrol@example.com", "password": STRONG_PASSWORD,
             "groups": ["inexistente"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # --- Guardas de auto-protección ----------------------------------------

    def test_admin_no_puede_autoeliminarse(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.delete(f"{self.URL}{self.admin.id}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_admin_no_puede_quitarse_is_staff(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f"{self.URL}{self.admin.id}/",
            {"is_staff": False},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)


class UserUnlockActionTests(AuthTestCase):
    """POST /api/v1/users/<id>/unlock/ — desbloqueo manual (solo admin)."""

    URL = "/api/v1/users/"

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            username="admin_unlock@example.com",
            email="admin_unlock@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.normal = User.objects.create_user(
            username="locked_user@example.com",
            email="locked_user@example.com",
            password=STRONG_PASSWORD,
        )

    def _lock_normal_user(self):
        from django.utils import timezone

        from .models import LoginLockout

        lockout, _ = LoginLockout.objects.get_or_create(user=self.normal)
        lockout.failed_attempts = 3
        lockout.locked_until = timezone.now() + timezone.timedelta(hours=1)
        lockout.save(update_fields=["failed_attempts", "locked_until"])
        return lockout

    def test_admin_desbloquea_usuario(self):
        from .models import LoginLockout

        self._lock_normal_user()
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(f"{self.URL}{self.normal.id}/unlock/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["is_locked"])

        lockout = LoginLockout.objects.get(user=self.normal)
        self.assertEqual(lockout.failed_attempts, 0)
        self.assertIsNone(lockout.locked_until)

    def test_unlock_permite_loguear_antes_de_que_pase_la_hora(self):
        self._lock_normal_user()
        self.client.force_authenticate(user=self.admin)
        self.client.post(f"{self.URL}{self.normal.id}/unlock/")
        self.client.force_authenticate(user=None)

        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "locked_user@example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_unlock_requiere_permisos_admin(self):
        self._lock_normal_user()
        otro_normal = User.objects.create_user(
            username="otro_normal@example.com",
            email="otro_normal@example.com",
            password=STRONG_PASSWORD,
        )
        self.client.force_authenticate(user=otro_normal)
        resp = self.client.post(f"{self.URL}{self.normal.id}/unlock/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unlock_anonimo_recibe_401(self):
        self._lock_normal_user()
        resp = self.client.post(f"{self.URL}{self.normal.id}/unlock/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_summary_incluye_locked(self):
        self._lock_normal_user()
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["summary"]["locked"], 1)


class UserBulkActionsTests(AuthTestCase):
    """POST /api/v1/users/bulk-actions/ — acciones masivas sobre una lista de IDs."""

    URL = "/api/v1/users/bulk-actions/"

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            username="admin_bulk@example.com",
            email="admin_bulk@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.user_a = User.objects.create_user(
            username="bulk_a@example.com", email="bulk_a@example.com", password=STRONG_PASSWORD,
        )
        self.user_b = User.objects.create_user(
            username="bulk_b@example.com", email="bulk_b@example.com", password=STRONG_PASSWORD,
        )

    def test_bulk_deactivate_desactiva_multiples_usuarios(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"ids": [self.user_a.id, self.user_b.id], "action": "deactivate"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertCountEqual(resp.data["updated"], [self.user_a.id, self.user_b.id])
        self.assertEqual(resp.data["skipped"], [])
        self.user_a.refresh_from_db()
        self.user_b.refresh_from_db()
        self.assertFalse(self.user_a.is_active)
        self.assertFalse(self.user_b.is_active)

    def test_bulk_activate_reactiva_multiples_usuarios(self):
        self.user_a.is_active = False
        self.user_a.save(update_fields=["is_active"])
        self.user_b.is_active = False
        self.user_b.save(update_fields=["is_active"])

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"ids": [self.user_a.id, self.user_b.id], "action": "activate"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertCountEqual(resp.data["updated"], [self.user_a.id, self.user_b.id])
        self.user_a.refresh_from_db()
        self.user_b.refresh_from_db()
        self.assertTrue(self.user_a.is_active)
        self.assertTrue(self.user_b.is_active)

    def test_bulk_set_role_cambia_rol_de_multiples_usuarios(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"ids": [self.user_a.id, self.user_b.id], "action": "set_role", "role": "designer"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertCountEqual(resp.data["updated"], [self.user_a.id, self.user_b.id])
        self.assertTrue(self.user_a.groups.filter(name="designer").exists())
        self.assertTrue(self.user_b.groups.filter(name="designer").exists())

    def test_bulk_set_role_admin_marca_is_staff(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"ids": [self.user_a.id], "action": "set_role", "role": "admin"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user_a.refresh_from_db()
        self.assertTrue(self.user_a.is_staff)

    def test_bulk_set_role_sin_role_devuelve_400(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL, {"ids": [self.user_a.id], "action": "set_role"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_actions_accion_invalida_devuelve_400(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL, {"ids": [self.user_a.id], "action": "delete_forever"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_actions_sin_ids_devuelve_400(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(self.URL, {"ids": [], "action": "deactivate"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_actions_reporta_id_inexistente_como_skipped(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL, {"ids": [self.user_a.id, 999999], "action": "deactivate"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["updated"], [self.user_a.id])
        self.assertEqual(len(resp.data["skipped"]), 1)
        self.assertEqual(resp.data["skipped"][0]["id"], 999999)

    def test_bulk_actions_salta_al_propio_admin(self):
        # El admin no puede aplicarse la acción masiva a sí mismo, igual que
        # en la edición individual (auto-protección).
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.URL,
            {"ids": [self.admin.id, self.user_a.id], "action": "deactivate"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["updated"], [self.user_a.id])
        self.assertEqual(resp.data["skipped"][0]["id"], self.admin.id)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_bulk_actions_requiere_permisos_admin(self):
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.post(
            self.URL, {"ids": [self.user_b.id], "action": "deactivate"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_bulk_actions_anonimo_recibe_401(self):
        resp = self.client.post(
            self.URL, {"ids": [self.user_a.id], "action": "deactivate"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class UserAdminAdvancedFilterTests(AuthTestCase):
    """Filtrado avanzado de /api/v1/users/: actividad, seguridad y origen."""

    URL = "/api/v1/users/"

    def setUp(self):
        super().setUp()
        from django.utils import timezone

        from .models import LoginLockout

        self.admin = User.objects.create_user(
            username="admin_filters@example.com",
            email="admin_filters@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )

        now = timezone.now()

        # Nunca inició sesión, se registró hoy.
        self.never_logged_in = User.objects.create_user(
            username="never_login@example.com",
            email="never_login@example.com",
            password=STRONG_PASSWORD,
        )

        # Se registró hace 100 días, no loguea hace 50 (inactivo).
        self.inactive_user = User.objects.create_user(
            username="inactivo@example.com",
            email="inactivo@example.com",
            password=STRONG_PASSWORD,
        )
        User.objects.filter(pk=self.inactive_user.pk).update(
            date_joined=now - timezone.timedelta(days=100),
            last_login=now - timezone.timedelta(days=50),
        )
        self.inactive_user.refresh_from_db()

        # Logueó ayer: activo recientemente.
        self.active_recent_user = User.objects.create_user(
            username="activo_reciente@example.com",
            email="activo_reciente@example.com",
            password=STRONG_PASSWORD,
        )
        User.objects.filter(pk=self.active_recent_user.pk).update(
            date_joined=now - timezone.timedelta(days=60),
            last_login=now - timezone.timedelta(days=1),
        )
        self.active_recent_user.refresh_from_db()

        # Cuenta Google: password unusable.
        self.google_user = User.objects.create_user(
            username="google_user@example.com",
            email="google_user@example.com",
        )
        self.google_user.set_unusable_password()
        self.google_user.save(update_fields=["password"])

        # Bloqueado ahora mismo.
        self.locked_user = User.objects.create_user(
            username="locked_user_f@example.com",
            email="locked_user_f@example.com",
            password=STRONG_PASSWORD,
        )
        lockout = LoginLockout.objects.create(
            user=self.locked_user,
            failed_attempts=3,
            locked_until=now + timezone.timedelta(hours=1),
        )

        # A un intento fallido de bloquearse (cerca_de_bloquearse).
        self.almost_locked_user = User.objects.create_user(
            username="casi_bloqueado@example.com",
            email="casi_bloqueado@example.com",
            password=STRONG_PASSWORD,
        )
        LoginLockout.objects.create(user=self.almost_locked_user, failed_attempts=2)

        # Un solo intento fallido (bucket "1-2").
        self.one_failed_user = User.objects.create_user(
            username="un_fallo@example.com",
            email="un_fallo@example.com",
            password=STRONG_PASSWORD,
        )
        LoginLockout.objects.create(user=self.one_failed_user, failed_attempts=1)

        self.client.force_authenticate(user=self.admin)

    def _ids(self, resp):
        return {u["id"] for u in resp.data["results"]}

    # --- Actividad ------------------------------------------------------

    def test_filtra_never_logged_in(self):
        resp = self.client.get(self.URL, {"never_logged_in": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = self._ids(resp)
        self.assertIn(self.never_logged_in.id, ids)
        self.assertNotIn(self.active_recent_user.id, ids)

    def test_filtra_inactive_days(self):
        # Inactivo hace más de 30 días: el inactive_user (50 días) entra,
        # el que logueó ayer no, y el que nunca logueó también entra.
        resp = self.client.get(self.URL, {"inactive_days": "30"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = self._ids(resp)
        self.assertIn(self.inactive_user.id, ids)
        self.assertIn(self.never_logged_in.id, ids)
        self.assertNotIn(self.active_recent_user.id, ids)

    def test_filtra_date_joined_range(self):
        from django.utils import timezone

        # inactive_user se registró hace 100 días: el rango [110, 90] días
        # atrás lo contiene.
        desde = (timezone.now() - timezone.timedelta(days=110)).strftime("%Y-%m-%d")
        hasta = (timezone.now() - timezone.timedelta(days=90)).strftime("%Y-%m-%d")

        # Rango invertido -> 400.
        resp = self.client.get(
            self.URL, {"date_joined_from": hasta, "date_joined_to": desde}
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        resp = self.client.get(
            self.URL, {"date_joined_from": desde, "date_joined_to": hasta}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn(self.inactive_user.id, self._ids(resp))

    def test_fecha_con_formato_invalido_devuelve_400(self):
        resp = self.client.get(self.URL, {"date_joined_from": "no-es-una-fecha"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inactive_days_no_numerico_devuelve_400(self):
        resp = self.client.get(self.URL, {"inactive_days": "mucho"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # --- Seguridad --------------------------------------------------------

    def test_filtra_locked_independiente_de_status(self):
        # locked=true encuentra al bloqueado aunque is_active siga en True.
        resp = self.client.get(self.URL, {"locked": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = self._ids(resp)
        self.assertIn(self.locked_user.id, ids)
        self.assertNotIn(self.almost_locked_user.id, ids)

        resp = self.client.get(self.URL, {"locked": "false"})
        ids = self._ids(resp)
        self.assertNotIn(self.locked_user.id, ids)
        self.assertIn(self.almost_locked_user.id, ids)

    def test_filtra_failed_attempts_buckets(self):
        resp = self.client.get(self.URL, {"failed_attempts": "cerca_de_bloquearse"})
        ids = self._ids(resp)
        self.assertIn(self.almost_locked_user.id, ids)
        self.assertNotIn(self.one_failed_user.id, ids)
        self.assertNotIn(self.locked_user.id, ids)

        resp = self.client.get(self.URL, {"failed_attempts": "1-2"})
        ids = self._ids(resp)
        self.assertIn(self.one_failed_user.id, ids)
        self.assertIn(self.almost_locked_user.id, ids)

        resp = self.client.get(self.URL, {"failed_attempts": "0"})
        ids = self._ids(resp)
        self.assertIn(self.never_logged_in.id, ids)  # sin fila de lockout
        self.assertNotIn(self.one_failed_user.id, ids)

    # --- Origen -------------------------------------------------------------

    def test_filtra_auth_method(self):
        resp = self.client.get(self.URL, {"auth_method": "google"})
        ids = self._ids(resp)
        self.assertIn(self.google_user.id, ids)
        self.assertNotIn(self.never_logged_in.id, ids)

        resp = self.client.get(self.URL, {"auth_method": "local"})
        ids = self._ids(resp)
        self.assertIn(self.never_logged_in.id, ids)
        self.assertNotIn(self.google_user.id, ids)

    # --- Ordenamiento ---------------------------------------------------

    def test_ordering_por_last_login(self):
        resp = self.client.get(self.URL, {"ordering": "-last_login"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        emails = [u["email"] for u in resp.data["results"]]
        # El que logueó más reciente (activo_reciente) va antes que el
        # inactivo (logueó hace 50 días); ambos antes que los null (van al final en SQLite ASC/DESC).
        self.assertLess(
            emails.index("activo_reciente@example.com"),
            emails.index("inactivo@example.com"),
        )

    # --- Combinaciones con filtros existentes (AND) --------------------

    def test_combina_locked_con_status_active(self):
        resp = self.client.get(self.URL, {"locked": "true", "status": "active"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn(self.locked_user.id, self._ids(resp))

        # Desactivar al usuario bloqueado: locked=true & status=active ya no debe verlo.
        self.locked_user.is_active = False
        self.locked_user.save(update_fields=["is_active"])
        resp = self.client.get(self.URL, {"locked": "true", "status": "active"})
        self.assertNotIn(self.locked_user.id, self._ids(resp))

    def test_combina_auth_method_con_search(self):
        resp = self.client.get(
            self.URL, {"auth_method": "google", "search": "google_user"}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn(self.google_user.id, self._ids(resp))

        resp = self.client.get(
            self.URL, {"auth_method": "local", "search": "google_user"}
        )
        self.assertNotIn(self.google_user.id, self._ids(resp))

    def test_combina_inactive_days_con_never_logged_in(self):
        resp = self.client.get(
            self.URL, {"inactive_days": "30", "never_logged_in": "true"}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = self._ids(resp)
        self.assertIn(self.never_logged_in.id, ids)
        self.assertNotIn(self.inactive_user.id, ids)  # sí logueó, aunque hace 50 días


class RoleCrudTests(AuthTestCase):
    """Creación y eliminación de roles personalizados (Groups).

    POST   /api/v1/auth/roles/
    DELETE /api/v1/auth/roles/<role_id>/
    """

    ROLES_URL = "/api/v1/auth/roles/"
    ADMIN = "admin"
    DESIGNER = "designer"
    OPERATOR = "operator"
    SUBSCRIBER = "subscriber"
    STRONG_PASSWORD = STRONG_PASSWORD

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group

        from .models import GroupRolePermission, RolePermission

        self.Group = Group
        self.GroupRolePermission = GroupRolePermission
        self.RolePermission = RolePermission

        for name in (self.ADMIN, self.DESIGNER, self.OPERATOR, self.SUBSCRIBER):
            Group.objects.get_or_create(name=name)

        self.admin_user = User.objects.create_user(
            username="crud_admin@example.com",
            email="crud_admin@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.admin_user.groups.add(Group.objects.get(name=self.ADMIN))

        self.designer_user = User.objects.create_user(
            username="crud_designer@example.com",
            email="crud_designer@example.com",
            password=STRONG_PASSWORD,
        )
        self.designer_user.groups.add(Group.objects.get(name=self.DESIGNER))

    # --- Creación -----------------------------------------------------------

    def test_admin_puede_crear_rol(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.post(self.ROLES_URL, {"name": "mi_rol"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["name"], "mi_rol")
        self.assertTrue(self.Group.objects.filter(name="mi_rol").exists())

    def test_no_admin_no_puede_crear_rol(self):
        self.client.force_authenticate(user=self.designer_user)
        resp = self.client.post(self.ROLES_URL, {"name": "mi_rol"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.Group.objects.filter(name="mi_rol").exists())

    def test_no_se_puede_crear_rol_duplicado(self):
        self.Group.objects.create(name="mi_rol")
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.post(self.ROLES_URL, {"name": "mi_rol"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_se_puede_crear_duplicado_por_mayusculas(self):
        self.Group.objects.create(name="mi_rol")
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.post(self.ROLES_URL, {"name": "MI_Rol"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nombre_obligatorio(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.post(self.ROLES_URL, {"name": "   "}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_se_puede_crear_rol_protegido(self):
        for name in (self.ADMIN, self.DESIGNER, self.OPERATOR, self.SUBSCRIBER):
            self.client.force_authenticate(user=self.admin_user)
            resp = self.client.post(self.ROLES_URL, {"name": name}, format="json")
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_se_puede_crear_alias_legacy(self):
        for name in ("administrador", "administradores", "administrator",
                     "diseñador", "diseñadores", "disenador", "disenadores",
                     "operador", "operadores", "user"):
            self.client.force_authenticate(user=self.admin_user)
            resp = self.client.post(self.ROLES_URL, {"name": name}, format="json")
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rol_nuevo_comienza_sin_permisos(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.post(self.ROLES_URL, {"name": "sin_permisos"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        group = self.Group.objects.get(name="sin_permisos")
        self.assertEqual(
            self.GroupRolePermission.objects.filter(group=group).count(),
            0,
        )

    def test_rol_nuevo_puede_recibir_permisos(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.post(self.ROLES_URL, {"name": "con_permisos"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        group = self.Group.objects.get(name="con_permisos")
        perm = self.RolePermission.objects.get(key="users.view")
        self.GroupRolePermission.objects.create(group=group, permission=perm)
        links = self.GroupRolePermission.objects.filter(group=group)
        self.assertEqual(links.count(), 1)
        self.assertEqual(links.first().permission.key, "users.view")

    # --- Eliminación --------------------------------------------------------

    def test_admin_puede_eliminar_rol_personalizado(self):
        group = self.Group.objects.create(name="eliminable")
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.delete(f"{self.ROLES_URL}{group.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(self.Group.objects.filter(pk=group.pk).exists())

    def test_no_admin_no_puede_eliminar_rol(self):
        group = self.Group.objects.create(name="eliminable")
        self.client.force_authenticate(user=self.designer_user)
        resp = self.client.delete(f"{self.ROLES_URL}{group.id}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(self.Group.objects.filter(pk=group.pk).exists())

    def test_no_se_puede_eliminar_admin(self):
        group = self.Group.objects.get(name=self.ADMIN)
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.delete(f"{self.ROLES_URL}{group.id}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(self.Group.objects.filter(pk=group.pk).exists())

    def test_no_se_puede_eliminar_designer(self):
        group = self.Group.objects.get(name=self.DESIGNER)
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.delete(f"{self.ROLES_URL}{group.id}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(self.Group.objects.filter(pk=group.pk).exists())

    def test_no_se_puede_eliminar_operator(self):
        group = self.Group.objects.get(name=self.OPERATOR)
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.delete(f"{self.ROLES_URL}{group.id}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(self.Group.objects.filter(pk=group.pk).exists())

    def test_no_se_puede_eliminar_subscriber(self):
        group = self.Group.objects.get(name=self.SUBSCRIBER)
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.delete(f"{self.ROLES_URL}{group.id}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(self.Group.objects.filter(pk=group.pk).exists())

    def test_eliminar_rol_inexistente_devuelve_404(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.delete(f"{self.ROLES_URL}999999/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_eliminar_rol_mueve_usuarios_a_subscriber(self):
        group = self.Group.objects.create(name="test_role")
        subscriber = self.Group.objects.get(name=self.SUBSCRIBER)
        user = User.objects.create_user(
            username="roluser@example.com",
            email="roluser@example.com",
            password=STRONG_PASSWORD,
        )
        user.groups.add(group)

        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.delete(f"{self.ROLES_URL}{group.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        user.refresh_from_db()
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertFalse(user.groups.filter(pk=group.pk).exists())
        self.assertTrue(user.groups.filter(pk=subscriber.pk).exists())

        from .permissions_map import get_effective_role
        self.assertEqual(get_effective_role(user), self.SUBSCRIBER)

    def test_eliminar_rol_borra_group_role_permissions(self):
        group = self.Group.objects.create(name="test_role")
        perm = self.RolePermission.objects.get(key="users.view")
        self.GroupRolePermission.objects.create(group=group, permission=perm)
        self.assertEqual(self.GroupRolePermission.objects.filter(group=group).count(), 1)

        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.delete(f"{self.ROLES_URL}{group.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.GroupRolePermission.objects.filter(group=group).count(), 0)

    def test_eliminar_rol_no_borra_usuarios(self):
        group = self.Group.objects.create(name="test_role")
        user = User.objects.create_user(
            username="keep@example.com",
            email="keep@example.com",
            password=STRONG_PASSWORD,
        )
        user.groups.add(group)

        self.client.force_authenticate(user=self.admin_user)
        self.client.delete(f"{self.ROLES_URL}{group.id}/")
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_eliminar_rol_no_toca_otros_roles_ni_permisos(self):
        group = self.Group.objects.create(name="test_role")
        perm_view = self.RolePermission.objects.get(key="users.view")
        perm_me = self.RolePermission.objects.get(key="users.me.view")
        self.GroupRolePermission.objects.create(group=group, permission=perm_view)

        admin_group = self.Group.objects.get(name=self.ADMIN)
        designer_group = self.Group.objects.get(name=self.DESIGNER)
        admin_before = set(
            self.GroupRolePermission.objects.filter(group=admin_group)
            .values_list("permission__key", flat=True)
        )
        designer_before = set(
            self.GroupRolePermission.objects.filter(group=designer_group)
            .values_list("permission__key", flat=True)
        )

        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.delete(f"{self.ROLES_URL}{group.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        self.assertEqual(
            set(self.GroupRolePermission.objects.filter(group=admin_group)
                .values_list("permission__key", flat=True)),
            admin_before,
        )
        self.assertEqual(
            set(self.GroupRolePermission.objects.filter(group=designer_group)
                .values_list("permission__key", flat=True)),
            designer_before,
        )
        self.assertTrue(self.RolePermission.objects.filter(pk=perm_me.pk).exists())

    def test_eliminar_rol_no_toca_is_staff_ni_is_superuser(self):
        group = self.Group.objects.create(name="test_role")
        user = User.objects.create_user(
            username="staff2@example.com",
            email="staff2@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
            is_superuser=True,
        )
        user.groups.add(group)

        self.client.force_authenticate(user=self.admin_user)
        self.client.delete(f"{self.ROLES_URL}{group.id}/")
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_eliminar_rol_de_usuario_con_otro_rol_conserva_el_otro(self):
        group = self.Group.objects.create(name="test_role")
        designer = self.Group.objects.get(name=self.DESIGNER)
        user = User.objects.create_user(
            username="multi@example.com",
            email="multi@example.com",
            password=STRONG_PASSWORD,
        )
        user.groups.add(group, designer)

        self.client.force_authenticate(user=self.admin_user)
        self.client.delete(f"{self.ROLES_URL}{group.id}/")
        user.refresh_from_db()
        self.assertFalse(user.groups.filter(pk=group.pk).exists())
        self.assertTrue(user.groups.filter(pk=designer.pk).exists())

        from .permissions_map import get_effective_role
        self.assertEqual(get_effective_role(user), self.DESIGNER)

    def test_caso_completo_eliminacion_rol_personalizado(self):
        """Caso crítico: crear rol, asignar usuario, darle permisos, eliminar y
        verificar que usuario sigue, queda subscriber y los demás roles
        permanecen intactos."""
        role = self.Group.objects.create(name="test_role")
        user_a = User.objects.create_user(
            username="casoa@example.com",
            email="casoa@example.com",
            password=STRONG_PASSWORD,
        )
        user_a.groups.add(role)

        view_perm = self.RolePermission.objects.get(key="users.view")
        self.GroupRolePermission.objects.create(group=role, permission=view_perm)

        # Antes de eliminar, el usuario pertenece al group test_role.
        from .permissions_map import get_effective_role
        self.assertTrue(user_a.groups.filter(pk=role.pk).exists())

        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.delete(f"{self.ROLES_URL}{role.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        # - Usuario A sigue existiendo.
        user_a.refresh_from_db()
        self.assertTrue(User.objects.filter(pk=user_a.pk).exists())
        # - Ya no pertenece a test_role.
        self.assertFalse(user_a.groups.filter(pk=role.pk).exists())
        # - Queda con rol efectivo subscriber.
        self.assertEqual(get_effective_role(user_a), self.SUBSCRIBER)
        # - El permiso de test_role ya no existe/asigna.
        self.assertFalse(self.GroupRolePermission.objects.filter(group__name="test_role").exists())
        # - El permiso en catálogo sigue existiendo (no se borra de la tabla RolePermission).
        self.assertTrue(self.RolePermission.objects.filter(key="users.view").exists())
        # - Los demás roles y permisos siguen intactos.
        admin_group = self.Group.objects.get(name=self.ADMIN)
        admin_perms = set(
            self.GroupRolePermission.objects.filter(group=admin_group)
            .values_list("permission__key", flat=True)
        )
        self.assertTrue({"users.view", "users.create", "users.me.view"}.issubset(admin_perms))

    # --- Los endpoints existentes siguen funcionando -------------------------

    def test_endpoints_permsisos_siguen_funcionando_tras_crud(self):
        role = self.Group.objects.create(name="persistente")
        perm = self.RolePermission.objects.get(key="users.view")
        self.GroupRolePermission.objects.create(group=role, permission=perm)

        self.client.force_authenticate(user=self.admin_user)
        # Crear otro rol.
        self.client.post(self.ROLES_URL, {"name": "otro_rol"}, format="json")
        # GET roles lista incluye ambos personalizados.
        resp = self.client.get(self.ROLES_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = {r["name"] for r in resp.data}
        self.assertIn("persistente", names)
        self.assertIn("otro_rol", names)

        # GET permisos del rol nuevo funciona.
        otro = self.Group.objects.get(name="otro_rol")
        resp = self.client.get(f"{self.ROLES_URL}{otro.id}/permissions/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["permissions"], [])

        # PUT permisos del rol personalizado funciona (endpoint existente).
        resp = self.client.put(
            f"{self.ROLES_URL}{otro.id}/permissions/",
            {"permissions": ["users.view"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual({p["key"] for p in resp.data["permissions"]}, {"users.view"})

        # Eliminar el rol de prueba no rompe el catálogo: sigue siendo
        # exactamente ALL_PERMISSIONS (ver RolePermissionSystemTests).
        self.client.delete(f"{self.ROLES_URL}{role.id}/")
        resp = self.client.get("/api/v1/auth/permissions/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), len(RolePermissionSystemTests.ALL_PERMISSIONS))

    def test_get_roles_lista_incluye_personalizados(self):
        self.Group.objects.create(name="auditor")
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.get(self.ROLES_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [r["name"] for r in resp.data]
        # Los cuatro básicos primero (orden canónico).
        self.assertEqual(
            names[:4],
            [self.ADMIN, self.DESIGNER, self.OPERATOR, self.SUBSCRIBER],
        )
        self.assertIn("auditor", names)


class DashboardTests(AuthTestCase):
    """Menú del dashboard (/api/v1/auth/users/me/dashboard/)."""

    URL = "/api/v1/auth/users/me/dashboard/"

    def setUp(self):
        super().setUp()
        self.subscriber = User.objects.create_user(
            username="subscriber@example.com",
            email="subscriber@example.com",
            password=STRONG_PASSWORD,
        )
        admin_group, _ = Group.objects.get_or_create(name="admin")
        self.admin = User.objects.create_user(
            username="admin@example.com",
            email="admin@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.admin.groups.add(admin_group)

    def test_subscriber_no_recibe_item_users(self):
        self.client.force_authenticate(user=self.subscriber)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["user"]["role"], "subscriber")
        self.assertFalse(resp.data["user"]["is_admin"])
        keys = [item["key"] for item in resp.data["menu"]]
        self.assertNotIn("users", keys)

    def test_admin_recibe_item_users(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["user"]["role"], "admin")
        self.assertTrue(resp.data["user"]["is_admin"])
        keys = [item["key"] for item in resp.data["menu"]]
        self.assertIn("users", keys)

    def test_anonimo_recibe_401(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_subscriber_recibe_direcciones_y_soporte_habilitados(self):
        self.client.force_authenticate(user=self.subscriber)
        resp = self.client.get(self.URL)
        by_key = {item["key"]: item for item in resp.data["menu"]}
        self.assertTrue(by_key["addresses"]["enabled"])
        self.assertEqual(by_key["addresses"]["url"], "pedidos.html#addressesSection")
        self.assertTrue(by_key["support"]["enabled"])
        self.assertEqual(by_key["support"]["url"], "ayuda.html")

    def test_labels_habilitado(self):
        # apps.labels ya tiene CRUD y pantalla propia (rotulos.html).
        self.client.force_authenticate(user=self.subscriber)
        resp = self.client.get(self.URL)
        by_key = {item["key"]: item for item in resp.data["menu"]}
        self.assertTrue(by_key["labels"]["enabled"])
        self.assertEqual(by_key["labels"]["url"], "rotulos.html")

    def test_documents_habilitado(self):
        # apps.documents ya tiene CRUD y pantalla propia (documentos.html).
        self.client.force_authenticate(user=self.subscriber)
        resp = self.client.get(self.URL)
        by_key = {item["key"]: item for item in resp.data["menu"]}
        self.assertTrue(by_key["documents"]["enabled"])
        self.assertEqual(by_key["documents"]["url"], "documentos.html")

    def test_processing_sigue_deshabilitado(self):
        # No hay backend de processing todavía: el tile se lista
        # deshabilitado en vez de apuntar a una pantalla inexistente.
        self.client.force_authenticate(user=self.subscriber)
        resp = self.client.get(self.URL)
        by_key = {item["key"]: item for item in resp.data["menu"]}
        self.assertFalse(by_key["processing"]["enabled"])


class SupportMessageTests(AuthTestCase):
    """POST /api/v1/auth/support/ — form de contacto del dashboard."""

    URL = "/api/v1/auth/support/"

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="support_user@example.com",
            email="support_user@example.com",
            password=STRONG_PASSWORD,
        )

    def test_usuario_autenticado_crea_mensaje(self):
        from .models import SupportMessage

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.URL,
            {"subject": "No puedo cambiar mi contraseña", "message": "Me tira error 500."},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        mensaje = SupportMessage.objects.get(user=self.user)
        self.assertEqual(mensaje.subject, "No puedo cambiar mi contraseña")
        self.assertEqual(mensaje.message, "Me tira error 500.")

    def test_mensaje_queda_asociado_al_usuario_autenticado_no_al_body(self):
        # Identidad siempre desde request.user: mandar otro user_id en el
        # body (si lo hubiera) no debería poder suplantar a otra cuenta.
        from .models import SupportMessage

        otro = User.objects.create_user(
            username="otro_support@example.com", email="otro_support@example.com",
            password=STRONG_PASSWORD,
        )
        self.client.force_authenticate(user=self.user)
        self.client.post(
            self.URL,
            {"subject": "Asunto", "message": "Mensaje", "user": otro.id},
            format="json",
        )
        mensaje = SupportMessage.objects.get(subject="Asunto")
        self.assertEqual(mensaje.user, self.user)
        self.assertNotEqual(mensaje.user, otro)

    def test_asunto_vacio_devuelve_400(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(self.URL, {"subject": "   ", "message": "Mensaje"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mensaje_vacio_devuelve_400(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(self.URL, {"subject": "Asunto", "message": ""}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_anonimo_recibe_401(self):
        resp = self.client.post(self.URL, {"subject": "Asunto", "message": "Mensaje"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class ForcedPasswordChangeTests(AuthTestCase):
    """PasswordChangeRequirement: activación (automática/manual), enforcement
    en toda la API (JWTAuthenticationWithPasswordPolicy) y desactivación al
    completar el cambio."""

    USERS_URL = "/api/v1/users/"
    DASHBOARD_URL = "/api/v1/auth/users/me/dashboard/"
    CHANGE_PASSWORD_URL = "/api/v1/auth/me/change-password/"
    ME_URL = "/api/v1/auth/me/"
    LOGOUT_URL = "/api/v1/auth/logout/"

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            username="admin_pwd@example.com",
            email="admin_pwd@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )

    # --- Activación automática (crear sin password) -------------------------

    def test_crear_sin_password_sin_setting_sigue_dando_400(self):
        # Regresión: sin ADMIN_CREATED_USER_PASSWORD configurado, el
        # comportamiento previo se mantiene intacto.
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.USERS_URL,
            {"email": "sin_pass@example.com", "full_name": "Sin Pass", "role": "subscriber", "status": "active"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_crear_sin_password_con_setting_usa_temporal_y_marca_flag(self):
        from django.test import override_settings

        from .models import PasswordChangeRequirement

        with override_settings(ADMIN_CREATED_USER_PASSWORD="Temporal2026"):
            self.client.force_authenticate(user=self.admin)
            resp = self.client.post(
                self.USERS_URL,
                {"email": "temp_user@example.com", "full_name": "Temp User", "role": "subscriber", "status": "active"},
                format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
            self.assertTrue(resp.data["must_change_password"])

            creado = User.objects.get(email="temp_user@example.com")
            requirement = PasswordChangeRequirement.objects.get(user=creado)
            self.assertTrue(requirement.must_change_password)

            # Puede loguearse con la contraseña temporal.
            self.client.force_authenticate(user=None)
            login_resp = self.client.post(
                "/api/v1/auth/login/",
                {"email": "temp_user@example.com", "password": "Temporal2026"},
                format="json",
            )
            self.assertEqual(login_resp.status_code, status.HTTP_200_OK)
            self.assertTrue(login_resp.data["user"]["must_change_password"])

    # --- Activación / desactivación manual -----------------------------------

    def test_admin_activa_flag_manualmente_sobre_usuario_existente(self):
        normal = User.objects.create_user(
            username="normal_pwd@example.com", email="normal_pwd@example.com", password=STRONG_PASSWORD,
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f"{self.USERS_URL}{normal.id}/", {"force_password_change": True}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["must_change_password"])

    def test_admin_desactiva_flag_manualmente(self):
        from .models import PasswordChangeRequirement

        normal = User.objects.create_user(
            username="normal_pwd2@example.com", email="normal_pwd2@example.com", password=STRONG_PASSWORD,
        )
        PasswordChangeRequirement.objects.create(user=normal, must_change_password=True)

        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f"{self.USERS_URL}{normal.id}/", {"force_password_change": False}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["must_change_password"])

    # --- Enforcement (no se puede saltear) -----------------------------------
    #
    # ``force_authenticate`` fuerza request.user directo y NO pasa por las
    # authentication classes (ahí vive el chequeo), así que estos tests
    # arman un JWT real y lo mandan por header, igual que en producción.

    def _crear_usuario_forzado(self):
        from .models import PasswordChangeRequirement

        user = User.objects.create_user(
            username="forzado@example.com", email="forzado@example.com", password=STRONG_PASSWORD,
        )
        PasswordChangeRequirement.objects.create(user=user, must_change_password=True)
        return user

    def _authenticate_with_real_jwt(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken

        access = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def test_usuario_forzado_no_puede_usar_otros_endpoints(self):
        user = self._crear_usuario_forzado()
        self._authenticate_with_real_jwt(user)
        resp = self.client.get(self.DASHBOARD_URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(resp.data.get("must_change_password"))

    def test_usuario_forzado_puede_ver_su_propio_perfil(self):
        user = self._crear_usuario_forzado()
        self._authenticate_with_real_jwt(user)
        resp = self.client.get(self.ME_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_usuario_forzado_puede_hacer_logout(self):
        user = self._crear_usuario_forzado()
        self._authenticate_with_real_jwt(user)
        resp = self.client.post(self.LOGOUT_URL, {"refresh": ""}, format="json")
        self.assertNotEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_usuario_forzado_puede_cambiar_password_y_flag_se_apaga(self):
        from .models import PasswordChangeRequirement

        user = self._crear_usuario_forzado()
        self._authenticate_with_real_jwt(user)

        resp = self.client.post(
            self.CHANGE_PASSWORD_URL,
            {
                "current_password": STRONG_PASSWORD,
                "new_password": "NuevaClave2026",
                "confirm_password": "NuevaClave2026",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        requirement = PasswordChangeRequirement.objects.get(user=user)
        self.assertFalse(requirement.must_change_password)

        # Ahora sí puede usar otros endpoints.
        resp2 = self.client.get(self.DASHBOARD_URL)
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)

    def test_usuario_sin_flag_no_se_ve_afectado(self):
        normal = User.objects.create_user(
            username="libre@example.com", email="libre@example.com", password=STRONG_PASSWORD,
        )
        self._authenticate_with_real_jwt(normal)
        resp = self.client.get(self.DASHBOARD_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class UserAdminOrdersIntegrationTests(AuthTestCase):
    """El listado/detalle de /api/v1/users/ trae orders_count y last_order_at
    (anotados vía Count/Max, sin N+1) desde apps.orders."""

    URL = "/api/v1/users/"

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            username="admin_orders@example.com",
            email="admin_orders@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.buyer = User.objects.create_user(
            username="buyer@example.com", email="buyer@example.com", password=STRONG_PASSWORD,
        )
        self.no_orders_user = User.objects.create_user(
            username="no_orders@example.com", email="no_orders@example.com", password=STRONG_PASSWORD,
        )

    def _crear_pedido(self, user, **overrides):
        from apps.orders.models import Address, Order

        address = Address.objects.create(
            user=user, street="Calle Falsa", number="123", city="Springfield",
        )
        return Order.objects.create(user=user, address=address, **overrides)

    def test_usuario_sin_pedidos_devuelve_cero_y_null(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        user_data = next(u for u in resp.data["results"] if u["id"] == self.no_orders_user.id)
        self.assertEqual(user_data["orders_count"], 0)
        self.assertIsNone(user_data["last_order_at"])

    def test_orders_count_cuenta_todos_los_pedidos_del_usuario(self):
        self._crear_pedido(self.buyer)
        self._crear_pedido(self.buyer)
        self._crear_pedido(self.buyer)

        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        user_data = next(u for u in resp.data["results"] if u["id"] == self.buyer.id)
        self.assertEqual(user_data["orders_count"], 3)
        self.assertIsNotNone(user_data["last_order_at"])

    def test_last_order_at_es_el_pedido_mas_reciente(self):
        from django.utils import timezone

        from apps.orders.models import Order

        primero = self._crear_pedido(self.buyer)
        Order.objects.filter(pk=primero.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=5)
        )
        ultimo = self._crear_pedido(self.buyer)

        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        user_data = next(u for u in resp.data["results"] if u["id"] == self.buyer.id)

        from django.utils.dateparse import parse_datetime

        ultimo.refresh_from_db()
        # Comparar el instante real (evita falsos negativos por cómo cada
        # lado serializa el timezone), truncado al segundo.
        devuelto = parse_datetime(user_data["last_order_at"])
        self.assertEqual(devuelto.replace(microsecond=0), ultimo.created_at.replace(microsecond=0))

    def test_detalle_de_usuario_tambien_incluye_orders_count(self):
        self._crear_pedido(self.buyer)
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(f"{self.URL}{self.buyer.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["orders_count"], 1)

    def test_orders_count_no_se_infla_con_otros_filtros_join(self):
        # Filtro que hace JOIN (role=admin usa groups) no debe multiplicar
        # el conteo de pedidos (regresión del distinct=True en el annotate).
        self._crear_pedido(self.buyer)
        self._crear_pedido(self.buyer)
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL, {"search": "buyer"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        user_data = next(u for u in resp.data["results"] if u["id"] == self.buyer.id)
        self.assertEqual(user_data["orders_count"], 2)


class UserExportCsvTests(AuthTestCase):
    """GET /api/v1/users/export/ — exportación CSV generada en el backend."""

    URL = "/api/v1/users/export/"

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            username="admin_csv@example.com",
            email="admin_csv@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.user_a = User.objects.create_user(
            username="csv_a@example.com", email="csv_a@example.com",
            password=STRONG_PASSWORD, first_name="Ana", last_name="Csv",
        )
        self.user_b = User.objects.create_user(
            username="csv_b@example.com", email="csv_b@example.com", password=STRONG_PASSWORD,
        )
        # Cuenta "Google": password unusable, mismo heurístico que el resto del proyecto.
        self.google_user = User.objects.create_user(
            username="csv_google@example.com", email="csv_google@example.com",
        )
        self.google_user.set_unusable_password()
        self.google_user.save(update_fields=["password"])

    def _parse_csv(self, response):
        import csv
        import io

        content = response.content.decode("utf-8")
        return list(csv.reader(io.StringIO(content)))

    def test_export_requiere_permisos_admin(self):
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_export_anonimo_401(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_export_devuelve_csv_con_header_y_todas_las_filas(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("attachment", resp["Content-Disposition"])

        rows = self._parse_csv(resp)
        header = rows[0]
        self.assertEqual(
            header,
            ["id", "nombre", "email", "rol", "estado", "fecha_registro",
             "ultimo_login", "metodo_autenticacion", "bloqueado",
             "total_pedidos", "fecha_ultimo_pedido"],
        )
        # admin + user_a + user_b + google_user = 4 filas de datos.
        self.assertEqual(len(rows) - 1, 4)

    def test_export_columna_nombre_y_metodo_autenticacion(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        rows = self._parse_csv(resp)
        by_email = {row[2]: row for row in rows[1:]}

        fila_a = by_email["csv_a@example.com"]
        self.assertEqual(fila_a[1], "Ana Csv")
        self.assertEqual(fila_a[7], "Local")

        fila_google = by_email["csv_google@example.com"]
        self.assertEqual(fila_google[7], "Google")

    def test_export_con_ids_filtra_solo_los_seleccionados(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL, {"ids": f"{self.user_a.id},{self.user_b.id}"})
        rows = self._parse_csv(resp)
        emails = {row[2] for row in rows[1:]}
        self.assertEqual(emails, {"csv_a@example.com", "csv_b@example.com"})

    def test_export_respeta_filtros_de_query_params(self):
        self.user_b.is_active = False
        self.user_b.save(update_fields=["is_active"])

        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL, {"status": "inactive"})
        rows = self._parse_csv(resp)
        emails = {row[2] for row in rows[1:]}
        self.assertEqual(emails, {"csv_b@example.com"})

    def test_export_ids_invalidos_devuelve_400(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL, {"ids": "abc"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class UserMetricsTests(AuthTestCase):
    """GET /api/v1/users/metrics/ — distribución por rol, altas por mes y
    método de autenticación."""

    URL = "/api/v1/users/metrics/"

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            username="admin_metrics@example.com",
            email="admin_metrics@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        designer_group, _ = Group.objects.get_or_create(name="designer")
        self.designer = User.objects.create_user(
            username="designer_metrics@example.com",
            email="designer_metrics@example.com",
            password=STRONG_PASSWORD,
        )
        self.designer.groups.add(designer_group)

        self.google_user = User.objects.create_user(
            username="google_metrics@example.com", email="google_metrics@example.com",
        )
        self.google_user.set_unusable_password()
        self.google_user.save(update_fields=["password"])

    def test_requiere_permisos_admin(self):
        self.client.force_authenticate(user=self.designer)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonimo_401(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_role_distribution_cuenta_por_rol_efectivo(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        by_role = {row["role"]: row["count"] for row in resp.data["role_distribution"]}
        self.assertEqual(by_role.get("admin"), 1)
        self.assertEqual(by_role.get("designer"), 1)
        # google_user no tiene group ni is_staff -> cae en el fallback subscriber.
        self.assertEqual(by_role.get("subscriber"), 1)

    def test_auth_method_distingue_local_de_google(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.data["auth_method"]["google"], 1)
        self.assertEqual(resp.data["auth_method"]["local"], 2)

    def test_signups_by_month_incluye_altas_del_mes_actual(self):
        from django.utils import timezone

        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL)
        current_month = timezone.now().strftime("%Y-%m")
        months = {row["month"]: row["count"] for row in resp.data["signups_by_month"]}
        self.assertIn(current_month, months)
        self.assertGreaterEqual(months[current_month], 3)

    def test_months_param_invalido_devuelve_400(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.URL, {"months": "abc"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class UserEmailVerifiedFunctionTests(AuthTestCase):
    """models.user_email_verified(): regla "sin fila = verificado"."""

    def test_sin_fila_de_verificacion_es_verificado(self):
        # Cubre cuentas viejas (previas a este flujo) y cuentas de Google, que
        # nunca crean una fila EmailVerification.
        from .models import user_email_verified

        user = User.objects.create_user(
            username="sinfila@example.com", email="sinfila@example.com",
        )
        self.assertTrue(user_email_verified(user))

    def test_con_fila_is_verified_true_es_verificado(self):
        from .models import EmailVerification, user_email_verified

        user = User.objects.create_user(
            username="verificado@example.com", email="verificado@example.com",
        )
        EmailVerification.objects.create(user=user, is_verified=True)
        self.assertTrue(user_email_verified(user))

    def test_con_fila_is_verified_false_es_pendiente(self):
        from .models import EmailVerification, user_email_verified

        user = User.objects.create_user(
            username="pendiente@example.com", email="pendiente@example.com",
        )
        EmailVerification.objects.create(user=user, is_verified=False)
        self.assertFalse(user_email_verified(user))


class RegisterEmailVerificationTests(AuthTestCase):
    """RegisterView dispara la verificación suave de email."""

    def test_register_crea_verificacion_pendiente_y_envia_correo(self):
        from django.core import mail

        from .models import EmailVerification

        resp = self.client.post(
            "/api/v1/auth/register/",
            {"email": "verify@example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(email="verify@example.com")
        verification = EmailVerification.objects.get(user=user)
        self.assertFalse(verification.is_verified)
        self.assertTrue(verification.token)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(verification.token, mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["verify@example.com"])

    def test_register_devuelve_email_verified_false_en_el_payload(self):
        resp = self.client.post(
            "/api/v1/auth/register/",
            {"email": "verify2@example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertFalse(resp.data["user"]["email_verified"])


class EmailVerificationConfirmTests(AuthTestCase):
    """POST /api/v1/auth/verify-email/confirm/"""

    URL = "/api/v1/auth/verify-email/confirm/"

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="confirm@example.com",
            email="confirm@example.com",
            password=STRONG_PASSWORD,
        )
        from .models import EmailVerification

        self.verification, _ = EmailVerification.objects.get_or_create(user=self.user)
        self.verification.refresh_token()

    def test_token_valido_verifica_la_cuenta(self):
        resp = self.client.post(self.URL, {"token": self.verification.token}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.verification.refresh_from_db()
        self.assertTrue(self.verification.is_verified)
        self.assertIsNotNone(self.verification.verified_at)

    def test_token_inexistente_devuelve_400(self):
        resp = self.client.post(self.URL, {"token": "no-existe"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_token_expirado_devuelve_400(self):
        from datetime import timedelta

        from django.utils import timezone

        self.verification.expires_at = timezone.now() - timedelta(days=1)
        self.verification.save(update_fields=["expires_at"])

        resp = self.client.post(self.URL, {"token": self.verification.token}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        self.verification.refresh_from_db()
        self.assertFalse(self.verification.is_verified)

    def test_sin_token_devuelve_400(self):
        resp = self.client.post(self.URL, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class EmailVerificationResendTests(AuthTestCase):
    """POST /api/v1/auth/verify-email/resend/"""

    URL = "/api/v1/auth/verify-email/resend/"

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="resend@example.com",
            email="resend@example.com",
            password=STRONG_PASSWORD,
        )

    def test_requiere_autenticacion(self):
        resp = self.client.post(self.URL, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_genera_un_token_nuevo_distinto_del_anterior(self):
        from django.core import mail

        from .models import EmailVerification

        verification, _ = EmailVerification.objects.get_or_create(user=self.user)
        token_anterior = verification.refresh_token()

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(self.URL, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        verification.refresh_from_db()
        self.assertNotEqual(verification.token, token_anterior)
        self.assertFalse(verification.is_verified)
        self.assertEqual(len(mail.outbox), 1)

    def test_usuario_ya_verificado_no_reenvia(self):
        from django.core import mail

        from .models import EmailVerification

        EmailVerification.objects.create(user=self.user, is_verified=True)

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(self.URL, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)


class EmailVerifiedInPayloadTests(AuthTestCase):
    """email_verified en el login (auth_response) y en GET /api/v1/auth/me/,
    en los tres casos de la regla "sin fila = verificado"."""

    def _login(self, email):
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": email, "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp

    def test_sin_fila_devuelve_email_verified_true(self):
        user = User.objects.create_user(
            username="payload_sinfila@example.com",
            email="payload_sinfila@example.com",
            password=STRONG_PASSWORD,
        )
        resp = self._login(user.email)
        self.assertTrue(resp.data["user"]["email_verified"])

        self.client.force_authenticate(user=user)
        me_resp = self.client.get("/api/v1/auth/me/")
        self.assertTrue(me_resp.data["email_verified"])

    def test_verificado_devuelve_email_verified_true(self):
        from .models import EmailVerification

        user = User.objects.create_user(
            username="payload_verificado@example.com",
            email="payload_verificado@example.com",
            password=STRONG_PASSWORD,
        )
        EmailVerification.objects.create(user=user, is_verified=True)

        resp = self._login(user.email)
        self.assertTrue(resp.data["user"]["email_verified"])

        self.client.force_authenticate(user=user)
        me_resp = self.client.get("/api/v1/auth/me/")
        self.assertTrue(me_resp.data["email_verified"])

    def test_pendiente_devuelve_email_verified_false(self):
        from .models import EmailVerification

        user = User.objects.create_user(
            username="payload_pendiente@example.com",
            email="payload_pendiente@example.com",
            password=STRONG_PASSWORD,
        )
        EmailVerification.objects.create(user=user, is_verified=False)

        resp = self._login(user.email)
        self.assertFalse(resp.data["user"]["email_verified"])

        self.client.force_authenticate(user=user)
        me_resp = self.client.get("/api/v1/auth/me/")
        self.assertFalse(me_resp.data["email_verified"])


class AuditTrailTests(AuthTestCase):
    """Auditoría (apps.audit): login exitoso/fallido/lockout y CRUD de
    usuarios dejan su AuditLog con la acción correcta."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group

        from .models import GroupRolePermission, RolePermission

        for name in ("admin", "designer", "operator", "subscriber"):
            Group.objects.get_or_create(name=name)

        self.admin_user = User.objects.create_user(
            username="audit_crud_admin@example.com",
            email="audit_crud_admin@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.admin_user.groups.add(Group.objects.get(name="admin"))

        self.target_user = User.objects.create_user(
            username="audit_target@example.com",
            email="audit_target@example.com",
            password=STRONG_PASSWORD,
        )
        self.target_user.groups.add(Group.objects.get(name="subscriber"))

    def _login(self, email, password):
        return self.client.post(
            "/api/v1/auth/login/", {"email": email, "password": password}, format="json"
        )

    def test_login_exitoso_deja_auditlog(self):
        from apps.audit.models import AuditLog

        resp = self._login(self.target_user.email, STRONG_PASSWORD)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        log = AuditLog.objects.filter(action="auth.login_success").latest("created_at")
        self.assertEqual(log.actor_id, self.target_user.id)
        self.assertEqual(log.category, "auth")

    def test_login_fallido_deja_auditlog(self):
        from apps.audit.models import AuditLog

        resp = self._login(self.target_user.email, "mal")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

        log = AuditLog.objects.filter(action="auth.login_failed").latest("created_at")
        self.assertIsNone(log.actor_id)
        self.assertEqual(log.actor_email, self.target_user.email)

    def test_lockout_deja_auditlog(self):
        from apps.audit.models import AuditLog

        for _ in range(3):
            self._login(self.target_user.email, "mal")

        log = AuditLog.objects.filter(action="auth.lockout").latest("created_at")
        self.assertEqual(log.target_repr, self.target_user.email)

    def test_crear_usuario_deja_auditlog_sin_password(self):
        from apps.audit.models import AuditLog

        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.post(
            "/api/v1/users/",
            {
                "full_name": "Nuevo Usuario",
                "email": "nuevo_crud@example.com",
                "role": "subscriber",
                "status": "active",
                "password": "Rotulos2026",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        log = AuditLog.objects.filter(action="user.create").latest("created_at")
        self.assertEqual(log.target_repr, "nuevo_crud@example.com")
        self.assertNotIn("password", log.changes)
        self.assertNotIn("Rotulos2026", str(log.changes))

    def test_editar_usuario_deja_auditlog_con_diff_sin_password(self):
        from apps.audit.models import AuditLog

        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.patch(
            f"/api/v1/users/{self.target_user.id}/",
            {"full_name": "Nombre Editado", "password": "OtraPass2026"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        log = AuditLog.objects.filter(action="user.update").latest("created_at")
        self.assertIn("first_name", log.changes)
        self.assertNotIn("password", log.changes)
        self.assertNotIn("OtraPass2026", str(log.changes))

    def test_desactivar_usuario_deja_auditlog(self):
        from apps.audit.models import AuditLog

        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.patch(
            f"/api/v1/users/{self.target_user.id}/", {"status": "inactive"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        log = AuditLog.objects.filter(action="user.deactivate").latest("created_at")
        self.assertEqual(log.changes["is_active"], {"from": True, "to": False})

    def test_cambiar_rol_deja_auditlog_role_change(self):
        from apps.audit.models import AuditLog

        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.patch(
            f"/api/v1/users/{self.target_user.id}/", {"role": "designer"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        log = AuditLog.objects.filter(action="user.role_change").latest("created_at")
        self.assertEqual(log.changes["role"], {"from": "subscriber", "to": "designer"})


class SupportInboxTests(AuthTestCase):
    """Bandeja de soporte del admin: permisos, visibilidad propia vs. admin
    y el efecto de responder (status/response/handled_by + auditoría)."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group

        for name in ("admin", "designer", "operator", "subscriber"):
            Group.objects.get_or_create(name=name)

        self.admin_user = User.objects.create_user(
            username="support_admin@example.com",
            email="support_admin@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.admin_user.groups.add(Group.objects.get(name="admin"))

        self.user_a = User.objects.create_user(
            username="support_user_a@example.com",
            email="support_user_a@example.com",
            password=STRONG_PASSWORD,
        )
        self.user_a.groups.add(Group.objects.get(name="subscriber"))

        self.user_b = User.objects.create_user(
            username="support_user_b@example.com",
            email="support_user_b@example.com",
            password=STRONG_PASSWORD,
        )
        self.user_b.groups.add(Group.objects.get(name="subscriber"))

        from .models import SupportMessage

        self.message_a = SupportMessage.objects.create(
            user=self.user_a, subject="Ayuda A", message="Mensaje de A"
        )
        self.message_b = SupportMessage.objects.create(
            user=self.user_b, subject="Ayuda B", message="Mensaje de B"
        )

    def test_no_admin_recibe_403_en_bandeja_admin(self):
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get("/api/v1/support-messages/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_recibe_200_en_bandeja_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.get("/api/v1/support-messages/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["pagination"]["count"], 2)
        self.assertIn("counts", resp.data)

    def test_usuario_ve_solo_sus_propios_mensajes(self):
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get("/api/v1/auth/support/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        subjects = {m["subject"] for m in results}
        self.assertEqual(subjects, {"Ayuda A"})

    def test_admin_responde_setea_responded_at_y_handled_by_y_audita(self):
        from apps.audit.models import AuditLog

        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.patch(
            f"/api/v1/support-messages/{self.message_a.id}/",
            {"response": "Ya lo resolvimos."},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(resp.data["responded_at"])
        self.assertEqual(resp.data["handled_by"], self.admin_user.id)
        self.assertEqual(resp.data["status"], "resolved")

        self.assertTrue(
            AuditLog.objects.filter(action="support.reply", target_id=str(self.message_a.id)).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="support.status_change", target_id=str(self.message_a.id)
            ).exists()
        )


class DashboardAdminMenuTests(AuthTestCase):
    """El menú del dashboard incluye audit/support_inbox solo para admin."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group

        for name in ("admin", "designer", "operator", "subscriber"):
            Group.objects.get_or_create(name=name)

        self.admin_user = User.objects.create_user(
            username="dash_admin@example.com",
            email="dash_admin@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.admin_user.groups.add(Group.objects.get(name="admin"))

        self.subscriber_user = User.objects.create_user(
            username="dash_subscriber@example.com",
            email="dash_subscriber@example.com",
            password=STRONG_PASSWORD,
        )
        self.subscriber_user.groups.add(Group.objects.get(name="subscriber"))

    def test_admin_ve_audit_y_support_inbox(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.get("/api/v1/auth/users/me/dashboard/")
        keys = {item["key"] for item in resp.data["menu"]}
        self.assertIn("audit", keys)
        self.assertIn("support_inbox", keys)

    def test_subscriber_no_ve_audit_ni_support_inbox(self):
        self.client.force_authenticate(user=self.subscriber_user)
        resp = self.client.get("/api/v1/auth/users/me/dashboard/")
        keys = {item["key"] for item in resp.data["menu"]}
        self.assertNotIn("audit", keys)
        self.assertNotIn("support_inbox", keys)
