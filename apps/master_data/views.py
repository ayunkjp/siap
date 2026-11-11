
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django.utils.dateparse import parse_date

from apps.accounts.decorators import role_required
from .models import BudgetYear, Faculty, StudyProgram
from apps.master_data.models import BudgetYear, Faculty, StudyProgram
from apps.accounts.audit import log_audit, get_client_ip
from apps.accounts.models import AuditLog


@role_required("ADMIN")
def faculty_index(request):
    return render(request, "master_data/faculty/index.html")


@role_required("ADMIN")
@require_GET
def faculty_data(request):
    faculties = Faculty.objects.all().values(
        "id",
        "name",
        "code",
        "address",
    )

    return JsonResponse({
        "data": list(faculties)
    })


@role_required("ADMIN")
@require_POST
def faculty_save(request):
    faculty_id = request.POST.get("id", "").strip()
    name = request.POST.get("name", "").strip()
    code = request.POST.get("code", "").strip().upper()
    address = request.POST.get("address", "").strip()

    if not name or not code:
        return JsonResponse({
            "success": False,
            "message": "Nama fakultas dan kode fakultas wajib diisi."
        }, status=400)

    if len(name) > 150 or len(code) > 20:
        return JsonResponse({
            "success": False,
            "message": "Nama maksimal 150 karakter dan kode maksimal 20 karakter."
        }, status=400)

    try:
        with transaction.atomic():
            if faculty_id:
                faculty = get_object_or_404(
                    Faculty,
                    pk=faculty_id,
                )
                is_new = False

                old_values = {
                    "name": faculty.name,
                    "code": faculty.code,
                    "address": faculty.address,
                }
            else:
                faculty = Faculty()
                is_new = True
                old_values = {}

            faculty.name = name
            faculty.code = code
            faculty.address = address or None

            faculty.full_clean()
            faculty.save()

            new_values = {
                "name": faculty.name,
                "code": faculty.code,
                "address": faculty.address,
            }

            log_audit(
                actor=request.user,
                action=(
                    AuditLog.Action.CREATE
                    if is_new
                    else AuditLog.Action.UPDATE
                ),
                instance=faculty,
                description=(
                    "Admin menambahkan Fakultas."
                    if is_new
                    else "Admin mengubah data Fakultas."
                ),
                changes={
                    "old": old_values,
                    "new": new_values,
                },
                ip_address=get_client_ip(request),
            )

        return JsonResponse({
            "success": True,
            "message": "Data fakultas berhasil disimpan."
        })

    except ValidationError as exc:
        if hasattr(exc, "message_dict"):
            errors = exc.message_dict
            message = " ".join(
                str(item)
                for field_errors in errors.values()
                for item in field_errors
            )
        else:
            message = " ".join(exc.messages)

        return JsonResponse({
            "success": False,
            "message": message
        }, status=400)

    except IntegrityError:
        return JsonResponse({
            "success": False,
            "message": "Kode fakultas sudah digunakan."
        }, status=400)

@role_required("ADMIN")
@require_POST
def faculty_delete(request, pk):
    faculty = get_object_or_404(Faculty, pk=pk)

    if StudyProgram.objects.filter(faculty=faculty).exists():
        return JsonResponse({
            "success": False,
            "message": (
                "Fakultas tidak dapat dihapus karena "
                "masih memiliki Program Studi."
            )
        }, status=400)

    with transaction.atomic():
        faculty_data = {
            "id": faculty.pk,
            "name": faculty.name,
            "code": faculty.code,
            "address": faculty.address,
        }

        log_audit(
            actor=request.user,
            action=AuditLog.Action.DELETE,
            instance=faculty,
            description="Admin menghapus data Fakultas.",
            changes={
                "deleted_data": faculty_data,
            },
            ip_address=get_client_ip(request),
        )

        faculty.delete()

    return JsonResponse({
        "success": True,
        "message": "Data fakultas berhasil dihapus."
    })


@role_required("ADMIN")
def study_program_index(request):
    faculties = Faculty.objects.all().order_by("name")

    return render(
        request,
        "master_data/study_program/index.html",
        {"faculties": faculties}
    )


