
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from apps.master_data.models import (
    TimeStampedModel,
    StudyProgram,
    BudgetYear,
)


class RKATSubmission(TimeStampedModel):

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Diajukan"
        FORWARDED = "FORWARDED", "Diteruskan ke Lembaga"
        DECISION_RECORDED = "DECISION_RECORDED", "Keputusan Lembaga Dicatat"
        ALLOCATION_ADJUSTMENT = "ALLOCATION_ADJUSTMENT", "Penyesuaian Alokasi"
        READY = "READY", "Siap Digunakan"

    study_program = models.ForeignKey(
        StudyProgram,
        on_delete=models.PROTECT,
        related_name="rkat_submissions"
    )

    budget_year = models.ForeignKey(
        BudgetYear,
        on_delete=models.PROTECT,
        related_name="rkat_submissions"
    )

    submission_number = models.CharField(
        max_length=50,
        unique=True
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT
    )

    submitted_at = models.DateTimeField(
        null=True,
        blank=True
    )

    notes = models.TextField(
        blank=True
    )

    class Meta:
        db_table = "rkat_submission"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["study_program", "budget_year"],
                name="unique_rkat_program_year"
            )
        ]

    @property
    def proposed_total(self):
        return self.activities.aggregate(
            total=Sum("proposed_amount")
        )["total"] or 0

    def __str__(self):
        return self.submission_number


class RKATActivity(TimeStampedModel):

    rkat = models.ForeignKey(
        RKATSubmission,
        on_delete=models.PROTECT,
        related_name="activities"
    )

    name = models.CharField(
        max_length=200
    )

    description = models.TextField(
        blank=True
    )

    proposed_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0
    )

    class Meta:
        db_table = "rkat_activity"
        ordering = ["id"]

    def clean(self):
        super().clean()

        if self.proposed_amount is not None and self.proposed_amount < 0:
            raise ValidationError({
                "proposed_amount": "Nominal tidak boleh negatif."
            })

        if self.rkat_id:
            if self.rkat.status != RKATSubmission.Status.DRAFT:
                if self._state.adding:
                    raise ValidationError(
                        "Kegiatan baru tidak dapat ditambahkan setelah RKAT diajukan."
                    )

    def __str__(self):
        return self.name


class RKATForwarding(TimeStampedModel):

    rkat = models.ForeignKey(
        RKATSubmission,
        on_delete=models.PROTECT,
        related_name="forwardings"
    )

    forwarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="rkat_forwardings"
    )

    forwarded_at = models.DateTimeField(
        default=timezone.now
    )

    reference_number = models.CharField(
        max_length=100,
        blank=True
    )

    notes = models.TextField(
        blank=True
    )

    class Meta:
        db_table = "rkat_forwarding"
        ordering = ["-forwarded_at"]

    def __str__(self):
        return f"Penerusan {self.rkat.submission_number}"


class InstitutionDecision(TimeStampedModel):
    class DecisionStatus(models.TextChoices):
        APPROVED = "APPROVED", "Disetujui"
        PARTIAL = "PARTIAL", "Disetujui Sebagian"
        REJECTED = "REJECTED", "Ditolak"

    rkat = models.ForeignKey(
        RKATSubmission,
        on_delete=models.PROTECT,
        related_name="institution_decisions"
    )

    decision_status = models.CharField(
        max_length=20,
        choices=DecisionStatus.choices
    )

    proposed_total = models.DecimalField(
        max_digits=15,
        decimal_places=2
    )

    approved_total = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0
    )

    decision_date = models.DateField()

    reference_number = models.CharField(
        max_length=100,
        blank=True
    )

    document = models.FileField(
        upload_to="rkat/decisions/",
        blank=True,
        null=True,
        max_length=500
    )

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_rkat_decisions"
    )

    notes = models.TextField(
        blank=True
    )

    class Meta:
        db_table = "rkat_institution_decision"
        ordering = ["-decision_date", "-created_at"]

    def clean(self):
        super().clean()

        if self.proposed_total < 0 or self.approved_total < 0:
            raise ValidationError(
                "Nominal keputusan tidak boleh negatif."
            )

        if self.approved_total > self.proposed_total:
            raise ValidationError({
                "approved_total": "Nominal disetujui tidak boleh melebihi nominal usulan."
            })

    def __str__(self):
        return f"Keputusan {self.rkat.submission_number}"


class ProgramBudgetAllocation(TimeStampedModel):

    rkat = models.OneToOneField(
        RKATSubmission,
        on_delete=models.PROTECT,
        related_name="budget_allocation"
    )

    final_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2
    )

    determined_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="determined_budget_allocations"
    )

    determined_at = models.DateTimeField(
        default=timezone.now
    )

    notes = models.TextField(
        blank=True
    )

    class Meta:
        db_table = "rkat_program_budget_allocation"

    @property
    def allocated_total(self):
        return self.activity_allocations.aggregate(
            total=Sum("amount")
        )["total"] or 0

    @property
    def remaining_amount(self):
        return self.final_amount - self.allocated_total

    def clean(self):
        super().clean()

        if self.final_amount <= 0:
            raise ValidationError({
                "final_amount": "Pagu final harus lebih besar dari nol."
            })

    def __str__(self):
        return f"Pagu {self.rkat.submission_number}"


class ActivityAllocation(TimeStampedModel):

    budget_allocation = models.ForeignKey(
        ProgramBudgetAllocation,
        on_delete=models.PROTECT,
        related_name="activity_allocations"
    )

    activity = models.OneToOneField(
        RKATActivity,
        on_delete=models.PROTECT,
        related_name="allocation"
    )

    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0
    )

    class Meta:
        db_table = "rkat_activity_allocation"

    def clean(self):
        super().clean()

        if self.amount < 0:
            raise ValidationError({
                "amount": "Nominal alokasi tidak boleh negatif."
            })

        if (
            self.budget_allocation_id
            and self.activity_id
            and self.activity.rkat_id != self.budget_allocation.rkat_id
        ):
            raise ValidationError(
                "Aktivitas tidak sesuai dengan RKAT alokasi."
            )

    def __str__(self):
        return f"{self.activity.name} - {self.amount}"


class RKATAllocationHistory(TimeStampedModel):

    allocation = models.ForeignKey(
        ActivityAllocation,
        on_delete=models.PROTECT,
        related_name="histories"
    )

    old_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2
    )

    new_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2
    )

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="rkat_allocation_changes"
    )

    reason = models.TextField()

    class Meta:
        db_table = "rkat_allocation_history"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Revisi {self.allocation.activity.name}"
