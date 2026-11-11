import uuid
from decimal import Decimal, InvalidOperation
from apps.accounts.models import UserProfile
from apps.master_data.models import BudgetYear
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .models import RequestCostItem
from .models import FundRequestInstitutionDecision

from apps.rkat.models import (
    RKATSubmission,
    RKATActivity,
    ActivityAllocation,
)

from apps.accounts.models import UserProfile, AuditLog
from apps.accounts.audit import log_audit

from .models import (
    FundRequest,
    FundRequestActivity,
    RequestCostItem,
    ApprovalHistory,
)

from apps.fund_requests.models import (
    FundRequest,
    FundRequestActivity,
    ApprovalHistory,
)

from apps.rkat.models import ActivityAllocation


# Status yang masih menggunakan atau mengunci alokasi RKAT.
PENDING_STATUSES = [
    FundRequest.Status.SUBMITTED,
    FundRequest.Status.REVISION,
    FundRequest.Status.FACULTY_CHECKED,
    FundRequest.Status.FORWARDED,
]

APPROVED_STATUSES = [
    FundRequest.Status.APPROVED,
    FundRequest.Status.PARTIALLY_APPROVED,
]


def get_user_role(user):
    try:
        return user.profile.role
    except Exception:
        raise ValidationError(
            "Pengguna belum memiliki profil SIAP."
        )


def get_reserved_amount(activity, exclude_request_id=None):
    """
    Menghitung nominal alokasi yang sudah digunakan
    atau dikunci oleh pengajuan lain pada aktivitas RKAT.
    """

    requests = FundRequestActivity.objects.filter(
        rkat_activity=activity,
        fund_request__status__in=(
            PENDING_STATUSES + APPROVED_STATUSES
        ),
    ).select_related("fund_request")

    if exclude_request_id:
        requests = requests.exclude(
            fund_request_id=exclude_request_id
        )

    reserved = Decimal("0")

    for item in requests:
        if item.fund_request.status in APPROVED_STATUSES:
            reserved += item.approved_amount or Decimal("0")
        else:
            reserved += item.requested_amount

    return reserved


def get_activity_allocation(activity):
    """
    Mengambil alokasi nominal untuk satu aktivitas RKAT.
    """

    allocation = (
        ActivityAllocation.objects
        .filter(activity=activity)
        .first()
    )

    if not allocation:
        raise ValidationError(
            f"Aktivitas '{activity.name}' belum memiliki alokasi anggaran."
        )

    return allocation


def validate_request_budget(fund_request):
    """
    Memastikan setiap aktivitas pengajuan tidak melebihi
    sisa alokasi RKAT yang tersedia.
    """

    request_items = fund_request.activities.select_related(
        "rkat_activity"
    )

    if not request_items.exists():
        raise ValidationError(
            "Pengajuan harus memiliki minimal satu aktivitas."
        )

    for item in request_items:
        allocation = get_activity_allocation(
            item.rkat_activity
        )

        reserved_by_others = get_reserved_amount(
            item.rkat_activity,
            exclude_request_id=fund_request.id,
        )

        available = allocation.amount - reserved_by_others

        if item.requested_amount <= 0:
            raise ValidationError(
                f"Nominal pengajuan aktivitas '{item.rkat_activity.name}' "
                "harus lebih besar dari nol."
            )

        if item.requested_amount > available:
            raise ValidationError(
                f"Pengajuan aktivitas '{item.rkat_activity.name}' "
                f"melebihi sisa alokasi. "
                f"Sisa tersedia: Rp{available:,.2f}."
            )


