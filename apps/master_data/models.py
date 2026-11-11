from django.db import models

# Create your models here.

from django.db import models
from django.core.exceptions import ValidationError


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Faculty(TimeStampedModel):
    name = models.CharField(
        max_length=150,
        verbose_name="Nama Fakultas"
    )

    code = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Kode Fakultas"
    )

    address = models.TextField(
        blank=True,
        null=True,
        verbose_name="Alamat"
    )

    class Meta:
        db_table = "master_faculty"
        ordering = ["name"]
        verbose_name = "Fakultas"
        verbose_name_plural = "Fakultas"

    def __str__(self):
        return self.name


class StudyProgram(TimeStampedModel):
    faculty = models.ForeignKey(
        Faculty,
        on_delete=models.PROTECT,
        related_name="study_programs"
    )

    name = models.CharField(
        max_length=150,
        verbose_name="Nama Program Studi"
    )

    code = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Kode Program Studi"
    )

    class Meta:
        db_table = "master_study_program"
        ordering = ["name"]
        verbose_name = "Program Studi"
        verbose_name_plural = "Program Studi"

    def __str__(self):
        return self.name


class BudgetYear(TimeStampedModel):
    name = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Tahun Anggaran"
    )

    start_date = models.DateField(
        verbose_name="Tanggal Mulai"
    )

    end_date = models.DateField(
        verbose_name="Tanggal Selesai"
    )

    is_active = models.BooleanField(
        default=False,
        verbose_name="Tahun Aktif"
    )

    class Meta:
        db_table = "master_budget_year"
        ordering = ["-start_date"]
        verbose_name = "Tahun Anggaran"
        verbose_name_plural = "Tahun Anggaran"

    def save(self, *args, **kwargs):
      from django.db import transaction

      with transaction.atomic():
          if self.is_active:
              BudgetYear.objects.filter(
                  is_active=True
              ).exclude(
                  pk=self.pk
              ).update(is_active=False)

          super().save(*args, **kwargs)

    def clean(self):
        super().clean()

        if self.start_date and self.end_date:
            if self.end_date <= self.start_date:
                raise ValidationError({
                    "end_date": "Tanggal selesai harus setelah tanggal mulai."
                })

    def __str__(self):
        return self.name