
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps import rkat
from apps.rkat.models import (
    RKATSubmission,
    ProgramBudgetAllocation,
    ActivityAllocation,
    RKATAllocationHistory,
)
from apps.accounts.audit import log_audit
from apps.accounts.models import AuditLog

from django.utils import timezone

from .models import (
    InstitutionDecision,
)


def get_user_role(user):
    try:
        return user.profile.role
    except Exception:
        raise ValidationError("Pengguna belum memiliki profil SIAP.")

@transaction.atomic
def set_program_budget_allocation(
    rkat_id,
    final_amount,
    actor,
    notes="",
    ip_address=None,
):
    """
    Fakultas menetapkan pagu final untuk satu Prodi.
    """

    if get_user_role(actor) != "FAKULTAS":
        raise ValidationError(
            "Hanya pengguna Fakultas yang dapat menetapkan pagu Prodi."
        )

    rkat = (
        RKATSubmission.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .get(pk=rkat_id)
    )

    if actor.profile.faculty_id != rkat.study_program.faculty_id:
        raise ValidationError(
            "Anda tidak berwenang mengelola RKAT Prodi ini."
        )

    if rkat.status not in ["DECISION_RECORDED", "ALLOCATION_ADJUSTMENT"]:
        raise ValidationError(
            "RKAT belum berada pada tahap penetapan atau penyesuaian pagu."
        )

    final_amount = Decimal(str(final_amount))

    if not final_amount.is_finite() or final_amount <= 0:
        raise ValidationError("Pagu final harus lebih besar dari nol.")

    decision = InstitutionDecision.objects.filter(
        rkat=rkat
    ).order_by("-decision_date", "-created_at").first()

    if not decision:
        raise ValidationError(
            "Keputusan Lembaga belum dicatat untuk RKAT ini."
        )

    if decision.decision_status not in ["APPROVED", "PARTIAL"]:
        raise ValidationError(
            "Keputusan Lembaga belum menyetujui anggaran RKAT ini."
        )

    if final_amount > decision.approved_total:
        raise ValidationError(
            "Pagu final Prodi tidak boleh melebihi nominal persetujuan Lembaga."
        )

    allocation = (
        ProgramBudgetAllocation.objects
        .select_for_update()
        .filter(rkat=rkat)
        .first()
    )

    old_final_amount = allocation.final_amount if allocation else None

    if allocation:
        current_allocated = allocation.activity_allocations.aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0")

        if current_allocated > final_amount:
            raise ValidationError(
                "Pagu baru lebih kecil daripada total alokasi aktivitas saat ini. "
                "Sesuaikan alokasi aktivitas terlebih dahulu."
            )

        allocation.final_amount = final_amount
        allocation.determined_by = actor
        allocation.notes = notes
        allocation.full_clean()
        allocation.save()
    else:
        allocation = ProgramBudgetAllocation(
            rkat=rkat,
            final_amount=final_amount,
            determined_by=actor,
            notes=notes,
        )
        allocation.full_clean()
        allocation.save()

    log_audit(
        actor=actor,
        action=(
            AuditLog.Action.CREATE
            if old_final_amount is None
            else AuditLog.Action.UPDATE
        ),
        instance=allocation,
        description=(
            f"{'Menetapkan' if old_final_amount is None else 'Mengubah'} "
            f"pagu final Prodi untuk RKAT {rkat.submission_number}."
        ),
        changes={
            "rkat": rkat.submission_number,
            "final_amount": {
                "old": (
                    str(old_final_amount)
                    if old_final_amount is not None
                    else None
                ),
                "new": str(final_amount),
            },
            "determined_by": actor.username,
            "notes": notes,
        },
        ip_address=ip_address,
    )

    rkat.status = RKATSubmission.Status.ALLOCATION_ADJUSTMENT
    rkat.save(update_fields=["status", "updated_at"])

    return allocation

