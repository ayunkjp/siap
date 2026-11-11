
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_POST, require_http_methods

from apps.accounts.models import UserProfile
from apps.fund_requests.models import FundRequest

from django.http import (
    HttpResponseForbidden,
    FileResponse,
    Http404,
)
from django.views.decorators.http import require_GET

from apps.accounts.models import AuditLog
from apps.accounts.audit import log_audit

from .models import (
    AccountabilityReport,
    AccountabilityAttachment,
)
from .services import (
    create_accountability_report,
    submit_accountability_report,
    return_accountability_report_for_revision,
    accept_accountability_report,
)
from apps.accounts.audit import get_client_ip

def get_accountability_profile(user):
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def get_report_queryset_for_user(user):
    """
    Membatasi LPJ berdasarkan role dan lingkup pengguna.
    """

    profile = get_accountability_profile(user)

    queryset = (
        AccountabilityReport.objects
        .select_related(
            "fund_request",
            "fund_request__study_program",
            "fund_request__budget_year",
            "prepared_by",
            "reviewed_by",
        )
        .prefetch_related("attachments", "histories")
    )

    if not profile:
        return queryset.none()

    if profile.role == "ADMIN":
        return queryset

    if profile.role == "PRODI":
        return queryset.filter(
            fund_request__study_program_id=profile.study_program_id
        )

    if profile.role == "FAKULTAS":
        return queryset.filter(
            fund_request__study_program__faculty_id=profile.faculty_id
        )

    return queryset.none()


