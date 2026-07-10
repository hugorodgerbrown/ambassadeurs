# Public-facing views package: the landing page, the two-step registration
# flow, the paid-tier deposit and tip flows, the willingness-to-pay survey,
# and the match accept/decline/report-no-show flow.
#
# This package used to be a single ``public/views.py`` module; it is split
# across sibling modules by concern (pages, registration, payments, tips,
# survey, match) with shared private helpers in ``_shared``. This file
# re-exports the full public surface so every existing import path — from
# ``public/urls.py``, ``config/urls.py``, ``accounts/views.py`` and
# ``debug/views.py`` — keeps resolving unchanged (``from public.views import
# ...`` / ``from . import views`` then ``views.<name>``).

from __future__ import annotations

from .match import (
    _match_context,
    _render_match_page,
    match_accept,
    match_decline,
    match_detail,
    match_report_no_show,
    match_withdraw,
)
from .pages import (
    about,
    colophon,
    download_application_form,
    faq,
    home,
    how_it_works,
    legal_page,
    queue_snapshot_page,
    service_worker,
)
from .payments import (
    register_payment_cancelled,
    register_payment_return,
    register_payment_start,
    stripe_webhook,
)
from .registration import (
    register,
    register_confirm,
    register_done,
    register_email_sent,
    register_form,
    register_role,
    register_role_derive,
)
from .survey import register_survey_submit
from .tips import tip_cancelled, tip_page, tip_return, tip_start

__all__ = [
    "_match_context",
    "_render_match_page",
    "about",
    "colophon",
    "download_application_form",
    "faq",
    "home",
    "how_it_works",
    "legal_page",
    "match_accept",
    "match_decline",
    "match_detail",
    "match_report_no_show",
    "match_withdraw",
    "queue_snapshot_page",
    "register",
    "register_confirm",
    "register_done",
    "register_email_sent",
    "register_form",
    "register_payment_cancelled",
    "register_payment_return",
    "register_payment_start",
    "register_role",
    "register_role_derive",
    "register_survey_submit",
    "service_worker",
    "stripe_webhook",
    "tip_cancelled",
    "tip_page",
    "tip_return",
    "tip_start",
]
