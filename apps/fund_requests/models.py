
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone
from decimal import Decimal
from apps.master_data.models import TimeStampedModel, StudyProgram, BudgetYear
from apps.rkat.models import RKATActivity


class FundRequest(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Diajukan ke Fakultas"
        REVISION = "REVISION", "Perlu Revisi"
        FACULTY_CHECKED = "FACULTY_CHECKED", "Diperiksa Fakultas"
        FORWARDED = "FORWARDED", "Diteruskan ke Lembaga"
        APPROVED = "APPROVED", "Disetujui Lembaga"
        PARTIALLY_APPROVED = "PARTIALLY_APPROVED", "Disetujui Sebagian"
        REJECTED = "REJECTED", "Ditolak"
        CANCELLED = "CANCELLED", "Dibatalkan"

    study_program = models.ForeignKey(
        StudyProgram,
        on_delete=models.PROTECT,
        related_name="fund_requests",
        verbose_name="Program Studi",
    )
    budget_year = models.ForeignKey(
        BudgetYear,
        on_delete=models.PROTECT,
        related_name="fund_requests",
        verbose_name="Tahun Anggaran",
    )
    request_number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Nomor Pengajuan",
    )
    subject = models.CharField(
        max_length=255,
        verbose_name="Perihal Pengajuan",
    )
    letter_date = models.DateField(
        default=timezone.localdate,
        verbose_name="Tanggal Surat",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_fund_requests",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "fund_requests"
        ordering = ["-created_at"]
        verbose_name = "Pengajuan Dana"
        verbose_name_plural = "Pengajuan Dana"

    @property
    def requested_total(self):
        return self.activities.aggregate(
            total=Sum("requested_amount")
        )["total"] or 0

    @property
    def approved_total(self):
        return self.activities.aggregate(
            total=Sum("approved_amount")
        )["total"] or 0

    def __str__(self):
        return self.request_number


class FundRequestActivity(TimeStampedModel):
    fund_request = models.ForeignKey(
        FundRequest,
        on_delete=models.CASCADE,
        related_name="activities",
    )
    rkat_activity = models.ForeignKey(
        RKATActivity,
        on_delete=models.PROTECT,
        related_name="fund_request_activities",
        verbose_name="Aktivitas RKAT",
    )
    requested_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name="Nominal Diajukan",
    )
    approved_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Nominal Disetujui Lembaga",
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "fund_request_activities"
        ordering = ["id"]
        verbose_name = "Aktivitas Pengajuan"
        verbose_name_plural = "Aktivitas Pengajuan"
        constraints = [
            models.UniqueConstraint(
                fields=["fund_request", "rkat_activity"],
                name="unique_rkat_activity_per_request",
            )
        ]

    def clean(self):
        super().clean()

        if self.requested_amount is not None and self.requested_amount < 0:
            raise ValidationError({
                "requested_amount": "Nominal pengajuan tidak boleh negatif."
            })

        if (
            self.approved_amount is not None
            and self.approved_amount < 0
        ):
            raise ValidationError({
                "approved_amount": "Nominal persetujuan tidak boleh negatif."
            })

        if (
            self.approved_amount is not None
            and self.requested_amount is not None
            and self.approved_amount > self.requested_amount
        ):
            raise ValidationError({
                "approved_amount": "Nominal disetujui tidak boleh melebihi nominal yang diajukan."
            })

        if self.fund_request_id and self.rkat_activity_id:
            rkat = self.rkat_activity.rkat

            if rkat.study_program_id != self.fund_request.study_program_id:
                raise ValidationError(
                    "Aktivitas RKAT bukan milik Program Studi pengaju."
                )

            if rkat.budget_year_id != self.fund_request.budget_year_id:
                raise ValidationError(
                    "Aktivitas RKAT bukan untuk tahun anggaran pengajuan ini."
                )

            if rkat.status != "READY":
                raise ValidationError(
                    "Aktivitas RKAT belum siap digunakan untuk pengajuan dana."
                )

    @property
    def cost_total(self):
        return sum(
            (
                item.total_amount
                for item in self.cost_items.all()
            ),
            Decimal("0"),
        )

    def __str__(self):
        return f"{self.fund_request.request_number} - {self.rkat_activity.name}"


