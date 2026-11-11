
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(*allowed_roles):
    def decorator(view_func):
        @login_required(login_url="login")
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            try:
                role = request.user.profile.role
            except Exception:
                raise PermissionDenied(
                    "Profil pengguna belum tersedia."
                )

            if role not in allowed_roles:
                raise PermissionDenied(
                    "Anda tidak memiliki hak akses ke halaman ini."
                )

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