@transaction.atomic
def set_activity_allocation(
    activity_id,
    amount,
    actor,
    reason,
    ip_address=None,
):
    """
    Prodi mengatur nominal alokasi pada aktivitas RKAT yang sudah ada.
    Setiap perubahan dicatat dalam riwayat.
    """

    if get_user_role(actor) != "PRODI":
        raise ValidationError(
            "Hanya pengguna Prodi yang dapat mengubah alokasi aktivitas."
        )

    if not reason or not reason.strip():
        raise ValidationError(
            "Alasan perubahan alokasi wajib diisi."
        )

    amount = Decimal(str(amount))

    if not amount.is_finite() or amount < 0:
        raise ValidationError(
            "Nominal alokasi tidak boleh negatif."
        )

    rkat_activity_model = (
        ActivityAllocation._meta.get_field("activity").remote_field.model
    )

    activity = (
        rkat_activity_model.objects
        .select_related("rkat__study_program")
        .get(pk=activity_id)
    )

    rkat = activity.rkat

    if actor.profile.study_program_id != rkat.study_program_id:
        raise ValidationError(
            "Anda tidak berwenang mengubah alokasi Prodi lain."
        )

    if rkat.status != "ALLOCATION_ADJUSTMENT":
        raise ValidationError(
            "RKAT belum berada pada tahap penyesuaian alokasi."
        )

    allocation = (
        ProgramBudgetAllocation.objects
        .select_for_update()
        .filter(rkat=rkat)
        .first()
    )

    if not allocation:
        raise ValidationError(
            "Pagu final Prodi belum ditetapkan oleh Fakultas."
        )

    existing = (
        ActivityAllocation.objects
        .select_for_update()
        .filter(activity=activity)
        .first()
    )

    old_amount = existing.amount if existing else Decimal("0")
    is_new_allocation = existing is None

    current_total = allocation.activity_allocations.aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")

    new_total = current_total - old_amount + amount

    if new_total > allocation.final_amount:
        raise ValidationError(
            "Total alokasi aktivitas tidak boleh melebihi pagu final Prodi."
        )

    if existing:
        existing.amount = amount
        existing.full_clean()
        existing.save()
    else:
        existing = ActivityAllocation(
            budget_allocation=allocation,
            activity=activity,
            amount=amount,
        )
        existing.full_clean()
        existing.save()

    if is_new_allocation or old_amount != amount:
        RKATAllocationHistory.objects.create(
            allocation=existing,
            old_amount=old_amount,
            new_amount=amount,
            changed_by=actor,
            reason=reason.strip(),
        )

        log_audit(
            actor=actor,
            action=(
                AuditLog.Action.CREATE
                if is_new_allocation
                else AuditLog.Action.UPDATE
            ),
            instance=existing,
            description=(
                f"{'Membuat' if is_new_allocation else 'Mengubah'} "
                f"alokasi aktivitas '{activity.name}' pada "
                f"RKAT {rkat.submission_number}."
            ),
            changes={
                "rkat": rkat.submission_number,
                "activity": activity.name,
                "amount": {
                    "old": str(old_amount),
                    "new": str(amount),
                },
                "reason": reason.strip(),
                "changed_by": actor.username,
            },
            ip_address=ip_address,
        )

    return existing

import uuid

from django.db import transaction
from django.core.exceptions import ValidationError

from apps.accounts.models import UserProfile
from apps.master_data.models import BudgetYear
from .models import RKATSubmission


@transaction.atomic
def create_rkat_submission(
    actor,
    notes="",
    ip_address=None,
):
    profile = actor.profile

    if profile.role != UserProfile.Role.PRODI:
        raise ValidationError(
            "Hanya pengguna Prodi yang dapat membuat RKAT."
        )

    if not profile.study_program_id:
        raise ValidationError(
            "Akun belum terhubung dengan Program Studi."
        )

    budget_year = BudgetYear.objects.filter(
        is_active=True
    ).first()

    if not budget_year:
        raise ValidationError(
            "Belum ada tahun anggaran yang aktif."
        )

    if RKATSubmission.objects.filter(
        study_program_id=profile.study_program_id,
        budget_year=budget_year,
    ).exists():
        raise ValidationError(
            "RKAT untuk Program Studi dan tahun anggaran ini sudah tersedia."
        )

    program = profile.study_program

    submission_number = (
        f"RKAT/{budget_year.name}/"
        f"{program.code}/"
        f"{uuid.uuid4().hex[:8].upper()}"
    )

    rkat = RKATSubmission.objects.create(
        study_program=program,
        budget_year=budget_year,
        submission_number=submission_number,
        status=RKATSubmission.Status.DRAFT,
        notes=notes,
    )

    log_audit(
        actor=actor,
        action=AuditLog.Action.CREATE,
        instance=rkat,
        description=(
            f"Prodi membuat RKAT {rkat.submission_number}."
        ),
        changes={
            "submission_number": rkat.submission_number,
            "study_program": program.name,
            "budget_year": budget_year.name,
            "status": rkat.status,
            "notes": notes,
        },
        ip_address=ip_address,
    )

    return rkat



from django.db import transaction
from django.core.exceptions import ValidationError

from apps.accounts.models import UserProfile
from .models import RKATSubmission, RKATActivity