@transaction.atomic
def submit_fund_request(request_id, actor, ip_address=None):
    """
    Prodi mengirim pengajuan dana ke Fakultas.
    """

    if get_user_role(actor) != "PRODI":
        raise ValidationError(
            "Hanya pengguna Prodi yang dapat mengirim pengajuan."
        )

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program")
        .get(pk=request_id)
    )

    if actor.profile.study_program_id != fund_request.study_program_id:
        raise ValidationError(
            "Anda tidak berwenang mengirim pengajuan Prodi lain."
        )

    if fund_request.status not in [
        FundRequest.Status.DRAFT,
        FundRequest.Status.REVISION,
    ]:
        raise ValidationError(
            "Status pengajuan tidak memungkinkan untuk dikirim."
        )

    # Kunci baris alokasi agar transaksi pengajuan tidak
    # memeriksa saldo yang sama secara bersamaan.
    activity_ids = list(
        fund_request.activities.values_list(
            "rkat_activity_id",
            flat=True,
        )
    )

    list(
        ActivityAllocation.objects
        .select_for_update()
        .filter(activity_id__in=activity_ids)
        .order_by("id")
    )

    validate_request_budget(fund_request)

    old_status = fund_request.status

    fund_request.status = FundRequest.Status.SUBMITTED
    fund_request.submitted_at = timezone.now()
    fund_request.full_clean()
    fund_request.save(
        update_fields=[
            "status",
            "submitted_at",
            "updated_at",
        ]
    )

    ApprovalHistory.objects.create(
        fund_request=fund_request,
        action=ApprovalHistory.Action.SUBMIT,
        actor=actor,
        notes="Pengajuan dikirim ke Fakultas.",
    )

    log_audit(
        actor=actor,
        action=AuditLog.Action.SUBMIT,
        instance=fund_request,
        description=(
            f"Prodi mengirim pengajuan dana "
            f"{fund_request.request_number} ke Fakultas."
        ),
        changes={
            "status": {
                "old": old_status,
                "new": fund_request.status,
            },
            "submitted_at": fund_request.submitted_at.isoformat(),
            "requested_total": str(fund_request.requested_total),
            "submitted_by": actor.username,
        },
        ip_address=ip_address,
    )

    return fund_request

@transaction.atomic
def create_fund_request(
        actor,
        subject,
        letter_date=None,
        notes="",
        ip_address=None,
    ):
    """
    Membuat pengajuan dana baru dengan status DRAFT.
    Pengajuan dibuat berdasarkan RKAT READY milik Prodi.
    """

    profile = actor.profile

    if profile.role != UserProfile.Role.PRODI:
        raise ValidationError(
            "Hanya pengguna Prodi yang dapat membuat pengajuan dana."
        )

    if not profile.study_program_id:
        raise ValidationError(
            "Akun belum terhubung dengan Program Studi."
        )

    subject = (subject or "").strip()

    if not subject:
        raise ValidationError(
            "Perihal pengajuan wajib diisi."
        )

    budget_year = BudgetYear.objects.filter(
        is_active=True
    ).first()

    if not budget_year:
        raise ValidationError(
            "Belum ada tahun anggaran yang aktif."
        )

    rkat = RKATSubmission.objects.filter(
        study_program_id=profile.study_program_id,
        budget_year=budget_year,
        status=RKATSubmission.Status.READY,
    ).first()

    if not rkat:
        raise ValidationError(
            "Belum tersedia RKAT berstatus READY untuk tahun anggaran aktif."
        )

    if not rkat.activities.exists():
        raise ValidationError(
            "RKAT belum memiliki kegiatan."
        )

    request_number = (
        f"REQ/{budget_year.name}/"
        f"{profile.study_program.code}/"
        f"{uuid.uuid4().hex[:8].upper()}"
    )

    fund_request = FundRequest(
        study_program=profile.study_program,
        budget_year=budget_year,
        request_number=request_number,
        subject=subject,
        letter_date=letter_date or timezone.localdate(),
        created_by=actor,
        status=FundRequest.Status.DRAFT,
        notes=notes,
    )

    fund_request.full_clean()
    fund_request.save()

    log_audit(
        actor=actor,
        action=AuditLog.Action.CREATE,
        instance=fund_request,
        description=(
            f"Membuat pengajuan dana {fund_request.request_number} "
            f"untuk Program Studi {fund_request.study_program.name}."
        ),
        changes={
            "request_number": fund_request.request_number,
            "subject": fund_request.subject,
            "budget_year": fund_request.budget_year.name,
            "status": fund_request.status,
            "created_by": actor.username,
        },
        ip_address=ip_address,
    )

    return fund_request


