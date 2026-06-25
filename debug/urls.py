"""URL routing for the debug test-data panel (namespace: ``debug``).

All routes are POST-only and guarded by ``require_debug``. The URL conf is
always mounted (``config/urls.py``); the decorator raises ``Http404`` in
production so these paths are effectively invisible.
"""

from django.urls import path

from . import views

app_name = "debug"

urlpatterns = [
    path("create-counterpart/", views.create_counterpart, name="create_counterpart"),
    path("counterpart/accept/", views.counterpart_accept, name="counterpart_accept"),
    path("counterpart/decline/", views.counterpart_decline, name="counterpart_decline"),
    path("counterpart/login/", views.counterpart_login, name="counterpart_login"),
]
