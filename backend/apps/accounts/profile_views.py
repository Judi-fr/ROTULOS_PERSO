"""Perfil del usuario autenticado (self-service).

Distinto del CRUD de admin (``management_views.py``): acá el usuario opera
sobre su PROPIA cuenta. Puede ver su perfil y editar solo su nombre y
apellido; el email (identidad/username) y los campos sensibles quedan fuera
de su alcance (ver ``ProfileSerializer``).

    GET   /api/v1/auth/me/   -> perfil del usuario autenticado
    PATCH /api/v1/auth/me/   -> edita first_name / last_name
"""

from rest_framework.generics import RetrieveUpdateAPIView

from .serializers import ProfileSerializer


class ProfileView(RetrieveUpdateAPIView):
    """GET/PATCH ``/api/v1/auth/me/`` sobre la cuenta del usuario del token.

    No hay ``<id>`` en la URL: ``get_object`` siempre devuelve
    ``request.user``, así que nadie puede ver o editar el perfil de otro por
    esta vía. Requiere autenticación (``IsAuthenticated`` es el permiso por
    defecto del proyecto).

    Solo se habilitan GET y PATCH: no se expone PUT para no invitar a un
    reemplazo total del recurso cuando lo único editable es el nombre.
    """

    serializer_class = ProfileSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user
