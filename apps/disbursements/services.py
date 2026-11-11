
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.fund_requests.models import FundRequest
from apps.disbursements.models import (
    InstitutionDisbursement,
    FacultyHandover,
)
from apps.accounts.audit import log_audit
from apps.accounts.models import AuditLog

APPROVED_STATUSES = [
    FundRequest.Status.APPROVED,
    FundRequest.Status.PARTIALLY_APPROVED,
]


def validate_faculty_access(fund_request, actor):
    try:
        profile = actor.profile
    except Exception:
        raise ValidationError("Profil pengguna belum tersedia.")

    if profile.role != "FAKULTAS":
        raise ValidationError(
            "Hanya pengguna Fakultas yang dapat mencatat transaksi dana."
        )

    if profile.faculty_id != fund_request.study_program.faculty_id:
        raise ValidationError(
            "Anda tidak berwenang mengelola dana Prodi ini."
        )


def get_institution_received_total(fund_request):
    return (
        InstitutionDisbursement.objects
        .filter(fund_request=fund_request)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )


def get_faculty_handover_total(fund_request):
    return (
        FacultyHandover.objects
        .filter(fund_request=fund_request)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )


@transaction.atomic
def record_institution_disbursement(
    request_id,
    amount,
    disbursed_at,
    actor,
    reference_number="",
    notes="",
):
    """
    Mencatat dana yang diterima Fakultas dari Lembaga.
    Dapat dilakukan secara bertahap.
    """

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .get(pk=request_id)
    )

    validate_faculty_access(fund_request, actor)

    if fund_request.status not in APPROVED_STATUSES:
        raise ValidationError(
            "Pencairan hanya dapat dicatat untuk pengajuan "
            "yang sudah disetujui Lembaga."
        )

    amount = Decimal(str(amount))

    if not amount.is_finite() or amount <= 0:
        raise ValidationError(
            "Nominal pencairan harus lebih besar dari nol."
        )

    received_total = get_institution_received_total(fund_request)
    approved_total = Decimal(str(fund_request.approved_total))

    if received_total + amount > approved_total:
        remaining = approved_total - received_total
        raise ValidationError(
            f"Pencairan melebihi nominal persetujuan Lembaga. "
            f"Sisa yang dapat diterima: Rp{remaining:,.2f}."
        )

    disbursement = InstitutionDisbursement(
        fund_request=fund_request,
        amount=amount,
        disbursed_at=disbursed_at,
        reference_number=reference_number,
        recorded_by=actor,
        notes=notes,
    )

    disbursement.full_clean()
    disbursement.save()

    log_audit(
        actor=actor,
        action=AuditLog.Action.CREATE,
        instance=disbursement,
        description=(
            f"Mencatat penerimaan dana dari Lembaga untuk "
            f"pengajuan {fund_request.request_number}."
        ),
        changes={
            "fund_request": fund_request.request_number,
            "amount": str(amount),
            "disbursed_at": disbursed_at.isoformat(),
            "reference_number": reference_number,
            "recorded_by": actor.username,
        },
    )

    return disbursement


@transaction.atomic
def record_faculty_handover(
    request_id,
    amount,
    handed_over_at,
    actor,
    reference_number="",
    received_by="",
    notes="",
):
    """
    Mencatat dana yang diserahkan Fakultas kepada Prodi.
    Total penyerahan tidak boleh melebihi dana yang sudah
    diterima Fakultas dari Lembaga.
    """

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .get(pk=request_id)
    )

    validate_faculty_access(fund_request, actor)

    if fund_request.status not in APPROVED_STATUSES:
        raise ValidationError(
            "Penyerahan dana hanya dapat dilakukan untuk "
            "pengajuan yang sudah disetujui Lembaga."
        )

    amount = Decimal(str(amount))

    if not amount.is_finite() or amount <= 0:
        raise ValidationError(
            "Nominal penyerahan harus lebih besar dari nol."
        )

    received_total = get_institution_received_total(fund_request)
    handed_over_total = get_faculty_handover_total(fund_request)

    available_at_faculty = received_total - handed_over_total

    if amount > available_at_faculty:
        raise ValidationError(
            f"Nominal penyerahan melebihi dana yang tersedia di Fakultas. "
            f"Dana tersedia: Rp{available_at_faculty:,.2f}."
        )

    handover = FacultyHandover(
        fund_request=fund_request,
        amount=amount,
        handed_over_at=handed_over_at,
        reference_number=reference_number,
        recorded_by=actor,
        received_by=received_by,
        notes=notes,
    )

    handover.full_clean()
    handover.save()

    log_audit(
        actor=actor,
        action=AuditLog.Action.CREATE,
        instance=handover,
        description=(
            f"Mencatat serah terima dana dari Fakultas kepada Prodi "
            f"untuk pengajuan {fund_request.request_number}."
        ),
        changes={
            "fund_request": fund_request.request_number,
            "amount": str(amount),
            "handed_over_at": handed_over_at.isoformat(),
            "reference_number": reference_number,
            "received_by": received_by,
            "recorded_by": actor.username,
        },
    )

    return handover

def get_disbursement_summary(fund_request):
    """
    Ringkasan dana untuk satu pengajuan.
    """

    approved = Decimal(str(fund_request.approved_total))

    received = get_institution_received_total(fund_request)
    handed_over = get_faculty_handover_total(fund_request)

    return {
        "approved_total": approved,
        "received_total": received,
        "handed_over_total": handed_over,
        "remaining_at_institution": approved - received,
        "remaining_at_faculty": received - handed_over,
        "remaining_to_be_handed_over": received - handed_over,
    }
