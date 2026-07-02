"""The billing domain: the prepaid registration deposit (ADR 0014).

Holds the ``Payment`` model and its Stripe-backed lifecycle services
(``capture``/``refund``/``forfeit``). Wiring the deposit into the registration
and match flows is left to later tickets (VERB-86/87/88); this app ships the
model and services only.
"""
