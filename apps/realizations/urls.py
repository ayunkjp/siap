
from django.urls import path
from . import views

app_name = "realizations"

urlpatterns = [
    path("", views.realization_index, name="index"),
    path(
        "<int:activity_id>/tambah/",
        views.realization_create,
        name="create",
    ),
    path(
        "<int:activity_id>/riwayat/",
        views.realization_history,
        name="history",
    ),
    path(
        "bukti/<int:realization_id>/unduh/",
        views.realization_evidence_download,
        name="evidence_download",
    ),
]
