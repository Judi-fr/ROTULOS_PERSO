"""``/api/v1/notices/``: los avisos del comercio para quien los mira
(``notices.visible_notices``) y descartarlos mientras dure el problema."""

from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MerchantNotice
from .notices import visible_notices


def notice_data(notice):
    return {
        "id": notice.id,
        "kind": notice.kind,
        "title": notice.title,
        "detail": notice.detail,
        "url": notice.url,
        "created_at": notice.created_at,
    }


class NoticeListView(APIView):
    """GET / — los avisos abiertos que la persona puede ver."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([notice_data(notice) for notice in visible_notices(request.user)])


class NoticeDismissView(APIView):
    """POST <id>/dismiss/ — no mostrarlo más (para todo el comercio) mientras
    dure el problema; si vuelve a pasar es un aviso nuevo."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        notice = next((item for item in visible_notices(request.user) if item.pk == pk), None)
        if notice is None:
            raise Http404
        MerchantNotice.objects.filter(pk=notice.pk).update(dismissed_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)