@role_required("ADMIN")
@require_GET
def study_program_data(request):
    programs = StudyProgram.objects.select_related("faculty").all()

    data = [
        {
            "id": program.id,
            "code": program.code,
            "name": program.name,
            "faculty_id": program.faculty_id,
            "faculty_name": program.faculty.name,
        }
        for program in programs
    ]

    return JsonResponse({"data": data})


@role_required("ADMIN")
@require_POST
def study_program_save(request):
    program_id = request.POST.get("id", "").strip()
    faculty_id = request.POST.get("faculty_id", "").strip()
    name = request.POST.get("name", "").strip()
    code = request.POST.get("code", "").strip().upper()

    if not faculty_id or not name or not code:
        return JsonResponse({
            "success": False,
            "message": "Fakultas, kode, dan nama Program Studi wajib diisi."
        }, status=400)

    if len(name) > 150 or len(code) > 20:
        return JsonResponse({
            "success": False,
            "message": "Nama maksimal 150 karakter dan kode maksimal 20 karakter."
        }, status=400)

    try:
        faculty = Faculty.objects.get(pk=faculty_id)

        with transaction.atomic():
            if program_id:
                program = get_object_or_404(
                    StudyProgram,
                    pk=program_id,
                )
                is_new = False

                old_values = {
                    "faculty_id": program.faculty_id,
                    "faculty_name": program.faculty.name,
                    "name": program.name,
                    "code": program.code,
                }
            else:
                program = StudyProgram()
                is_new = True
                old_values = {}

            program.faculty = faculty
            program.name = name
            program.code = code

            program.full_clean()
            program.save()

            new_values = {
                "faculty_id": program.faculty_id,
                "faculty_name": program.faculty.name,
                "name": program.name,
                "code": program.code,
            }

            log_audit(
                actor=request.user,
                action=(
                    AuditLog.Action.CREATE
                    if is_new
                    else AuditLog.Action.UPDATE
                ),
                instance=program,
                description=(
                    "Admin menambahkan Program Studi."
                    if is_new
                    else "Admin mengubah data Program Studi."
                ),
                changes={
                    "old": old_values,
                    "new": new_values,
                },
                ip_address=get_client_ip(request),
            )

        return JsonResponse({
            "success": True,
            "message": "Data Program Studi berhasil disimpan."
        })

    except Faculty.DoesNotExist:
        return JsonResponse({
            "success": False,
            "message": "Fakultas yang dipilih tidak ditemukan."
        }, status=400)

    except ValidationError as exc:
        if hasattr(exc, "message_dict"):
            message = " ".join(
                str(item)
                for errors in exc.message_dict.values()
                for item in errors
            )
        else:
            message = " ".join(exc.messages)

        return JsonResponse({
            "success": False,
            "message": message
        }, status=400)

    except IntegrityError:
        return JsonResponse({
            "success": False,
            "message": "Kode Program Studi sudah digunakan."
        }, status=400)


@role_required("ADMIN")
@require_POST
def study_program_delete(request, pk):
    program = get_object_or_404(StudyProgram, pk=pk)

    # Cegah penghapusan jika masih ada data yang terhubung.
    for relation in StudyProgram._meta.related_objects:
        related_model = relation.related_model
        related_field = relation.field.name

        if related_model.objects.filter(
            **{related_field: program}
        ).exists():
            return JsonResponse({
                "success": False,
                "message": (
                    "Program Studi tidak dapat dihapus karena masih "
                    "digunakan oleh data lain."
                )
            }, status=400)

    with transaction.atomic():
        program_data = {
            "id": program.pk,
            "faculty_id": program.faculty_id,
            "faculty_name": program.faculty.name,
            "name": program.name,
            "code": program.code,
        }

        log_audit(
            actor=request.user,
            action=AuditLog.Action.DELETE,
            instance=program,
            description="Admin menghapus data Program Studi.",
            changes={
                "deleted_data": program_data,
            },
            ip_address=get_client_ip(request),
        )

        program.delete()

    return JsonResponse({
        "success": True,
        "message": "Data Program Studi berhasil dihapus."
    })


@role_required("ADMIN")
def budget_year_index(request):
    return render(
        request,
        "master_data/budget_year/index.html"
    )