@transaction.atomic
def save_rkat_activity(
    rkat_id,
    actor,
    name,
    description,
    proposed_amount,
    activity_id=None,
    ip_address=None,
):
    profile = actor.profile

    if profile.role != UserProfile.Role.PRODI:
        raise ValidationError(
            "Hanya Prodi yang dapat mengelola komponen RKAT."
        )

    rkat = RKATSubmission.objects.select_for_update().get(
        pk=rkat_id
    )

    if rkat.study_program_id != profile.study_program_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke RKAT ini."
        )

    if rkat.status != RKATSubmission.Status.DRAFT:
        raise ValidationError(
            "Komponen RKAT hanya dapat diubah selama status masih Draft."
        )

    if activity_id:
        activity = RKATActivity.objects.get(
            pk=activity_id,
            rkat=rkat,
        )

        is_new = False

        old_values = {
            "name": activity.name,
            "description": activity.description,
            "proposed_amount": str(activity.proposed_amount),
        }
    else:
        activity = RKATActivity(rkat=rkat)
        is_new = True
        old_values = {}

    activity.name = name
    activity.description = description
    activity.proposed_amount = proposed_amount

    activity.full_clean()
    activity.save()

    new_values = {
        "name": activity.name,
        "description": activity.description,
        "proposed_amount": str(activity.proposed_amount),
    }

    log_audit(
        actor=actor,
        action=(
            AuditLog.Action.CREATE
            if is_new
            else AuditLog.Action.UPDATE
        ),
        instance=activity,
        description=(
            f"{'Menambahkan' if is_new else 'Mengubah'} "
            f"kegiatan '{activity.name}' pada RKAT "
            f"{rkat.submission_number}."
        ),
        changes={
            "rkat": rkat.submission_number,
            "study_program": rkat.study_program.name,
            "old": old_values,
            "new": new_values,
        },
        ip_address=ip_address,
    )

    return activity


@transaction.atomic
def delete_rkat_activity(activity_id, actor, ip_address=None):
    profile = actor.profile

    if profile.role != UserProfile.Role.PRODI:
        raise ValidationError(
            "Hanya Prodi yang dapat menghapus komponen RKAT."
        )

    activity = RKATActivity.objects.select_related(
        "rkat",
        "rkat__study_program",
    ).select_for_update().get(pk=activity_id)

    if activity.rkat.study_program_id != profile.study_program_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke komponen RKAT ini."
        )

    if activity.rkat.status != RKATSubmission.Status.DRAFT:
        raise ValidationError(
            "Komponen tidak dapat dihapus karena RKAT sudah diajukan."
        )

    # Simpan data sebelum kegiatan dihapus.
    deleted_data = {
        "activity_id": activity.pk,
        "name": activity.name,
        "description": activity.description,
        "proposed_amount": str(activity.proposed_amount),
        "rkat": activity.rkat.submission_number,
        "study_program": activity.rkat.study_program.name,
    }

    # Catat penghapusan ke Audit Log.
    log_audit(
        actor=actor,
        action=AuditLog.Action.DELETE,
        instance=activity,
        description=(
            f"Menghapus kegiatan '{activity.name}' "
            f"dari RKAT {activity.rkat.submission_number}."
        ),
        changes={
            "deleted_data": deleted_data,
        },
        ip_address=ip_address,
    )

    activity.delete()

    return activity


from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import UserProfile
from .models import (
    RKATSubmission,
    RKATForwarding,
)


@transaction.atomic
def submit_rkat(rkat_id, actor, ip_address=None):
    profile = actor.profile

    if profile.role != UserProfile.Role.PRODI:
        raise ValidationError(
            "Hanya Prodi yang dapat mengajukan RKAT."
        )

    rkat = RKATSubmission.objects.select_for_update().get(
        pk=rkat_id
    )

    if rkat.study_program_id != profile.study_program_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke RKAT ini."
        )

    if rkat.status != RKATSubmission.Status.DRAFT:
        raise ValidationError(
            "Hanya RKAT berstatus Draft yang dapat diajukan."
        )

    if not rkat.activities.exists():
        raise ValidationError(
            "RKAT harus memiliki minimal satu kegiatan."
        )

    if rkat.proposed_total <= 0:
        raise ValidationError(
            "Total usulan RKAT harus lebih besar dari nol."
        )

    old_status = rkat.status

    rkat.status = RKATSubmission.Status.SUBMITTED
    rkat.submitted_at = timezone.now()
    rkat.save(update_fields=[
        "status",
        "submitted_at",
        "updated_at",
    ])

    log_audit(
        actor=actor,
        action=AuditLog.Action.SUBMIT,
        instance=rkat,
        description=(
            f"Prodi mengajukan RKAT {rkat.submission_number} "
            f"untuk tahun anggaran {rkat.budget_year.name}."
        ),
        changes={
            "status": {
                "old": old_status,
                "new": rkat.status,
            },
            "submitted_at": rkat.submitted_at.isoformat(),
            "proposed_total": str(rkat.proposed_total),
        },
        ip_address=ip_address,
    )

    return rkat


