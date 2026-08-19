from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from .access import shared_access_lock, shared_access_login

urlpatterns = [
    path("access/", shared_access_login, name="shared_access_login"),
    path("access/lock/", shared_access_lock, name="shared_access_lock"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", include("pages.urls")),
]

if settings.DEBUG:
    import debug_toolbar

    urlpatterns = [
        path("__debug__/", include(debug_toolbar.urls)),
    ] + urlpatterns