@role_required("ADMIN")
@require_GET
def budget_year_data(request):
    years = BudgetYear.objects.all().order_by("-start_date")

    data = [
        {
            "id": year.id,
            "name": year.name,
            "start_date": year.start_date.strftime("%Y-%m-%d"),
            "end_date": year.end_date.strftime("%Y-%m-%d"),
            "is_active": year.is_active,
        }
        for year in years
    ]

    return JsonResponse({"data": data})


@role_required("ADMIN")
@require_POST
def budget_year_save(request):
    year_id = request.POST.get("id", "").strip()
    name = request.POST.get("name", "").strip()
    start_date = parse_date(request.POST.get("start_date", ""))
    end_date = parse_date(request.POST.get("end_date", ""))
    is_active = request.POST.get("is_active") == "on"

    if not name or not start_date or not end_date:
        return JsonResponse({
            "success": False,
            "message": "Semua data wajib diisi dengan benar."
        }, status=400)

    if len(name) > 20:
        return JsonResponse({
            "success": False,
            "message": "Nama tahun anggaran maksimal 20 karakter."
        }, status=400)

    try:
        with transaction.atomic():
            if year_id:
                year = get_object_or_404(
                    BudgetYear,
                    pk=year_id,
                )
                is_new = False

                old_values = {
                    "name": year.name,
                    "start_date": year.start_date.isoformat(),
                    "end_date": year.end_date.isoformat(),
                    "is_active": year.is_active,
                }
            else:
                year = BudgetYear()
                is_new = True
                old_values = {}

            year.name = name
            year.start_date = start_date
            year.end_date = end_date
            year.is_active = is_active

            year.full_clean()
            year.save()

            new_values = {
                "name": year.name,
                "start_date": year.start_date.isoformat(),
                "end_date": year.end_date.isoformat(),
                "is_active": year.is_active,
            }

            log_audit(
                actor=request.user,
                action=(
                    AuditLog.Action.CREATE
                    if is_new
                    else AuditLog.Action.UPDATE
                ),
                instance=year,
                description=(
                    "Admin menambahkan Tahun Anggaran."
                    if is_new
                    else "Admin mengubah Tahun Anggaran."
                ),
                changes={
                    "old": old_values,
                    "new": new_values,
                },
                ip_address=get_client_ip(request),
            )

        return JsonResponse({
            "success": True,
            "message": "Tahun anggaran berhasil disimpan."
        })

    except ValidationError as exc:
        if hasattr(exc, "message_dict"):
            message = " ".join(
                str(item)
                for errors in exc.message_dict.values()
                for item in errors
            )
        else:
            message = " ".join(exc.messages)

        return JsonResponse({
            "success": False,
            "message": message
        }, status=400)

    except IntegrityError:
        return JsonResponse({
            "success": False,
            "message": "Nama tahun anggaran sudah digunakan."
        }, status=400)


@role_required("ADMIN")
@require_POST
def budget_year_delete(request, pk):
    year = get_object_or_404(BudgetYear, pk=pk)

    # Cegah penghapusan tahun yang sudah memiliki data transaksi.
    for relation in BudgetYear._meta.related_objects:
        related_model = relation.related_model
        related_field = relation.field.name

        if related_model.objects.filter(
            **{related_field: year}
        ).exists():
            return JsonResponse({
                "success": False,
                "message": (
                    "Tahun anggaran tidak dapat dihapus karena "
                    "sudah digunakan oleh data lain."
                )
            }, status=400)

    with transaction.atomic():
        year_data = {
            "id": year.pk,
            "name": year.name,
            "start_date": year.start_date.isoformat(),
            "end_date": year.end_date.isoformat(),
            "is_active": year.is_active,
        }

        log_audit(
            actor=request.user,
            action=AuditLog.Action.DELETE,
            instance=year,
            description="Admin menghapus Tahun Anggaran.",
            changes={
                "deleted_data": year_data,
            },
            ip_address=get_client_ip(request),
        )

        year.delete()

    return JsonResponse({
        "success": True,
        "message": "Tahun anggaran berhasil dihapus."
    })
