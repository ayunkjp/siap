from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from apps.accounts.decorators import role_required
from .services import create_rkat_submission
from .services import save_rkat_activity, delete_rkat_activity
from .services import submit_rkat, forward_rkat
from .services import record_institution_decision, get_user_role
from decimal import Decimal, InvalidOperation
from django.shortcuts import get_object_or_404
from django.db import transaction
from .models import RKATSubmission, RKATActivity
from datetime import date
from pathlib import Path
from django.conf import settings
from django.http import HttpResponseForbidden 
from django.shortcuts import render, redirect
from django.contrib import messages
from .models import InstitutionDecision
from django.views.decorators.http import require_http_methods
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from apps.accounts.audit import get_client_ip


@role_required("ADMIN", "PRODI", "FAKULTAS")
def rkat_index(request):
    return render(request, "rkat/index.html")


@role_required("ADMIN", "PRODI", "FAKULTAS")
@require_GET
def rkat_data(request):
    profile = request.user.profile

    queryset = RKATSubmission.objects.select_related(
        "study_program",
        "study_program__faculty",
        "budget_year",
    )

    if profile.role == "PRODI":
        queryset = queryset.filter(
            study_program_id=profile.study_program_id
        )

    elif profile.role == "FAKULTAS":
        queryset = queryset.filter(
            study_program__faculty_id=profile.faculty_id
        )

    data = []

    for rkat in queryset.order_by("-created_at"):
        data.append({
            "id": rkat.id,
            "submission_number": rkat.submission_number,
            "study_program": rkat.study_program.name,
            "faculty": rkat.study_program.faculty.name,
            "budget_year": rkat.budget_year.name,
            "proposed_total": str(rkat.proposed_total),
            "status": rkat.get_status_display(),
            "status_code": rkat.status,
        })

    return JsonResponse({"data": data})


@role_required("PRODI")
@require_POST
def rkat_create(request):
    notes = request.POST.get("notes", "").strip()

    try:
        rkat = create_rkat_submission(
            ip_address=get_client_ip(request),
            actor=request.user,
            notes=notes,
        )

        return JsonResponse({
            "success": True,
            "message": "Draft RKAT berhasil dibuat.",
            "id": rkat.id,
            "submission_number": rkat.submission_number,
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
            "message": message,
        }, status=400)


def get_rkat_queryset_for_user(user):
    profile = user.profile

    queryset = RKATSubmission.objects.select_related(
        "study_program",
        "study_program__faculty",
        "budget_year",
    )

    if profile.role == "PRODI":
        queryset = queryset.filter(
            study_program_id=profile.study_program_id
        )

    elif profile.role == "FAKULTAS":
        queryset = queryset.filter(
            study_program__faculty_id=profile.faculty_id
        )

    return queryset


@role_required("ADMIN", "PRODI", "FAKULTAS")
def rkat_detail(request, pk):
    rkat = get_object_or_404(
        get_rkat_queryset_for_user(request.user),
        pk=pk,
    )

    return render(request, "rkat/detail.html", {
        "rkat": rkat,
        "activities": rkat.activities.all(),
        "can_edit": (
            request.user.profile.role == "PRODI"
            and rkat.status == RKATSubmission.Status.DRAFT
        ),
    })


@role_required("PRODI")
@require_POST
def rkat_activity_save(request, rkat_id):
    activity_id = request.POST.get("id", "").strip()
    name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()
    amount_input = request.POST.get("proposed_amount", "").strip()

    if not name or not amount_input:
        return JsonResponse({
            "success": False,
            "message": "Nama kegiatan dan nominal usulan wajib diisi."
        }, status=400)

    try:
        amount = Decimal(amount_input)

        if not amount.is_finite() or amount < 0:
            raise InvalidOperation

        activity = save_rkat_activity(
            rkat_id=rkat_id,
            actor=request.user,
            name=name,
            description=description,
            proposed_amount=amount,
            activity_id=activity_id or None,
            ip_address=get_client_ip(request),
        )

        return JsonResponse({
            "success": True,
            "message": "Komponen RKAT berhasil disimpan.",
            "id": activity.id,
        })

    except (InvalidOperation, ValueError):
        return JsonResponse({
            "success": False,
            "message": "Nominal usulan tidak valid."
        }, status=400)

    except (ValidationError, RKATSubmission.DoesNotExist,
            RKATActivity.DoesNotExist) as exc:
        message = (
            " ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else "Data RKAT atau komponen tidak ditemukan."
        )

        return JsonResponse({
            "success": False,
            "message": message
        }, status=400)


