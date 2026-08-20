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
    """Los tres roles del proyecto se siembran vía migración de datos
    (accounts.0001_seed_roles) y deben existir como Groups."""

    def test_los_tres_roles_existen(self):
        from django.contrib.auth.models import Group

        nombres = set(Group.objects.values_list("name", flat=True))
        self.assertTrue(
            {"administradores", "diseñadores", "operadores"}.issubset(nombres)
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
        "users.me.view",
        "users.me.edit",
        "users.me.change_password",
    }

    ME_VIEW_PERMISSIONS = {
        "users.me.view",
        "users.me.edit",
        "users.me.change_password",
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
        self.assertEqual(operator_perm, self.ME_VIEW_PERMISSIONS)
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

        self.assertEqual(self.RolePermission.objects.count(), 8)
        self.assertEqual(
            self.GroupRolePermission.objects.count(),
            # admin tiene 8 + 3 roles con 3 cada uno = 8 + 9 = 17
            8 + 3 * 3,
        )

    # --- Endpoints autenticados (admin) --------------------------------------

    def test_get_roles_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.get("/api/v1/auth/roles/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 4)
        names = [r["name"] for r in resp.data]
        self.assertEqual(names, [self.ADMIN, self.DESIGNER, self.OPERATOR, self.SUBSCRIBER])

    def test_get_permissions_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.get("/api/v1/auth/permissions/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 8)
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
             "groups": ["diseñadores", "operadores"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        creado = User.objects.get(email="conrol@example.com")
        self.assertEqual(
            set(creado.groups.values_list("name", flat=True)),
            {"diseñadores", "operadores"},
        )
        # La respuesta devuelve los roles por nombre.
        self.assertEqual(set(resp.data["groups"]), {"diseñadores", "operadores"})

    def test_admin_edita_roles(self):
        self.client.force_authenticate(user=self.admin)
        self.normal.groups.add(Group.objects.get(name="operadores"))
        # Reemplaza los roles por uno solo.
        resp = self.client.patch(
            f"{self.URL}{self.normal.id}/",
            {"groups": ["administradores"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(self.normal.groups.values_list("name", flat=True)),
            {"administradores"},
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
