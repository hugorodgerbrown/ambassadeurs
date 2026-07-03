# Glossary — domain term → code symbol

Maps the domain language in [`CLAUDE.md`](../CLAUDE.md) to the code symbols that
implement it. Add a row when a term gains a symbol.

| Domain term | Code symbol | Location |
|-------------|-------------|----------|
| Registration | `matching.models.Registration` | `matching/models.py` |
| Role (ambassador / referee) | `matching.models.Registration.Role` | `matching/models.py` |
| Pool status (waiting → matched → confirmed / withdrawn) | `matching.models.Registration.Status` | `matching/models.py` |
| Preferred resort / ticket office | `matching.models.Resort` | `matching/models.py` |
| Prior-season pass attestation | `Registration.prior_pass` (`PriorPass` TextChoices: `NONE / SEASONAL / ANNUAL / MONT4`) | `matching/models.py` |
| Queue priority | `Registration.priority` | `matching/models.py` |
| Match | `matching.models.Match` | `matching/models.py` |
| Match state (proposed → accepted / declined / expired / abandoned) | `matching.models.Match.Status` | `matching/models.py` |
| Which side of a match a party is on | `matching.models.Match.Side` | `matching/models.py` |
| Contact window | `settings.CONTACT_WINDOW_HOURS` (env var) | `config/settings/base.py` |
| Registration window | `settings.REGISTRATION_OPENS_AT` / `settings.REGISTRATION_CLOSES_AT` (env vars) | `config/settings/base.py` |
| Is registration open? | `matching.services.is_registration_open` | `matching/services.py` |
| Eligible pair check | `matching.services.is_eligible_pair` | `matching/services.py` |
| Propose a match | `matching.services.propose_match` | `matching/services.py` |
| Register a participant | `matching.services.register_participant` | `matching/services.py` |
| Match notification | `matching.services.send_match_notification` | `matching/services.py` |
| State transition audit log | `core.models.StateTransitionLog` | `core/models.py` |
| Record a state transition | `core.services.record_transition` | `core/services.py` |
| Record a match acceptance | `matching.services.record_acceptance` | `matching/services.py` |
| Record a match decline | `matching.services.record_decline` | `matching/services.py` |
| Update own profile | `accounts.services.update_account` | `accounts/services.py` |
| Delete own account | `accounts.services.delete_account` | `accounts/services.py` |
| Email-confirmation signed link | `accounts.tokens.make_registration_confirmation_token` | `accounts/tokens.py` |
| Magic-link login token | `accounts.tokens.make_login_token` / `read_login_token` | `accounts/tokens.py` |
| Send a magic-link login email | `accounts.services.send_login_email` | `accounts/services.py` |
| Send a templated email | `core.emails.send_templated_email` | `core/emails.py` |
| Deferred matching moment | `settings.MATCHING_OPENS_AT` (env var) | `config/settings/base.py` |
| Tiered prepaid registration fee schedule | `settings.REGISTRATION_FEE_TIERS` (env var) | `config/settings/base.py` |
| When does matching open? (deferred-matching gate) | `matching.pricing_config.matching_opens_at` | `matching/pricing_config.py` |
| Resolve the fee for a registration date | `matching.pricing_config.fee_chf_for` | `matching/pricing_config.py` |
| Locked prepaid registration fee (CHF) on a registration | `matching.models.Registration.fee_chf` | `matching/models.py` |
| Prepaid registration deposit (audit row) | `billing.models.Payment` | `billing/models.py` |
| Deposit lifecycle (held → captured / refunded / forfeited) | `billing.models.Payment.Status` | `billing/models.py` |
| Keep a deposit (successful match) | `billing.services.payments.capture` | `billing/services/payments.py` |
| Refund a deposit (via Stripe) | `billing.services.payments.refund` | `billing/services/payments.py` |
| Forfeit a deposit (post-accept no-show) | `billing.services.payments.forfeit` | `billing/services/payments.py` |