class RequestCostItem(TimeStampedModel):
    request_activity = models.ForeignKey(
        FundRequestActivity,
        on_delete=models.CASCADE,
        related_name="cost_items",
    )
    description = models.CharField(
        max_length=255,
        verbose_name="Uraian Biaya",
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=1,
        verbose_name="Jumlah",
    )
    unit = models.CharField(
        max_length=50,
        verbose_name="Satuan",
    )
    unit_price = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name="Harga Satuan",
    )

    class Meta:
        db_table = "request_cost_items"
        ordering = ["id"]
        verbose_name = "Rincian Biaya"
        verbose_name_plural = "Rincian Biaya"

    @property
    def total_amount(self):
        return self.quantity * self.unit_price

    def clean(self):
        super().clean()

        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({
                "quantity": "Jumlah harus lebih besar dari nol."
            })

        if self.unit_price is not None and self.unit_price < 0:
            raise ValidationError({
                "unit_price": "Harga satuan tidak boleh negatif."
            })

    def __str__(self):
        return self.description


class ApprovalHistory(TimeStampedModel):
    class Action(models.TextChoices):
        SUBMIT = "SUBMIT", "Diajukan"
        CHECK = "CHECK", "Diperiksa Fakultas"
        REVISION = "REVISION", "Dikembalikan untuk Revisi"
        FORWARD = "FORWARD", "Diteruskan ke Lembaga"
        RECORD_APPROVAL = "RECORD_APPROVAL", "Keputusan Lembaga Dicatat"
        REJECT = "REJECT", "Ditolak"
        CANCEL = "CANCEL", "Dibatalkan"

    fund_request = models.ForeignKey(
        FundRequest,
        on_delete=models.PROTECT,
        related_name="approval_histories",
    )
    action = models.CharField(
        max_length=30,
        choices=Action.choices,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="fund_request_actions",
    )
    notes = models.TextField(blank=True)
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fund_request_approval_histories"
        ordering = ["acted_at"]
        verbose_name = "Riwayat Pengajuan"
        verbose_name_plural = "Riwayat Pengajuan"

    def __str__(self):
        return f"{self.fund_request.request_number} - {self.get_action_display()}"


class Status(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Diajukan ke Fakultas"
    REVISION = "REVISION", "Perlu Revisi"
    FACULTY_CHECKED = "FACULTY_CHECKED", "Diperiksa Fakultas"
    FORWARDED = "FORWARDED", "Diteruskan ke Lembaga"
    APPROVED = "APPROVED", "Disetujui Lembaga"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED", "Disetujui Sebagian"
    REJECTED = "REJECTED", "Ditolak"
    CANCELLED = "CANCELLED", "Dibatalkan"


class FundRequestInstitutionDecision(TimeStampedModel):
    class DecisionStatus(models.TextChoices):
        APPROVED = "APPROVED", "Disetujui"
        PARTIAL = "PARTIAL", "Disetujui Sebagian"
        REJECTED = "REJECTED", "Ditolak"

    fund_request = models.OneToOneField(
        FundRequest,
        on_delete=models.PROTECT,
        related_name="institution_decision",
        verbose_name="Pengajuan Dana",
    )
    decision_status = models.CharField(
        max_length=20,
        choices=DecisionStatus.choices,
    )
    decision_date = models.DateField(
        verbose_name="Tanggal Keputusan",
    )
    reference_number = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Nomor Surat Keputusan",
    )
    document = models.FileField(
        upload_to="fund_requests/decisions/%Y/%m/",
        blank=True,
        null=True,
        max_length=500,
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_fund_request_decisions",
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "fund_request_institution_decisions"
        verbose_name = "Keputusan Lembaga atas Pengajuan"
        verbose_name_plural = "Keputusan Lembaga atas Pengajuan"

    def __str__(self):
        return (
            f"Keputusan {self.fund_request.request_number} - "
            f"{self.get_decision_status_display()}"
        )
