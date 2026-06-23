"""URL routing for the public site (namespace: ``public``)."""

from django.urls import path

from . import views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    path("register/<slug:role>/", views.register, name="register"),
    path("register/<slug:role>/done/", views.register_done, name="register_done"),
    path("legal/<slug:page>/", views.legal_page, name="legal"),
]
