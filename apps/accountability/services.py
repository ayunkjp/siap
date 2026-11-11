
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.accounts.audit import log_audit
from apps.accounts.models import AuditLog

from apps.fund_requests.models import FundRequest
from apps.realizations.models import Realization

from .models import (
    AccountabilityReport,
    AccountabilityHistory,
)


APPROVED_STATUSES = [
    FundRequest.Status.APPROVED,
    FundRequest.Status.PARTIALLY_APPROVED,
]


# ==========================================================
# VALIDASI AKSES PENGGUNA
# ==========================================================

def validate_prodi_access(fund_request, actor):
    """
    Memastikan pengguna merupakan Prodi pemilik pengajuan.
    """

    try:
        profile = actor.profile
    except Exception:
        raise ValidationError(
            "Profil pengguna belum tersedia."
        )

    if profile.role != "PRODI":
        raise ValidationError(
            "Hanya pengguna Prodi yang dapat melakukan tindakan ini."
        )

    if profile.study_program_id != fund_request.study_program_id:
        raise ValidationError(
            "Anda tidak berwenang mengakses pengajuan Prodi lain."
        )

    return profile


def validate_faculty_access(report, actor):
    """
    Memastikan pengguna Fakultas sesuai dengan Fakultas pemilik Prodi.
    """

    try:
        profile = actor.profile
    except Exception:
        raise ValidationError(
            "Profil pengguna belum tersedia."
        )

    if profile.role != "FAKULTAS":
        raise ValidationError(
            "Hanya pengguna Fakultas yang dapat memeriksa LPJ."
        )

    if (
        profile.faculty_id
        != report.fund_request.study_program.faculty_id
    ):
        raise ValidationError(
            "Anda tidak berwenang memeriksa LPJ Prodi ini."
        )

    return profile


# ==========================================================
# VALIDASI KELENGKAPAN LPJ
# ==========================================================

def validate_accountability_completeness(report):
    """
    Memeriksa kelengkapan realisasi dan dokumen LPJ.

    Ketentuan:
    1. Harus ada minimal satu transaksi realisasi.
    2. Harus ada minimal satu lampiran LPJ.
    3. Setiap transaksi realisasi harus memiliki bukti.
    """

    fund_request = report.fund_request

    # Periksa apakah sudah ada realisasi.
    realizations = Realization.objects.filter(
        request_activity__fund_request=fund_request
    )

    if not realizations.exists():
        raise ValidationError(
            "Belum ada realisasi penggunaan dana untuk dilaporkan."
        )

    # Periksa lampiran LPJ.
    if not report.attachments.exists():
        raise ValidationError(
            "LPJ harus memiliki minimal satu dokumen lampiran."
        )

    # Periksa bukti setiap transaksi realisasi.
    if realizations.filter(evidence="").exists():
        raise ValidationError(
            "Masih terdapat transaksi realisasi yang belum memiliki "
            "bukti pengeluaran."
        )

    for realization in realizations:
        if not realization.evidence:
            raise ValidationError(
                "Setiap transaksi realisasi wajib memiliki "
                "bukti pengeluaran."
            )

    return True


# ==========================================================
# MEMBUAT LPJ
# ==========================================================

@transaction.atomic
def create_accountability_report(
    fund_request_id,
    actor,
    ip_address=None,
):
    """
    Membuat satu LPJ untuk satu pengajuan dana.
    """

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program")
        .filter(pk=fund_request_id)
        .first()
    )

    if not fund_request:
        raise ValidationError(
            "Pengajuan dana tidak ditemukan."
        )

    validate_prodi_access(fund_request, actor)

    if fund_request.status not in APPROVED_STATUSES:
        raise ValidationError(
            "LPJ hanya dapat dibuat untuk pengajuan "
            "yang sudah disetujui Lembaga."
        )

    if hasattr(fund_request, "accountability_report"):
        raise ValidationError(
            "LPJ untuk pengajuan ini sudah pernah dibuat."
        )

    report = AccountabilityReport(
        fund_request=fund_request,
        prepared_by=actor,
        status=AccountabilityReport.Status.DRAFT,
    )

    report.full_clean()
    report.save()

    log_audit(
        actor=actor,
        action=AuditLog.Action.CREATE,
        instance=report,
        description=(
            f"Membuat LPJ untuk pengajuan "
            f"{fund_request.request_number}."
        ),
        changes={
            "fund_request": fund_request.request_number,
            "study_program": str(fund_request.study_program),
            "status": report.status,
            "prepared_by": actor.username,
        },
        ip_address=ip_address,
    )

    return report


# ==========================================================
# MENGIRIM LPJ KE FAKULTAS
# ==========================================================

