# Migration: rework Registration (drop Season/PriceCategory FKs, add user + new
# fields) and add the Match model.
#
# Sequenced after matching/0002 (which created Registration with the old schema)
# so that Django's dependency graph is satisfied. The accounts/Account model is
# still present at this point; its deletion is handled by accounts/0002.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Rework Registration to the single-season shape and introduce Match."""

    dependencies = [
        ("matching", "0002_registration"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Drop the old Registration (which referenced Season, PriceCategory,
        #    Account) and recreate it with the new schema. On a greenfield DB
        #    there is no data to preserve.
        migrations.DeleteModel(name="Registration"),
        # 2. Drop Season and PriceCategory (no longer needed).
        migrations.DeleteModel(name="PriceCategory"),
        migrations.DeleteModel(name="Season"),
        # 3. Create the new Registration model.
        migrations.CreateModel(
            name="Registration",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "role",
                    models.CharField(
                        choices=[("AMBASSADOR", "Ambassador"), ("REFEREE", "Referee")],
                        max_length=16,
                    ),
                ),
                ("phone", models.CharField(blank=True, max_length=32)),
                (
                    "preferred_language",
                    models.CharField(
                        blank=True,
                        choices=[("en", "English"), ("fr", "French")],
                        max_length=8,
                    ),
                ),
                (
                    "preferred_location",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("VERBIER", "Verbier"),
                            ("THYON", "Thyon"),
                            ("NENDAZ", "Nendaz"),
                            ("VEYSONNAZ", "Veysonnaz"),
                            ("LA_TZOUMAZ", "La Tzoumaz"),
                            ("BRUSON", "Bruson"),
                        ],
                        help_text="Soft preference; used to rank matches, never to gate them.",
                        max_length=16,
                    ),
                ),
                (
                    "prior_pass",
                    models.CharField(
                        choices=[
                            ("NONE", "None — I did not hold a prior pass"),
                            ("SEASONAL", "Seasonal pass (4 Vallées)"),
                            ("ANNUAL", "Annual pass (4 Vallées)"),
                            ("MONT4", "Mont 4 Card / special reduction"),
                        ],
                        default="NONE",
                        help_text=(
                            "Prior-season pass attestation. Ambassadors must hold "
                            "SEASONAL, ANNUAL, or MONT4. Referees are genuinely new "
                            "and hold NONE."
                        ),
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("WAITING", "Waiting"),
                            ("MATCHED", "Matched"),
                            ("CONFIRMED", "Confirmed"),
                            ("WITHDRAWN", "Withdrawn"),
                        ],
                        default="WAITING",
                        max_length=16,
                    ),
                ),
                (
                    "priority",
                    models.IntegerField(
                        default=0,
                        help_text=(
                            "Queue priority; higher is nearer the front. "
                            "Adjusted by flaking."
                        ),
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="registration",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        # 4. Create the Match model.
        migrations.CreateModel(
            name="Match",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PROPOSED", "Proposed"),
                            ("ACCEPTED", "Accepted"),
                            ("DECLINED", "Declined"),
                            ("EXPIRED", "Expired"),
                        ],
                        default="PROPOSED",
                        max_length=16,
                    ),
                ),
                (
                    "expires_at",
                    models.DateTimeField(
                        help_text=(
                            "When the contact window closes; both re-queue if not "
                            "mutually accepted by then."
                        ),
                    ),
                ),
                (
                    "ambassador_registration",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="matches_as_ambassador",
                        to="matching.registration",
                    ),
                ),
                (
                    "referee_registration",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="matches_as_referee",
                        to="matching.registration",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
