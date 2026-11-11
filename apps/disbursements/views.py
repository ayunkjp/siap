
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_http_methods

from apps.accounts.models import UserProfile
from apps.fund_requests.models import FundRequest

from .models import InstitutionDisbursement, FacultyHandover
from .services import (
    APPROVED_STATUSES,
    get_disbursement_summary,
    record_institution_disbursement,
    record_faculty_handover,
)


def get_approved_fund_requests_for_faculty(user):
    """
    Mengambil pengajuan yang sudah disetujui Lembaga
    dan berada dalam cakupan Fakultas pengguna.
    """

    return (
        FundRequest.objects
        .filter(
            study_program__faculty_id=user.profile.faculty_id,
            status__in=APPROVED_STATUSES,
        )
        .select_related(
            "study_program",
            "study_program__faculty",
            "budget_year",
        )
        .order_by("-created_at")
    )


def parse_transaction_date(value):
    """
    Mengubah tanggal dari input HTML menjadi date.
    """

    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValidationError("Tanggal transaksi tidak valid.")

@login_required
def disbursement_index(request):
    """
    Daftar pencairan Lembaga ke Fakultas dan
    penyerahan Fakultas ke Prodi.
    """

    if request.user.profile.role != UserProfile.Role.FAKULTAS:
        return HttpResponseForbidden(
            "Halaman ini hanya dapat diakses oleh Fakultas."
        )

    fund_requests = get_approved_fund_requests_for_faculty(
        request.user
    )

    rows = []

    for fund_request in fund_requests:
        rows.append({
            "fund_request": fund_request,
            "summary": get_disbursement_summary(fund_request),
        })

    return render(
        request,
        "disbursements/index.html",
        {
            "rows": rows,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def institution_disbursement_create(request, request_id):
    """
    Form pencatatan pencairan dana dari Lembaga ke Fakultas.
    """

    if request.user.profile.role != UserProfile.Role.FAKULTAS:
        return HttpResponseForbidden(
            "Hanya Fakultas yang dapat mencatat pencairan."
        )

    fund_request = get_object_or_404(
        get_approved_fund_requests_for_faculty(request.user),
        pk=request_id,
    )

    summary = get_disbursement_summary(fund_request)

    if request.method == "POST":
        try:
            amount = Decimal(
                request.POST.get("amount", "")
            )

            if not amount.is_finite():
                raise ValidationError("Nominal pencairan tidak valid.")

            disbursed_at = parse_transaction_date(
                request.POST.get("disbursed_at", "")
            )

            record_institution_disbursement(
                request_id=fund_request.pk,
                amount=amount,
                disbursed_at=disbursed_at,
                actor=request.user,
                reference_number=request.POST.get(
                    "reference_number", ""
                ).strip(),
                notes=request.POST.get("notes", "").strip(),
            )

            messages.success(
                request,
                "Pencairan dana dari Lembaga berhasil dicatat.",
            )

            return redirect("disbursements:index")

        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Nominal pencairan tidak valid.")

        except ValidationError as e:
            messages.error(request, " ".join(e.messages))

    return render(
        request,
        "disbursements/institution_form.html",
        {
            "fund_request": fund_request,
            "summary": summary,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def faculty_handover_create(request, request_id):
    """
    Form pencatatan penyerahan dana dari Fakultas ke Prodi.
    """

    if request.user.profile.role != UserProfile.Role.FAKULTAS:
        return HttpResponseForbidden(
            "Hanya Fakultas yang dapat mencatat penyerahan dana."
        )

    fund_request = get_object_or_404(
        get_approved_fund_requests_for_faculty(request.user),
        pk=request_id,
    )

    summary = get_disbursement_summary(fund_request)

    if request.method == "POST":
        try:
            amount = Decimal(
                request.POST.get("amount", "")
            )

            if not amount.is_finite():
                raise ValidationError("Nominal penyerahan tidak valid.")

            handed_over_at = parse_transaction_date(
                request.POST.get("handed_over_at", "")
            )

            record_faculty_handover(
                request_id=fund_request.pk,
                amount=amount,
                handed_over_at=handed_over_at,
                actor=request.user,
                reference_number=request.POST.get(
                    "reference_number", ""
                ).strip(),
                received_by=request.POST.get(
                    "received_by", ""
                ).strip(),
                notes=request.POST.get("notes", "").strip(),
            )

            messages.success(
                request,
                "Penyerahan dana kepada Prodi berhasil dicatat.",
            )

            return redirect("disbursements:index")

        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Nominal penyerahan tidak valid.")

        except ValidationError as e:
            messages.error(request, " ".join(e.messages))

    return render(
        request,
        "disbursements/handover_form.html",
        {
            "fund_request": fund_request,
            "summary": summary,
        },
    )
