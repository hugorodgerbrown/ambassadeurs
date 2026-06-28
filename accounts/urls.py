"""URL routing for the account self-service area (namespace: ``accounts``).

Login flow (VERB-46 magic-link):
  accounts:login        GET/POST  — email form / send link
  accounts:login_sent   GET       — "check your inbox" page
  accounts:login_verify GET/POST  — confirm token / complete login
  accounts:logout       GET/POST  — confirmation page / log out
"""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # Magic-link login (VERB-46)
    path("login/", views.login_request, name="login"),
    path("login/sent/", views.login_sent, name="login_sent"),
    path("login/<str:token>/", views.login_verify, name="login_verify"),
    path("logout/", views.logout_view, name="logout"),
    # Account self-service
    path("", views.account_detail, name="detail"),
    path("edit/", views.account_edit, name="edit"),
    path("delete/", views.account_delete, name="delete"),
    path("match/", views.account_match, name="match"),
    path(
        "resend-confirmation/",
        views.account_resend_confirmation,
        name="resend_confirmation",
    ),
]
