
from django.urls import path

from . import views

app_name = "accountability"

urlpatterns = [
    path(
        "",
        views.accountability_index,
        name="index",
    ),
    path(
        "buat/<int:fund_request_id>/",
        views.accountability_create,
        name="create",
    ),
    path(
        "<int:pk>/",
        views.accountability_detail,
        name="detail",
    ),
    path(
        "<int:pk>/lampiran/tambah/",
        views.accountability_attachment_add,
        name="attachment_add",
    ),
    path(
        "<int:pk>/kirim/",
        views.accountability_submit,
        name="submit",
    ),
    path(
        "<int:pk>/revisi/",
        views.accountability_revision,
        name="revision",
    ),
    path(
        "<int:pk>/terima/",
        views.accountability_accept,
        name="accept",
    ),
    path(
        "lampiran/<int:attachment_id>/unduh/",
        views.accountability_attachment_download,
        name="attachment_download",
    ),
]
