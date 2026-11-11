from django.contrib import admin
from django.urls import path, include


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("master-data/", include("apps.master_data.urls")),
    path("rkat/", include("apps.rkat.urls")),
    path(
        "pengajuan-dana/",
        include("apps.fund_requests.urls"),
    ),
    path(
        "pencairan-dana/",
        include("apps.disbursements.urls"),
    ),
    path(
        "realisasi/",
        include("apps.realizations.urls"),
    ),
    path(
        "lpj/",
        include("apps.accountability.urls"),
    ),
]