@role_required("PRODI")
@require_POST
def rkat_activity_delete(request, activity_id):
    try:
        delete_rkat_activity(
            activity_id=activity_id,
            actor=request.user,
            ip_address=get_client_ip(request),
        )

        return JsonResponse({
            "success": True,
            "message": "Komponen RKAT berhasil dihapus."
        })

    except (ValidationError, RKATActivity.DoesNotExist) as exc:
        message = (
            " ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else "Komponen RKAT tidak ditemukan."
        )

        return JsonResponse({
            "success": False,
            "message": message
        }, status=400)

@role_required("ADMIN", "PRODI", "FAKULTAS")
@require_GET
def rkat_activity_data(request, rkat_id):
    rkat = get_object_or_404(
        get_rkat_queryset_for_user(request.user),
        pk=rkat_id,
    )

    activities = rkat.activities.all().order_by("id")

    data = [
        {
            "id": activity.id,
            "name": activity.name,
            "description": activity.description or "",
            "proposed_amount": str(activity.proposed_amount),
        }
        for activity in activities
    ]

    return JsonResponse({"data": data})



@role_required("PRODI")
@require_POST
def rkat_submit(request, pk):
    try:
        rkat = submit_rkat(
            rkat_id=pk,
            actor=request.user,
            ip_address=get_client_ip(request),
        )

        return JsonResponse({
            "success": True,
            "message": "RKAT berhasil diajukan ke Fakultas.",
            "status": rkat.get_status_display(),
        })

    except ValidationError as exc:
        message = (
            " ".join(exc.messages)
            if hasattr(exc, "messages")
            else str(exc)
        )

        return JsonResponse({
            "success": False,
            "message": message,
        }, status=400)




@role_required("FAKULTAS")
@require_POST
def rkat_forward(request, pk):
    reference_number = request.POST.get(
        "reference_number", ""
    ).strip()

    notes = request.POST.get("notes", "").strip()

    try:
        forward_rkat(
            rkat_id=pk,
            actor=request.user,
            reference_number=reference_number,
            notes=notes,
            ip_address=get_client_ip(request),
        )

        return JsonResponse({
            "success": True,
            "message": "RKAT berhasil diteruskan ke Lembaga.",
        })

    except ValidationError as exc:
        message = (
            " ".join(exc.messages)
            if hasattr(exc, "messages")
            else str(exc)
        )

        return JsonResponse({
            "success": False,
            "message": message,
        }, status=400)