@transaction.atomic
def save_fund_request_activity(
    request_id,
    activity_id,
    requested_amount,
    actor,
    notes="",
    ip_address=None,
):
    """
    Menambahkan atau memperbarui kegiatan pada pengajuan dana.
    Hanya kegiatan dari RKAT READY yang dapat dipilih.
    """

    if get_user_role(actor) != "PRODI":
        raise ValidationError(
            "Hanya pengguna Prodi yang dapat mengelola kegiatan pengajuan."
        )

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .filter(pk=request_id)
        .first()
    )

    if not fund_request:
        raise ValidationError("Pengajuan dana tidak ditemukan.")

    if fund_request.study_program_id != actor.profile.study_program_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke pengajuan Prodi lain."
        )

    if fund_request.status not in [
        FundRequest.Status.DRAFT,
        FundRequest.Status.REVISION,
    ]:
        raise ValidationError(
            "Kegiatan hanya dapat diubah pada pengajuan Draft atau Perlu Revisi."
        )

    try:
        amount = Decimal(str(requested_amount))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Nominal pengajuan tidak valid.")

    if not amount.is_finite() or amount <= 0:
        raise ValidationError(
            "Nominal pengajuan harus lebih besar dari nol."
        )

    # Ambil kegiatan RKAT tanpa menggunakan get_object_or_404
    activity = (
        RKATActivity.objects
        .select_related(
            "rkat__study_program",
            "rkat__budget_year",
        )
        .filter(pk=activity_id)
        .first()
    )

    if not activity:
        raise ValidationError("Kegiatan RKAT tidak ditemukan.")

    if activity.rkat.study_program_id != fund_request.study_program_id:
        raise ValidationError(
            "Kegiatan bukan milik Program Studi pengaju."
        )

    if activity.rkat.budget_year_id != fund_request.budget_year_id:
        raise ValidationError(
            "Kegiatan bukan untuk tahun anggaran pengajuan ini."
        )

    if activity.rkat.status != RKATSubmission.Status.READY:
        raise ValidationError(
            "Kegiatan RKAT belum siap digunakan."
        )

    # Kunci alokasi agar pemeriksaan sisa anggaran konsisten
    allocation = (
        ActivityAllocation.objects
        .select_for_update()
        .filter(activity=activity)
        .first()
    )

    if not allocation:
        raise ValidationError(
            "Kegiatan belum memiliki alokasi anggaran."
        )

    # Periksa apakah kegiatan sudah ada pada pengajuan ini
    existing = FundRequestActivity.objects.filter(
        fund_request=fund_request,
        rkat_activity=activity,
    ).first()

    # Hitung anggaran yang sudah dipesan oleh pengajuan lain
    reserved_by_others = get_reserved_amount(
        activity,
        exclude_request_id=fund_request.pk,
    )

    available = allocation.amount - reserved_by_others

    if amount > available:
        raise ValidationError(
            f"Nominal pengajuan melebihi sisa alokasi kegiatan. "
            f"Sisa tersedia: Rp{available:,.2f}."
        )

    # Simpan kegiatan pengajuan
    if existing:
        old_amount = existing.requested_amount
        old_notes = existing.notes

        existing.requested_amount = amount
        existing.notes = notes
        existing.full_clean()
        existing.save()

        request_activity = existing
        audit_action = AuditLog.Action.UPDATE

        changes = {
            "requested_amount": {
                "old": str(old_amount),
                "new": str(amount),
            },
            "notes": {
                "old": old_notes,
                "new": notes,
            },
        }

        description = (
            f"Mengubah kegiatan '{activity.name}' pada "
            f"pengajuan {fund_request.request_number}."
        )

    else:
        request_activity = FundRequestActivity(
            fund_request=fund_request,
            rkat_activity=activity,
            requested_amount=amount,
            notes=notes,
        )

        request_activity.full_clean()
        request_activity.save()

        audit_action = AuditLog.Action.CREATE

        changes = {
            "activity": activity.name,
            "requested_amount": str(amount),
            "notes": notes,
        }

        description = (
            f"Menambahkan kegiatan '{activity.name}' pada "
            f"pengajuan {fund_request.request_number}."
        )

    log_audit(
        actor=actor,
        action=audit_action,
        instance=request_activity,
        description=description,
        changes={
            "fund_request": fund_request.request_number,
            **changes,
            "changed_by": actor.username,
        },
        ip_address=ip_address,
    )

    return request_activity



