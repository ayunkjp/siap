from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_http_methods
from datetime import date
from pathlib import Path
from django.core.validators import validate_image_file_extension
from django.conf import settings
from apps.accounts.audit import get_client_ip
from .models import FundRequestInstitutionDecision
from .services import record_fund_request_decision
from apps.accounts.audit import get_client_ip


from apps.accounts.models import UserProfile
from .services import (
    get_user_role,
    create_fund_request,
    save_fund_request_activity,
    delete_fund_request_activity,
    save_request_cost_item,
    delete_request_cost_item,
    submit_fund_request,
    check_fund_request,
    return_fund_request_for_revision,
    forward_fund_request,
    get_fund_usage_status,
)
from django.db.models import Sum
from apps.rkat.models import RKATActivity
from .models import FundRequest, FundRequestActivity, RequestCostItem

def get_fund_request_queryset_for_user(user):
    """
    Membatasi data pengajuan sesuai hak akses pengguna.
    """

    queryset = FundRequest.objects.select_related(
        "study_program",
        "study_program__faculty",
        "budget_year",
        "created_by",
    )

    role = get_user_role(user)

    if role == UserProfile.Role.PRODI:
        return queryset.filter(
            study_program_id=user.profile.study_program_id
        )

    if role == UserProfile.Role.FAKULTAS:
        return queryset.filter(
            study_program__faculty_id=user.profile.faculty_id
        )

    if role == UserProfile.Role.ADMIN:
        return queryset

    return queryset.none()



@login_required
def fund_request_index(request):

    role = get_user_role(request.user)

    if role not in [
        UserProfile.Role.ADMIN,
        UserProfile.Role.PRODI,
        UserProfile.Role.FAKULTAS,
    ]:
        return HttpResponseForbidden("Akses tidak diizinkan.")

    fund_requests = get_fund_request_queryset_for_user(
        request.user
    )

    # Status penggunaan hanya ditampilkan kepada Prodi.
    if role == UserProfile.Role.PRODI:
        for fund_request in fund_requests:
            fund_request.usage_status = get_fund_usage_status(
                fund_request
            )

    context = {
        "fund_requests": fund_requests,
        "user_role": role,
    }

    return render(
        request,
        "fund_requests/index.html",
        context,
    )


@login_required
@require_http_methods(["GET", "POST"])
def fund_request_create(request):

    if get_user_role(request.user) != UserProfile.Role.PRODI:
        return HttpResponseForbidden(
            "Hanya Prodi yang dapat membuat pengajuan dana."
        )

    if request.method == "POST":

        subject = request.POST.get("subject", "").strip()
        letter_date = request.POST.get("letter_date") or None
        notes = request.POST.get("notes", "").strip()

        try:
            fund_request = create_fund_request(
                actor=request.user,
                subject=subject,
                letter_date=letter_date,
                notes=notes,
                ip_address=get_client_ip(request),
            )

            messages.success(
                request,
                "Draft pengajuan dana berhasil dibuat."
            )

            return redirect(
                "fund_requests:detail",
                pk=fund_request.pk,
            )

        except ValidationError as e:
            messages.error(request, " ".join(e.messages))

    return render(
        request,
        "fund_requests/create.html",
    )



@login_required
def fund_request_detail(request, pk):

    role = get_user_role(request.user)

    if role not in [
        UserProfile.Role.ADMIN,
        UserProfile.Role.PRODI,
        UserProfile.Role.FAKULTAS,
    ]:
        return HttpResponseForbidden("Akses tidak diizinkan.")

    fund_request = get_object_or_404(
        get_fund_request_queryset_for_user(request.user),
        pk=pk,
    )

    context = {
        "fund_request": fund_request,
        "activities": fund_request.activities.select_related(
            "rkat_activity",
        ).prefetch_related(
            "cost_items",
        ),
        "user_role": role,
        "can_edit": (
            role == UserProfile.Role.PRODI
            and fund_request.status in [
                FundRequest.Status.DRAFT,
                FundRequest.Status.REVISION,
            ]
        ),
    }
    

    return render(
        request,
        "fund_requests/detail.html",
        context,
    )

