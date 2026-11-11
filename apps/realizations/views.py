
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_http_methods

from apps.accounts.models import UserProfile
from apps.fund_requests.models import FundRequest, FundRequestActivity
from apps.disbursements.models import FacultyHandover
from django.http import FileResponse, Http404
from django.views.decorators.http import require_GET

from apps.accounts.audit import log_audit, get_client_ip
from apps.accounts.models import AuditLog

from .models import Realization
from .services import (
    APPROVED_STATUSES,
    get_handover_total,
    get_realization_total,
    record_realization,
)


def get_prodi_profile(user):
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return None

    if profile.role != "PRODI":
        return None

    return profile


@login_required
def realization_index(request):
    profile = get_prodi_profile(request.user)

    if not profile:
        return HttpResponseForbidden(
            "Halaman ini hanya dapat diakses oleh Prodi."
        )

    fund_requests = (
        FundRequest.objects
        .filter(
            study_program_id=profile.study_program_id,
            status__in=APPROVED_STATUSES,
        )
        .select_related("study_program", "budget_year")
        .prefetch_related("activities")
        .order_by("-created_at")
    )

    rows = []

    for fund_request in fund_requests:
        activities = []

        for activity in fund_request.activities.all():
            realized = (
                Realization.objects
                .filter(request_activity=activity)
                .aggregate(total=Sum("amount"))["total"]
                or Decimal("0")
            )

            approved = activity.approved_amount or Decimal("0")

            activities.append({
                "activity": activity,
                "approved": approved,
                "realized": realized,
                "remaining": approved - realized,
            })

        rows.append({
            "fund_request": fund_request,
            "activities": activities,
            "handover_total": get_handover_total(fund_request),
            "realization_total": get_realization_total(fund_request),
            "remaining_handover": (
                get_handover_total(fund_request)
                - get_realization_total(fund_request)
            ),
        })

    return render(
        request,
        "realizations/index.html",
        {"rows": rows},
    )