@transaction.atomic
def delete_fund_request_activity(
    activity_id,
    actor,
    ip_address=None,
):
    if get_user_role(actor) != "PRODI":
        raise ValidationError(
            "Hanya Prodi yang dapat menghapus kegiatan pengajuan."
        )

    item = (
        FundRequestActivity.objects
        .select_for_update()
        .select_related("fund_request", "rkat_activity")
        .filter(pk=activity_id)
        .first()
    )

    if not item:
        raise ValidationError(
            "Kegiatan pengajuan tidak ditemukan."
        )

    fund_request = item.fund_request

    if fund_request.study_program_id != actor.profile.study_program_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke pengajuan ini."
        )

    if fund_request.status not in [
        FundRequest.Status.DRAFT,
        FundRequest.Status.REVISION,
    ]:
        raise ValidationError(
            "Kegiatan tidak dapat dihapus pada status pengajuan saat ini."
        )

    # 1. Catat seluruh rincian biaya sebelum penghapusan.
    cost_items = list(
        item.cost_items.select_for_update().all()
    )

    for cost_item in cost_items:
        log_audit(
            actor=actor,
            action=AuditLog.Action.DELETE,
            instance=cost_item,
            description=(
                f"Menghapus rincian biaya '{cost_item.description}' "
                f"dari pengajuan {fund_request.request_number}."
            ),
            changes={
                "fund_request": fund_request.request_number,
                "activity": item.rkat_activity.name,
                "description": cost_item.description,
                "quantity": str(cost_item.quantity),
                "unit": cost_item.unit,
                "unit_price": str(cost_item.unit_price),
                "total_amount": str(cost_item.total_amount),
                "deleted_by": actor.username,
            },
            ip_address=ip_address,
        )

    # 2. Catat penghapusan kegiatan induk.
    log_audit(
        actor=actor,
        action=AuditLog.Action.DELETE,
        instance=item,
        description=(
            f"Menghapus kegiatan '{item.rkat_activity.name}' "
            f"dari pengajuan {fund_request.request_number}."
        ),
        changes={
            "fund_request": fund_request.request_number,
            "activity": item.rkat_activity.name,
            "requested_amount": str(item.requested_amount),
            "notes": item.notes,
            "deleted_by": actor.username,
        },
        ip_address=ip_address,
    )

    # 3. Hapus kegiatan dan rincian biaya terkait.
    item.delete()


