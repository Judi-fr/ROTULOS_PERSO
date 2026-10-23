"""El panel agrupado (2026-10-20, pedido del usuario: "hay muchos botones y
se terminó creando un menú larguísimo").

En vez de un botón por pantalla, seis secciones (siete para el admin). Cada
una abre su pantalla principal y lleva adentro, como links, las funciones
secundarias que esa pantalla también muestra arriba. Una sección o un link
aparece solo si la persona tiene el permiso (que ya cruza rol y accesos del
equipo, ver ``permissions_map.user_has_permission``). El ``menu`` plano de
``DashboardView`` se sigue mandando para lo que todavía lo lea.

``onboarding``: los primeros pasos de un comercio nuevo, solo para el dueño.
"""

from __future__ import annotations

from .permissions_map import get_effective_role, user_has_permission


def _links(user, candidates):
    return [{"label": label, "url": url} for label, url, permission in candidates if permission is None or user_has_permission(user, permission)]


def build_sections(user):
    can = lambda permission: user_has_permission(user, permission)  # noqa: E731
    sections = []

    def add(key, label, icon, url, description, links):
        sections.append(
            {"key": key, "label": label, "icon": icon, "url": url, "description": description, "links": _links(user, links)}
        )

    if can("orders.view"):
        add(
            "orders", "Pedidos", "mdi mdi-package-variant", "pedidos.html",
            "Despachar, imprimir y seguir tus pedidos.",
            [
                ("Importar planilla", "importar.html", "orders.import"),
                ("Exportar", "exportar_pedidos.html", "orders.export"),
                ("Cambiar estado", "estado_pedidos.html", "orders.dispatch"),
            ],
        )
    if can("labels.batch") or can("documents.view"):
        add(
            "print", "Imprimir", "mdi mdi-printer",
            "imprimir_rotulos.html" if can("labels.batch") else "documentos.html",
            "Rótulos de muchos pedidos de una vez.",
            [
                ("Documentos", "documentos.html", "documents.view"),
                ("Cola de impresión", "cola_impresion.html", "labels.batch"),
                ("Impresoras", "impresoras.html", "printers.manage"),
            ],
        )
    if can("labels.view"):
        add(
            "design", "Diseño de rótulos", "mdi mdi-view-grid-plus-outline", "mis_plantillas.html",
            "Tus plantillas y cómo sale cada rótulo.",
            [
                ("Rótulos guardados", "mis_rotulos.html", "labels.view"),
                ("Desde una foto", "importar_rotulo.html", "processing.import"),
                ("Editor de diseños", "editor_layout.html", "plantillas.edit"),
            ],
        )
    if can("stores.manage") or can("carriers.manage"):
        add(
            "stores", "Tiendas y envíos", "mdi mdi-store-outline",
            "tiendas.html" if can("stores.manage") else "tarifas_envio.html",
            "Tus tiendas, tarifas y transportistas en el checkout.",
            [
                ("Tarifas", "tarifas_envio.html", "carriers.manage"),
                ("Checkout", "checkout_transportistas.html", "carriers.manage"),
                ("Rótulos de la tienda", "rotulos_tienda.html", "orders.view"),
            ],
        )
    add(
        "business", "Mi cuenta", "mdi mdi-account-group-outline", "organizacion.html",
        "Tu perfil, los datos del comercio y tu equipo.",
        # Perfil y datos del comercio son una sola página (organizacion.html,
        # 2026-10-21). Quien maneja el equipo ve sus herramientas; el resto, un
        # atajo a cada parte de la página (así la tarjeta no queda vacía).
        [
            ("Equipo", "equipo.html", "team.manage"),
            ("Perfiles del equipo", "equipo.html#perfiles", "team.manage"),
            ("Actividad", "equipo.html#actividad", "team.manage"),
        ]
        if can("team.manage")
        else [
            ("Tu perfil", "organizacion.html#perfil", None),
            ("Datos del comercio", "organizacion.html#comercio", None),
            ("Tu actividad", "mi_actividad.html", "users.me.view"),
        ],
    )
    add(
        "help", "Ayuda", "mdi mdi-lifebuoy", "ayuda.html", "Preguntas frecuentes, consultas a soporte y avisos.",
        [
            ("Preguntas frecuentes", "ayuda.html#preguntas", None),
            ("Escribir a soporte", "ayuda.html#nuevo-mensaje", "support.create"),
            ("Avisos", "notificaciones.html", None),
        ],
    )
    if get_effective_role(user) == "admin":
        add(
            "admin", "Administración", "mdi mdi-shield-account-outline", "gestionuser.html",
            "Usuarios, roles, auditoría, soporte y estado del sistema.",
            [
                ("Auditoría", "auditoria.html", "audit.view"),
                ("Soporte", "soporte_admin.html", "support.view_all"),
                ("Estado del sistema", "estado_sistema.html", "monitoring.view"),
            ],
        )
    return sections


def onboarding(user):
    """``{"steps": [...]}`` para el dueño del comercio actual; ``None`` para
    un empleado, el admin o quien no tiene comercio (la guía es de quien arma
    el comercio)."""
    from apps.carriers.models import CarrierAccount
    from apps.integrations.models import StoreConnection
    from apps.labels.models import LabelTemplate

    from .models import AccountSettings, TeamMember
    from .teams import membership

    member = membership(user)
    if get_effective_role(user) == "admin" or member is None or member.role != TeamMember.Role.OWNER:
        return None
    # Los datos son del comercio (su usuario de datos), no de la persona.
    user = member.team.data_user
    settings_row = AccountSettings.objects.filter(user=user).first()
    steps = [
        {
            "key": "store",
            "label": "Conectá tu tienda",
            "url": "tiendas.html",
            "done": StoreConnection.objects.filter(owner=user, status=StoreConnection.Status.ACTIVE).exists(),
        },
        {
            "key": "account",
            "label": "Completá los datos del comercio",
            "url": "organizacion.html",
            "done": bool(settings_row and (settings_row.sender_name or settings_row.legal_name)),
        },
        {
            "key": "template",
            "label": "Elegí la plantilla de tus rótulos",
            "url": "mis_plantillas.html",
            "done": LabelTemplate.objects.filter(owner=user, is_active=True).exists()
            or bool(settings_row and settings_row.default_template_id),
        },
        {
            "key": "carrier",
            "label": "Cargá tu transportista",
            "url": "pedidos.html",
            "done": CarrierAccount.objects.filter(owner=user, is_active=True).exists()
            or bool(settings_row and settings_row.default_carrier),
        },
    ]
    return {"steps": steps, "done": sum(step["done"] for step in steps), "total": len(steps)}
