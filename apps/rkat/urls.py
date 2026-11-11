
from django.urls import path
from . import views

app_name = "rkat"

urlpatterns = [
    path("", views.rkat_index, name="index"),
    path("data/", views.rkat_data, name="data"),
    path("buat/", views.rkat_create, name="create"),
    path("<int:pk>/", views.rkat_detail, name="detail"),

    path(
        "<int:rkat_id>/komponen/data/",
        views.rkat_activity_data,
        name="activity_data"
    ),

    path(
        "<int:rkat_id>/komponen/save/",
        views.rkat_activity_save,
        name="activity_save"
    ),

    path(
        "komponen/delete/<int:activity_id>/",
        views.rkat_activity_delete,
        name="activity_delete"
    ),
    path(
        "<int:pk>/ajukan/",
        views.rkat_submit,
        name="submit"
    ),
    path(
        "<int:pk>/teruskan/",
        views.rkat_forward,
        name="forward"
    ),
    path(
        "<int:pk>/keputusan/",
        views.rkat_record_decision,
        name="record_decision"
    ),
    path(
        "<int:pk>/pagu/",
        views.rkat_set_budget,
        name="set_budget"
    ),
    path(
        "<int:pk>/penyesuaian-alokasi/",
        views.rkat_adjust_allocations,
        name="adjust_allocations"
    ),
]