@transaction.atomic
def save_request_cost_item(
    request_activity_id,
    description,
    quantity,
    unit,
    unit_price,
    actor,
    cost_item_id=None,
    ip_address=None,
):
    """
    Membuat atau mengubah rincian biaya kegiatan pengajuan.
    Hanya Prodi pemilik pengajuan yang dapat mengelola rincian biaya.
    """

    if get_user_role(actor) != "PRODI":
        raise ValidationError("Hanya Prodi yang dapat mengelola rincian biaya.")

    request_activity = (
        FundRequestActivity.objects
        .select_related("fund_request")
        .filter(pk=request_activity_id)
        .first()
    )

    if not request_activity:
        raise ValidationError("Kegiatan pengajuan tidak ditemukan.")

    fund_request = request_activity.fund_request

    if fund_request.study_program_id != actor.profile.study_program_id:
        raise ValidationError("Anda tidak memiliki akses ke pengajuan ini.")

    if fund_request.status not in [
        FundRequest.Status.DRAFT,
        FundRequest.Status.REVISION,
    ]:
        raise ValidationError(
            "Rincian biaya hanya dapat diubah saat pengajuan berstatus Draft atau Revisi."
        )

    description = (description or "").strip()
    unit = (unit or "").strip()

    if not description:
        raise ValidationError("Deskripsi rincian biaya wajib diisi.")

    if not unit:
        raise ValidationError("Satuan wajib diisi.")

    try:
        quantity = Decimal(str(quantity))
        unit_price = Decimal(str(unit_price))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Kuantitas dan harga satuan harus berupa angka yang valid.")

    if quantity <= 0:
        raise ValidationError("Kuantitas harus lebih besar dari 0.")

    if unit_price < 0:
        raise ValidationError("Harga satuan tidak boleh negatif.")

    is_new = not cost_item_id

    if cost_item_id:
        cost_item = (
            RequestCostItem.objects
            .select_for_update()
            .filter(
                pk=cost_item_id,
                request_activity=request_activity,
            )
            .first()
        )

        if not cost_item:
            raise ValidationError("Rincian biaya tidak ditemukan.")

        old_values = {
            "description": cost_item.description,
            "quantity": str(cost_item.quantity),
            "unit": cost_item.unit,
            "unit_price": str(cost_item.unit_price),
        }

    else:
        cost_item = RequestCostItem(
            request_activity=request_activity
        )

        old_values = None

    cost_item.description = description
    cost_item.quantity = quantity
    cost_item.unit = unit
    cost_item.unit_price = unit_price

    cost_item.full_clean()
    cost_item.save()

    new_values = {
        "description": cost_item.description,
        "quantity": str(cost_item.quantity),
        "unit": cost_item.unit,
        "unit_price": str(cost_item.unit_price),
        "total_amount": str(cost_item.total_amount),
    }

    log_audit(
        actor=actor,
        action=(
            AuditLog.Action.CREATE
            if is_new
            else AuditLog.Action.UPDATE
        ),
        instance=cost_item,
        description=(
            f"{'Menambahkan' if is_new else 'Mengubah'} rincian biaya "
            f"'{cost_item.description}' pada pengajuan "
            f"{fund_request.request_number}."
        ),
        changes={
            "fund_request": fund_request.request_number,
            "activity": request_activity.rkat_activity.name,
            "old": old_values,
            "new": new_values,
            "changed_by": actor.username,
        },
        ip_address=ip_address,
    )

    return cost_item


@transaction.atomic
def delete_request_cost_item(
    cost_item_id,
    actor,
    ip_address=None,
):
    """
    Menghapus rincian biaya milik pengajuan Prodi.
    """

    if get_user_role(actor) != "PRODI":
        raise ValidationError("Hanya Prodi yang dapat menghapus rincian biaya.")

    cost_item = (
        RequestCostItem.objects
        .select_related("request_activity__fund_request")
        .filter(pk=cost_item_id)
        .first()
    )

    if not cost_item:
        raise ValidationError("Rincian biaya tidak ditemukan.")

    fund_request = cost_item.request_activity.fund_request

    if fund_request.study_program_id != actor.profile.study_program_id:
        raise ValidationError("Anda tidak memiliki akses ke pengajuan ini.")

    if fund_request.status not in [
        FundRequest.Status.DRAFT,
        FundRequest.Status.REVISION,
    ]:
        raise ValidationError(
            "Rincian biaya hanya dapat dihapus saat pengajuan berstatus Draft atau Revisi."
        )

    log_audit(
        actor=actor,
        action=AuditLog.Action.DELETE,
        instance=cost_item,
        description=(
            f"Menghapus rincian biaya '{cost_item.description}' "
            f"dari pengajuan {fund_request.request_number}."
        ),
        changes={
            "fund_request": fund_request.request_number,
            "activity": cost_item.request_activity.rkat_activity.name,
            "description": cost_item.description,
            "quantity": str(cost_item.quantity),
            "unit": cost_item.unit,
            "unit_price": str(cost_item.unit_price),
            "total_amount": str(cost_item.total_amount),
            "deleted_by": actor.username,
        },
        ip_address=ip_address,
    )

    cost_item.delete()

