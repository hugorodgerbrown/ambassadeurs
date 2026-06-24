# Management command: expire contact-window-lapsed matches and re-queue.
#
# Intended to run on a schedule (e.g. hourly via Render cron) to sweep PROPOSED
# matches whose expires_at has passed. Delegates all business logic to
# matching.services.expire_lapsed_matches.

from django.core.management.base import BaseCommand

from matching.services import expire_lapsed_matches


class Command(BaseCommand):
    """Expire all PROPOSED matches whose contact window has lapsed.

    Transitions each lapsed match to EXPIRED and re-queues both registrations:
    the accepting side goes to the front of the queue; the non-responding side
    has a flake recorded and is sent to the back (or suspended on second flake).
    """

    help = "Expire contact-window-lapsed PROPOSED matches and re-queue registrations."

    def handle(self, *args: object, **options: object) -> None:
        """Run the expiry sweep and write a summary to stdout."""
        count = expire_lapsed_matches()
        self.stdout.write(f"Expired {count} matches.")
