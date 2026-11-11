
from django.urls import path

from . import views


app_name = "disbursements"

urlpatterns = [
    path(
        "",
        views.disbursement_index,
        name="index",
    ),
    path(
        "<int:request_id>/pencairan/tambah/",
        views.institution_disbursement_create,
        name="institution_create",
    ),
    path(
        "<int:request_id>/penyerahan/tambah/",
        views.faculty_handover_create,
        name="handover_create",
    ),
]
