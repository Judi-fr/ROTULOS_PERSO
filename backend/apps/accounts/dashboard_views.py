"""Dashboard (menú) del usuario autenticado.

Pantalla de aterrizaje para usuarios no administradores: la identidad sale
siempre de ``request.user`` (JWT) y el menú se arma en el backend según el
rol efectivo, para que el frontend no tenga que decidir qué ítems mostrar
por rol.

    GET /api/v1/auth/users/me/dashboard/
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions_map import get_effective_role


class DashboardView(APIView):
    """GET ``/api/v1/auth/users/me/dashboard/``

    No recibe ningún id por parámetro: siempre responde sobre
    ``request.user``, así que un usuario nunca puede pedir el dashboard (ni
    filtrar datos) de otra cuenta por esta vía.

    El ítem "users" (gestión de usuarios) solo se agrega al menú si el rol
    efectivo es ``admin``. Las secciones que todavía no tienen pantalla
    propia (labels/documents/processing) se devuelven con
    ``enabled: False`` y sin URL en vez de omitirse o inventar una ruta que
    no existe.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        role = get_effective_role(user)
        is_admin = role == "admin"

        full_name = " ".join(filter(None, [user.first_name, user.last_name])).strip()

        menu = []
        if is_admin:
            menu.append(
                {
                    "key": "users",
                    "label": "Gestión de usuarios",
                    "url": "gestionuser.html",
                    "enabled": True,
                }
            )
            menu.append(
                {
                    "key": "audit",
                    "label": "Registros de auditoría",
                    "url": "gestionuser.html#audit",
                    "enabled": True,
                }
            )
            menu.append(
                {
                    "key": "support_inbox",
                    "label": "Mensajes de soporte",
                    "url": "gestionuser.html#support",
                    "enabled": True,
                }
            )

        # Direcciones y pedidos: self-service, disponible para los cuatro
        # roles (ver apps.orders y los permisos orders.*/addresses.manage).
        # "Direcciones guardadas" reusa la misma pantalla (pedidos.html ya
        # tiene su propia sección de direcciones): no hace falta una página
        # nueva para lo que ya es un CRUD completo ahí.
        menu.append(
            {"key": "orders", "label": "Mis pedidos", "url": "pedidos.html", "enabled": True}
        )
        menu.append(
            {
                "key": "addresses",
                "label": "Direcciones guardadas",
                "url": "pedidos.html#addressesSection",
                "enabled": True,
            }
        )

        # Labels/documents/processing todavía no tienen pantalla propia (sus
        # apps backend exponen urlpatterns vacíos): se listan deshabilitados
        # en vez de omitirse o apuntar a una URL inventada.
        menu.extend(
            [
                {"key": "labels", "label": "Mis rótulos", "url": "", "enabled": False},
                {"key": "documents", "label": "Mis documentos", "url": "", "enabled": False},
                {"key": "processing", "label": "Generar rótulo", "url": "", "enabled": False},
                {"key": "profile", "label": "Mi perfil", "url": "perfil.html", "enabled": True},
                {"key": "support", "label": "Ayuda / Soporte", "url": "ayuda.html", "enabled": True},
            ]
        )

        return Response(
            {
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "username": user.username,
                    "full_name": full_name or user.email,
                    "role": role,
                    "is_admin": is_admin,
                },
                "menu": menu,
            }
        )
