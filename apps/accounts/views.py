
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.utils.http import url_has_allowed_host_and_scheme
from django.conf import settings
from apps.master_data.models import BudgetYear
from apps.rkat.models import RKATSubmission
from apps.fund_requests.models import FundRequest
from apps.accountability.models import AccountabilityReport
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.core.exceptions import ValidationError

from apps.master_data.models import Faculty, StudyProgram
from .models import UserProfile
from django.db import transaction, IntegrityError
from django.utils.dateparse import parse_date
from django.db.models import Q
from .models import UserProfile, AuditLog
from apps.accounts.audit import log_audit, get_client_ip


def get_dashboard_url(user):
    try:
        role = user.profile.role
    except Exception:
        return "login"

    dashboard_urls = {
        "ADMIN": "accounts:admin_dashboard",
        "PRODI": "accounts:prodi_dashboard",
        "FAKULTAS": "accounts:fakultas_dashboard",
    }

    return dashboard_urls.get(role, "accounts:login")


def login_view(request):
    if request.user.is_authenticated:
        return redirect(get_dashboard_url(request.user))

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        if not username or not password:
            messages.error(
                request,
                "Username dan password wajib diisi.",
            )
            return render(request, "auth/login.html")

        user = authenticate(
            request,
            username=username,
            password=password,
        )

        if user is not None:
            try:
                user.profile
            except Exception:
                messages.error(
                    request,
                    "Akun belum memiliki profil SIAP. Hubungi Admin.",
                )
                return render(request, "auth/login.html")

            if not user.is_active:
                messages.error(
                    request,
                    "Akun Anda sedang tidak aktif.",
                )
                return render(request, "auth/login.html")

            login(request, user)

            next_url = request.POST.get("next") or request.GET.get("next")

            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)

            return redirect(get_dashboard_url(user))

        messages.error(
            request,
            "Username atau password tidak sesuai.",
        )

    return render(
        request,
        "auth/login.html",
        {"next": request.GET.get("next", "")},
    )


@login_required(login_url="accounts:login")
@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, "Anda berhasil keluar dari SIAP.")
    return redirect("accounts:login")





from apps.accounts.decorators import role_required



def build_dashboard_context(user):
    role = user.profile.role

    active_budget_year = BudgetYear.objects.filter(
        is_active=True
    ).first()

    rkat_queryset = RKATSubmission.objects.all()
    request_queryset = FundRequest.objects.all()
    lpj_queryset = AccountabilityReport.objects.all()

    # Pembatasan data berdasarkan role.
    if role == "PRODI":
        study_program_id = user.profile.study_program_id

        rkat_queryset = rkat_queryset.filter(
            study_program_id=study_program_id
        )

        request_queryset = request_queryset.filter(
            study_program_id=study_program_id
        )

        lpj_queryset = lpj_queryset.filter(
            fund_request__study_program_id=study_program_id
        )

    elif role == "FAKULTAS":
        faculty_id = user.profile.faculty_id

        rkat_queryset = rkat_queryset.filter(
            study_program__faculty_id=faculty_id
        )

        request_queryset = request_queryset.filter(
            study_program__faculty_id=faculty_id
        )

        lpj_queryset = lpj_queryset.filter(
            fund_request__study_program__faculty_id=faculty_id
        )

    # Batasi ringkasan ke tahun anggaran aktif.
    if active_budget_year:
        rkat_queryset = rkat_queryset.filter(
            budget_year=active_budget_year
        )

        request_queryset = request_queryset.filter(
            budget_year=active_budget_year
        )

        lpj_queryset = lpj_queryset.filter(
            fund_request__budget_year=active_budget_year
        )
    else:
        rkat_queryset = rkat_queryset.none()
        request_queryset = request_queryset.none()
        lpj_queryset = lpj_queryset.none()

    recent_requests = (
        request_queryset
        .select_related("study_program", "budget_year")
        .order_by("-created_at")[:5]
    )

    return {
        "dashboard_title": f"Dashboard {user.profile.get_role_display()}",
        "active_budget_year": active_budget_year,
        "total_rkat": rkat_queryset.count(),
        "total_requests": request_queryset.count(),
        "total_lpj": lpj_queryset.count(),
        "recent_requests": recent_requests,
    }


