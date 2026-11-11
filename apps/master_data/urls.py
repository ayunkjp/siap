
from django.urls import path
from . import views

app_name = "master_data"

urlpatterns = [
    path("fakultas/", views.faculty_index, name="faculty_index"),
    path("fakultas/data/", views.faculty_data, name="faculty_data"),
    path("fakultas/save/", views.faculty_save, name="faculty_save"),
    path("fakultas/delete/<int:pk>/", views.faculty_delete, name="faculty_delete"),
    path(
        "program-studi/",
        views.study_program_index,
        name="study_program_index"
    ),
    path(
        "program-studi/data/",
        views.study_program_data,
        name="study_program_data"
    ),
    path(
        "program-studi/save/",
        views.study_program_save,
        name="study_program_save"
    ),
    path(
        "program-studi/delete/<int:pk>/",
        views.study_program_delete,
        name="study_program_delete"
    ),

    path(
    "tahun-anggaran/",
    views.budget_year_index,
    name="budget_year_index"
    ),
    path(
        "tahun-anggaran/data/",
        views.budget_year_data,
        name="budget_year_data"
    ),
    path(
        "tahun-anggaran/save/",
        views.budget_year_save,
        name="budget_year_save"
    ),
    path(
        "tahun-anggaran/delete/<int:pk>/",
        views.budget_year_delete,
        name="budget_year_delete"
    ),

]
