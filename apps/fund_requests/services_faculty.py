
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.fund_requests.models import (
    FundRequest,
    FundRequestActivity,
    ApprovalHistory,
    FundRequestInstitutionDecision,
)


def validate_faculty_access(fund_request, actor):
    try:
        profile = actor.profile
    except Exception:
        raise ValidationError("Profil pengguna belum tersedia.")

    if profile.role != "FAKULTAS":
        raise ValidationError(
            "Hanya pengguna Fakultas yang dapat melakukan tindakan ini."
        )

    if profile.faculty_id != fund_request.study_program.faculty_id:
        raise ValidationError(
            "Anda tidak berwenang mengakses pengajuan Prodi ini."
        )


@transaction.atomic
def check_fund_request(request_id, actor, notes=""):
    """
    Fakultas menyatakan pengajuan sudah diperiksa.
    """

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .get(pk=request_id)
    )

    validate_faculty_access(fund_request, actor)

    if fund_request.status != FundRequest.Status.SUBMITTED:
        raise ValidationError(
            "Hanya pengajuan yang baru dikirim Prodi yang dapat diperiksa."
        )

    if not fund_request.activities.exists():
        raise ValidationError(
            "Pengajuan belum memiliki rincian aktivitas."
        )

    for item in fund_request.activities.all():
        if item.requested_amount <= 0:
            raise ValidationError(
                "Terdapat nominal pengajuan yang tidak valid."
            )

    fund_request.status = FundRequest.Status.FACULTY_CHECKED
    fund_request.save(update_fields=["status", "updated_at"])

    ApprovalHistory.objects.create(
        fund_request=fund_request,
        action=ApprovalHistory.Action.CHECK,
        actor=actor,
        notes=notes or "Pengajuan telah diperiksa Fakultas.",
    )

    return fund_request


@transaction.atomic
def return_fund_request_for_revision(request_id, actor, notes):
    """
    Fakultas mengembalikan pengajuan kepada Prodi.
    """

    if not notes or not notes.strip():
        raise ValidationError(
            "Catatan revisi wajib diisi."
        )

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .get(pk=request_id)
    )

    validate_faculty_access(fund_request, actor)

    if fund_request.status not in [
        FundRequest.Status.SUBMITTED,
        FundRequest.Status.FACULTY_CHECKED,
    ]:
        raise ValidationError(
            "Status pengajuan tidak memungkinkan untuk dikembalikan."
        )

    fund_request.status = FundRequest.Status.REVISION
    fund_request.notes = notes.strip()
    fund_request.save(
        update_fields=["status", "notes", "updated_at"]
    )

    ApprovalHistory.objects.create(
        fund_request=fund_request,
        action=ApprovalHistory.Action.REVISION,
        actor=actor,
        notes=notes.strip(),
    )

    return fund_request


@transaction.atomic
def forward_fund_request(request_id, actor, notes=""):
    """
    Fakultas meneruskan pengajuan ke Lembaga.
    """

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .get(pk=request_id)
    )

    validate_faculty_access(fund_request, actor)

    if fund_request.status != FundRequest.Status.FACULTY_CHECKED:
        raise ValidationError(
            "Pengajuan harus diperiksa Fakultas sebelum diteruskan."
        )

    if not fund_request.activities.exists():
        raise ValidationError(
            "Pengajuan tidak memiliki rincian aktivitas."
        )

    fund_request.status = FundRequest.Status.FORWARDED
    fund_request.save(update_fields=["status", "updated_at"])

    ApprovalHistory.objects.create(
        fund_request=fund_request,
        action=ApprovalHistory.Action.FORWARD,
        actor=actor,
        notes=notes or "Pengajuan diteruskan ke Lembaga.",
    )

    return fund_request


@transaction.atomic
def record_institution_decision(
    request_id,
    actor,
    decision_date,
    activity_approvals,
    reference_number="",
    document=None,
    notes="",
):
    """
    Mencatat keputusan Lembaga dan nominal persetujuan
    untuk setiap aktivitas pengajuan.

    activity_approvals:
    {
        activity_id: approved_amount,
        ...
    }
    """

    try:
        profile = actor.profile
    except Exception:
        raise ValidationError("Profil pengguna belum tersedia.")

    if profile.role not in ["ADMIN", "FAKULTAS"]:
        raise ValidationError(
            "Pengguna tidak berwenang mencatat keputusan Lembaga."
        )

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .get(pk=request_id)
    )

    if profile.role == "FAKULTAS":
        validate_faculty_access(fund_request, actor)

    if fund_request.status != FundRequest.Status.FORWARDED:
        raise ValidationError(
            "Keputusan hanya dapat dicatat setelah pengajuan diteruskan."
        )

    items = list(
        FundRequestActivity.objects
        .select_for_update()
        .filter(fund_request=fund_request)
    )

    if not items:
        raise ValidationError(
            "Pengajuan belum memiliki rincian aktivitas."
        )

    expected_ids = {item.id for item in items}

    if set(activity_approvals.keys()) != expected_ids:
        raise ValidationError(
            "Nominal keputusan harus diberikan untuk seluruh aktivitas "
            "dalam pengajuan, tanpa aktivitas tambahan."
        )

    total_requested = Decimal("0")
    total_approved = Decimal("0")

    for item in items:
        approved_amount = Decimal(
            str(activity_approvals[item.id])
        )

        if approved_amount < 0:
            raise ValidationError(
                "Nominal persetujuan tidak boleh negatif."
            )

        if approved_amount > item.requested_amount:
            raise ValidationError(
                f"Persetujuan aktivitas '{item.rkat_activity.name}' "
                "melebihi nominal yang diajukan."
            )

        item.approved_amount = approved_amount
        item.full_clean()
        item.save(update_fields=["approved_amount", "updated_at"])

        total_requested += item.requested_amount
        total_approved += approved_amount

    if total_approved == 0:
        decision_status = (
            FundRequestInstitutionDecision.DecisionStatus.REJECTED
        )
        request_status = FundRequest.Status.REJECTED
    elif total_approved == total_requested:
        decision_status = (
            FundRequestInstitutionDecision.DecisionStatus.APPROVED
        )
        request_status = FundRequest.Status.APPROVED
    else:
        decision_status = (
            FundRequestInstitutionDecision.DecisionStatus.PARTIAL
        )
        request_status = FundRequest.Status.PARTIALLY_APPROVED

    decision = FundRequestInstitutionDecision(
        fund_request=fund_request,
        decision_status=decision_status,
        decision_date=decision_date,
        reference_number=reference_number,
        recorded_by=actor,
        notes=notes,
    )

    if document:
        decision.document = document

    decision.full_clean()
    decision.save()

    fund_request.status = request_status
    fund_request.save(update_fields=["status", "updated_at"])

    ApprovalHistory.objects.create(
        fund_request=fund_request,
        action=ApprovalHistory.Action.RECORD_APPROVAL,
        actor=actor,
        notes=(
            f"Keputusan Lembaga: {decision.get_decision_status_display()}. "
            f"Total diajukan: Rp{total_requested:,.2f}. "
            f"Total disetujui: Rp{total_approved:,.2f}. "
            f"{notes}"
        ),
    )

    return decision