@login_required
@require_http_methods(["GET", "POST"])
def realization_create(request, activity_id):
    profile = get_prodi_profile(request.user)

    if not profile:
        return HttpResponseForbidden(
            "Halaman ini hanya dapat diakses oleh Prodi."
        )

    activity = get_object_or_404(
        FundRequestActivity.objects.select_related(
            "fund_request",
            "fund_request__study_program",
        ),
        pk=activity_id,
        fund_request__study_program_id=profile.study_program_id,
        fund_request__status__in=APPROVED_STATUSES,
    )

    fund_request = activity.fund_request

    realized = (
        Realization.objects
        .filter(request_activity=activity)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    approved = activity.approved_amount or Decimal("0")
    handover_total = get_handover_total(fund_request)
    request_realized = get_realization_total(fund_request)

    remaining_activity = approved - realized
    remaining_handover = handover_total - request_realized

    if request.method == "POST":
        realization_date_value = request.POST.get("realization_date", "")
        description = request.POST.get("description", "").strip()
        amount = request.POST.get("amount", "").strip()
        evidence = request.FILES.get("evidence")
        notes = request.POST.get("notes", "").strip()

        try:
            realization_date = date.fromisoformat(
                realization_date_value
            )
        except (ValueError, TypeError):
            realization_date = None

        errors = []

        if not realization_date:
            errors.append("Tanggal pengeluaran tidak valid.")

        if not description:
            errors.append("Uraian pengeluaran wajib diisi.")
        elif len(description) > 255:
            errors.append("Uraian maksimal 255 karakter.")

        try:
            parsed_amount = Decimal(amount)
            if not parsed_amount.is_finite() or parsed_amount <= 0:
                errors.append("Nominal harus lebih besar dari nol.")
        except (InvalidOperation, TypeError, ValueError):
            parsed_amount = None
            errors.append("Nominal realisasi tidak valid.")

        if not evidence:
            errors.append("Bukti pengeluaran wajib dilampirkan.")
        else:
            extension = Path(evidence.name).suffix.lower()
            allowed_extensions = [".pdf", ".jpg", ".jpeg", ".png"]

            if extension not in allowed_extensions:
                errors.append(
                    "Bukti harus berupa file PDF, JPG, JPEG, atau PNG."
                )

            if evidence.size > 5 * 1024 * 1024:
                errors.append("Ukuran bukti maksimal 5 MB.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            try:
                record_realization(
                    activity_id=activity.pk,
                    realization_date=realization_date,
                    description=description,
                    amount=parsed_amount,
                    evidence=evidence,
                    actor=request.user,
                    notes=notes,
                )
            except ValidationError as exc:
                if hasattr(exc, "messages"):
                    for error in exc.messages:
                        messages.error(request, error)
                else:
                    messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "Realisasi penggunaan dana berhasil dicatat."
                )
                return redirect("realizations:index")

    context = {
        "activity": activity,
        "fund_request": fund_request,
        "approved": approved,
        "realized": realized,
        "remaining_activity": remaining_activity,
        "handover_total": handover_total,
        "request_realized": request_realized,
        "remaining_handover": remaining_handover,
    }

    return render(
        request,
        "realizations/create.html",
        context,
    )


@login_required
def realization_history(request, activity_id):
    profile = get_prodi_profile(request.user)

    if not profile:
        return HttpResponseForbidden(
            "Halaman ini hanya dapat diakses oleh Prodi."
        )

    activity = get_object_or_404(
        FundRequestActivity.objects.select_related(
            "fund_request",
            "fund_request__study_program",
            "rkat_activity",
        ),
        pk=activity_id,
        fund_request__study_program_id=profile.study_program_id,
    )

    realizations = (
        Realization.objects
        .filter(request_activity=activity)
        .select_related("recorded_by")
        .order_by("-realization_date", "-created_at")
    )

    total_realized = (
        realizations.aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    context = {
        "activity": activity,
        "fund_request": activity.fund_request,
        "realizations": realizations,
        "total_realized": total_realized,
        "approved_amount": activity.approved_amount or Decimal("0"),
        "remaining": (
            (activity.approved_amount or Decimal("0"))
            - total_realized
        ),
    }

    return render(
        request,
        "realizations/history.html",
        context,
    )


@login_required
def realization_index(request):
    profile = get_prodi_profile(request.user)

    if not profile:
        return HttpResponseForbidden(
            "Halaman ini hanya dapat diakses oleh Prodi."
        )

    fund_requests = (
        FundRequest.objects
        .filter(
            study_program_id=profile.study_program_id,
            status__in=APPROVED_STATUSES,
        )
        .select_related("study_program", "budget_year")
        .prefetch_related("activities")
        .order_by("-created_at")
    )

    rows = []

    grand_approved = Decimal("0")
    grand_handover = Decimal("0")
    grand_realized = Decimal("0")

    for fund_request in fund_requests:
        activities = []
        approved_total = Decimal("0")

        for activity in fund_request.activities.all():
            realized = (
                Realization.objects
                .filter(request_activity=activity)
                .aggregate(total=Sum("amount"))["total"]
                or Decimal("0")
            )

            approved = activity.approved_amount or Decimal("0")
            remaining = approved - realized

            approved_total += approved

            activities.append({
                "activity": activity,
                "approved": approved,
                "realized": realized,
                "remaining": remaining,
            })

        handover_total = get_handover_total(fund_request)
        realization_total = get_realization_total(fund_request)

        remaining_handover = handover_total - realization_total

        realization_percentage = Decimal("0")

        if handover_total > 0:
            realization_percentage = (
                realization_total / handover_total
            ) * Decimal("100")

        rows.append({
            "fund_request": fund_request,
            "activities": activities,
            "approved_total": approved_total,
            "handover_total": handover_total,
            "realization_total": realization_total,
            "remaining_handover": remaining_handover,
            "realization_percentage": realization_percentage,
        })

        grand_approved += approved_total
        grand_handover += handover_total
        grand_realized += realization_total

    grand_remaining = grand_handover - grand_realized

    grand_percentage = Decimal("0")

    if grand_handover > 0:
        grand_percentage = (
            grand_realized / grand_handover
        ) * Decimal("100")

    summary = {
        "approved": grand_approved,
        "handover": grand_handover,
        "realized": grand_realized,
        "remaining": grand_remaining,
        "percentage": grand_percentage,
    }

    return render(
        request,
        "realizations/index.html",
        {
            "rows": rows,
            "summary": summary,
        },
    )


@login_required
@require_GET
def realization_evidence_download(request, realization_id):
    """
    Mengunduh bukti realisasi dengan pembatasan akses berdasarkan role.
    """

    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        return HttpResponseForbidden(
            "Profil pengguna belum tersedia."
        )

    queryset = Realization.objects.select_related(
        "request_activity",
        "request_activity__fund_request",
        "request_activity__fund_request__study_program",
        "request_activity__fund_request__study_program__faculty",
    )

    if profile.role == "ADMIN":
        pass

    elif profile.role == "PRODI":
        queryset = queryset.filter(
            request_activity__fund_request__study_program_id=(
                profile.study_program_id
            )
        )

    elif profile.role == "FAKULTAS":
        queryset = queryset.filter(
            request_activity__fund_request__study_program__faculty_id=(
                profile.faculty_id
            )
        )

    else:
        return HttpResponseForbidden(
            "Anda tidak memiliki akses mengunduh bukti realisasi."
        )

    realization = get_object_or_404(
        queryset,
        pk=realization_id,
    )

    if not realization.evidence:
        raise Http404("Bukti realisasi tidak ditemukan.")

    try:
        file_handle = realization.evidence.open("rb")
    except (FileNotFoundError, OSError, ValueError):
        raise Http404("File bukti realisasi tidak ditemukan.")

    log_audit(
        actor=request.user,
        action=AuditLog.Action.OTHER,
        instance=realization,
        description=(
            "Mengunduh bukti realisasi penggunaan dana "
            f"pengajuan {realization.request_activity.fund_request.request_number}."
        ),
        changes={
            "realization_id": realization.pk,
            "request_number": (
                realization.request_activity.fund_request.request_number
            ),
            "activity": str(
                realization.request_activity.rkat_activity
            ),
            "activity_type": "EVIDENCE_DOWNLOAD",
        },
        ip_address=get_client_ip(request),
    )

    return FileResponse(
        file_handle,
        as_attachment=True,
        filename=Path(realization.evidence.name).name,
    )