# Glossary — domain term → code symbol

Maps the domain language in [`CLAUDE.md`](../CLAUDE.md) to the code symbols that
implement it. Add a row when a term gains a symbol.

| Domain term | Code symbol | Location |
|-------------|-------------|----------|
| Season | `matching.models.Season` | `matching/models.py` |
| Price category | `matching.models.PriceCategory` | `matching/models.py` |
| Registration | `matching.models.Registration` | `matching/models.py` |
| Role (ambassador / referee) | `matching.models.Registration.Role` | `matching/models.py` |
| Pool status (waiting → matched → confirmed / withdrawn) | `matching.models.Registration.Status` | `matching/models.py` |
| Preferred resort / ticket office | `matching.models.Resort` | `matching/models.py` |
| Prior-season attestation | `Registration.held_prior_pass` | `matching/models.py` |
| Discount exclusion (Mont 4 / special reduction) | `Registration.discount_eligible` | `matching/models.py` |
| Queue priority | `Registration.priority` | `matching/models.py` |
| Account profile | `accounts.models.Account` | `accounts/models.py` |
| Register a participant | `matching.services.register_participant` | `matching/services.py` |
