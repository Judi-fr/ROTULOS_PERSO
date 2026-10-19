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

from .permissions_map import get_effective_role, user_has_permission


class DashboardView(APIView):
    """GET ``/api/v1/auth/users/me/dashboard/``

    No recibe ningún id por parámetro: siempre responde sobre
    ``request.user``, así que un usuario nunca puede pedir el dashboard (ni
    filtrar datos) de otra cuenta por esta vía.

    El ítem "users" (gestión de usuarios) solo se agrega al menú si el rol
    efectivo es ``admin``. Las secciones que todavía no tienen pantalla
    propia (documents/processing) se devuelven con ``enabled: False`` y sin
    URL en vez de omitirse o inventar una ruta que no existe.
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
                    "url": "auditoria.html",
                    "enabled": True,
                }
            )
            menu.append(
                {
                    "key": "support_inbox",
                    "label": "Mensajes de soporte",
                    "url": "soporte_admin.html",
                    "enabled": True,
                }
            )
            menu.append(
                {
                    "key": "system_status",
                    "label": "Estado del sistema",
                    "url": "estado_sistema.html",
                    "enabled": True,
                }
            )
            # Integraciones (claves de API y webhooks) no va en el menú: se
            # entra desde tiendas.html, botón "Conexión mediante API".

        # Carga operativa de pedidos (stories 20-22): alta manual + importación
        # CSV/Excel + plantillas de mapeo. Va a admin y operator (mismos roles
        # que "orders.create_manual"/"orders.import", ver permissions_map),
        # NO a designer/subscriber -- ellos solo tienen el self-service de
        # "orders"/"addresses" ya listado más abajo.
        if is_admin or user_has_permission(user, "orders.create_manual"):
            menu.append(
                {
                    "key": "orders_ingestion",
                    "label": "Carga e importación de pedidos",
                    "url": "importar.html",
                    "enabled": True,
                }
            )

        # Direcciones y pedidos: self-service, disponible para los cuatro
        # roles (ver apps.orders y los permisos orders.*/addresses.manage).
        # "Direcciones guardadas" reusa la misma pantalla (pedidos.html ya
        # tiene su propia sección de direcciones): no hace falta una página
        # nueva para lo que ya es un CRUD completo ahí.
        # Tiendas online conectadas (apps.integrations): mismo permiso que
        # exige la API para conectarlas. Desde los comercios con equipo, cada
        # entrada depende del acceso que el dueño le dio a la persona (los
        # permisos ya cruzan rol y equipo, ver permissions_map).
        if user_has_permission(user, "stores.manage"):
            menu.append(
                {"key": "stores", "label": "Tiendas conectadas", "url": "tiendas.html", "enabled": True}
            )
            # Los rótulos que la tienda pidió desde SU panel: solo lectura,
            # para ver por qué falló uno (ver apps.integrations.providers.tiendanube.labels).
            menu.append(
                {
                    "key": "store_labels",
                    "label": "Rótulos de la tienda",
                    "url": "rotulos_tienda.html",
                    "enabled": True,
                }
            )
        # La tabla CP -> precio con la que cotizamos el envío en el
        # checkout de esa tienda (ver apps.integrations.shipping_rates).
        if user_has_permission(user, "carriers.manage"):
            menu.append(
                {
                    "key": "shipping_rates",
                    "label": "Tarifas de envío",
                    "url": "tarifas_envio.html",
                    "enabled": True,
                }
            )
        if user_has_permission(user, "orders.view"):
            menu.append(
                {"key": "orders", "label": "Mis pedidos", "url": "pedidos.html", "enabled": True}
            )
        # Andreani (apps.carriers) no tiene botones propios: se despacha desde
        # Mis pedidos, y su cuenta y su precio en el checkout se abren desde ahí.
        # Imprimir los rótulos de varios pedidos de una (apps.labels batch):
        # mismo permiso que exige LabelBatchView.
        if user_has_permission(user, "labels.batch"):
            menu.append(
                {
                    "key": "labels_print",
                    "label": "Imprimir rótulos",
                    "url": "imprimir_rotulos.html",
                    "enabled": True,
                }
            )
            # Lo mandado directo a una impresora (apps.printing). Las
            # impresoras se configuran desde ahí y desde Imprimir rótulos.
            menu.append(
                {
                    "key": "print_queue",
                    "label": "Cola de impresión",
                    "url": "cola_impresion.html",
                    "enabled": True,
                }
            )
        # Acciones masivas sobre pedidos propios (apps.orders.bulk_views),
        # con el mismo permiso que exige cada endpoint: la planilla de retiro
        # y la exportación solo leen; cargar seguimientos y cambiar estados
        # despachan, como POST /orders/<id>/ship/.
        # La planilla de retiro no tiene botón propio: se arma en Mis pedidos
        # con los pedidos elegidos (assets/js/pedidos/planilla.js).
        if user_has_permission(user, "orders.dispatch"):
            # Cargar seguimientos tampoco: es un botón de Mis pedidos
            # (assets/js/pedidos/seguimientos.js).
            menu.append(
                {
                    "key": "orders_bulk_status",
                    "label": "Cambiar estado de pedidos",
                    "url": "estado_pedidos.html",
                    "enabled": True,
                }
            )
        if user_has_permission(user, "orders.export"):
            menu.append(
                {
                    "key": "orders_export",
                    "label": "Exportar pedidos",
                    "url": "exportar_pedidos.html",
                    "enabled": True,
                }
            )
        # Las direcciones NO son una entrada aparte: pedidos.html ya tiene
        # su propia sección con el CRUD completo, y listarlas también acá
        # era ofrecer dos puertas al mismo lugar.
        if user_has_permission(user, "labels.view"):
            menu.extend(
                [
                    {"key": "labels", "label": "Mis rótulos", "url": "mis_rotulos.html", "enabled": True},
                    {
                        "key": "templates",
                        "label": "Plantillas",
                        "url": "mis_plantillas.html",
                        "enabled": True,
                    },
                ]
            )
        if user_has_permission(user, "documents.view"):
            menu.append(
                {
                    "key": "documents",
                    "label": "Mis documentos",
                    "url": "documentos.html",
                    "enabled": True,
                }
            )
        # Leer un rótulo de papel con el modelo y proponer la plantilla
        # (apps.processing). Se llamaba "Generar rótulo", que describía otra
        # cosa —generar rótulos ya se hace en mis_rotulos.html— y encima
        # estaba deshabilitado teniendo el backend listo.
        if user_has_permission(user, "processing.import"):
            menu.append(
                {
                    "key": "processing",
                    "label": "Importar rótulo desde una foto",
                    "url": "importar_rotulo.html",
                    "enabled": True,
                }
            )
        # Editor de los diseños por elementos (ElementLayout): lo que produce
        # la importación se puede corregir a mano acá.
        if user_has_permission(user, "plantillas.edit"):
            menu.append(
                {
                    "key": "element_layouts",
                    "label": "Editor de diseños",
                    "url": "editor_layout.html",
                    "enabled": True,
                }
            )
        # El equipo del comercio y lo que hizo cada uno (apps/accounts/teams.py).
        if user_has_permission(user, "team.manage"):
            menu.append({"key": "team", "label": "Mi equipo", "url": "equipo.html", "enabled": True})
            menu.append(
                {
                    "key": "team_activity",
                    "label": "Actividad del equipo",
                    "url": "actividad_equipo.html",
                    "enabled": True,
                }
            )
        menu.extend(
            [
                {"key": "profile", "label": "Mi perfil", "url": "perfil.html", "enabled": True},
                # Datos del negocio (AccountSettings): fiscales, remitente y
                # logo por defecto, preferencias.
                {"key": "account_settings", "label": "Datos de la cuenta", "url": "organizacion.html", "enabled": True},
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
