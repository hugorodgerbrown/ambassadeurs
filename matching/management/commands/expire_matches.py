# Management command: expire contact-window-lapsed matches and re-queue.
#
# Intended to run on a schedule (e.g. hourly via Render cron) to sweep PROPOSED
# matches whose expires_at has passed. Delegates all business logic to
# matching.services.expire_lapsed_matches.

from django.core.management.base import BaseCommand
from django.utils import timezone

from matching.services import expire_lapsed_matches


class Command(BaseCommand):
    """Expire all PROPOSED/PENDING matches whose contact window has lapsed.

    Transitions each lapsed match to EXPIRED. The kept-faith side (already
    accepted) is re-queued to the front of the pool; the non-responding side
    is PAUSED — out of the pool, but able to self-rejoin from their account
    page (VERB-74 / ADR 0013). The two-strike flake model is retired.
    """

    help = "Expire contact-window-lapsed PROPOSED matches and re-queue registrations."

    def handle(self, *args: object, **options: object) -> None:
        """Run the expiry sweep and write a summary to stdout.

        Reads "now" here (inversion of control, VERB-100) and passes it as the
        sweep's cutoff, keeping ``expire_lapsed_matches`` a pure function of
        its arguments.
        """
        count = expire_lapsed_matches(cutoff=timezone.now())
        self.stdout.write(f"Expired {count} matches.")
