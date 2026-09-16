"""Tests de autenticación: rotación de refresh, revocación (logout),
throttling de login, normalización de email y validación de contraseña.

Cubren el "Bloque 1" de endurecimiento: no dependen del frontend ni de
cookies (eso es una fase posterior); ejercitan el contrato JSON actual.

La segunda mitad del archivo (desde "Contratos que consume el frontend")
cubre lo que usan las pantallas de frontend/: la forma de las respuestas,
Google Sign-In, la recuperación de contraseña, la desactivación de usuarios
y CORS.
"""

import re
from unittest.mock import ANY, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
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


# ---------------------------------------------------------------------------
# Contratos que consume el frontend (frontend/assets/js/auth-api.js)
# ---------------------------------------------------------------------------
#
# Lo que sigue cubre lo que el frontend lee o da por hecho: la forma de las
# respuestas, los mensajes que muestra tal cual, y los flujos completos de
# Google y de recuperación de contraseña, que no tenían ningún test.

RESET_URL_TEST = "http://front.test/reset-password.html"
CLAVES_USER = {"id", "email", "first_name", "last_name", "picture"}


def _link_de_reset(cuerpo_del_mail):
    """Saca uid y token del link del correo, igual que reset-password.html
    los saca del query string al abrir el enlace."""
    match = re.search(r"\?uid=([^&\s]+)&token=(\S+)", cuerpo_del_mail)
    assert match, "el correo no trae un link con uid y token"
    return match.group(1), match.group(2)