@transaction.atomic
def submit_accountability_report(report_id, actor, notes="", ip_address=None):
    """
    Prodi mengirim LPJ kepada Fakultas.
    """

    report = (
        AccountabilityReport.objects
        .select_for_update()
        .select_related(
            "fund_request",
            "fund_request__study_program",
        )
        .filter(pk=report_id)
        .first()
    )

    if not report:
        raise ValidationError(
            "LPJ tidak ditemukan."
        )

    fund_request = report.fund_request

    validate_prodi_access(fund_request, actor)

    if report.prepared_by_id != actor.pk:
        raise ValidationError(
            "Hanya penyusun LPJ yang dapat mengirim laporan ini."
        )

    if report.status not in [
        AccountabilityReport.Status.DRAFT,
        AccountabilityReport.Status.REVISION,
    ]:
        raise ValidationError(
            "Status LPJ tidak memungkinkan untuk dikirim."
        )

    # Periksa seluruh kelengkapan LPJ.
    validate_accountability_completeness(report)
    old_status = report.status

    report.status = AccountabilityReport.Status.SUBMITTED
    report.submitted_at = timezone.now()
    report.notes = notes
    report.revision_notes = ""

    report.full_clean()

    report.save(
        update_fields=[
            "status",
            "submitted_at",
            "notes",
            "revision_notes",
            "updated_at",
        ]
    )

    # Catat riwayat pengiriman.
    AccountabilityHistory.objects.create(
        report=report,
        action=AccountabilityHistory.Action.SUBMIT,
        actor=actor,
        notes=notes or "LPJ dikirim ke Fakultas.",
    )
    log_audit(
        actor=actor,
        action=AuditLog.Action.SUBMIT,
        instance=report,
        description=(
            f"Mengirim LPJ pengajuan "
            f"{report.fund_request.request_number} "
            f"kepada Fakultas."
        ),
        changes={
            "old_status": old_status,
            "new_status": report.status,
            "notes": notes,
            "submitted_by": actor.username,
            "submitted_at": report.submitted_at.isoformat(),
        },
        ip_address=ip_address,
    )

    return report


# ==========================================================
# FAKULTAS MENGEMBALIKAN LPJ UNTUK REVISI
# ==========================================================

@transaction.atomic
def return_accountability_report_for_revision(
    report_id,
    actor,
    notes="",
    ip_address=None,
):
    """
    Fakultas mengembalikan LPJ kepada Prodi untuk diperbaiki.
    """

    notes = (notes or "").strip()

    if not notes:
        raise ValidationError(
            "Catatan revisi wajib diisi."
        )

    report = (
        AccountabilityReport.objects
        .select_for_update()
        .select_related(
            "fund_request__study_program__faculty"
        )
        .filter(pk=report_id)
        .first()
    )

    if not report:
        raise ValidationError(
            "LPJ tidak ditemukan."
        )

    validate_faculty_access(report, actor)

    if report.status != AccountabilityReport.Status.SUBMITTED:
        raise ValidationError(
            "Hanya LPJ yang sudah dikirim yang dapat direvisi."
        )

    old_status = report.status

    report.status = AccountabilityReport.Status.REVISION
    report.reviewed_by = actor
    report.reviewed_at = timezone.now()
    report.revision_notes = notes

    report.full_clean()

    report.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "revision_notes",
            "updated_at",
        ]
    )

    # Catat riwayat pengembalian.
    AccountabilityHistory.objects.create(
        report=report,
        action=AccountabilityHistory.Action.REVISION,
        actor=actor,
        notes=notes,
    )
    log_audit(
        actor=actor,
        action=AuditLog.Action.REVISION,
        instance=report,
        description=(
            f"Mengembalikan LPJ pengajuan "
            f"{report.fund_request.request_number} "
            f"kepada Prodi untuk revisi."
        ),
        changes={
            "old_status": old_status,
            "new_status": report.status,
            "revision_notes": notes,
            "reviewed_by": actor.username,
            "reviewed_at": report.reviewed_at.isoformat(),
        },
        ip_address=ip_address,
    )

    return report


# ==========================================================
# FAKULTAS MENERIMA LPJ
# ==========================================================

@transaction.atomic
def accept_accountability_report(
    report_id,
    actor,
    notes="",
    ip_address=None,
):
    """
    Fakultas menerima LPJ setelah pemeriksaan.
    """

    report = (
        AccountabilityReport.objects
        .select_for_update()
        .select_related(
            "fund_request__study_program__faculty"
        )
        .filter(pk=report_id)
        .first()
    )

    if not report:
        raise ValidationError(
            "LPJ tidak ditemukan."
        )

    validate_faculty_access(report, actor)

    if report.status != AccountabilityReport.Status.SUBMITTED:
        raise ValidationError(
            "Hanya LPJ yang sudah dikirim yang dapat diterima."
        )

    # Periksa kelengkapan sebelum LPJ diterima.
    validate_accountability_completeness(report)
    old_status = report.status

    report.status = AccountabilityReport.Status.ACCEPTED
    report.reviewed_by = actor
    report.reviewed_at = timezone.now()
    report.notes = notes

    report.full_clean()

    report.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "notes",
            "updated_at",
        ]
    )

    # Catat riwayat penerimaan.
    AccountabilityHistory.objects.create(
        report=report,
        action=AccountabilityHistory.Action.ACCEPT,
        actor=actor,
        notes=notes or "LPJ diterima oleh Fakultas.",
    )
    log_audit(
        actor=actor,
        action=AuditLog.Action.ACCEPT,
        instance=report,
        description=(
            f"Menerima LPJ pengajuan "
            f"{report.fund_request.request_number}."
        ),
        changes={
            "old_status": old_status,
            "new_status": report.status,
            "notes": notes,
            "reviewed_by": actor.username,
            "reviewed_at": report.reviewed_at.isoformat(),
        },
        ip_address=ip_address,
    )

    return report
