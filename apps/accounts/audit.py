
from apps.accounts.models import AuditLog


def log_audit(
    actor,
    action,
    instance,
    description="",
    changes=None,
    ip_address=None,
):
    """
    Mencatat aktivitas penting pengguna pada sistem.
    """

    if not actor or not actor.is_authenticated:
        raise ValueError(
            "Audit log harus memiliki pengguna yang valid."
        )

    if instance is None:
        raise ValueError(
            "Objek yang dicatat tidak boleh kosong."
        )

    if action not in AuditLog.Action.values:
        raise ValueError(
            "Jenis tindakan audit tidak valid."
        )

    return AuditLog.objects.create(
        actor=actor,
        action=action,
        app_label=instance._meta.app_label,
        model_name=instance._meta.model_name,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        description=description,
        changes=changes or {},
        ip_address=ip_address,
    )


def get_client_ip(request):
    """
    Mengambil IP dari koneksi langsung.
    """

    if not request:
        return None

    return request.META.get("REMOTE_ADDR")
