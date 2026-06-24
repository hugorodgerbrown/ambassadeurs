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
| Email-verification signed link | `accounts.tokens.make_email_verification_token` | `accounts/tokens.py` |
| Verify a participant's email | `accounts.services.get_or_create_participant_user` | `accounts/services.py` |