@login_required
@require_http_methods(["GET", "POST"])
def rkat_record_decision(request, pk):
    role = get_user_role(request.user)

    if role not in ["ADMIN", "FAKULTAS"]:
        return HttpResponseForbidden(
            "Anda tidak memiliki akses untuk mencatat keputusan Lembaga."
        )

    rkat = get_object_or_404(
        get_rkat_queryset_for_user(request.user),
        pk=pk
    )

    # Cek apakah keputusan sudah pernah dicatat
    existing_decision = InstitutionDecision.objects.filter(
        rkat=rkat
    ).first()

    if existing_decision:
        messages.info(request, "Keputusan Lembaga untuk RKAT ini sudah dicatat.")
        return redirect("rkat:detail", pk=rkat.pk)

    if rkat.status != RKATSubmission.Status.FORWARDED:
        messages.warning(
            request,
            "Keputusan hanya dapat dicatat untuk RKAT yang sudah diteruskan ke Lembaga."
        )
        return redirect("rkat:detail", pk=rkat.pk)

    if request.method == "POST":
        decision_status = request.POST.get("decision_status")
        approved_total = request.POST.get("approved_total")
        decision_date = request.POST.get("decision_date")
        reference_number = request.POST.get("reference_number", "").strip()
        notes = request.POST.get("notes", "").strip()
        document = request.FILES.get("document")

        try:
            if not reference_number:
                raise ValidationError("Nomor referensi keputusan wajib diisi.")

            if not decision_date:
                raise ValidationError("Tanggal keputusan wajib diisi.")

            parsed_date = date.fromisoformat(decision_date)

            if parsed_date > date.today():
                raise ValidationError("Tanggal keputusan tidak boleh di masa depan.")

            if document:
                allowed_extensions = [".pdf", ".jpg", ".jpeg", ".png"]
                extension = Path(document.name).suffix.lower()

                if extension not in allowed_extensions:
                    raise ValidationError(
                        "Dokumen harus berformat PDF, JPG, JPEG, atau PNG."
                    )

                max_size = 5 * 1024 * 1024

                if document.size > max_size:
                    raise ValidationError(
                        "Ukuran dokumen maksimal 5 MB."
                    )

            record_institution_decision(
                rkat_id=rkat.pk,
                actor=request.user,
                ip_address=get_client_ip(request),
                decision_status=decision_status,
                approved_total=approved_total,
                decision_date=parsed_date,
                reference_number=reference_number,
                document=document,
                notes=notes,
            )

            messages.success(
                request,
                "Keputusan Lembaga berhasil dicatat."
            )
            return redirect("rkat:detail", pk=rkat.pk)

        except (ValidationError, ValueError) as e:
            if isinstance(e, ValidationError):
                if hasattr(e, "messages"):
                    error_message = " ".join(e.messages)
                else:
                    error_message = str(e)
            else:
                error_message = "Format tanggal atau nominal tidak valid."

            messages.error(request, error_message)

    context = {
        "rkat": rkat,
        "decision_status_choices": InstitutionDecision.DecisionStatus.choices,
    }

    return render(
        request,
        "rkat/record_decision.html",
        context
    )

from .models import (
    RKATSubmission,
    InstitutionDecision,
    ProgramBudgetAllocation,
)

from .services import (
    get_user_role,
    set_program_budget_allocation,
)

@login_required
@require_http_methods(["GET", "POST"])
def rkat_set_budget(request, pk):
    # Hanya Fakultas yang boleh menetapkan pagu
    if get_user_role(request.user) != "FAKULTAS":
        return HttpResponseForbidden(
            "Hanya Fakultas yang dapat menetapkan pagu final."
        )

    # Menggunakan queryset sesuai lingkup Fakultas
    rkat = get_object_or_404(
        get_rkat_queryset_for_user(request.user),
        pk=pk
    )

    # RKAT harus sudah memiliki keputusan Lembaga
    decision = InstitutionDecision.objects.filter(
        rkat=rkat
    ).order_by("-decision_date", "-created_at").first()

    if not decision:
        messages.error(
            request,
            "Keputusan Lembaga belum dicatat."
        )
        return redirect("rkat:detail", pk=rkat.pk)

    if decision.decision_status not in [
        InstitutionDecision.DecisionStatus.APPROVED,
        InstitutionDecision.DecisionStatus.PARTIAL,
    ]:
        messages.error(
            request,
            "RKAT tidak dapat ditetapkan pagunya karena keputusan Lembaga ditolak."
        )
        return redirect("rkat:detail", pk=rkat.pk)

    if rkat.status not in [
        RKATSubmission.Status.DECISION_RECORDED,
        RKATSubmission.Status.ALLOCATION_ADJUSTMENT,
    ]:
        messages.error(
            request,
            "RKAT belum berada pada tahap penetapan pagu."
        )
        return redirect("rkat:detail", pk=rkat.pk)

    allocation = ProgramBudgetAllocation.objects.filter(
        rkat=rkat
    ).first()

    if request.method == "POST":
        final_amount_input = request.POST.get("final_amount", "").strip()
        notes = request.POST.get("notes", "").strip()

        try:
            if not final_amount_input:
                raise ValidationError("Nominal pagu final wajib diisi.")

            try:
                final_amount = Decimal(final_amount_input)
            except InvalidOperation:
                raise ValidationError("Format nominal pagu tidak valid.")

            if not final_amount.is_finite():
                raise ValidationError("Nominal pagu tidak valid.")

            if final_amount <= 0:
                raise ValidationError("Pagu final harus lebih besar dari nol.")

            if final_amount > decision.approved_total:
                raise ValidationError(
                    "Pagu final tidak boleh melebihi persetujuan Lembaga."
                )

            set_program_budget_allocation(
                rkat_id=rkat.pk,
                final_amount=final_amount,
                actor=request.user,
                notes=notes,
                ip_address=get_client_ip(request),
            )

            messages.success(
                request,
                "Pagu final Prodi berhasil ditetapkan."
            )

            return redirect("rkat:detail", pk=rkat.pk)

        except ValidationError as e:
            messages.error(request, " ".join(e.messages))

    context = {
        "rkat": rkat,
        "decision": decision,
        "allocation": allocation,
    }

    return render(
        request,
        "rkat/set_budget.html",
        context
    )

