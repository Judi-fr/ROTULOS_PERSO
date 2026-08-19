from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class UserAdminApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin@example.com",
            email="admin@example.com",
            password="Pass123",
            first_name="Admin",
            last_name="User",
            is_staff=True,
            is_active=True,
        )
        self.normal_user = User.objects.create_user(
            username="normal@example.com",
            email="normal@example.com",
            password="Pass123",
            first_name="Normal",
            last_name="User",
            is_staff=False,
            is_active=True,
        )

    def authenticate_as_admin(self):
        token = str(RefreshToken.for_user(self.admin).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def authenticate_as_user(self):
        token = str(RefreshToken.for_user(self.normal_user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_admin_user_list_requires_staff(self):
        self.authenticate_as_user()
        url = reverse("user-admin-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_list_users(self):
        self.authenticate_as_admin()
        url = reverse("user-admin-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["summary"], {"total": 2, "active": 2, "inactive": 0})
        self.assertEqual(response.data["pagination"]["page_size"], 20)

    def test_admin_can_get_real_user_statistics(self):
        User.objects.create_user(
            username="inactive@example.com",
            email="inactive@example.com",
            password="Pass123",
            is_active=False,
        )
        self.authenticate_as_admin()

        response = self.client.get(reverse("user-admin-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"], {"total": 3, "active": 2, "inactive": 1})

    def test_admin_can_search_users_by_name_email_or_role(self):
        staff_user = User.objects.create_user(
            username="staff@example.com",
            email="staff@example.com",
            password="Pass123",
            first_name="Marina",
            is_staff=True,
        )
        self.authenticate_as_admin()
        url = reverse("user-admin-list")

        by_name = self.client.get(url, {"search": "Marina"})
        by_email = self.client.get(url, {"search": "normal@example"})
        by_role = self.client.get(url, {"search": "administrator"})

        self.assertEqual([user["id"] for user in by_name.data["results"]], [staff_user.id])
        self.assertEqual([user["id"] for user in by_email.data["results"]], [self.normal_user.id])
        self.assertIn(staff_user.id, [user["id"] for user in by_role.data["results"]])

    def test_status_and_role_filters_work_on_queryset(self):
        inactive_admin = User.objects.create_user(
            username="inactive-admin@example.com",
            email="inactive-admin@example.com",
            password="Pass123",
            first_name="Inactive",
            last_name="Admin",
            is_staff=True,
            is_active=False,
        )
        self.authenticate_as_admin()
        url = reverse("user-admin-list")

        by_status_active = self.client.get(url, {"status": "active"})
        by_status_inactive = self.client.get(url, {"status": "inactive"})
        by_status_all = self.client.get(url, {"status": "all"})
        by_role_admin = self.client.get(url, {"role": "admin"})
        by_role_user = self.client.get(url, {"role": "user"})
        by_status_and_search = self.client.get(url, {"status": "inactive", "search": "inactive"})

        self.assertEqual([user["id"] for user in by_status_active.data["results"]], [self.admin.id, self.normal_user.id])
        self.assertEqual([user["id"] for user in by_status_inactive.data["results"]], [inactive_admin.id])
        self.assertEqual([user["id"] for user in by_status_all.data["results"]], [self.admin.id, self.normal_user.id, inactive_admin.id])
        self.assertEqual([user["id"] for user in by_role_admin.data["results"]], [self.admin.id, inactive_admin.id])
        self.assertEqual([user["id"] for user in by_role_user.data["results"]], [self.normal_user.id])
        self.assertEqual([user["id"] for user in by_status_and_search.data["results"]], [inactive_admin.id])

    def test_filters_combine_with_search_pagination_and_ordering(self):
        for index in range(21):
            User.objects.create_user(
                username=f"filtered-{index:02d}@example.com",
                email=f"filtered-{index:02d}@example.com",
                password="Pass123",
                first_name="Filtered",
                is_staff=False,
                is_active=True,
            )
        self.authenticate_as_admin()

        response = self.client.get(
            reverse("user-admin-list"),
            {
                "status": "active",
                "role": "user",
                "search": "filtered",
                "ordering": "-email",
                "page": 2,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["pagination"]["count"], 21)
        self.assertEqual(response.data["pagination"]["page"], 2)
        self.assertEqual([user["email"] for user in response.data["results"]], ["filtered-00@example.com"])

    def test_crud_endpoints_require_admin_permissions(self):
        self.client.credentials()
        list_url = reverse("user-admin-list")
        detail_url = reverse("user-admin-detail", kwargs={"pk": self.normal_user.pk})

        cases = [
            ("GET", list_url, None),
            ("GET", detail_url, None),
            ("POST", list_url, {"email": "new@example.com", "full_name": "New Person", "role": "admin", "status": "active"}),
            ("PUT", detail_url, {"email": "updated@example.com", "full_name": "Updated Person", "role": "user", "status": "active"}),
            ("PATCH", detail_url, {"status": "inactive"}),
            ("DELETE", detail_url, None),
            ("DELETE", f"{detail_url}?permanent=true", None),
        ]

        for method, url, payload in cases:
            if method == "GET":
                response = self.client.get(url)
            elif method == "POST":
                response = self.client.post(url, payload, format="json")
            elif method == "PUT":
                response = self.client.put(url, payload, format="json")
            elif method == "PATCH":
                response = self.client.patch(url, payload, format="json")
            else:
                response = self.client.delete(url)
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED, msg=f"{method} should require JWT")

        self.authenticate_as_user()
        for method, url, payload in cases:
            if method == "GET" and url == list_url:
                response = self.client.get(url)
            elif method == "GET":
                response = self.client.get(url)
            elif method == "POST":
                response = self.client.post(url, payload, format="json")
            elif method == "PUT":
                response = self.client.put(url, payload, format="json")
            elif method == "PATCH":
                response = self.client.patch(url, payload, format="json")
            else:
                response = self.client.delete(url)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, msg=f"{method} should be forbidden for non-admin")

        self.authenticate_as_admin()
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.post(list_url, {"email": "created@example.com", "full_name": "Created Person", "role": "admin", "status": "active"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        response = self.client.put(detail_url, {"email": "updated@example.com", "full_name": "Updated Person", "role": "user", "status": "active"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.patch(detail_url, {"status": "active"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        response = self.client.delete(f"{detail_url}?permanent=true")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_reactivation_via_patch_requires_admin_permissions(self):
        self.normal_user.is_active = False
        self.normal_user.save(update_fields=["is_active"])
        other_user = User.objects.create_user(
            username="active-normal@example.com",
            email="active-normal@example.com",
            password="Pass123",
            first_name="Active",
            last_name="Normal",
            is_staff=False,
            is_active=True,
        )
        detail_url = reverse("user-admin-detail", kwargs={"pk": self.normal_user.pk})
        payload = {"status": "active"}

        self.client.credentials()
        response = self.client.patch(detail_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        token = str(RefreshToken.for_user(other_user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.patch(detail_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate_as_admin()
        response = self.client.patch(detail_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.normal_user.refresh_from_db()
        self.assertTrue(self.normal_user.is_active)

    def test_delete_user_soft_deletes_instead_of_hard_delete(self):
        self.authenticate_as_admin()
        url = reverse("user-admin-detail", kwargs={"pk": self.normal_user.pk})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.normal_user.refresh_from_db()
        self.assertFalse(self.normal_user.is_active)
        self.assertTrue(User.objects.filter(pk=self.normal_user.pk).exists())

    def test_admin_can_permanently_delete_user(self):
        self.authenticate_as_admin()
        url = f'{reverse("user-admin-detail", kwargs={"pk": self.normal_user.pk})}?permanent=true'

        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.normal_user.pk).exists())

    def test_admin_create_transforms_form_fields_in_the_backend(self):
        self.authenticate_as_admin()
        response = self.client.post(
            reverse("user-admin-list"),
            {
                "email": "new@example.com",
                "full_name": "New Person",
                "role": "admin",
                "status": "inactive",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["display_name"], "New Person")
        self.assertEqual(response.data["role_key"], "admin")
        self.assertEqual(response.data["status_key"], "inactive")
        user = User.objects.get(email="new@example.com")
        self.assertEqual(user.username, "new@example.com")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_active)


class ProfileApiTests(APITestCase):
    """Pruebas del perfil propio (/users/me/) y seguridad de usuarios normales."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin@example.com",
            email="admin@example.com",
            password="StrongPass1",
            first_name="Admin",
            last_name="User",
            is_staff=True,
            is_active=True,
        )
        self.normal_user = User.objects.create_user(
            username="normal@example.com",
            email="normal@example.com",
            password="StrongPass1",
            first_name="Normal",
            last_name="User",
            is_staff=False,
            is_active=True,
        )

    def auth(self, user):
        token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_authenticated_user_can_get_own_profile(self):
        self.auth(self.normal_user)
        response = self.client.get(reverse("me"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "normal@example.com")
        self.assertEqual(response.data["id"], self.normal_user.id)

    def test_user_can_modify_own_name_and_email(self):
        self.auth(self.normal_user)
        response = self.client.patch(
            reverse("me"),
            {"full_name": "Nuevo Nombre", "email": "nuevo@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.normal_user.refresh_from_db()
        self.assertEqual(self.normal_user.email, "nuevo@example.com")
        self.assertEqual(self.normal_user.first_name, "Nuevo")
        self.assertEqual(self.normal_user.last_name, "Nombre")

    def test_user_cannot_change_own_role(self):
        self.auth(self.normal_user)
        response = self.client.patch(
            reverse("me"),
            {"role": "admin", "is_staff": True, "status": "active"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.normal_user.refresh_from_db()
        self.assertFalse(self.normal_user.is_staff)
        # El rol efectivo no debe haber cambiado a admin.
        from .serializers import get_user_role
        self.assertEqual(get_user_role(self.normal_user), "subscriber")

    def test_user_cannot_change_own_status(self):
        self.auth(self.normal_user)
        response = self.client.patch(
            reverse("me"),
            {"status": "inactive", "is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.normal_user.refresh_from_db()
        self.assertTrue(self.normal_user.is_active)

    def test_normal_user_cannot_use_admin_list(self):
        self.auth(self.normal_user)
        response = self.client.get(reverse("user-admin-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_normal_user_cannot_modify_another_user(self):
        self.auth(self.normal_user)
        url = reverse("user-admin-detail", kwargs={"pk": self.admin.pk})
        response = self.client.patch(
            url, {"email": "hacked@example.com", "full_name": "Hacked"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_can_change_own_password(self):
        self.auth(self.normal_user)
        response = self.client.patch(
            reverse("me"),
            {
                "current_password": "StrongPass1",
                "new_password": "NewStrong1",
                "confirm_new_password": "NewStrong1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.normal_user.refresh_from_db()
        self.assertTrue(self.normal_user.check_password("NewStrong1"))

    def test_wrong_current_password_rejected(self):
        self.auth(self.normal_user)
        response = self.client.patch(
            reverse("me"),
            {
                "current_password": "WrongPass1",
                "new_password": "NewStrong1",
                "confirm_new_password": "NewStrong1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mismatched_new_password_rejected(self):
        self.auth(self.normal_user)
        response = self.client.patch(
            reverse("me"),
            {
                "current_password": "StrongPass1",
                "new_password": "NewStrong1",
                "confirm_new_password": "Different1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unauthenticated_gets_401(self):
        self.client.credentials()
        response = self.client.get(reverse("me"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        response2 = self.client.get(reverse("user-admin-list"))
        self.assertEqual(response2.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_can_still_list_users(self):
        self.auth(self.admin)
        response = self.client.get(reverse("user-admin-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data["results"]), 2)