@transaction.atomic
def check_fund_request(request_id, actor, notes="", ip_address=None):
    """
    Fakultas mencatat bahwa pengajuan telah selesai diperiksa.
    Tidak berarti Fakultas menyetujui nominal anggaran.
    """

    if get_user_role(actor) != "FAKULTAS":
        raise ValidationError(
            "Hanya Fakultas yang dapat memeriksa pengajuan."
        )

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .filter(pk=request_id)
        .first()
    )

    if not fund_request:
        raise ValidationError("Pengajuan dana tidak ditemukan.")

    if fund_request.study_program.faculty_id != actor.profile.faculty_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke pengajuan Fakultas lain."
        )

    if fund_request.status != FundRequest.Status.SUBMITTED:
        raise ValidationError(
            "Hanya pengajuan yang berstatus Diajukan ke Fakultas "
            "yang dapat diperiksa."
        )

    old_status = fund_request.status

    fund_request.status = FundRequest.Status.FACULTY_CHECKED
    fund_request.save(update_fields=["status", "updated_at"])

    ApprovalHistory.objects.create(
        fund_request=fund_request,
        action=ApprovalHistory.Action.CHECK,
        actor=actor,
        notes=notes,
    )

    log_audit(
        actor=actor,
        action=AuditLog.Action.UPDATE,
        instance=fund_request,
        description=(
            f"Fakultas memeriksa pengajuan dana "
            f"{fund_request.request_number}."
        ),
        changes={
            "status": {
                "old": old_status,
                "new": fund_request.status,
            },
            "notes": notes,
            "checked_by": actor.username,
        },
        ip_address=ip_address,
    )

    return fund_request


@transaction.atomic
def return_fund_request_for_revision(
    request_id,
    actor,
    notes,
    ip_address=None,
):
    """
    Fakultas mengembalikan pengajuan kepada Prodi untuk diperbaiki.
    """

    if get_user_role(actor) != "FAKULTAS":
        raise ValidationError(
            "Hanya Fakultas yang dapat mengembalikan pengajuan."
        )

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .filter(pk=request_id)
        .first()
    )

    if not fund_request:
        raise ValidationError("Pengajuan dana tidak ditemukan.")

    if fund_request.study_program.faculty_id != actor.profile.faculty_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke pengajuan Fakultas lain."
        )

    if fund_request.status not in [
        FundRequest.Status.SUBMITTED,
        FundRequest.Status.FACULTY_CHECKED,
    ]:
        raise ValidationError(
            "Pengajuan tidak dapat dikembalikan pada status saat ini."
        )

    notes = (notes or "").strip()

    if not notes:
        raise ValidationError(
            "Catatan revisi wajib diisi agar Prodi mengetahui bagian yang harus diperbaiki."
        )

    old_status = fund_request.status

    fund_request.status = FundRequest.Status.REVISION
    fund_request.save(update_fields=["status", "updated_at"])

    ApprovalHistory.objects.create(
        fund_request=fund_request,
        action=ApprovalHistory.Action.REVISION,
        actor=actor,
        notes=notes,
    )

    log_audit(
        actor=actor,
        action=AuditLog.Action.REVISION,
        instance=fund_request,
        description=(
            f"Fakultas mengembalikan pengajuan "
            f"{fund_request.request_number} kepada Prodi untuk revisi."
        ),
        changes={
            "status": {
                "old": old_status,
                "new": fund_request.status,
            },
            "revision_notes": notes,
            "returned_by": actor.username,
        },
        ip_address=ip_address,
    )

    return fund_request


@transaction.atomic
def forward_fund_request(
    request_id,
    actor,
    notes="",
    ip_address=None,
):
    """
    Fakultas meneruskan pengajuan yang sudah diperiksa ke Lembaga.
    Fakultas tidak menetapkan persetujuan nominal anggaran.
    """

    if get_user_role(actor) != "FAKULTAS":
        raise ValidationError(
            "Hanya Fakultas yang dapat meneruskan pengajuan."
        )

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .filter(pk=request_id)
        .first()
    )

    if not fund_request:
        raise ValidationError("Pengajuan dana tidak ditemukan.")

    if fund_request.study_program.faculty_id != actor.profile.faculty_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke pengajuan Fakultas lain."
        )

    if fund_request.status != FundRequest.Status.FACULTY_CHECKED:
        raise ValidationError(
            "Pengajuan harus selesai diperiksa Fakultas sebelum diteruskan."
        )

    old_status = fund_request.status

    fund_request.status = FundRequest.Status.FORWARDED
    fund_request.save(update_fields=["status", "updated_at"])

    ApprovalHistory.objects.create(
        fund_request=fund_request,
        action=ApprovalHistory.Action.FORWARD,
        actor=actor,
        notes=notes,
    )

    log_audit(
        actor=actor,
        action=AuditLog.Action.FORWARD,
        instance=fund_request,
        description=(
            f"Fakultas meneruskan pengajuan "
            f"{fund_request.request_number} ke Lembaga."
        ),
        changes={
            "status": {
                "old": old_status,
                "new": fund_request.status,
            },
            "notes": notes,
            "forwarded_by": actor.username,
        },
        ip_address=ip_address,
    )

    return fund_request