@transaction.atomic
def forward_rkat(
    rkat_id,
    actor,
    reference_number,
    notes="",
    ip_address=None,
):
    profile = actor.profile

    if profile.role != UserProfile.Role.FAKULTAS:
        raise ValidationError(
            "Hanya Fakultas yang dapat meneruskan RKAT."
        )

    rkat = RKATSubmission.objects.select_for_update().select_related(
        "study_program"
    ).get(pk=rkat_id)

    if rkat.study_program.faculty_id != profile.faculty_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke RKAT ini."
        )

    if rkat.status != RKATSubmission.Status.SUBMITTED:
        raise ValidationError(
            "Hanya RKAT yang sudah diajukan Prodi yang dapat diteruskan."
        )

    forwarding = RKATForwarding(
        rkat=rkat,
        forwarded_by=actor,
        reference_number=reference_number,
        notes=notes,
    )
    forwarding.full_clean()
    forwarding.save()

    old_status = rkat.status

    rkat.status = RKATSubmission.Status.FORWARDED
    rkat.save(update_fields=["status", "updated_at"])

    log_audit(
        actor=actor,
        action=AuditLog.Action.FORWARD,
        instance=forwarding,
        description=(
            f"Fakultas meneruskan RKAT {rkat.submission_number} "
            f"ke Lembaga."
        ),
        changes={
            "rkat": rkat.submission_number,
            "status": {
                "old": old_status,
                "new": rkat.status,
            },
            "reference_number": reference_number,
            "forwarded_by": actor.username,
            "forwarded_at": forwarding.forwarded_at.isoformat(),
        },
        ip_address=ip_address,
    )

    return forwarding


@transaction.atomic
def record_institution_decision(
    rkat_id,
    actor,
    decision_status,
    approved_total,
    decision_date,
    reference_number,
    document=None,
    notes="",
    ip_address=None,
):
    """
    Mencatat keputusan anggaran dari Lembaga.
    Dilakukan oleh Admin atau Fakultas.
    """

    role = get_user_role(actor)

    if role not in ["ADMIN", "FAKULTAS"]:
        raise ValidationError(
            "Anda tidak memiliki hak untuk mencatat keputusan Lembaga."
        )

    rkat = (
        RKATSubmission.objects
        .select_for_update()
        .select_related("study_program__faculty")
        .get(pk=rkat_id)
    )

    # Pemeriksaan akses Fakultas
    if role == "FAKULTAS":
        profile = actor.profile

        if rkat.study_program.faculty_id != profile.faculty_id:
            raise ValidationError(
                "Anda tidak memiliki akses ke RKAT Prodi ini."
            )

    if rkat.status != RKATSubmission.Status.FORWARDED:
        raise ValidationError(
            "Keputusan hanya dapat dicatat setelah RKAT diteruskan ke Lembaga."
        )

    # Pastikan keputusan belum pernah dicatat
    if InstitutionDecision.objects.filter(rkat=rkat).exists():
        raise ValidationError(
            "Keputusan Lembaga untuk RKAT ini sudah pernah dicatat."
        )

    approved_total = Decimal(str(approved_total))

    if approved_total < 0:
        raise ValidationError(
            "Nominal persetujuan tidak boleh negatif."
        )

    if approved_total > rkat.proposed_total:
        raise ValidationError(
            "Nominal persetujuan tidak boleh melebihi total pengajuan RKAT."
        )

    if decision_status == InstitutionDecision.DecisionStatus.REJECTED:
        if approved_total != Decimal("0"):
            raise ValidationError(
                "Nominal persetujuan untuk keputusan ditolak harus 0."
            )

    elif decision_status == InstitutionDecision.DecisionStatus.PARTIAL:
        if approved_total <= 0 or approved_total >= rkat.proposed_total:
            raise ValidationError(
                "Persetujuan sebagian harus lebih dari 0 dan kurang dari total pengajuan."
            )

    elif decision_status == InstitutionDecision.DecisionStatus.APPROVED:
        if approved_total <= 0:
            raise ValidationError(
                "Nominal persetujuan harus lebih dari 0."
            )

    else:
        raise ValidationError(
            "Status keputusan Lembaga tidak valid."
        )

    decision = InstitutionDecision(
        rkat=rkat,
        decision_status=decision_status,
        proposed_total=rkat.proposed_total,
        approved_total=approved_total,
        decision_date=decision_date,
        reference_number=reference_number,
        document=document,
        recorded_by=actor,
        notes=notes,
    )

    decision.full_clean()
    decision.save()

    old_status = rkat.status

    rkat.status = RKATSubmission.Status.DECISION_RECORDED
    rkat.save(update_fields=["status", "updated_at"])

    audit_action = (
        AuditLog.Action.REJECT
        if decision_status == InstitutionDecision.DecisionStatus.REJECTED
        else AuditLog.Action.APPROVE
    )

    log_audit(
        actor=actor,
        action=audit_action,
        instance=decision,
        description=(
            f"Mencatat keputusan Lembaga untuk RKAT "
            f"{rkat.submission_number}: "
            f"{decision.get_decision_status_display()}."
        ),
        changes={
            "rkat": rkat.submission_number,
            "decision_status": decision_status,
            "proposed_total": str(rkat.proposed_total),
            "approved_total": str(approved_total),
            "decision_date": decision_date.isoformat(),
            "reference_number": reference_number,
            "rkat_status": {
                "old": old_status,
                "new": rkat.status,
            },
            "recorded_by": actor.username,
        },
        ip_address=ip_address,
    )

    return decision

