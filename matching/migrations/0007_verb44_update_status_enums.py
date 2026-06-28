# VERB-44: Split Registration.Status and Match.Status into two independent
# state machines.
#
# Registration.Status changes:
#   PENDING  → UNVERIFIED  (not-yet-email-confirmed)
#   WAITING  → VERIFIED    (confirmed and in the pool)
#   MATCHED  → VERIFIED    (match progress now on Match.Status)
#   CONFIRMED→ VERIFIED    (match progress now on Match.Status)
#   WITHDRAWN, SUSPENDED — unchanged
#
# Match.Status changes:
#   ABANDONED → CANCELLED  (post-accept no-show; renamed for clarity)
#   PENDING   — new state  (one side accepted, awaiting the other)
#
# The RunPython step is defensive: there is no production data, but it ensures
# migrate is correct in any seeded environment (e.g. local dev databases).

from django.db import migrations, models


def remap_statuses_forwards(apps: object, schema_editor: object) -> None:
    """Remap legacy status values to the new enum values."""
    Registration = apps.get_model("matching", "Registration")  # type: ignore[attr-defined]
    Match = apps.get_model("matching", "Match")  # type: ignore[attr-defined]

    # Registration: old → new
    Registration.objects.filter(status="PENDING").update(status="UNVERIFIED")
    Registration.objects.filter(status="WAITING").update(status="VERIFIED")
    Registration.objects.filter(status="MATCHED").update(status="VERIFIED")
    Registration.objects.filter(status="CONFIRMED").update(status="VERIFIED")

    # Match: ABANDONED → CANCELLED
    Match.objects.filter(status="ABANDONED").update(status="CANCELLED")

    # Match: single-timestamp PROPOSED rows → PENDING
    # (one side accepted but the match status was never updated to PENDING
    # under the old state machine).
    Match.objects.filter(
        status="PROPOSED",
    ).exclude(
        ambassador_accepted_at=None,
        referee_accepted_at=None,
    ).update(status="PENDING")


def remap_statuses_backwards(apps: object, schema_editor: object) -> None:
    """Reverse the status remap (best-effort; information-lossy for Registration)."""
    Registration = apps.get_model("matching", "Registration")  # type: ignore[attr-defined]
    Match = apps.get_model("matching", "Match")  # type: ignore[attr-defined]

    # We cannot recover MATCHED / CONFIRMED from VERIFIED; map all back to WAITING.
    Registration.objects.filter(status="VERIFIED").update(status="WAITING")
    Registration.objects.filter(status="UNVERIFIED").update(status="PENDING")

    # Match: CANCELLED → ABANDONED, PENDING → PROPOSED
    Match.objects.filter(status="CANCELLED").update(status="ABANDONED")
    Match.objects.filter(status="PENDING").update(status="PROPOSED")


class Migration(migrations.Migration):
    dependencies = [
        ("matching", "0006_add_decline_hash_and_prior_decline_count"),
    ]

    operations = [
        migrations.RunPython(
            remap_statuses_forwards,
            remap_statuses_backwards,
        ),
        migrations.AlterField(
            model_name="match",
            name="status",
            field=models.CharField(
                choices=[
                    ("PROPOSED", "Proposed"),
                    ("PENDING", "Pending"),
                    ("ACCEPTED", "Accepted"),
                    ("DECLINED", "Declined"),
                    ("EXPIRED", "Expired"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="PROPOSED",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="registration",
            name="status",
            field=models.CharField(
                choices=[
                    ("UNVERIFIED", "Unverified"),
                    ("VERIFIED", "Verified"),
                    ("WITHDRAWN", "Withdrawn"),
                    ("SUSPENDED", "Suspended"),
                ],
                default="VERIFIED",
                max_length=16,
            ),
        ),
    ]
