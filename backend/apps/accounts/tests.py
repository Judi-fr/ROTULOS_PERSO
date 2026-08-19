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
        # PageNumberPagination -> {count, next, previous, results}
        self.assertIn("results", resp.data)
        self.assertEqual(resp.data["count"], 2)

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

    def test_admin_elimina_usuario(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.delete(f"{self.URL}{self.normal.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.normal.pk).exists())

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