class ContratoRespuestaTests(AuthTestCase):
    """Forma de las respuestas de autenticación que el frontend guarda o
    muestra sin transformar."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="contrato@example.com",
            email="contrato@example.com",
            password=STRONG_PASSWORD,
            first_name="Ana",
            last_name="Pérez",
        )

    def _login(self):
        return self.client.post(
            "/api/v1/auth/login/",
            {"email": "contrato@example.com", "password": STRONG_PASSWORD},
            format="json",
        )

    def test_login_devuelve_user_con_las_claves_que_guarda_el_front(self):
        # guardarSesion() hace localStorage.setItem("user", data.user): si una
        # clave cambia de nombre, la sesión se guarda rota sin ningún error.
        resp = self._login()
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(set(resp.data["user"]), CLAVES_USER)
        self.assertEqual(resp.data["user"]["email"], "contrato@example.com")
        # En el login manual no hay avatar: solo Google lo aporta.
        self.assertEqual(resp.data["user"]["picture"], "")

    def test_login_sin_campos_da_400_con_detail(self):
        # motivo() muestra `detail`: sin esa clave el usuario vería "Error 400."
        resp = self.client.post("/api/v1/auth/login/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", resp.data)

    def test_login_con_email_mal_formado_da_400(self):
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "sin-arroba", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", resp.data)

    def test_access_renovado_autentica_de_verdad(self):
        # Es exactamente lo que hace apiFetch() ante un 401: refresh y
        # reintento. El test de rotación ya verifica que el refresh devuelve un
        # token; este verifica que ese token abre las puertas.
        refresh = self._login().data["refresh"]
        renovado = self.client.post(
            "/api/v1/auth/refresh/", {"refresh": refresh}, format="json"
        )
        self.assertEqual(renovado.status_code, status.HTTP_200_OK)

        access = renovado.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["email"], "contrato@example.com")

    def test_access_invalido_da_401_y_no_403(self):
        # apiFetch() solo intenta renovar ante un 401. Si un token roto diera
        # 403, el front mostraría "sin permisos" en vez de renovar la sesión.
        self.client.credentials(HTTP_AUTHORIZATION="Bearer no.es.un.jwt")
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_informa_is_staff(self):
        # La respuesta del login no dice si el usuario es admin; /auth/me/ es
        # la única fuente que tiene el front para decidirlo.
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/api/v1/auth/me/")
        self.assertIn("is_staff", resp.data)
        self.assertFalse(resp.data["is_staff"])


class RegistroValidacionesTests(AuthTestCase):
    """Rechazos del registro que register.html muestra tal cual."""

    URL = "/api/v1/auth/register/"

    def test_email_duplicado_da_400_y_no_crea_otra_cuenta(self):
        User.objects.create_user(
            username="existe@example.com",
            email="existe@example.com",
            password=STRONG_PASSWORD,
        )
        # Con otra capitalización: la normalización tiene que detectarlo igual.
        resp = self.client.post(
            self.URL,
            {"email": "Existe@Example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["detail"], "Ya existe una cuenta con ese email.")
        self.assertEqual(
            User.objects.filter(username__iexact="existe@example.com").count(), 1
        )

    def test_email_mal_formado_da_400(self):
        resp = self.client.post(
            self.URL,
            {"email": "nada@", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", resp.data)

    def test_registro_guarda_nombre_y_devuelve_el_mismo_user_que_el_login(self):
        resp = self.client.post(
            self.URL,
            {"email": "conombre@example.com", "password": STRONG_PASSWORD,
             "first_name": "  Luz ", "last_name": "Díaz"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # register.html usa guardarSesion() igual que el login.
        self.assertEqual(set(resp.data["user"]), CLAVES_USER)
        self.assertEqual(resp.data["user"]["first_name"], "Luz")


@override_settings(GOOGLE_CLIENT_ID="cliente-de-prueba.apps.googleusercontent.com")
class GoogleAuthTests(AuthTestCase):
    """POST /api/v1/auth/google/. La verificación contra Google se simula: lo
    que se prueba es qué hace la vista con cada resultado posible."""

    URL = "/api/v1/auth/google/"
    VERIFICAR = "apps.accounts.views.id_token.verify_oauth2_token"

    CLAIMS = {
        "email": "Gus@Example.com",
        "email_verified": True,
        "given_name": "Gus",
        "family_name": "Gómez",
        "picture": "https://lh3.googleusercontent.com/avatar",
    }

    def _entrar(self, claims=None, **kwargs):
        with patch(self.VERIFICAR, return_value=claims or self.CLAIMS, **kwargs):
            return self.client.post(self.URL, {"credential": "tok"}, format="json")

    def test_sin_credential_da_400(self):
        resp = self.client.post(self.URL, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_token_invalido_da_401(self):
        with patch(self.VERIFICAR, side_effect=ValueError("firma inválida")):
            resp = self.client.post(self.URL, {"credential": "x"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_verifica_contra_nuestro_client_id(self):
        # Sin el audience correcto, un ID token emitido para OTRA aplicación
        # serviría para entrar a esta. Es el chequeo que no puede faltar.
        with patch(self.VERIFICAR, return_value=self.CLAIMS) as verificar:
            self.client.post(self.URL, {"credential": "tok"}, format="json")
        verificar.assert_called_once_with(
            "tok", ANY, "cliente-de-prueba.apps.googleusercontent.com"
        )

    def test_email_no_verificado_da_401_y_no_crea_cuenta(self):
        resp = self._entrar({**self.CLAIMS, "email_verified": False})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(User.objects.filter(username="gus@example.com").exists())

    def test_primer_ingreso_crea_cuenta_sin_contrasena(self):
        resp = self._entrar()
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)
        self.assertEqual(set(resp.data["user"]), CLAVES_USER)
        self.assertEqual(resp.data["user"]["picture"], self.CLAIMS["picture"])

        user = User.objects.get(username="gus@example.com")
        self.assertEqual(user.first_name, "Gus")
        # Sin contraseña usable: nadie puede entrar a esta cuenta por /login/.
        self.assertFalse(user.has_usable_password())

    def test_cuenta_manual_existente_no_se_duplica_ni_pierde_su_clave(self):
        # Quien se registró con email y después entra con Google tiene que caer
        # en la MISMA cuenta, y su contraseña tiene que seguir sirviendo.
        User.objects.create_user(
            username="gus@example.com",
            email="gus@example.com",
            password=STRONG_PASSWORD,
        )
        resp = self._entrar()
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            User.objects.filter(username__iexact="gus@example.com").count(), 1
        )
        self.assertTrue(
            User.objects.get(username="gus@example.com").has_usable_password()
        )

    @override_settings(GOOGLE_CLIENT_ID="")
    def test_sin_client_id_configurado_da_500(self):
        resp = self.client.post(self.URL, {"credential": "x"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)


@override_settings(PASSWORD_RESET_URL=RESET_URL_TEST)
class PasswordResetRequestTests(AuthTestCase):
    """POST /api/v1/auth/password-reset/ (recuperar-password.html).

    El test runner de Django reemplaza EMAIL_BACKEND por el de memoria, así
    que ningún test manda un correo real aunque el .env tenga SMTP.
    """

    URL = "/api/v1/auth/password-reset/"

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="olvido@example.com",
            email="olvido@example.com",
            password=STRONG_PASSWORD,
        )

    def test_email_existente_manda_un_correo_con_el_link(self):
        resp = self.client.post(self.URL, {"email": "olvido@example.com"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["olvido@example.com"])
        # El link apunta a la página del front configurada, con uid y token.
        self.assertIn(RESET_URL_TEST + "?uid=", mail.outbox[0].body)
        _link_de_reset(mail.outbox[0].body)

    def test_email_inexistente_responde_igual_y_no_manda_nada(self):
        # Misma respuesta exacta para no revelar qué emails están registrados.
        # recuperar-password.html muestra este `detail` tal cual: si el texto
        # difiriera entre ambos casos, la pantalla filtraría la información.
        existe = self.client.post(self.URL, {"email": "olvido@example.com"}, format="json")
        mail.outbox.clear()
        no_existe = self.client.post(self.URL, {"email": "nadie@example.com"}, format="json")

        self.assertEqual(no_existe.status_code, status.HTTP_200_OK)
        self.assertEqual(no_existe.data, existe.data)
        self.assertEqual(len(mail.outbox), 0)

    def test_usuario_inactivo_no_recibe_correo(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        resp = self.client.post(self.URL, {"email": "olvido@example.com"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_sin_email_da_400(self):
        resp = self.client.post(self.URL, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(PASSWORD_RESET_URL=RESET_URL_TEST)
class PasswordResetConfirmTests(AuthTestCase):
    """POST /api/v1/auth/password-reset/confirm/ (reset-password.html)."""

    URL = "/api/v1/auth/password-reset/confirm/"
    NUEVA = "Cambiada2026"
    LINK_INVALIDO = "El enlace de restablecimiento no es válido o expiró."

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="reset@example.com",
            email="reset@example.com",
            password=STRONG_PASSWORD,
        )
        self.uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.token = default_token_generator.make_token(self.user)

    def _confirmar(self, **cambios):
        cuerpo = {"uid": self.uid, "token": self.token, "new_password": self.NUEVA}
        cuerpo.update(cambios)
        return self.client.post(self.URL, cuerpo, format="json")

    def _login(self, password):
        return self.client.post(
            "/api/v1/auth/login/",
            {"email": "reset@example.com", "password": password},
            format="json",
        )

    def test_flujo_completo_desde_el_correo(self):
        # Pedir el link, sacarlo del correo y confirmarlo: el recorrido real
        # entre recuperar-password.html y reset-password.html.
        self.client.post(
            "/api/v1/auth/password-reset/", {"email": "reset@example.com"}, format="json"
        )
        uid, token = _link_de_reset(mail.outbox[0].body)

        resp = self.client.post(
            self.URL,
            {"uid": uid, "token": token, "new_password": self.NUEVA},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._login(self.NUEVA).status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._login(STRONG_PASSWORD).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_token_invalido_da_400_con_detail(self):
        resp = self._confirmar(token="token-inventado")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["detail"], self.LINK_INVALIDO)

    def test_uid_basura_da_el_mismo_error_que_token_invalido(self):
        # Mismo mensaje para uid y token: distinguirlos le diría a un atacante
        # qué ids de usuario existen.
        resp = self._confirmar(uid="no-es-base64")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["detail"], self.LINK_INVALIDO)

    def test_link_no_sirve_dos_veces(self):
        self.assertEqual(self._confirmar().status_code, status.HTTP_200_OK)
        # El token depende del hash de la contraseña: al cambiarla, se invalida.
        segundo = self._confirmar(new_password="OtraMas2026")
        self.assertEqual(segundo.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._login(self.NUEVA).status_code, status.HTTP_200_OK)

    def test_contrasena_debil_da_400_y_no_cambia_nada(self):
        resp = self._confirmar(new_password="debil")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", resp.data)
        self.assertEqual(self._login(STRONG_PASSWORD).status_code, status.HTTP_200_OK)

    def test_faltan_campos_da_400(self):
        resp = self.client.post(self.URL, {"uid": self.uid}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_revoca_las_sesiones_abiertas(self):
        # reset-password.html borra la sesión guardada porque el backend revoca
        # los refresh vigentes. Este test es el que sostiene ese borrado.
        refresh = self._login(STRONG_PASSWORD).data["refresh"]
        self.assertEqual(self._confirmar().status_code, status.HTTP_200_OK)

        resp = self.client.post(
            "/api/v1/auth/refresh/", {"refresh": refresh}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class DesactivacionTests(AuthTestCase):
    """Lo que significa el botón "Desactivar" de gestionuser.html, que manda
    PATCH {is_active: false}. Tiene que cortar el acceso, no solo cambiar un
    texto en la tabla."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            username="jefe@example.com",
            email="jefe@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.empleado = User.objects.create_user(
            username="empleado@example.com",
            email="empleado@example.com",
            password=STRONG_PASSWORD,
        )

    def _login_empleado(self):
        return self.client.post(
            "/api/v1/auth/login/",
            {"email": "empleado@example.com", "password": STRONG_PASSWORD},
            format="json",
        )

    def _desactivar(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f"/api/v1/users/{self.empleado.id}/", {"is_active": False}, format="json"
        )
        self.client.force_authenticate(user=None)
        return resp

    def test_patch_is_active_desactiva(self):
        self.assertEqual(self._desactivar().status_code, status.HTTP_200_OK)
        self.empleado.refresh_from_db()
        self.assertFalse(self.empleado.is_active)

    def test_desactivado_no_puede_loguearse(self):
        self._desactivar()
        self.assertEqual(
            self._login_empleado().status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_access_emitido_antes_deja_de_servir(self):
        # El caso que importa: alguien ya logueado a quien se desactiva. Si su
        # access siguiera valiendo hasta vencer, "Desactivar" no frenaría nada
        # durante esos minutos.
        access = self._login_empleado().data["access"]
        self._desactivar()

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class AltaDesdeElPanelTests(AuthTestCase):
    """El cuerpo exacto que arma el panel "Crear usuario" de gestionuser.html."""

    URL = "/api/v1/users/"

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            username="panel@example.com",
            email="panel@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        self.client.force_authenticate(user=self.admin)

    def test_rol_administradores_se_traduce_a_is_staff_sin_grupos(self):
        # El panel no asigna el Group "administradores": manda is_staff y una
        # lista de grupos vacía. Si el backend exigiera el grupo, los admins
        # creados desde la pantalla no tendrían permisos.
        resp = self.client.post(
            self.URL,
            {"email": "nuevoadmin@example.com", "password": STRONG_PASSWORD,
             "first_name": "Nuevo", "last_name": "Admin",
             "is_active": True, "is_staff": True, "groups": []},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        creado = User.objects.get(username="nuevoadmin@example.com")
        self.assertTrue(creado.is_staff)
        self.assertFalse(creado.groups.exists())

        # Y ese admin recién creado efectivamente puede usar la pantalla.
        self.client.force_authenticate(user=creado)
        self.assertEqual(self.client.get(self.URL).status_code, status.HTTP_200_OK)

    def test_alta_con_estado_inactivo_y_rol(self):
        resp = self.client.post(
            self.URL,
            {"email": "pausado@example.com", "password": STRONG_PASSWORD,
             "first_name": "Pausado", "last_name": "",
             "is_active": False, "is_staff": False, "groups": ["operadores"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        creado = User.objects.get(username="pausado@example.com")
        self.assertFalse(creado.is_active)
        self.assertEqual(
            list(creado.groups.values_list("name", flat=True)), ["operadores"]
        )

    def test_listado_trae_los_campos_que_pinta_la_tabla(self):
        # aFila() lee estos campos de cada usuario. Si falta uno, la fila sale
        # con "undefined" o la fecha como "Invalid Date".
        resp = self.client.get(self.URL)
        fila = resp.data["results"][0]
        for campo in ("id", "email", "first_name", "last_name",
                      "is_staff", "is_active", "groups", "date_joined"):
            self.assertIn(campo, fila)

    def test_error_de_alta_viene_por_campo(self):
        # El panel arma el aviso recorriendo {campo: [mensajes]} porque en el
        # alta los errores no vienen en `detail`.
        resp = self.client.post(
            self.URL, {"email": "sinclave@example.com"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", resp.data)


FRONT = "http://localhost:5173"


# dev.py pone CORS_ALLOW_ALL_ORIGINS=True, y los tests corren con dev: sin
# apagarlo acá, cualquier origen recibe "*" y estos tests no probarían nada.
# Se prueba la lista blanca, que es el mecanismo que rige en producción.
@override_settings(CORS_ALLOW_ALL_ORIGINS=False, CORS_ALLOWED_ORIGINS=[FRONT])
class CorsTests(AuthTestCase):
    """El frontend corre en otro origen (:5173) que la API (:8000). Sin estas
    cabeceras el navegador bloquea la respuesta aunque el backend responda
    bien, y el front solo ve "No se pudo conectar"."""

    def test_produccion_no_abre_cors_a_cualquier_origen(self):
        # El "permitir todo" de dev.py es cómodo en local, pero si alguien lo
        # mueve a base.py o lo copia en prod.py, cualquier sitio podría leer
        # las respuestas de la API desde el navegador de un usuario.
        from config.settings import prod

        self.assertFalse(getattr(prod, "CORS_ALLOW_ALL_ORIGINS", False))

    def _preflight(self, origen, metodo="POST", cabeceras="content-type"):
        return self.client.options(
            "/api/v1/auth/login/",
            HTTP_ORIGIN=origen,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD=metodo,
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS=cabeceras,
        )

    def test_preflight_del_front_esta_permitido(self):
        resp = self._preflight(FRONT)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Access-Control-Allow-Origin"], FRONT)

    def test_preflight_permite_la_cabecera_authorization(self):
        # apiFetch() manda Authorization en cada llamada privada: si el
        # preflight no la habilita, todas las pantallas con sesión fallan.
        resp = self._preflight(
            FRONT, metodo="GET", cabeceras="authorization, content-type"
        )
        permitidas = resp["Access-Control-Allow-Headers"].lower()
        self.assertIn("authorization", permitidas)
        self.assertIn("content-type", permitidas)

    def test_origen_ajeno_no_recibe_permiso(self):
        resp = self._preflight("http://sitio-ajeno.example")
        self.assertNotIn("Access-Control-Allow-Origin", resp)

    def test_expone_las_cabeceras_de_aviso_del_rotulo(self):
        # Sin exponerlas, el JS del front no puede leer qué campos faltaron o
        # se truncaron al imprimir, aunque el backend las mande.
        resp = self.client.get("/api/v1/health/", HTTP_ORIGIN=FRONT)
        expuestas = resp["Access-Control-Expose-Headers"]
        for cabecera in ("X-Rotulo-Faltantes", "X-Rotulo-Truncados", "X-Rotulo-Avisos"):
            self.assertIn(cabecera, expuestas)
