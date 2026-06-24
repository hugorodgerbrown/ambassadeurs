# Test factories for the public app.

import factory

from public.models import FormDownload


class FormDownloadFactory(factory.django.DjangoModelFactory[FormDownload]):
    """Factory for FormDownload.

    No domain fields beyond BaseModel timestamps — call .create() with no
    arguments to record a download row.
    """

    class Meta:
        model = FormDownload
