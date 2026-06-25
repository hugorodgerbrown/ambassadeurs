"""URL routing for the account self-service area (namespace: ``accounts``)."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.account_detail, name="detail"),
    path("edit/", views.account_edit, name="edit"),
    path("delete/", views.account_delete, name="delete"),
    path(
        "resend-confirmation/",
        views.account_resend_confirmation,
        name="resend_confirmation",
    ),
]