@role_required("ADMIN")
def admin_dashboard(request):
    context = build_dashboard_context(request.user)
    return render(request, "home.html", context)


@role_required("PRODI")
def prodi_dashboard(request):
    context = build_dashboard_context(request.user)
    return render(request, "home.html", context)


@role_required("FAKULTAS")
def fakultas_dashboard(request):
    context = build_dashboard_context(request.user)
    return render(request, "home.html", context)


User = get_user_model()


@role_required("ADMIN")
def user_management_index(request):
    return render(request, "accounts/users/index.html", {
        "faculties": Faculty.objects.all(),
        "study_programs": StudyProgram.objects.select_related("faculty").all(),
    })


@role_required("ADMIN")
@require_GET
def user_management_data(request):
    profiles = UserProfile.objects.select_related(
        "user", "faculty", "study_program",
        "study_program__faculty"
    ).order_by("user__username")

    data = []

    for profile in profiles:
        data.append({
            "id": profile.id,
            "user_id": profile.user_id,
            "username": profile.user.username,
            "email": profile.user.email,
            "first_name": profile.user.first_name,
            "last_name": profile.user.last_name,
            "role": profile.role,
            "role_display": profile.get_role_display(),
            "faculty_id": profile.faculty_id,
            "faculty_name": profile.faculty.name if profile.faculty else "-",
            "study_program_id": profile.study_program_id,
            "study_program_name": (
                profile.study_program.name
                if profile.study_program else "-"
            ),
            "is_active": profile.user.is_active,
        })

    return JsonResponse({"data": data})