@transaction.atomic
def record_fund_request_decision(
    request_id,
    approved_amounts,
    decision_status,
    actor,
    decision_date,
    reference_number,
    document=None,
    notes="",
    ip_address=None,
):
    """
    Mencatat keputusan Lembaga atas pengajuan dana.
    Fakultas hanya mencatat keputusan, bukan menetapkan persetujuan sendiri.

    approved_amounts:
        Dictionary dengan format:
        {
            activity_id: approved_amount,
            ...
        }
    """

    if get_user_role(actor) != "FAKULTAS":
        raise ValidationError(
            "Hanya Fakultas yang dapat mencatat keputusan Lembaga."
        )

    fund_request = (
        FundRequest.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .filter(pk=request_id)
        .first()
    )

    if not fund_request:
        raise ValidationError("Pengajuan dana tidak ditemukan.")

    if fund_request.study_program.faculty_id != actor.profile.faculty_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke pengajuan Fakultas lain."
        )

    if fund_request.status != FundRequest.Status.FORWARDED:
        raise ValidationError(
            "Keputusan hanya dapat dicatat setelah pengajuan diteruskan ke Lembaga."
        )

    if FundRequestInstitutionDecision.objects.filter(
        fund_request=fund_request
    ).exists():
        raise ValidationError(
            "Keputusan Lembaga untuk pengajuan ini sudah pernah dicatat."
        )

    activities = list(
        FundRequestActivity.objects
        .select_for_update()
        .filter(fund_request=fund_request)
        .order_by("pk")
    )

    if not activities:
        raise ValidationError(
            "Pengajuan belum memiliki kegiatan."
        )

    if not isinstance(approved_amounts, dict):
        raise ValidationError(
            "Data nominal persetujuan tidak valid."
        )

    activity_ids = {str(item.pk) for item in activities}

    if set(str(key) for key in approved_amounts.keys()) != activity_ids:
        raise ValidationError(
            "Nominal persetujuan harus diisi untuk seluruh kegiatan pengajuan."
        )

    valid_decisions = [
        FundRequestInstitutionDecision.DecisionStatus.APPROVED,
        FundRequestInstitutionDecision.DecisionStatus.PARTIAL,
        FundRequestInstitutionDecision.DecisionStatus.REJECTED,
    ]

    if decision_status not in valid_decisions:
        raise ValidationError("Status keputusan Lembaga tidak valid.")

    approved_total = Decimal("0")
    requested_total = Decimal("0")
    parsed_amounts = {}

    for item in activities:
        requested_total += item.requested_amount

        raw_amount = approved_amounts.get(
            str(item.pk),
            approved_amounts.get(item.pk),
        )

        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError(
                f"Nominal persetujuan untuk kegiatan '{item.rkat_activity.name}' tidak valid."
            )

        if not amount.is_finite() or amount < 0:
            raise ValidationError(
                f"Nominal persetujuan untuk kegiatan '{item.rkat_activity.name}' tidak boleh negatif."
            )

        if amount > item.requested_amount:
            raise ValidationError(
                f"Nominal persetujuan kegiatan '{item.rkat_activity.name}' "
                "tidak boleh melebihi nominal pengajuan."
            )

        parsed_amounts[item.pk] = amount
        approved_total += amount

    if decision_status == FundRequestInstitutionDecision.DecisionStatus.REJECTED:
        if approved_total != 0:
            raise ValidationError(
                "Keputusan Ditolak harus memiliki nominal persetujuan nol untuk seluruh kegiatan."
            )

    elif decision_status == FundRequestInstitutionDecision.DecisionStatus.APPROVED:
        if approved_total != requested_total:
            raise ValidationError(
                "Keputusan Disetujui harus menyetujui seluruh nominal pengajuan."
            )

    elif decision_status == FundRequestInstitutionDecision.DecisionStatus.PARTIAL:
        if approved_total <= 0 or approved_total >= requested_total:
            raise ValidationError(
                "Keputusan Disetujui Sebagian harus lebih dari nol dan kurang dari total pengajuan."
            )

    reference_number = (reference_number or "").strip()

    if not reference_number:
        raise ValidationError(
            "Nomor referensi keputusan Lembaga wajib diisi."
        )

    if not decision_date:
        raise ValidationError(
            "Tanggal keputusan Lembaga wajib diisi."
        )

    # Simpan nominal persetujuan pada setiap kegiatan
    for item in activities:
        item.approved_amount = parsed_amounts[item.pk]
        item.full_clean()
        item.save(update_fields=["approved_amount", "updated_at"])

    # Simpan dokumen dan informasi keputusan Lembaga
    decision = FundRequestInstitutionDecision(
        fund_request=fund_request,
        decision_status=decision_status,
        decision_date=decision_date,
        reference_number=reference_number,
        document=document,
        recorded_by=actor,
        notes=notes,
    )

    decision.full_clean()
    decision.save()

    # Perbarui status pengajuan
    if decision_status == FundRequestInstitutionDecision.DecisionStatus.APPROVED:
        fund_request.status = FundRequest.Status.APPROVED

    elif decision_status == FundRequestInstitutionDecision.DecisionStatus.PARTIAL:
        fund_request.status = FundRequest.Status.PARTIALLY_APPROVED

    else:
        fund_request.status = FundRequest.Status.REJECTED

    fund_request.save(update_fields=["status", "updated_at"])

    audit_action = (
        AuditLog.Action.REJECT
        if decision_status == FundRequestInstitutionDecision.DecisionStatus.REJECTED
        else AuditLog.Action.APPROVE
    )

    log_audit(
        actor=actor,
        action=audit_action,
        instance=decision,
        description=(
            f"Mencatat keputusan Lembaga untuk pengajuan "
            f"{fund_request.request_number}: "
            f"{decision.get_decision_status_display()}."
        ),
        changes={
            "fund_request": fund_request.request_number,
            "decision_status": decision_status,
            "requested_total": str(requested_total),
            "approved_total": str(approved_total),
            "approved_amounts": {
                str(activity_id): str(amount)
                for activity_id, amount in parsed_amounts.items()
            },
            "reference_number": reference_number,
            "decision_date": decision_date.isoformat(),
            "fund_request_status": fund_request.status,
            "recorded_by": actor.username,
        },
        ip_address=ip_address,
    )

    return decision




