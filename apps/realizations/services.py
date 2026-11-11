
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.fund_requests.models import FundRequest
from apps.disbursements.models import FacultyHandover
from apps.realizations.models import Realization
from apps.accounts.audit import log_audit
from apps.accounts.models import AuditLog


APPROVED_STATUSES = [
    FundRequest.Status.APPROVED,
    FundRequest.Status.PARTIALLY_APPROVED,
]


def get_handover_total(fund_request):
    return (
        FacultyHandover.objects
        .filter(fund_request=fund_request)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )


def get_realization_total(fund_request):
    return (
        Realization.objects
        .filter(request_activity__fund_request=fund_request)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )



@transaction.atomic
def record_realization(
    activity_id,
    realization_date,
    description,
    amount,
    evidence,
    actor,
    notes="",
):
    """
    Prodi mencatat realisasi untuk aktivitas pengajuan.
    Realisasi dibatasi oleh nominal persetujuan aktivitas
    dan total dana yang telah diserahkan Fakultas kepada Prodi.
    """

    try:
        profile = actor.profile
    except Exception:
        raise ValidationError("Profil pengguna belum tersedia.")

    if profile.role != "PRODI":
        raise ValidationError(
            "Hanya pengguna Prodi yang dapat mencatat realisasi."
        )

    from apps.fund_requests.models import FundRequestActivity

    request_activity = (
        FundRequestActivity.objects
        .select_for_update()
        .select_related("fund_request", "rkat_activity")
        .filter(pk=activity_id)
        .first()
    )

    if not request_activity:
        raise ValidationError(
            "Aktivitas pengajuan tidak ditemukan."
        )

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .get(pk=request_activity.fund_request_id)
    )

    if profile.study_program_id != fund_request.study_program_id:
        raise ValidationError(
            "Anda tidak berwenang mencatat realisasi Prodi lain."
        )

    if fund_request.status not in APPROVED_STATUSES:
        raise ValidationError(
            "Realisasi hanya dapat dicatat untuk pengajuan "
            "yang sudah disetujui Lembaga."
        )

    if request_activity.approved_amount is None:
        raise ValidationError(
            "Nominal persetujuan aktivitas belum tersedia."
        )

    try:
        amount = Decimal(str(amount))
    except Exception:
        raise ValidationError("Nominal realisasi tidak valid.")

    if not amount.is_finite() or amount <= 0:
        raise ValidationError(
            "Nominal realisasi harus lebih besar dari nol."
        )

    if not evidence:
        raise ValidationError(
            "Bukti pengeluaran wajib dilampirkan."
        )

    activity_realized = (
        Realization.objects
        .filter(request_activity=request_activity)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    if activity_realized + amount > request_activity.approved_amount:
        remaining = request_activity.approved_amount - activity_realized
        raise ValidationError(
            f"Realisasi melebihi nominal persetujuan aktivitas. "
            f"Sisa: Rp{remaining:,.2f}."
        )

    handed_over = get_handover_total(fund_request)
    realized = get_realization_total(fund_request)

    if realized + amount > handed_over:
        remaining = handed_over - realized
        raise ValidationError(
            f"Realisasi melebihi dana yang sudah diterima Prodi. "
            f"Sisa dana: Rp{remaining:,.2f}."
        )

    realization = Realization(
        request_activity=request_activity,
        realization_date=realization_date,
        description=description,
        amount=amount,
        evidence=evidence,
        recorded_by=actor,
        notes=notes,
    )

    realization.full_clean()
    realization.save()

    log_audit(
        actor=actor,
        action=AuditLog.Action.CREATE,
        instance=realization,
        description=(
            f"Mencatat realisasi dana untuk pengajuan "
            f"{fund_request.request_number}, "
            f"aktivitas: {request_activity.rkat_activity.name}."
        ),
        changes={
            "fund_request": fund_request.request_number,
            "rkat_activity": request_activity.rkat_activity.name,
            "requested_amount": str(request_activity.requested_amount),
            "approved_amount": str(request_activity.approved_amount),
            "realization_amount": str(amount),
            "realization_date": realization_date.isoformat(),
            "description": description,
            "recorded_by": actor.username,
        },
    )

    return realization