@transaction.atomic
def finalize_rkat_allocation(
    rkat_id,
    actor,
    ip_address=None,
):
    """
    Prodi menyelesaikan penyesuaian alokasi RKAT.
    RKAT berubah menjadi READY setelah seluruh kegiatan
    memiliki alokasi dan totalnya tidak melebihi pagu final.
    """

    if get_user_role(actor) != "PRODI":
        raise ValidationError(
            "Hanya pengguna Prodi yang dapat menyelesaikan alokasi RKAT."
        )

    rkat = (
        RKATSubmission.objects
        .select_for_update()
        .select_related("study_program")
        .get(pk=rkat_id)
    )

    if rkat.study_program_id != actor.profile.study_program_id:
        raise ValidationError(
            "Anda tidak memiliki akses ke RKAT ini."
        )

    if rkat.status != RKATSubmission.Status.ALLOCATION_ADJUSTMENT:
        raise ValidationError(
            "RKAT belum berada pada tahap penyesuaian alokasi."
        )

    budget_allocation = (
        ProgramBudgetAllocation.objects
        .select_for_update()
        .filter(rkat=rkat)
        .first()
    )

    if not budget_allocation:
        raise ValidationError(
            "Pagu final belum ditetapkan oleh Fakultas."
        )

    activities = rkat.activities.all()

    if not activities.exists():
        raise ValidationError(
            "RKAT belum memiliki kegiatan."
        )

    allocated_activity_ids = set(
        ActivityAllocation.objects.filter(
            budget_allocation=budget_allocation
        ).values_list("activity_id", flat=True)
    )

    missing_activities = [
        activity.name
        for activity in activities
        if activity.id not in allocated_activity_ids
    ]

    if missing_activities:
        raise ValidationError(
            "Masih ada kegiatan yang belum diatur alokasinya: "
            + ", ".join(missing_activities)
        )

    allocated_total = budget_allocation.allocated_total

    if allocated_total <= 0:
        raise ValidationError(
            "Total alokasi harus lebih besar dari nol."
        )

    if allocated_total > budget_allocation.final_amount:
        raise ValidationError(
            "Total alokasi melebihi pagu final."
        )

    old_status = rkat.status

    rkat.status = RKATSubmission.Status.READY
    rkat.save(update_fields=["status", "updated_at"])

    log_audit(
        actor=actor,
        action=AuditLog.Action.UPDATE,
        instance=rkat,
        description=(
            f"Prodi menyelesaikan alokasi RKAT "
            f"{rkat.submission_number}. RKAT siap digunakan "
            f"untuk pengajuan dana."
        ),
        changes={
            "status": {
                "old": old_status,
                "new": rkat.status,
            },
            "final_budget": str(budget_allocation.final_amount),
            "allocated_total": str(allocated_total),
            "remaining_budget": str(
                budget_allocation.final_amount - allocated_total
            ),
            "finalized_by": actor.username,
        },
        ip_address=ip_address,
    )

    return rkat