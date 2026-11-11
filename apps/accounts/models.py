
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.master_data.models import (
    TimeStampedModel,
    Faculty,
    StudyProgram,
)


class UserProfile(TimeStampedModel):

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        PRODI = "PRODI", "Prodi"
        FAKULTAS = "FAKULTAS", "Fakultas"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        verbose_name="Role",
    )

    faculty = models.ForeignKey(
        Faculty,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="user_profiles",
    )

    study_program = models.ForeignKey(
        StudyProgram,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="user_profiles",
    )

    class Meta:
        db_table = "accounts_user_profile"
        verbose_name = "Profil Pengguna"
        verbose_name_plural = "Profil Pengguna"

    def clean(self):
        super().clean()

        if self.role == self.Role.ADMIN:
            if self.faculty or self.study_program:
                raise ValidationError(
                    "Admin tidak boleh terikat ke Fakultas atau Prodi."
                )

        elif self.role == self.Role.FAKULTAS:
            if not self.faculty:
                raise ValidationError({
                    "faculty": "Pengguna Fakultas wajib memiliki Fakultas."
                })

            if self.study_program:
                raise ValidationError({
                    "study_program": (
                        "Pengguna Fakultas tidak boleh terikat langsung ke Prodi."
                    )
                })

        elif self.role == self.Role.PRODI:
            if not self.study_program:
                raise ValidationError({
                    "study_program": "Pengguna Prodi wajib memiliki Prodi."
                })

            if self.faculty:
                raise ValidationError(
                    "Pengguna Prodi cukup terhubung melalui Program Studi."
                )

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"


class AuditLogQuerySet(models.QuerySet):
    """
    Mencegah perubahan dan penghapusan Audit Log melalui QuerySet.
    """

    def update(self, **kwargs):
        raise ValidationError("Audit Log tidak boleh diubah.")

    def delete(self):
        raise ValidationError("Audit Log tidak boleh dihapus.")


class AuditLog(models.Model):
    objects = AuditLogQuerySet.as_manager()

    class Action(models.TextChoices):
        CREATE = "CREATE", "Membuat"
        UPDATE = "UPDATE", "Mengubah"
        DELETE = "DELETE", "Menghapus"
        SUBMIT = "SUBMIT", "Mengirim"
        APPROVE = "APPROVE", "Menyetujui"
        REJECT = "REJECT", "Menolak"
        REVISION = "REVISION", "Mengembalikan untuk revisi"
        FORWARD = "FORWARD", "Meneruskan"
        ACCEPT = "ACCEPT", "Menerima"
        OTHER = "OTHER", "Lainnya"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_logs",
        verbose_name="Pengguna",
    )

    action = models.CharField(
        max_length=20,
        choices=Action.choices,
        verbose_name="Tindakan",
    )

    app_label = models.CharField(
        max_length=100,
        verbose_name="Nama aplikasi",
    )

    model_name = models.CharField(
        max_length=100,
        verbose_name="Nama model",
    )

    object_id = models.CharField(
        max_length=100,
        verbose_name="ID objek",
    )

    object_repr = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Representasi objek",
    )

    description = models.TextField(
        blank=True,
        verbose_name="Keterangan",
    )

    changes = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Perubahan data",
    )

    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name="Alamat IP",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Waktu aktivitas",
    )

    class Meta:
        db_table = "accounts_audit_log"
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Log"
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"{self.actor} - "
            f"{self.get_action_display()} - "
            f"{self.model_name} #{self.object_id}"
        )

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.objects.filter(pk=self.pk).exists():
            raise ValidationError(
                "Audit Log yang sudah tersimpan tidak boleh diubah."
            )

        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit Log tidak boleh dihapus.")