@login_required
@require_http_methods(["GET", "POST"])
def rkat_set_budget(request, pk):
    # Hanya Fakultas yang boleh menetapkan pagu
    if get_user_role(request.user) != "FAKULTAS":
        return HttpResponseForbidden(
            "Hanya Fakultas yang dapat menetapkan pagu final."
        )

    # Menggunakan queryset sesuai lingkup Fakultas
    rkat = get_object_or_404(
        get_rkat_queryset_for_user(request.user),
        pk=pk
    )

    # RKAT harus sudah memiliki keputusan Lembaga
    decision = InstitutionDecision.objects.filter(
        rkat=rkat
    ).order_by("-decision_date", "-created_at").first()

    if not decision:
        messages.error(
            request,
            "Keputusan Lembaga belum dicatat."
        )
        return redirect("rkat:detail", pk=rkat.pk)

    if decision.decision_status not in [
        InstitutionDecision.DecisionStatus.APPROVED,
        InstitutionDecision.DecisionStatus.PARTIAL,
    ]:
        messages.error(
            request,
            "RKAT tidak dapat ditetapkan pagunya karena keputusan Lembaga ditolak."
        )
        return redirect("rkat:detail", pk=rkat.pk)

    if rkat.status not in [
        RKATSubmission.Status.DECISION_RECORDED,
        RKATSubmission.Status.ALLOCATION_ADJUSTMENT,
    ]:
        messages.error(
            request,
            "RKAT belum berada pada tahap penetapan pagu."
        )
        return redirect("rkat:detail", pk=rkat.pk)

    allocation = ProgramBudgetAllocation.objects.filter(
        rkat=rkat
    ).first()

    if request.method == "POST":
        final_amount_input = request.POST.get("final_amount", "").strip()
        notes = request.POST.get("notes", "").strip()

        try:
            if not final_amount_input:
                raise ValidationError("Nominal pagu final wajib diisi.")

            try:
                final_amount = Decimal(final_amount_input)
            except InvalidOperation:
                raise ValidationError("Format nominal pagu tidak valid.")

            if not final_amount.is_finite():
                raise ValidationError("Nominal pagu tidak valid.")

            if final_amount <= 0:
                raise ValidationError("Pagu final harus lebih besar dari nol.")

            if final_amount > decision.approved_total:
                raise ValidationError(
                    "Pagu final tidak boleh melebihi persetujuan Lembaga."
                )

            set_program_budget_allocation(
                rkat_id=rkat.pk,
                final_amount=final_amount,
                actor=request.user,
                notes=notes,
            )

            messages.success(
                request,
                "Pagu final Prodi berhasil ditetapkan."
            )

            return redirect("rkat:detail", pk=rkat.pk)

        except ValidationError as e:
            messages.error(request, " ".join(e.messages))

    context = {
        "rkat": rkat,
        "decision": decision,
        "allocation": allocation,
    }

    return render(
        request,
        "rkat/set_budget.html",
        context
    )

