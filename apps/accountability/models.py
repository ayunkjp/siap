
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.master_data.models import TimeStampedModel
from apps.fund_requests.models import FundRequest
from decimal import Decimal

import os

MAX_ACCOUNTABILITY_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

ALLOWED_FILE_SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".docx": (b"PK\x03\x04",),
    ".xlsx": (b"PK\x03\x04",),
}


def validate_accountability_file(uploaded_file):
    """
    Validasi ukuran, ekstensi, dan signature dasar file LPJ.
    """

    extension = os.path.splitext(uploaded_file.name)[1].lower()

    if extension not in ALLOWED_FILE_SIGNATURES:
        raise ValidationError(
            "Format file harus PDF, JPG, JPEG, PNG, DOC, DOCX, atau XLSX."
        )

    if uploaded_file.size > MAX_ACCOUNTABILITY_FILE_SIZE:
        raise ValidationError(
            "Ukuran file lampiran maksimal 10 MB."
        )

    try:
        uploaded_file.seek(0)
        file_signature = uploaded_file.read(8)
        uploaded_file.seek(0)
    except (AttributeError, OSError):
        raise ValidationError(
            "File tidak dapat dibaca atau formatnya tidak valid."
        )

    valid_signatures = ALLOWED_FILE_SIGNATURES[extension]

    if not any(
        file_signature.startswith(signature)
        for signature in valid_signatures
    ):
        raise ValidationError(
            "Isi file tidak sesuai dengan format file yang dipilih."
        )

class AccountabilityReport(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Dikirim ke Fakultas"
        REVISION = "REVISION", "Perlu Revisi"
        ACCEPTED = "ACCEPTED", "Diterima Fakultas"

    fund_request = models.OneToOneField(
        FundRequest,
        on_delete=models.PROTECT,
        related_name="accountability_report",
        verbose_name="Pengajuan Dana",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="prepared_accountability_reports",
        verbose_name="Disusun Oleh",
    )
    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Tanggal Pengiriman",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_accountability_reports",
        null=True,
        blank=True,
        verbose_name="Diperiksa Oleh",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Tanggal Pemeriksaan",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Catatan LPJ",
    )
    revision_notes = models.TextField(
        blank=True,
        verbose_name="Catatan Revisi",
    )

    class Meta:
        db_table = "accountability_reports"
        ordering = ["-created_at"]
        verbose_name = "Laporan Pertanggungjawaban"
        verbose_name_plural = "Laporan Pertanggungjawaban"

    def clean(self):
        super().clean()

        if self.fund_request_id:
            if self.fund_request.status not in [
                FundRequest.Status.APPROVED,
                FundRequest.Status.PARTIALLY_APPROVED,
            ]:
                raise ValidationError({
                    "fund_request": (
                        "LPJ hanya dapat dibuat untuk pengajuan "
                        "yang sudah disetujui Lembaga."
                    )
                })
            
    @property
    def realization_total(self):
        return (
            self.fund_request.activities.aggregate(
                total=models.Sum("realizations__amount")
            )["total"]
            or Decimal("0")
        )

    def __str__(self):
        return f"LPJ - {self.fund_request.request_number}"


class AccountabilityAttachment(TimeStampedModel):
    report = models.ForeignKey(
        AccountabilityReport,
        on_delete=models.PROTECT,
        related_name="attachments",
    )

    title = models.CharField(
        max_length=255,
        verbose_name="Nama Dokumen",
    )

    file = models.FileField(
        upload_to="accountability/attachments/%Y/%m/",
        max_length=500,
        validators=[validate_accountability_file],
        verbose_name="File Lampiran",
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_accountability_attachments",
    )

    class Meta:
        db_table = "accountability_attachments"
        ordering = ["created_at"]
        verbose_name = "Lampiran LPJ"
        verbose_name_plural = "Lampiran LPJ"

    def clean(self):
        super().clean()

        if self.report_id:
            if self.report.status not in [
                AccountabilityReport.Status.DRAFT,
                AccountabilityReport.Status.REVISION,
            ]:
                raise ValidationError({
                    "report": (
                        "Lampiran hanya dapat diubah ketika LPJ "
                        "berstatus Draft atau Perlu Revisi."
                    )
                })

    def delete(self, *args, **kwargs):
        if self.report.status not in [
            AccountabilityReport.Status.DRAFT,
            AccountabilityReport.Status.REVISION,
        ]:
            raise ValidationError(
                "Lampiran tidak dapat dihapus setelah LPJ dikirim."
            )

        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.title


class AccountabilityHistory(TimeStampedModel):
    class Action(models.TextChoices):
        SUBMIT = "SUBMIT", "Dikirim ke Fakultas"
        REVISION = "REVISION", "Dikembalikan untuk Revisi"
        ACCEPT = "ACCEPT", "Diterima Fakultas"

    report = models.ForeignKey(
        AccountabilityReport,
        on_delete=models.PROTECT,
        related_name="histories",
    )
    action = models.CharField(
        max_length=20,
        choices=Action.choices,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="accountability_actions",
    )
    notes = models.TextField(blank=True)
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accountability_histories"
        ordering = ["acted_at"]
        verbose_name = "Riwayat LPJ"
        verbose_name_plural = "Riwayat LPJ"

    def __str__(self):
        return (
            f"{self.report.fund_request.request_number} - "
            f"{self.get_action_display()}"
        )