@login_required
def accountability_index(request):
    profile = get_accountability_profile(request.user)

    if not profile or profile.role not in ["ADMIN", "PRODI", "FAKULTAS"]:
        return HttpResponseForbidden(
            "Anda tidak memiliki akses ke halaman LPJ."
        )

    reports = get_report_queryset_for_user(request.user).order_by(
        "-created_at"
    )

    available_requests = FundRequest.objects.none()

    if profile.role == "PRODI":
        available_requests = (
            FundRequest.objects
            .filter(
                study_program_id=profile.study_program_id,
                status__in=[
                    FundRequest.Status.APPROVED,
                    FundRequest.Status.PARTIALLY_APPROVED,
                ],
                accountability_report__isnull=True,
            )
            .select_related("budget_year")
            .order_by("-created_at")
        )

    return render(
        request,
        "accountability/index.html",
        {
            "reports": reports,
            "available_requests": available_requests,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def accountability_create(request, fund_request_id):
    profile = get_accountability_profile(request.user)

    if not profile or profile.role != "PRODI":
        return HttpResponseForbidden(
            "Hanya Prodi yang dapat membuat LPJ."
        )

    fund_request = get_object_or_404(
        FundRequest.objects.select_related(
            "study_program",
            "budget_year",
        ),
        pk=fund_request_id,
        study_program_id=profile.study_program_id,
        status__in=[
            FundRequest.Status.APPROVED,
            FundRequest.Status.PARTIALLY_APPROVED,
        ],
        accountability_report__isnull=True,
    )

    if request.method == "POST":
        try:
            report = create_accountability_report(
                fund_request_id=fund_request.pk,
                actor=request.user,
                ip_address=get_client_ip(request),
            )
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
        else:
            messages.success(request, "LPJ berhasil dibuat.")
            return redirect(
                "accountability:detail",
                pk=report.pk,
            )

    return render(
        request,
        "accountability/create.html",
        {"fund_request": fund_request},
    )


@login_required
def accountability_detail(request, pk):
    profile = get_accountability_profile(request.user)

    if not profile or profile.role not in ["ADMIN", "PRODI", "FAKULTAS"]:
        return HttpResponseForbidden(
            "Anda tidak memiliki akses ke LPJ ini."
        )

    report = get_object_or_404(
        get_report_queryset_for_user(request.user),
        pk=pk,
    )

    activities = report.fund_request.activities.prefetch_related(
        "realizations"
    ).select_related("rkat_activity")

    realization_rows = []

    for activity in activities:
        realizations = activity.realizations.select_related(
            "recorded_by"
        ).order_by("realization_date", "created_at")

        realization_rows.append({
            "activity": activity,
            "realizations": realizations,
            "total": sum(
                (item.amount for item in realizations),
                start=__import__("decimal").Decimal("0"),
            ),
        })

    can_edit = (
        profile.role == "PRODI"
        and report.fund_request.study_program_id == profile.study_program_id
        and report.prepared_by_id == request.user.pk
        and report.status in [
            AccountabilityReport.Status.DRAFT,
            AccountabilityReport.Status.REVISION,
        ]
    )

    can_review = (
        profile.role == "FAKULTAS"
        and report.status == AccountabilityReport.Status.SUBMITTED
    )

    return render(
        request,
        "accountability/detail.html",
        {
            "report": report,
            "realization_rows": realization_rows,
            "can_edit": can_edit,
            "can_review": can_review,
        },
    )


@login_required
@require_POST
def accountability_attachment_add(request, pk):
    profile = get_accountability_profile(request.user)

    if not profile or profile.role != "PRODI":
        return HttpResponseForbidden(
            "Hanya Prodi yang dapat mengunggah lampiran LPJ."
        )

    report = get_object_or_404(
        get_report_queryset_for_user(request.user),
        pk=pk,
        prepared_by=request.user,
        status__in=[
            AccountabilityReport.Status.DRAFT,
            AccountabilityReport.Status.REVISION,
        ],
    )

    title = request.POST.get("title", "").strip()
    uploaded_file = request.FILES.get("file")

    errors = []

    if not title:
        errors.append("Nama dokumen wajib diisi.")

    if len(title) > 255:
        errors.append("Nama dokumen maksimal 255 karakter.")

    if not uploaded_file:
        errors.append("File lampiran wajib dipilih.")
    else:
        extension = Path(uploaded_file.name).suffix.lower()
        allowed_extensions = [
            ".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx", ".xlsx",
        ]

        if extension not in allowed_extensions:
            errors.append(
                "Format file harus PDF, JPG, PNG, DOC, DOCX, atau XLSX."
            )

        if uploaded_file.size > 10 * 1024 * 1024:
            errors.append("Ukuran file maksimal 10 MB.")

    if errors:
        for error in errors:
            messages.error(request, error)
    else:
        attachment = AccountabilityAttachment(
            report=report,
            title=title,
            file=uploaded_file,
            uploaded_by=request.user,
        )

        try:
            attachment.full_clean()
            attachment.save()
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
        else:
            messages.success(request, "Lampiran LPJ berhasil diunggah.")

    return redirect("accountability:detail", pk=report.pk)


@login_required
@require_POST
def accountability_submit(request, pk):
    report = get_object_or_404(
        get_report_queryset_for_user(request.user),
        pk=pk,
    )

    notes = request.POST.get("notes", "").strip()

    try:
        submit_accountability_report(
            report_id=report.pk,
            actor=request.user,
            notes=notes,
            ip_address=get_client_ip(request),
        )
    except ValidationError as exc:
        for error in exc.messages:
            messages.error(request, error)
    else:
        messages.success(request, "LPJ berhasil dikirim ke Fakultas.")

    return redirect("accountability:detail", pk=report.pk)


@login_required
@require_POST
def accountability_revision(request, pk):
    report = get_object_or_404(
        get_report_queryset_for_user(request.user),
        pk=pk,
    )

    notes = request.POST.get("notes", "").strip()

    try:
        return_accountability_report_for_revision(
        report_id=report.pk,
        actor=request.user,
        notes=notes,
        ip_address=get_client_ip(request),
    )
    except ValidationError as exc:
        for error in exc.messages:
            messages.error(request, error)
    else:
        messages.success(request, "LPJ dikembalikan kepada Prodi.")

    return redirect("accountability:detail", pk=report.pk)


@login_required
@require_POST
def accountability_accept(request, pk):
    report = get_object_or_404(
        get_report_queryset_for_user(request.user),
        pk=pk,
    )

    notes = request.POST.get("notes", "").strip()

    try:
        accept_accountability_report(
            report_id=report.pk,
            actor=request.user,
            notes=notes,
            ip_address=get_client_ip(request),
        )
    except ValidationError as exc:
        for error in exc.messages:
            messages.error(request, error)
    else:
        messages.success(request, "LPJ berhasil diterima Fakultas.")

    return redirect("accountability:detail", pk=report.pk)

@login_required
@require_GET
def accountability_attachment_download(request, attachment_id):
    """
    Mengunduh lampiran LPJ setelah memeriksa hak akses pengguna.
    """

    attachment = get_object_or_404(
        AccountabilityAttachment.objects.select_related(
            "report",
            "report__fund_request",
        ),
        pk=attachment_id,
        report__in=get_report_queryset_for_user(request.user),
    )

    try:
        file_handle = attachment.file.open("rb")
    except (FileNotFoundError, OSError, ValueError):
        raise Http404("Dokumen LPJ tidak ditemukan.")

    log_audit(
        actor=request.user,
        action=AuditLog.Action.OTHER,
        instance=attachment,
        description=(
            f"Mengunduh lampiran LPJ "
            f"{attachment.report.fund_request.request_number}."
        ),
        changes={
            "attachment_id": attachment.pk,
            "document_title": attachment.title,
            "report_id": attachment.report_id,
            "activity": "DOWNLOAD",
        },
        ip_address=get_client_ip(request),
    )

    return FileResponse(
        file_handle,
        as_attachment=True,
        filename=Path(attachment.file.name).name,
    )