def get_fund_usage_status(fund_request):
    """
    Menghitung status penggunaan dana berdasarkan
    serah-terima, realisasi, dan status LPJ.
    """

    from apps.disbursements.models import FacultyHandover
    from apps.realizations.models import Realization
    from apps.accountability.models import AccountabilityReport

    report = AccountabilityReport.objects.filter(
        fund_request=fund_request
    ).first()

    # LPJ sudah diterima Fakultas.
    if report and report.status == AccountabilityReport.Status.ACCEPTED:
        return "LPJ Diterima Fakultas"

    # LPJ sudah dikirim kepada Fakultas.
    if report and report.status == AccountabilityReport.Status.SUBMITTED:
        return "Dikirim ke Fakultas"

    # LPJ masih disusun atau diperbaiki.
    if report and report.status in [
        AccountabilityReport.Status.DRAFT,
        AccountabilityReport.Status.REVISION,
    ]:
        return "Proses penyusunan LPJ"

    # Periksa serah-terima Fakultas kepada Prodi.
    handover_exists = FacultyHandover.objects.filter(
        fund_request=fund_request
    ).exists()

    if not handover_exists:
        return "Pencairan dari Fakultas"

    # Periksa realisasi penggunaan dana.
    realization_exists = Realization.objects.filter(
        request_activity__fund_request=fund_request
    ).exists()

    if realization_exists:
        return "Sedang proses penggunaan"

    return "Belum digunakan"
