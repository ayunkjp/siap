
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.master_data.models import TimeStampedModel
from apps.fund_requests.models import FundRequest


class InstitutionDisbursement(TimeStampedModel):
    """
    Pencairan dana dari Lembaga kepada Fakultas.
    """

    fund_request = models.ForeignKey(
        FundRequest,
        on_delete=models.PROTECT,
        related_name="institution_disbursements",
        verbose_name="Pengajuan Dana",
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name="Nominal Diterima Fakultas",
    )
    disbursed_at = models.DateField(
        verbose_name="Tanggal Pencairan",
    )
    reference_number = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Nomor Referensi",
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_institution_disbursements",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Keterangan",
    )

    class Meta:
        db_table = "institution_disbursements"
        ordering = ["-disbursed_at", "-created_at"]
        verbose_name = "Pencairan Lembaga ke Fakultas"
        verbose_name_plural = "Pencairan Lembaga ke Fakultas"

    def clean(self):
        super().clean()

        if self.amount is not None and self.amount <= 0:
            raise ValidationError({
                "amount": "Nominal pencairan harus lebih besar dari nol."
            })

    def __str__(self):
        return (
            f"{self.fund_request.request_number} - "
            f"{self.amount}"
        )


class FacultyHandover(TimeStampedModel):
    """
    Penyerahan dana dari Fakultas kepada Prodi.
    """

    fund_request = models.ForeignKey(
        FundRequest,
        on_delete=models.PROTECT,
        related_name="faculty_handovers",
        verbose_name="Pengajuan Dana",
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name="Nominal Diserahkan ke Prodi",
    )
    handed_over_at = models.DateField(
        verbose_name="Tanggal Penyerahan",
    )
    reference_number = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Nomor Bukti Penyerahan",
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_faculty_handovers",
    )
    received_by = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="Penerima Dana",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Keterangan",
    )

    class Meta:
        db_table = "faculty_handovers"
        ordering = ["-handed_over_at", "-created_at"]
        verbose_name = "Penyerahan Dana Fakultas ke Prodi"
        verbose_name_plural = "Penyerahan Dana Fakultas ke Prodi"

    def clean(self):
        super().clean()

        if self.amount is not None and self.amount <= 0:
            raise ValidationError({
                "amount": "Nominal penyerahan harus lebih besar dari nol."
            })

    def __str__(self):
        return (
            f"{self.fund_request.request_number} - "
            f"{self.amount}"
        )