# Migration: delete the Account model.
#
# Account has been superseded — participant attributes (phone, preferred_language)
# now live on matching.Registration (OneToOneField to User). This migration must
# run AFTER matching/0003 which drops the Registration.account FK, so that
# Account has no incoming references when it is deleted.

from django.db import migrations


class Migration(migrations.Migration):
    """Delete the Account model; participant attributes moved to Registration."""

    dependencies = [
        ("accounts", "0001_initial"),
        ("matching", "0003_rework_registration_add_match"),
    ]

    operations = [
        migrations.DeleteModel(name="Account"),
    ]