@role_required("ADMIN")
@require_POST
def user_management_save(request):
    profile_id = request.POST.get("id", "").strip()
    username = request.POST.get("username", "").strip()
    email = request.POST.get("email", "").strip()
    first_name = request.POST.get("first_name", "").strip()
    last_name = request.POST.get("last_name", "").strip()
    password = request.POST.get("password", "")
    role = request.POST.get("role", "").strip()
    faculty_id = request.POST.get("faculty_id", "").strip()
    study_program_id = request.POST.get("study_program_id", "").strip()

    valid_roles = [
        UserProfile.Role.ADMIN,
        UserProfile.Role.PRODI,
        UserProfile.Role.FAKULTAS,
    ]

    if not username or not role:
        return JsonResponse({
            "success": False,
            "message": "Username dan role wajib diisi."
        }, status=400)

    if role not in valid_roles:
        return JsonResponse({
            "success": False,
            "message": "Role tidak valid."
        }, status=400)

    if not profile_id and not password:
        return JsonResponse({
            "success": False,
            "message": "Password wajib diisi untuk akun baru."
        }, status=400)

    if password and len(password) < 8:
        return JsonResponse({
            "success": False,
            "message": "Password minimal 8 karakter."
        }, status=400)

    try:
        with transaction.atomic():
            if profile_id:
                profile = get_object_or_404(
                    UserProfile.objects.select_related(
                        "user", "faculty", "study_program"
                    ),
                    pk=profile_id,
                )
                user = profile.user
                is_new = False

                old_values = {
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "role": profile.role,
                    "faculty_id": profile.faculty_id,
                    "faculty_name": (
                        profile.faculty.name if profile.faculty else None
                    ),
                    "study_program_id": profile.study_program_id,
                    "study_program_name": (
                        profile.study_program.name
                        if profile.study_program else None
                    ),
                    "is_active": user.is_active,
                }
            else:
                user = User()
                profile = UserProfile(user=user)
                is_new = True
                old_values = {}

            user.username = username
            user.email = email
            user.first_name = first_name
            user.last_name = last_name

            password_changed = bool(password)

            if password_changed:
                user.set_password(password)

            user.full_clean()
            user.save()

            profile.user = user
            profile.role = role

            if role == UserProfile.Role.ADMIN:
                profile.faculty = None
                profile.study_program = None

            elif role == UserProfile.Role.FAKULTAS:
                if not faculty_id:
                    raise ValidationError(
                        "Fakultas wajib dipilih untuk role Fakultas."
                    )

                profile.faculty = get_object_or_404(
                    Faculty,
                    pk=faculty_id,
                )
                profile.study_program = None

            elif role == UserProfile.Role.PRODI:
                if not study_program_id:
                    raise ValidationError(
                        "Program Studi wajib dipilih untuk role Prodi."
                    )

                profile.study_program = get_object_or_404(
                    StudyProgram,
                    pk=study_program_id,
                )
                profile.faculty = None

            profile.full_clean()
            profile.save()

            new_values = {
                "username": user.username,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": profile.role,
                "faculty_id": profile.faculty_id,
                "faculty_name": (
                    profile.faculty.name if profile.faculty else None
                ),
                "study_program_id": profile.study_program_id,
                "study_program_name": (
                    profile.study_program.name
                    if profile.study_program else None
                ),
                "is_active": user.is_active,
                "password_changed": password_changed,
            }

            log_audit(
                actor=request.user,
                action=(
                    AuditLog.Action.CREATE
                    if is_new
                    else AuditLog.Action.UPDATE
                ),
                instance=profile,
                description=(
                    "Admin membuat akun pengguna SIAP."
                    if is_new
                    else "Admin mengubah data akun pengguna SIAP."
                ),
                changes={
                    "old": old_values,
                    "new": new_values,
                },
                ip_address=get_client_ip(request),
            )

        return JsonResponse({
            "success": True,
            "message": "Akun pengguna berhasil disimpan."
        })

    except ValidationError as exc:
        if hasattr(exc, "message_dict"):
            message = " ".join(
                str(item)
                for errors in exc.message_dict.values()
                for item in errors
            )
        else:
            message = " ".join(exc.messages)

        return JsonResponse({
            "success": False,
            "message": message
        }, status=400)

    except IntegrityError:
        return JsonResponse({
            "success": False,
            "message": "Username atau email sudah digunakan."
        }, status=400)


@role_required("ADMIN")
@require_POST
def user_management_toggle_status(request, pk):
    profile = get_object_or_404(
        UserProfile.objects.select_related("user"),
        pk=pk,
    )

    # Admin tidak boleh menonaktifkan atau mengubah status akunnya sendiri.
    if profile.user_id == request.user.id:
        return JsonResponse({
            "success": False,
            "message": "Anda tidak dapat mengubah status akun sendiri."
        }, status=400)

    with transaction.atomic():
        old_status = profile.user.is_active
        profile.user.is_active = not old_status

        profile.user.save(update_fields=["is_active"])

        new_status = profile.user.is_active

        status = (
            "diaktifkan"
            if new_status
            else "dinonaktifkan"
        )

        log_audit(
            actor=request.user,
            action=AuditLog.Action.UPDATE,
            instance=profile,
            description=f"Admin {status} akun pengguna SIAP.",
            changes={
                "is_active": {
                    "old": old_status,
                    "new": new_status,
                },
                "username": profile.user.username,
            },
            ip_address=get_client_ip(request),
        )

    return JsonResponse({
        "success": True,
        "message": f"Akun berhasil {status}."
    })


@role_required("ADMIN")
def audit_log_index(request):
    app_labels = (
        AuditLog.objects
        .values_list("app_label", flat=True)
        .distinct()
        .order_by("app_label")
    )

    users = (
        AuditLog.objects
        .values_list("actor__username", flat=True)
        .distinct()
        .order_by("actor__username")
    )

    return render(
        request,
        "accounts/audit_logs/index.html",
        {
            "app_labels": app_labels,
            "audit_actions": AuditLog.Action.choices,
            "audit_users": users,
        },
    )