from .models import (
    RKATSubmission,
    ProgramBudgetAllocation,
    ActivityAllocation,
    RKATAllocationHistory,
)

from .services import (
    get_user_role,
    set_activity_allocation,
    finalize_rkat_allocation,
)

@login_required
@require_http_methods(["GET", "POST"])
def rkat_adjust_allocations(request, pk):

    if get_user_role(request.user) != "PRODI":
        return HttpResponseForbidden(
            "Hanya Prodi yang dapat mengatur alokasi kegiatan."
        )

    rkat = get_object_or_404(
        get_rkat_queryset_for_user(request.user),
        pk=pk
    )

    if rkat.status == RKATSubmission.Status.READY:
        messages.info(request, "RKAT sudah siap digunakan.")
        return redirect("rkat:detail", pk=rkat.pk)

    if rkat.status != RKATSubmission.Status.ALLOCATION_ADJUSTMENT:
        messages.warning(
            request,
            "RKAT belum memasuki tahap penyesuaian alokasi."
        )
        return redirect("rkat:detail", pk=rkat.pk)

    budget_allocation = ProgramBudgetAllocation.objects.filter(
        rkat=rkat
    ).first()

    if not budget_allocation:
        messages.error(
            request,
            "Pagu final belum ditetapkan oleh Fakultas."
        )
        return redirect("rkat:detail", pk=rkat.pk)

    if request.method == "POST":

        action = request.POST.get("action")

        try:

            if action == "save":

                activity_id = request.POST.get("activity_id")
                amount_input = request.POST.get("amount", "").strip()
                reason = request.POST.get("reason", "").strip()

                if not activity_id:
                    raise ValidationError("Kegiatan tidak valid.")

                if not amount_input:
                    raise ValidationError("Nominal alokasi wajib diisi.")

                try:
                    amount = Decimal(amount_input)
                except InvalidOperation:
                    raise ValidationError("Format nominal tidak valid.")

                if not amount.is_finite():
                    raise ValidationError("Nominal tidak valid.")

                set_activity_allocation(
                    activity_id=activity_id,
                    amount=amount,
                    actor=request.user,
                    reason=reason,
                    ip_address=get_client_ip(request),
                )

                messages.success(
                    request,
                    "Alokasi kegiatan berhasil disimpan."
                )

            elif action == "finalize":

                finalize_rkat_allocation(
                    rkat_id=rkat.pk,
                    actor=request.user,
                    ip_address=get_client_ip(request),
                )

                messages.success(
                    request,
                    "Penyesuaian alokasi berhasil diselesaikan. "
                    "RKAT sekarang siap digunakan."
                )

                return redirect("rkat:detail", pk=rkat.pk)

            else:
                raise ValidationError("Aksi tidak dikenal.")

        except ValidationError as e:
            messages.error(request, " ".join(e.messages))

        except ValueError:
            messages.error(request, "Data nominal tidak valid.")

        return redirect(
            "rkat:adjust_allocations",
            pk=rkat.pk
        )

    activities = rkat.activities.all()

    allocations = ActivityAllocation.objects.filter(
        budget_allocation=budget_allocation
    ).select_related("activity")

    allocation_map = {
        item.activity_id: item
        for item in allocations
    }

    activity_rows = []

    for activity in activities:
        allocation = allocation_map.get(activity.pk)

        histories = (
            allocation.histories.select_related("changed_by").all()
            if allocation else []
        )

        activity_rows.append({
            "activity": activity,
            "allocation": allocation,
            "histories": histories,
        })

    context = {
        "rkat": rkat,
        "budget_allocation": budget_allocation,
        "activity_rows": activity_rows,
    }

    return render(
        request,
        "rkat/adjust_allocations.html",
        context
    )