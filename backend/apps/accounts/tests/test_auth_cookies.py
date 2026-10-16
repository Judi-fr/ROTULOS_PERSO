"""Tokens en cookies HttpOnly para el frontend web (T#364/T#366,
``auth_cookies.py``). El modo con ``Authorization: Bearer`` y tokens en el
cuerpo sigue igual y lo cubren los demás tests."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()
PASSWORD = "Segura123"
COOKIE_MODE = {"HTTP_X_AUTH_MODE": "cookie"}
ACCESS, REFRESH = "rotulos_access", "rotulos_refresh"


@override_settings(AUTH_COOKIE_SECURE=True)
class AuthCookieTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="galleta@example.com", email="galleta@example.com", password=PASSWORD)

    def login(self, **headers):
        return self.client.post(
            "/api/v1/auth/login/", {"email": "galleta@example.com", "password": PASSWORD}, format="json", **headers
        )

    def test_login_in_cookie_mode_sets_cookies_and_hides_tokens(self):
        response = self.login(**COOKIE_MODE)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        self.assertEqual(response.data["user"]["email"], "galleta@example.com")

        access, refresh = response.cookies[ACCESS], response.cookies[REFRESH]
        for cookie in (access, refresh):
            self.assertTrue(cookie["httponly"])
            self.assertTrue(cookie["secure"])
            self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(access["path"], "/api/")
        self.assertEqual(refresh["path"], "/api/v1/auth/")
        self.assertEqual(access["max-age"], 15 * 60)
        self.assertEqual(AccessToken(access.value)["user_id"], str(self.user.pk))

    def test_login_without_header_keeps_tokens_in_body(self):
        response = self.login()
        self.assertIn("access", response.data)
        self.assertNotIn(ACCESS, response.cookies)

    def test_cookie_authenticates_reads_and_requires_header_to_write(self):
        self.login(**COOKIE_MODE)
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, status.HTTP_200_OK)

        # Una escritura sin el header propio puede venir de otro sitio: no.
        response = self.client.patch("/api/v1/auth/me/", {"first_name": "Ana"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.patch("/api/v1/auth/me/", {"first_name": "Ana"}, format="json", **COOKIE_MODE)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_without_cookie_or_with_a_bad_one_is_401(self):
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.cookies[ACCESS] = "no-es-un-jwt"
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_dead_access_cookie_does_not_block_login(self):
        self.client.cookies[ACCESS] = "no-es-un-jwt"
        self.assertEqual(self.login(**COOKIE_MODE).status_code, status.HTTP_200_OK)

    def test_refresh_from_cookie_rotates_both(self):
        old_refresh = self.login(**COOKIE_MODE).cookies[REFRESH].value
        response = self.client.post("/api/v1/auth/refresh/", {}, format="json", **COOKIE_MODE)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {})
        new_refresh = response.cookies[REFRESH].value
        self.assertNotEqual(new_refresh, old_refresh)
        self.assertTrue(response.cookies[ACCESS].value)
        # El viejo quedó revocado (rotación + blacklist de SIMPLE_JWT).
        self.assertTrue(BlacklistedToken.objects.filter(token__token=old_refresh).exists())

    def test_refresh_without_session_in_cookie_mode_clears_cookies(self):
        response = self.client.post("/api/v1/auth/refresh/", {}, format="json", **COOKIE_MODE)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.cookies[REFRESH].value, "")
        self.assertEqual(response.cookies[ACCESS].value, "")

    def test_refresh_with_body_still_works_as_before(self):
        refresh = self.login().data["refresh"]
        response = self.client.post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_logout_revokes_the_cookie_refresh_and_deletes_cookies(self):
        refresh = self.login(**COOKIE_MODE).cookies[REFRESH].value
        response = self.client.post("/api/v1/auth/logout/", {}, format="json", **COOKIE_MODE)
        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)
        self.assertEqual(response.cookies[ACCESS].value, "")
        self.assertEqual(response.cookies[REFRESH].value, "")
        self.assertTrue(BlacklistedToken.objects.filter(token__token=refresh).exists())

    def test_register_in_cookie_mode(self):
        response = self.client.post(
            "/api/v1/auth/register/", {"email": "nueva@example.com", "password": PASSWORD}, format="json", **COOKIE_MODE
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("access", response.data)
        self.assertIn(ACCESS, response.cookies)
