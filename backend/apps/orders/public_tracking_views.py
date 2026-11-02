"""GET /api/v1/tracking/<token>/ — lo que muestra ``seguimiento.html``, sin
sesión. Ver ``public_tracking`` (por qué el link va firmado y qué se muestra)."""

from django.http import Http404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.accounts.teams import business_display_name
from apps.labels.label_rendering import sender_fields, sender_logo

from .models import Order
from .public_tracking import order_id_from_token


class PublicTrackingView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AnonRateThrottle]

    def get(self, request, token):
        order_id = order_id_from_token(token)
        order = (
            Order.objects.select_related("address", "store_connection", "user")
            .prefetch_related("status_events")
            .filter(pk=order_id)
            .first()
            if order_id
            else None
        )
        if order is None:
            raise Http404
        store = order.store_connection
        logo = sender_logo(order.user, store)
        address = order.address
        destination = ", ".join(part for part in (address.city, address.state) if part) if address else ""
        return Response(
            {
                "business": sender_fields(order.user, store)["remitente"] or business_display_name(order.user),
                "logo": request.build_absolute_uri(logo.url) if logo else None,
                "number": order.external_number or str(order.pk),
                "status": order.status,
                "status_label": order.get_status_display(),
                "events": [
                    {"status": event.status, "status_label": event.get_status_display(), "at": event.created_at}
                    for event in sorted(order.status_events.all(), key=lambda event: event.created_at)
                ],
                "carrier": order.carrier,
                "tracking_number": order.tracking_number,
                "tracking_url": order.tracking_url,
                "destination": destination,
                "created_at": order.created_at,
            }
        )
