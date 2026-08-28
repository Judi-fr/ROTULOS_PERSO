import csv
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.models import Group
from django.db.models import Count, Max, Q
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from .models import LoginLockout
from .pagination import UserAdminPagination
from .permissions_map import get_effective_role, user_has_permission
from .serializers import ROLE_CHOICES, UserAdminSerializer, ensure_role_groups

User = get_user_model()


def is_google_account_q():
    """``Q`` que identifica cuentas creadas por Google (password "unusable").

    Compartido por el filtro ``auth_method``, la exportación CSV y las
    métricas, para no duplicar la heurística en tres lugares (ver
    ``GoogleAuthView`` / ``_get_auth_method_filter``).
    """
    return Q(password__startswith=UNUSABLE_PASSWORD_PREFIX)


class UserAdminViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("id")
    serializer_class = UserAdminSerializer
    pagination_class = UserAdminPagination
    # La autenticación continúa siendo obligatoria; el permiso atómico se
    # verifica por acción para que la asignación en Roles tenga efecto real.
    permission_classes = [permissions.IsAuthenticated]

    def _require_permission(self, permission):
        if not user_has_permission(self.request.user, permission):
            raise PermissionDenied("No tenés permisos para realizar esta acción.")

    def _required_update_permissions(self, instance):
        """Determina permisos para editar y para cambios de estado."""
        required = set()
        data = self.request.data
        status_value = data.get("status", data.get("is_active"))
        if status_value is not None:
            is_active = (
                str(status_value).strip().lower() == "active"
                if isinstance(status_value, str)
                else bool(status_value)
            )
            if is_active != instance.is_active:
                required.add("users.reactivate" if is_active else "users.deactivate")

        non_status_fields = set(data.keys()) - {"status", "is_active"}
        if non_status_fields:
            required.add("users.edit")
        return required or {"users.edit"}

    def _get_status_filter(self):
        status_param = self.request.query_params.get("status", "").strip().lower()
        if status_param == "active":
            return Q(is_active=True)
        if status_param == "inactive":
            return Q(is_active=False)
        return Q()

    def _get_role_filter(self):
        role_param = self.request.query_params.get("role", "").strip().lower()
        if not role_param or role_param == "all":
            return Q()

        # Normalizar: "administrador" es alias de "admin"
        if role_param == "administrator":
            role_param = "admin"

        # "user" es alias legacy: usuarios normales sin rol admin/designer/operator
        if role_param == "user":
            special_roles = ["admin", "designer", "operator"]
            return Q(is_staff=False) & ~Q(groups__name__in=special_roles)

        if role_param in ROLE_CHOICES:
            ensure_role_groups()
            # Para admin: incluir usuarios con group Y usuarios staff efectivos
            # (sin group explícito, pero is_staff=True)
            if role_param == "admin":
                try:
                    group = Group.objects.get(name="admin")
                    return Q(groups=group) | Q(is_staff=True)
                except Group.DoesNotExist:
                    return Q(is_staff=True)

            # Para otros roles (designer, operator, subscriber): filtrar por group
            try:
                group = Group.objects.get(name=role_param)
                return Q(groups=group)
            except Group.DoesNotExist:
                return Q(pk__in=[])

        return Q()

    def _get_search_filter(self):
        search = self.request.query_params.get("search", "").strip()
        if not search:
            return Q()

        # Normalizar: "administrador" es alias de "admin" en búsquedas
        search_lower = search.lower()
        if search_lower == "administrator":
            search_lower = "admin"

        filters = (
            Q(email__icontains=search)
            | Q(username__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
        )

        # También buscar por rol: el término de búsqueda puede ser un nombre de rol
        role = search_lower
        if role in ROLE_CHOICES:
            ensure_role_groups()
            # Para admin: buscar usuarios con group y usuarios staff efectivos
            if role == "admin":
                try:
                    group = Group.objects.get(name="admin")
                    filters |= Q(groups=group) | Q(is_staff=True)
                except Group.DoesNotExist:
                    filters |= Q(is_staff=True)
            else:
                try:
                    group = Group.objects.get(name=role)
                    filters |= Q(groups=group)
                except Group.DoesNotExist:
                    pass

        return filters

    # --- Filtros de actividad (fechas / inactividad) ------------------------

    def _parse_date_param(self, param_name):
        """Parsea un query param de fecha (YYYY-MM-DD). None si no vino,
        400 (ValidationError) si el formato es inválido."""
        raw = self.request.query_params.get(param_name, "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError(
                {param_name: f"Formato de fecha inválido (usar YYYY-MM-DD): {raw!r}."}
            )

    def _get_date_range_filter(self, field, from_param, to_param):
        """Filtro de rango genérico sobre un DateTimeField, comparando por
        fecha (``__date``) para que 'to' incluya el día completo."""
        date_from = self._parse_date_param(from_param)
        date_to = self._parse_date_param(to_param)
        if date_from and date_to and date_from > date_to:
            raise ValidationError(
                {to_param: f"'{to_param}' no puede ser anterior a '{from_param}'."}
            )
        q = Q()
        if date_from:
            q &= Q(**{f"{field}__date__gte": date_from})
        if date_to:
            q &= Q(**{f"{field}__date__lte": date_to})
        return q

    def _get_date_joined_filter(self):
        return self._get_date_range_filter("date_joined", "date_joined_from", "date_joined_to")

    def _get_last_login_range_filter(self):
        return self._get_date_range_filter("last_login", "last_login_from", "last_login_to")

    def _get_never_logged_in_filter(self):
        if self.request.query_params.get("never_logged_in", "").strip().lower() == "true":
            return Q(last_login__isnull=True)
        return Q()

    def _get_inactive_days_filter(self):
        """``inactive_days=N``: sin login en los últimos N días. Incluye a
        quien nunca inició sesión (nunca tuvo un login "reciente")."""
        raw = self.request.query_params.get("inactive_days", "").strip()
        if not raw:
            return Q()
        try:
            days = int(raw)
        except ValueError:
            raise ValidationError(
                {"inactive_days": f"'inactive_days' debe ser un número entero: {raw!r}."}
            )
        if days < 0:
            raise ValidationError({"inactive_days": "'inactive_days' no puede ser negativo."})
        cutoff = timezone.now() - timedelta(days=days)
        return Q(last_login__lt=cutoff) | Q(last_login__isnull=True)

    # --- Filtros de seguridad (lockout) --------------------------------------

    def _get_locked_filter(self):
        """``locked=true/false``: independiente de ``status`` (Active/Inactive)."""
        raw = self.request.query_params.get("locked", "").strip().lower()
        is_locked_now = Q(login_lockout__locked_until__gt=timezone.now())
        if raw == "true":
            return is_locked_now
        if raw == "false":
            return ~is_locked_now
        return Q()

    def _get_failed_attempts_filter(self):
        """``failed_attempts=0|1-2|cerca_de_bloquearse`` (un valor por vez,
        igual que ``role``/``status``). "cerca_de_bloquearse" = a un intento
        fallido del bloqueo (``LoginLockout.THRESHOLD - 1``)."""
        raw = self.request.query_params.get("failed_attempts", "").strip().lower()
        if raw == "0":
            return Q(login_lockout__isnull=True) | Q(login_lockout__failed_attempts=0)
        if raw == "1-2":
            return Q(login_lockout__failed_attempts__in=[1, 2])
        if raw == "cerca_de_bloquearse":
            return Q(login_lockout__failed_attempts=LoginLockout.THRESHOLD - 1)
        return Q()

    # --- Filtro de origen (método de autenticación) --------------------------

    def _get_auth_method_filter(self):
        """``auth_method=local/google``. Las cuentas creadas por Google quedan
        con password "unusable" (``GoogleAuthView``); es una heurística, no un
        campo persistido: si una cuenta Google después resetea su password por
        email, deja de distinguirse de una cuenta local."""
        raw = self.request.query_params.get("auth_method", "").strip().lower()
        is_google = is_google_account_q()
        if raw == "google":
            return is_google
        if raw == "local":
            return ~is_google
        return Q()

    def _get_ordering(self, queryset):
        ordering_param = self.request.query_params.get("ordering", "").strip()
        if not ordering_param:
            return queryset.order_by("id")

        allowed_fields = {
            "id", "email", "first_name", "last_name", "is_active", "is_staff",
            "date_joined", "last_login",
        }
        ordering_fields = []
        for raw_field in ordering_param.split(","):
            field = raw_field.strip()
            if not field:
                continue
            descending = field.startswith("-")
            field_name = field[1:] if descending else field
            if field_name in allowed_fields:
                ordering_fields.append(f"-{field_name}" if descending else field_name)

        if ordering_fields:
            return queryset.order_by(*ordering_fields)
        return queryset.order_by("id")

    def get_queryset(self):
        # orders_count / last_order_at: anotados acá (no en el serializer)
        # para que sea una sola query con JOIN+GROUP BY en vez de N+1; el
        # serializer solo lee los atributos ya calculados por el ORM.
        # distinct=True porque los otros filtros (groups, login_lockout)
        # pueden multiplicar filas antes de agregar.
        queryset = super().get_queryset().annotate(
            orders_count=Count("orders", distinct=True),
            last_order_at=Max("orders__created_at"),
        )
        filters = (
            self._get_status_filter()
            & self._get_role_filter()
            & self._get_search_filter()
            & self._get_date_joined_filter()
            & self._get_last_login_range_filter()
            & self._get_never_logged_in_filter()
            & self._get_inactive_days_filter()
            & self._get_locked_filter()
            & self._get_failed_attempts_filter()
            & self._get_auth_method_filter()
        )
        if filters:
            queryset = queryset.filter(filters).distinct()
        return self._get_ordering(queryset)

    def list(self, request, *args, **kwargs):
        """Lista usuarios con resumen global y metadatos de paginación."""
        self._require_permission("users.view")
        response = super().list(request, *args, **kwargs)
        response.data["summary"] = {
            "total": User.objects.count(),
            "active": User.objects.filter(is_active=True).count(),
            "inactive": User.objects.filter(is_active=False).count(),
            "locked": User.objects.filter(
                login_lockout__locked_until__gt=timezone.now()
            ).count(),
        }
        return response

    def retrieve(self, request, *args, **kwargs):
        self._require_permission("users.view")
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        self._require_permission("users.create")
        return super().create(request, *args, **kwargs)

    def _is_self_target(self, instance):
        return self.request.user == instance

    def _reject_self_admin_mutation(self, instance, data):
        """Impide que un admin altere su propio rol, staff o estado vía CRUD."""
        if not self._is_self_target(instance):
            return None

        if any(field in data for field in ("role", "is_staff")):
            return Response(
                {"detail": "No puede cambiar su propio rol o permisos de staff."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if any(field in data for field in ("status", "is_active")):
            return Response(
                {"detail": "No puede desactivarse a sí mismo."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def destroy(self, request, *args, **kwargs):
        """Desactiva por defecto; ?permanent=true elimina de forma irreversible."""
        self._require_permission("users.deactivate")
        instance = self.get_object()

        if self._is_self_target(instance):
            return Response(
                {"detail": "No puede desactivar o eliminarse a sí mismo."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.query_params.get("permanent", "").lower() == "true":
            instance.delete()
        else:
            instance.is_active = False
            instance.save(update_fields=["is_active"])
        return Response(status=204)

    def update(self, request, *args, **kwargs):
        """Actualiza un usuario. El admin no puede alterarse a sí mismo en rol/estado."""
        instance = self.get_object()
        for permission in self._required_update_permissions(instance):
            self._require_permission(permission)
        rejection = self._reject_self_admin_mutation(instance, request.data)
        if rejection is not None:
            return rejection
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        for permission in self._required_update_permissions(instance):
            self._require_permission(permission)
        rejection = self._reject_self_admin_mutation(instance, request.data)
        if rejection is not None:
            return rejection
        return super().partial_update(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="bulk-actions")
    def bulk_actions(self, request, *args, **kwargs):
        """POST /api/v1/users/bulk-actions/

        Aplica una acción en lote (activar/desactivar/cambiar rol) sobre una
        lista de IDs, reusando las mismas reglas que ya rigen la edición
        individual (permiso requerido según la acción, auto-protección del
        admin). No es todo-o-nada: cada ID se procesa por separado y la
        respuesta detalla qué se aplicó y qué se saltó (y por qué).

        Body: {"ids": [1,2,3], "action": "activate"|"deactivate"|"set_role",
               "role": "designer"}  (role solo requerido para "set_role")
        """
        action_name = str(request.data.get("action", "")).strip().lower()
        raw_ids = request.data.get("ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValidationError({"ids": "Debe indicar al menos un ID de usuario."})
        try:
            ids = [int(raw_id) for raw_id in raw_ids]
        except (TypeError, ValueError):
            raise ValidationError({"ids": "Todos los IDs deben ser números enteros."})

        permission_by_action = {
            "activate": "users.reactivate",
            "deactivate": "users.deactivate",
            "set_role": "users.edit",
        }
        if action_name not in permission_by_action:
            raise ValidationError(
                {"action": f"Acción inválida: {action_name!r}. Usar activate, deactivate o set_role."}
            )
        self._require_permission(permission_by_action[action_name])

        target_groups = None
        role = None
        if action_name == "set_role":
            role = str(request.data.get("role", "")).strip().lower()
            if not role:
                raise ValidationError({"role": "Debe indicar un rol para 'set_role'."})
            role_group_names = UserAdminSerializer._role_to_group_names(role)
            if not role_group_names:
                raise ValidationError({"role": f"Rol inválido: {role!r}."})
            UserAdminSerializer._ensure_groups_exist(role_group_names)
            target_groups = list(Group.objects.filter(name__in=role_group_names))

        found_users = {user.id: user for user in User.objects.filter(id__in=ids)}
        updated, skipped = [], []

        for user_id in ids:
            instance = found_users.get(user_id)
            if instance is None:
                skipped.append({"id": user_id, "reason": "No existe."})
                continue
            if instance == request.user:
                skipped.append(
                    {"id": user_id, "reason": "No podés aplicarte esta acción a vos mismo."}
                )
                continue

            if action_name == "activate":
                if not instance.is_active:
                    instance.is_active = True
                    instance.save(update_fields=["is_active"])
                updated.append(user_id)
            elif action_name == "deactivate":
                if instance.is_active:
                    instance.is_active = False
                    instance.save(update_fields=["is_active"])
                updated.append(user_id)
            else:  # set_role
                instance.groups.set(target_groups)
                if role == "admin" and not instance.is_staff:
                    instance.is_staff = True
                    instance.save(update_fields=["is_staff"])
                updated.append(user_id)

        return Response({"action": action_name, "updated": updated, "skipped": skipped})

    @action(detail=True, methods=["post"])
    def unlock(self, request, *args, **kwargs):
        """POST /api/v1/users/<id>/unlock/

        Desbloqueo manual: resetea el contador de intentos fallidos y
        ``locked_until`` aunque no haya pasado la hora de bloqueo. Requiere
        el permiso ``users.unlock`` (solo admin, ver 0009_seed_unlock_permission).
        """
        self._require_permission("users.unlock")
        instance = self.get_object()
        lockout, _ = LoginLockout.objects.get_or_create(user=instance)
        lockout.unlock()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request, *args, **kwargs):
        """GET /api/v1/users/export/?<mismos filtros que el listado>&ids=1,2,3

        Exporta a CSV el resultado de ``get_queryset()`` con los filtros de
        query params ya activos (misma lógica que el listado paginado) o,
        si viene ``ids``, solo esos usuarios (para exportar la selección
        actual del panel). Genera el archivo en el backend (módulo ``csv``
        estándar), no en el navegador.
        """
        self._require_permission("users.view")

        queryset = self.get_queryset().select_related("login_lockout")
        raw_ids = request.query_params.get("ids", "").strip()
        if raw_ids:
            try:
                ids = [int(part) for part in raw_ids.split(",") if part.strip()]
            except ValueError:
                raise ValidationError({"ids": "Todos los IDs deben ser números enteros."})
            queryset = queryset.filter(id__in=ids)

        today = timezone.now().strftime("%Y-%m-%d")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="usuarios-{today}.csv"'

        writer = csv.writer(response)
        writer.writerow([
            "id", "nombre", "email", "rol", "estado", "fecha_registro",
            "ultimo_login", "metodo_autenticacion", "bloqueado",
            "total_pedidos", "fecha_ultimo_pedido",
        ])
        for user in queryset:
            lockout = getattr(user, "login_lockout", None)
            full_name = " ".join(filter(None, [user.first_name, user.last_name])).strip()
            writer.writerow([
                user.id,
                full_name or user.email,
                user.email,
                get_effective_role(user).capitalize(),
                "Activo" if user.is_active else "Inactivo",
                user.date_joined.strftime("%Y-%m-%d") if user.date_joined else "",
                user.last_login.isoformat() if user.last_login else "",
                "Google" if user.password.startswith(UNUSABLE_PASSWORD_PREFIX) else "Local",
                "Sí" if (lockout and lockout.is_locked()) else "No",
                user.orders_count or 0,
                user.last_order_at.isoformat() if user.last_order_at else "",
            ])
        return response

    @action(detail=False, methods=["get"], url_path="metrics")
    def metrics(self, request, *args, **kwargs):
        """GET /api/v1/users/metrics/?months=6

        Métricas simples para el panel: usuarios por rol, altas por mes
        (últimos N meses, default 6, tope 12) y método de autenticación.
        """
        self._require_permission("users.view")

        raw_months = request.query_params.get("months", "6").strip()
        try:
            months = int(raw_months)
        except ValueError:
            raise ValidationError({"months": f"'months' debe ser un número entero: {raw_months!r}."})
        months = max(1, min(months, 12))

        # Distribución por rol: se recorre en Python con get_effective_role()
        # (fuente única de verdad de permissions_map) en vez de reimplementar
        # la precedencia Group/is_staff en SQL — el volumen de usuarios de
        # este proyecto no justifica esa duplicación.
        role_counts = {}
        for user in User.objects.all().prefetch_related("groups"):
            role = get_effective_role(user)
            role_counts[role] = role_counts.get(role, 0) + 1
        role_distribution = [
            {"role": role, "label": role.capitalize(), "count": count}
            for role, count in sorted(role_counts.items(), key=lambda item: (-item[1], item[0]))
        ]

        cutoff = (timezone.now() - timedelta(days=30 * months)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        signups_qs = (
            User.objects.filter(date_joined__gte=cutoff)
            .annotate(month=TruncMonth("date_joined"))
            .values("month")
            .annotate(count=Count("id"))
            .order_by("month")
        )
        signups_by_month = [
            {"month": row["month"].strftime("%Y-%m"), "count": row["count"]}
            for row in signups_qs
            if row["month"] is not None
        ]

        is_google = is_google_account_q()
        auth_method = {
            "google": User.objects.filter(is_google).count(),
            "local": User.objects.exclude(is_google).count(),
        }

        return Response({
            "role_distribution": role_distribution,
            "signups_by_month": signups_by_month,
            "auth_method": auth_method,
        })