@role_required("ADMIN")
@require_GET
def audit_log_data(request):
    """
    Endpoint DataTables Server-Side untuk Audit Log.
    """

    # Parameter DataTables
    try:
        draw = int(request.GET.get("draw", 1))
        start = max(int(request.GET.get("start", 0)), 0)
        length = int(request.GET.get("length", 25))
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "Parameter DataTables tidak valid."},
            status=400,
        )

    # Batasi jumlah data per permintaan.
    if length < 1:
        length = 25

    length = min(length, 100)

    # Query dasar
    base_queryset = AuditLog.objects.select_related("actor").all()

    records_total = base_queryset.count()
    queryset = base_queryset

    # ------------------------------------------------------
    # FILTER MODUL
    # ------------------------------------------------------
    app_label = request.GET.get("app_label", "").strip()

    if app_label:
        queryset = queryset.filter(app_label=app_label)

    # ------------------------------------------------------
    # FILTER JENIS AKTIVITAS
    # ------------------------------------------------------
    action = request.GET.get("action", "").strip()

    if action:
        valid_actions = dict(AuditLog.Action.choices)

        if action in valid_actions:
            queryset = queryset.filter(action=action)

    # ------------------------------------------------------
    # FILTER PENGGUNA
    # ------------------------------------------------------
    username = request.GET.get("username", "").strip()

    if username:
        queryset = queryset.filter(actor__username=username)

    # ------------------------------------------------------
    # FILTER TANGGAL
    # ------------------------------------------------------
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if date_from:
        parsed_date = parse_date(date_from)

        if parsed_date:
            queryset = queryset.filter(
                created_at__date__gte=parsed_date
            )

    if date_to:
        parsed_date = parse_date(date_to)

        if parsed_date:
            queryset = queryset.filter(
                created_at__date__lte=parsed_date
            )

    # ------------------------------------------------------
    # PENCARIAN DATATABLES
    # ------------------------------------------------------
    search = request.GET.get("search[value]", "").strip()

    if search:
        queryset = queryset.filter(
            Q(actor__username__icontains=search)
            | Q(description__icontains=search)
            | Q(object_repr__icontains=search)
            | Q(object_id__icontains=search)
            | Q(model_name__icontains=search)
            | Q(app_label__icontains=search)
            | Q(action__icontains=search)
        )

    records_filtered = queryset.count()

    # ------------------------------------------------------
    # PENGURUTAN DATA
    # ------------------------------------------------------
    ordering_columns = {
        1: "created_at",
        2: "actor__username",
        3: "app_label",
        4: "action",
        5: "object_repr",
        6: "description",
    }

    order_column = request.GET.get("order[0][column]", "1")
    order_direction = request.GET.get("order[0][dir]", "desc")

    try:
        order_column = int(order_column)
    except (TypeError, ValueError):
        order_column = 1

    order_field = ordering_columns.get(
        order_column,
        "created_at",
    )

    if order_direction == "desc":
        order_field = f"-{order_field}"

    queryset = queryset.order_by(order_field, "-id")

    # ------------------------------------------------------
    # PAGINATION DATABASE
    # ------------------------------------------------------
    logs = queryset[start:start + length]

    data = []

    for log in logs:
        data.append({
            "id": log.id,
            "created_at": log.created_at.strftime(
                "%d-%m-%Y %H:%M:%S"
            ),
            "username": log.actor.username,
            "action": log.action,
            "app_label": log.app_label,
            "model_name": log.model_name,
            "object_id": log.object_id,
            "object_repr": log.object_repr,
            "description": log.description,
            "changes": log.changes,
        })

    return JsonResponse({
        "draw": draw,
        "recordsTotal": records_total,
        "recordsFiltered": records_filtered,
        "data": data,
    })
