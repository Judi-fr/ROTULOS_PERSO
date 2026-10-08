"""Helpers de los tests de integraciones que usan todas las plataformas."""

from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


def auth_headers_for(user):
    token = RefreshToken.for_user(user)
    return {"HTTP_AUTHORIZATION": f"Bearer {token.access_token}"}


def fake_response(status_code=200, json_data=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {} if json_data is None else json_data
    return response


def make_user(email):
    user = User.objects.create_user(username=email, email=email, password="Clave123!")
    group, _ = Group.objects.get_or_create(name="subscriber")
    user.groups.add(group)
    return user