@login_required
@require_http_methods(["GET", "POST"])
def fund_request_activity_add(request, pk):
    if get_user_role(request.user) != UserProfile.Role.PRODI:
        return HttpResponseForbidden("Akses hanya untuk Prodi.")

    fund_request = get_object_or_404(
        get_fund_request_queryset_for_user(request.user),
        pk=pk,
    )

    if fund_request.status not in [
        FundRequest.Status.DRAFT,
        FundRequest.Status.REVISION,
    ]:
        messages.error(request, "Pengajuan tidak dapat diubah.")
        return redirect("fund_requests:detail", pk=pk)

    selected_activity_ids = fund_request.activities.values_list(
        "rkat_activity_id",
        flat=True,
    )

    available_activities = (
        RKATActivity.objects
        .filter(
            rkat__study_program_id=fund_request.study_program_id,
            rkat__budget_year_id=fund_request.budget_year_id,
            rkat__status="READY",
            allocation__isnull=False,
        )
        .exclude(pk__in=selected_activity_ids)
        .select_related("rkat", "allocation")
        .order_by("name")
    )

    if request.method == "POST":
        activity_id = request.POST.get("activity_id")
        requested_amount = request.POST.get("requested_amount")
        notes = request.POST.get("notes", "").strip()

        try:
            save_fund_request_activity(
                request_id=fund_request.pk,
                activity_id=activity_id,
                requested_amount=requested_amount,
                actor=request.user,
                notes=notes,
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Kegiatan berhasil ditambahkan.")
            return redirect("fund_requests:detail", pk=pk)

        except ValidationError as e:
            messages.error(request, " ".join(e.messages))

    return render(
        request,
        "fund_requests/activity_add.html",
        {
            "fund_request": fund_request,
            "available_activities": available_activities,
        },
    )


@login_required
@require_http_methods(["POST"])
def fund_request_activity_delete(request, activity_id):
    if get_user_role(request.user) != UserProfile.Role.PRODI:
        return HttpResponseForbidden("Akses hanya untuk Prodi.")

    try:
        activity = get_object_or_404(
            FundRequestActivity.objects.select_related("fund_request"),
            pk=activity_id,
            fund_request__study_program_id=request.user.profile.study_program_id,
        )

        request_id = activity.fund_request_id

        delete_fund_request_activity(
            activity_id=activity_id,
            actor=request.user,
            ip_address=get_client_ip(request),
        )

        messages.success(request, "Kegiatan berhasil dihapus.")
        return redirect("fund_requests:detail", pk=request_id)

    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("fund_requests:index")


@login_required
@require_http_methods(["GET", "POST"])
def fund_request_cost_item_save(request, activity_id, cost_item_id=None):
    if get_user_role(request.user) != UserProfile.Role.PRODI:
        return HttpResponseForbidden("Akses hanya untuk Prodi.")

    request_activity = get_object_or_404(
        FundRequestActivity.objects.select_related(
            "fund_request",
            "rkat_activity",
        ),
        pk=activity_id,
        fund_request__study_program_id=request.user.profile.study_program_id,
    )

    fund_request = request_activity.fund_request

    if fund_request.status not in [
        FundRequest.Status.DRAFT,
        FundRequest.Status.REVISION,
    ]:
        messages.error(request, "Rincian biaya tidak dapat diubah.")
        return redirect("fund_requests:detail", pk=fund_request.pk)

    cost_item = None

    if cost_item_id is not None:
        cost_item = get_object_or_404(
            RequestCostItem,
            pk=cost_item_id,
            request_activity=request_activity,
        )

    if request.method == "POST":
        try:
            save_request_cost_item(
                request_activity_id=request_activity.pk,
                description=request.POST.get("description", ""),
                quantity=request.POST.get("quantity"),
                unit=request.POST.get("unit", ""),
                unit_price=request.POST.get("unit_price"),
                actor=request.user,
                cost_item_id=cost_item.pk if cost_item else None,
                ip_address=get_client_ip(request),
            )

            messages.success(request, "Rincian biaya berhasil disimpan.")
            return redirect("fund_requests:detail", pk=fund_request.pk)

        except ValidationError as e:
            messages.error(request, " ".join(e.messages))

    return render(
        request,
        "fund_requests/cost_item_form.html",
        {
            "fund_request": fund_request,
            "request_activity": request_activity,
            "cost_item": cost_item,
        },
    )

@login_required
@require_http_methods(["POST"])
def fund_request_cost_item_delete(request, cost_item_id):
    if get_user_role(request.user) != UserProfile.Role.PRODI:
        return HttpResponseForbidden("Akses hanya untuk Prodi.")

    cost_item = get_object_or_404(
        RequestCostItem.objects.select_related(
            "request_activity__fund_request"
        ),
        pk=cost_item_id,
        request_activity__fund_request__study_program_id=(
            request.user.profile.study_program_id
        ),
    )

    request_id = cost_item.request_activity.fund_request_id

    try:
        delete_request_cost_item(
            cost_item_id=cost_item_id,
            actor=request.user,
            ip_address=get_client_ip(request),
        )
        messages.success(request, "Rincian biaya berhasil dihapus.")

    except ValidationError as e:
        messages.error(request, " ".join(e.messages))

    return redirect("fund_requests:detail", pk=request_id)

@login_required
@require_http_methods(["POST"])
def fund_request_submit(request, pk):
    if get_user_role(request.user) != UserProfile.Role.PRODI:
        return HttpResponseForbidden(
            "Hanya Prodi yang dapat mengajukan pengajuan dana."
        )

    fund_request = get_object_or_404(
        get_fund_request_queryset_for_user(request.user),
        pk=pk,
    )

    try:
        submit_fund_request(
            request_id=fund_request.pk,
            actor=request.user,
            ip_address=get_client_ip(request),
        )

        messages.success(
            request,
            "Pengajuan dana berhasil dikirim ke Fakultas."
        )

    except ValidationError as e:
        messages.error(request, " ".join(e.messages))

    return redirect(
        "fund_requests:detail",
        pk=fund_request.pk,
    )

@login_required
@require_http_methods(["POST"])
def fund_request_check(request, pk):
    if get_user_role(request.user) != UserProfile.Role.FAKULTAS:
        return HttpResponseForbidden(
            "Hanya Fakultas yang dapat memeriksa pengajuan."
        )

    fund_request = get_object_or_404(
        get_fund_request_queryset_for_user(request.user),
        pk=pk,
    )

    notes = request.POST.get("notes", "").strip()

    try:
        check_fund_request(
            request_id=fund_request.pk,
            actor=request.user,
            notes=notes,
            ip_address=get_client_ip(request),
        )

        messages.success(
            request,
            "Pengajuan berhasil ditandai sudah diperiksa."
        )

    except ValidationError as e:
        messages.error(request, " ".join(e.messages))

    return redirect(
        "fund_requests:detail",
        pk=fund_request.pk,
    )


@login_required
@require_http_methods(["POST"])
def fund_request_revision(request, pk):
    if get_user_role(request.user) != UserProfile.Role.FAKULTAS:
        return HttpResponseForbidden(
            "Hanya Fakultas yang dapat mengembalikan pengajuan."
        )

    fund_request = get_object_or_404(
        get_fund_request_queryset_for_user(request.user),
        pk=pk,
    )

    notes = request.POST.get("notes", "").strip()

    try:
        return_fund_request_for_revision(
            request_id=fund_request.pk,
            actor=request.user,
            notes=notes,
            ip_address=get_client_ip(request),
        )

        messages.success(
            request,
            "Pengajuan berhasil dikembalikan kepada Prodi untuk revisi."
        )

    except ValidationError as e:
        messages.error(request, " ".join(e.messages))

    return redirect(
        "fund_requests:detail",
        pk=fund_request.pk,
    )


@login_required
@require_http_methods(["POST"])
def fund_request_forward(request, pk):
    if get_user_role(request.user) != UserProfile.Role.FAKULTAS:
        return HttpResponseForbidden(
            "Hanya Fakultas yang dapat meneruskan pengajuan."
        )

    fund_request = get_object_or_404(
        get_fund_request_queryset_for_user(request.user),
        pk=pk,
    )

    notes = request.POST.get("notes", "").strip()

    try:
        forward_fund_request(
            request_id=fund_request.pk,
            actor=request.user,
            notes=notes,
            ip_address=get_client_ip(request),
        )

        messages.success(
            request,
            "Pengajuan berhasil diteruskan ke Lembaga."
        )

    except ValidationError as e:
        messages.error(request, " ".join(e.messages))

    return redirect(
        "fund_requests:detail",
        pk=fund_request.pk,
    )

@login_required
@require_http_methods(["GET", "POST"])
def fund_request_record_decision(request, pk):
    if get_user_role(request.user) != UserProfile.Role.FAKULTAS:
        return HttpResponseForbidden(
            "Hanya Fakultas yang dapat mencatat keputusan Lembaga."
        )

    fund_request = get_object_or_404(
        get_fund_request_queryset_for_user(request.user),
        pk=pk,
    )

    if fund_request.status != FundRequest.Status.FORWARDED:
        messages.error(
            request,
            "Keputusan hanya dapat dicatat setelah pengajuan diteruskan ke Lembaga.",
        )
        return redirect("fund_requests:detail", pk=pk)

    if FundRequestInstitutionDecision.objects.filter(
        fund_request=fund_request
    ).exists():
        messages.error(
            request,
            "Keputusan Lembaga untuk pengajuan ini sudah pernah dicatat.",
        )
        return redirect("fund_requests:detail", pk=pk)

    activities = list(
        fund_request.activities.select_related(
            "rkat_activity"
        ).order_by("pk")
    )

    if request.method == "POST":
        decision_status = request.POST.get("decision_status", "").strip()
        decision_date_raw = request.POST.get("decision_date", "").strip()
        reference_number = request.POST.get("reference_number", "").strip()
        notes = request.POST.get("notes", "").strip()
        document = request.FILES.get("document")

        # Ambil nominal persetujuan untuk setiap kegiatan
        approved_amounts = {
            str(item.pk): request.POST.get(
                f"approved_amount_{item.pk}", ""
            )
            for item in activities
        }

        try:
            if not decision_date_raw:
                raise ValidationError("Tanggal keputusan wajib diisi.")

            try:
                decision_date = date.fromisoformat(decision_date_raw)
            except ValueError:
                raise ValidationError("Format tanggal keputusan tidak valid.")

            if document:
                allowed_extensions = [".pdf", ".doc", ".docx"]
                extension = Path(document.name).suffix.lower()

                if extension not in allowed_extensions:
                    raise ValidationError(
                        "Dokumen keputusan harus berformat PDF, DOC, atau DOCX."
                    )

                if document.size > 5 * 1024 * 1024:
                    raise ValidationError(
                        "Ukuran dokumen maksimal 5 MB."
                    )

            record_fund_request_decision(
                request_id=fund_request.pk,
                approved_amounts=approved_amounts,
                decision_status=decision_status,
                actor=request.user,
                decision_date=decision_date,
                reference_number=reference_number,
                document=document,
                notes=notes,
                ip_address=get_client_ip(request),
            )

            messages.success(
                request,
                "Keputusan Lembaga berhasil dicatat."
            )

            return redirect(
                "fund_requests:detail",
                pk=fund_request.pk,
            )

        except ValidationError as e:
            messages.error(request, " ".join(e.messages))

    return render(
        request,
        "fund_requests/record_decision.html",
        {
            "fund_request": fund_request,
            "activities": activities,
            "decision_choices": FundRequestInstitutionDecision.DecisionStatus.choices,
        },
    )