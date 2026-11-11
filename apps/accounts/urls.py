
from django.urls import path
from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    # Autentikasi
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # Dashboard
    path(
        "dashboard/admin/",
        views.admin_dashboard,
        name="admin_dashboard",
    ),
    path(
        "dashboard/prodi/",
        views.prodi_dashboard,
        name="prodi_dashboard",
    ),
    path(
        "dashboard/fakultas/",
        views.fakultas_dashboard,
        name="fakultas_dashboard",
    ),

    # Manajemen Pengguna
    path(
        "manajemen-pengguna/",
        views.user_management_index,
        name="user_management_index",
    ),
    path(
        "manajemen-pengguna/data/",
        views.user_management_data,
        name="user_management_data",
    ),
    path(
        "manajemen-pengguna/save/",
        views.user_management_save,
        name="user_management_save",
    ),
    path(
        "manajemen-pengguna/status/<int:pk>/",
        views.user_management_toggle_status,
        name="user_management_toggle_status",
    ),

    # Audit Log
    path(
        "audit-log/",
        views.audit_log_index,
        name="audit_log_index",
    ),
    path(
        "audit-log/data/",
        views.audit_log_data,
        name="audit_log_data",
    ),
]
