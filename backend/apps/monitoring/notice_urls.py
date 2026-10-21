from django.urls import path

from .notice_views import NoticeDismissView, NoticeListView

urlpatterns = [
    path("", NoticeListView.as_view(), name="notices"),
    path("<int:pk>/dismiss/", NoticeDismissView.as_view(), name="notice-dismiss"),
]
