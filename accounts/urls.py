"""URL routing for the account self-service area (namespace: ``accounts``).

Login flow (VERB-46 magic-link):
  accounts:login        GET/POST  — email form / send link
  accounts:login_sent   GET       — "check your inbox" page
  accounts:login_verify GET/POST  — confirm token / complete login
  accounts:logout       GET/POST  — confirmation page / log out

Passkeys (ADR 0030), JSON endpoints driven by static/js/passkeys.js:
  accounts:passkey_sign_in_options   POST — sign-in challenge
  accounts:passkey_sign_in           POST — verify and sign in
  accounts:passkey_register_options  POST — registration challenge (signed in)
  accounts:passkey_register          POST — verify and save (signed in)
  accounts:passkey_delete            POST — remove one (signed in)
"""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # Magic-link login (VERB-46)
    path("login/", views.login_request, name="login"),
    path("login/sent/", views.login_sent, name="login_sent"),
    # Passkey sign-in sits before login/<token>/ so "passkey" is never read
    # as a token.
    path(
        "login/passkey/options/",
        views.passkey_sign_in_options,
        name="passkey_sign_in_options",
    ),
    path("login/passkey/", views.passkey_sign_in, name="passkey_sign_in"),
    path("login/<str:token>/", views.login_verify, name="login_verify"),
    path("logout/", views.logout_view, name="logout"),
    # Account self-service
    path("", views.account_detail, name="detail"),
    path("edit/", views.account_edit, name="edit"),
    path("delete/", views.account_delete, name="delete"),
    path("match/", views.account_match, name="match"),
    path("rejoin/", views.account_rejoin_queue, name="rejoin_queue"),
    path(
        "passkeys/options/",
        views.passkey_register_options,
        name="passkey_register_options",
    ),
    path("passkeys/", views.passkey_register, name="passkey_register"),
    path("passkeys/<int:pk>/delete/", views.passkey_delete, name="passkey_delete"),
    path(
        "resend-confirmation/",
        views.account_resend_confirmation,
        name="resend_confirmation",
    ),
]
