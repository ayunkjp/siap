 
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.master_data.models import TimeStampedModel
from apps.fund_requests.models import FundRequestActivity


class Realization(TimeStampedModel):
    request_activity = models.ForeignKey(
        FundRequestActivity,
        on_delete=models.PROTECT,
        related_name="realizations",
        verbose_name="Aktivitas Pengajuan",
    )
    realization_date = models.DateField(
        verbose_name="Tanggal Pengeluaran",
    )
    description = models.CharField(
        max_length=255,
        verbose_name="Uraian Pengeluaran",
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name="Nominal Realisasi",
    )
    evidence = models.FileField(
        upload_to="realizations/evidence/%Y/%m/",
        max_length=500,
        verbose_name="Bukti Pengeluaran",
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_realizations",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Keterangan",
    )

    class Meta:
        db_table = "realizations"
        ordering = ["-realization_date", "-created_at"]
        verbose_name = "Realisasi Penggunaan Dana"
        verbose_name_plural = "Realisasi Penggunaan Dana"

    def clean(self):
        super().clean()

        if self.amount is not None and self.amount <= 0:
            raise ValidationError({
                "amount": "Nominal realisasi harus lebih besar dari nol."
            })

        
        if self.request_activity_id:
            fund_request = self.request_activity.fund_request

            if fund_request.status not in [
                "APPROVED",
                "PARTIALLY_APPROVED",
            ]:
                raise ValidationError(
                    "Realisasi hanya dapat dicatat untuk pengajuan "
                    "yang sudah disetujui Lembaga."
                )

    def __str__(self):
        return (
            f"{self.request_activity.fund_request.request_number} - "
            f"{self.description}"
        )
