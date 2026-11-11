from __future__ import annotations

import io
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import BaseCommand, call_command, CommandError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.master_data.models import Faculty, StudyProgram, BudgetYear
from apps.rkat.models import (
    RKATSubmission,
    RKATActivity,
    RKATForwarding,
    InstitutionDecision as RKATInstitutionDecision,
    ProgramBudgetAllocation,
    ActivityAllocation,
)
from apps.fund_requests.models import (
    FundRequest,
    FundRequestActivity,
    RequestCostItem,
    ApprovalHistory,
    FundRequestInstitutionDecision,
)
from apps.disbursements.models import InstitutionDisbursement, FacultyHandover
from apps.realizations.models import Realization
from apps.accountability.models import (
    AccountabilityReport,
    AccountabilityHistory,
)


class Command(BaseCommand):
    help = (
        "Mengosongkan database SQLite lokal dan membuat data dummy SIAP. "
        "Wajib dijalankan dengan --confirm."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Konfirmasi penghapusan seluruh data pada database aktif.",
        )

    def handle(self, *args, **options):
        if not options["confirm"]:
            self.stdout.write(self.style.WARNING(
                "MODE AMAN: belum ada data yang diubah.\n"
                "Periksa database aktif, buat backup, lalu jalankan:\n"
                "python manage.py reset_and_seed_siap --confirm"
            ))
            return

        db_name = str(settings.DATABASES["default"]["NAME"])
        self.stdout.write(self.style.WARNING(
            f"Database aktif: {db_name}\n"
            "PERINGATAN: perintah ini menjalankan Django flush dan "
            "menghapus seluruh data dari database aktif."
        ))

        # Backup logis sebelum flush. File backup dibuat di direktori kerja.
        backup_path = Path.cwd() / (
            f"backup_siap_before_seed_{timezone.now():%Y%m%d_%H%M%S}.json"
        )
        output = io.StringIO()
        try:
            call_command(
                "dumpdata",
                "--natural-foreign",
                "--natural-primary",
                indent=2,
                stdout=output,
                verbosity=0,
            )
            backup_path.write_text(output.getvalue(), encoding="utf-8")
        except Exception as exc:
            raise RuntimeError(
                "Backup dumpdata gagal. Database tidak jadi di-reset. "
                f"Detail: {exc}"
            ) from exc

        self.stdout.write(self.style.SUCCESS(
            f"Backup dibuat: {backup_path}"
        ))

        # Batasi command ini untuk database SQLite lokal.
        if connection.vendor != "sqlite":
            raise CommandError(
                "Command ini hanya boleh dijalankan pada database SQLite lokal. "
                f"Database aktif menggunakan vendor: {connection.vendor}"
            )

        # Pengosongan dan seluruh proses seeding berada dalam satu transaksi.
        # Jika pembuatan data gagal, perubahan database dibatalkan (rollback).
        with transaction.atomic():
            # flush menghapus data tabel tetapi mempertahankan skema dan migration.
            # Operasi SQL flush juga dapat membersihkan AuditLog yang memang
            # dilindungi dari delete() melalui ORM biasa.
            call_command("flush", interactive=False, verbosity=0)

            users = self.create_users()
            faculties, programs = self.create_master(users)
            years = self.create_budget_years()
            rkat_by_program_year = self.create_rkats(
                users, programs, years
            )
            requests = self.create_fund_requests(
                users, programs, years, rkat_by_program_year
            )
            self.create_disbursements(users, requests)
            self.create_realizations(users, requests)
            self.create_accountability(users, requests)

            expected = {
                "users": 7,
                "faculties": 2,
                "study_programs": 4,
                "budget_years": 3,
                "rkats": 12,
                "fund_requests": 13,
            }
            actual = {
                "users": get_user_model().objects.count(),
                "faculties": Faculty.objects.count(),
                "study_programs": StudyProgram.objects.count(),
                "budget_years": BudgetYear.objects.count(),
                "rkats": RKATSubmission.objects.count(),
                "fund_requests": FundRequest.objects.count(),
            }
            mismatches = {
                key: (expected[key], actual[key])
                for key in expected
                if expected[key] != actual[key]
            }
            if mismatches:
                raise CommandError(
                    f"Verifikasi jumlah data gagal (target, aktual): {mismatches}"
                )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("DATA DUMMY SIAP BERHASIL DIBUAT"))
        self.stdout.write(f"Fakultas             : {Faculty.objects.count()}")
        self.stdout.write(f"Program Studi        : {StudyProgram.objects.count()}")
        self.stdout.write(f"Tahun Anggaran       : {BudgetYear.objects.count()}")
        self.stdout.write(f"Pengguna             : {get_user_model().objects.count()}")
        self.stdout.write(f"RKAT                 : {RKATSubmission.objects.count()}")
        self.stdout.write(f"Kegiatan RKAT        : {RKATActivity.objects.count()}")
        self.stdout.write(f"Pengajuan Dana       : {FundRequest.objects.count()}")
        self.stdout.write(f"Riwayat Pengajuan    : {ApprovalHistory.objects.count()}")
        self.stdout.write(f"Pencairan Lembaga    : {InstitutionDisbursement.objects.count()}")
        self.stdout.write(f"Penyerahan Fakultas  : {FacultyHandover.objects.count()}")
        self.stdout.write(f"Realisasi            : {Realization.objects.count()}")
        self.stdout.write(f"LPJ                  : {AccountabilityReport.objects.count()}")
        self.stdout.write("")
        self.stdout.write("Login Admin: admin / 123")
        self.stdout.write("Password akun dummy lain juga: 123")
        self.stdout.write(self.style.WARNING(
            "Gunakan hanya pada database lokal. Ganti seluruh password "
            "sebelum aplikasi dipakai di lingkungan nyata."
        ))

    def create_users(self):
        User = get_user_model()
        definitions = [
            ("admin", "Admin", "SIAP", True, True),
            ("fakultas1", "Dekan", "FEB", False, False),
            ("fakultas2", "Dekan", "FST", False, False),
            ("prodi1", "Kaprodi", "Manajemen", False, False),
            ("prodi2", "Kaprodi", "Akuntansi", False, False),
            ("prodi3", "Kaprodi", "Informatika", False, False),
            ("prodi4", "Kaprodi", "Sistem Informasi", False, False),
        ]
        users = {}
        for username, first, last, is_staff, is_superuser in definitions:
            user = User.objects.create_user(
                username=username,
                password="123",
                email=f"{username}@siap.local",
                first_name=first,
                last_name=last,
                is_active=True,
                is_staff=is_staff,
                is_superuser=is_superuser,
            )
            users[username] = user
        return users

    def create_master(self, users):
        feb = Faculty.objects.create(
            code="FAK-01",
            name="Fakultas Ekonomi dan Bisnis",
            address="Kampus Utama",
        )
        fst = Faculty.objects.create(
            code="FAK-02",
            name="Fakultas Sains dan Teknologi",
            address="Kampus Utama",
        )

        program_specs = [
            ("PRD-01", "Manajemen", feb),
            ("PRD-02", "Akuntansi", feb),
            ("PRD-03", "Informatika", fst),
            ("PRD-04", "Sistem Informasi", fst),
        ]
        programs = {}
        for code, name, faculty in program_specs:
            programs[code] = StudyProgram.objects.create(
                code=code, name=name, faculty=faculty
            )

        UserProfile.objects.create(user=users["admin"], role=UserProfile.Role.ADMIN)
        UserProfile.objects.create(
            user=users["fakultas1"],
            role=UserProfile.Role.FAKULTAS,
            faculty=feb,
        )
        UserProfile.objects.create(
            user=users["fakultas2"],
            role=UserProfile.Role.FAKULTAS,
            faculty=fst,
        )
        for index, code in enumerate(programs, start=1):
            UserProfile.objects.create(
                user=users[f"prodi{index}"],
                role=UserProfile.Role.PRODI,
                study_program=programs[code],
            )
        return {"FAK-01": feb, "FAK-02": fst}, programs

    def create_budget_years(self):
        # Tahun aktif dipilih 2026; tahun lain untuk menguji histori dan rencana.
        specs = [
            ("2025", date(2025, 1, 1), date(2025, 12, 31), False),
            ("2027", date(2027, 1, 1), date(2027, 12, 31), False),
            ("2026", date(2026, 1, 1), date(2026, 12, 31), True),
        ]
        years = {}
        for name, start, end, active in specs:
            years[name] = BudgetYear.objects.create(
                name=name,
                start_date=start,
                end_date=end,
                is_active=active,
            )
        return years

    def create_rkats(self, users, programs, years):
        # Buat RKAT dalam status DRAFT terlebih dahulu karena clean()
        # melarang penambahan kegiatan baru setelah RKAT diajukan.
        status_by_year = {
            "2025": [
                RKATSubmission.Status.DRAFT,
                RKATSubmission.Status.SUBMITTED,
                RKATSubmission.Status.FORWARDED,
                RKATSubmission.Status.DECISION_RECORDED,
            ],
            "2026": [RKATSubmission.Status.READY] * 4,
            "2027": [
                RKATSubmission.Status.ALLOCATION_ADJUSTMENT,
                RKATSubmission.Status.READY,
                RKATSubmission.Status.DRAFT,
                RKATSubmission.Status.SUBMITTED,
            ],
        }
        activity_names = [
            ("Seminar dan Kuliah Tamu", "Kegiatan akademik dan peningkatan kompetensi."),
            ("Pengadaan Bahan Praktikum", "Kebutuhan bahan dan perlengkapan pembelajaran."),
            ("Penelitian dan Pengabdian", "Dukungan kegiatan penelitian dan pengabdian."),
        ]
        result = {}
        for year_name, statuses in status_by_year.items():
            for (program_code, program), status in zip(programs.items(), statuses):
                rkat = RKATSubmission.objects.create(
                    study_program=program,
                    budget_year=years[year_name],
                    submission_number=f"RKAT/{year_name}/{program_code}",
                    status=RKATSubmission.Status.DRAFT,
                    notes=f"Data simulasi RKAT {year_name} - {program.name}.",
                )
                activities = []
                for idx, (name, description) in enumerate(activity_names, start=1):
                    activity = RKATActivity(
                        rkat=rkat,
                        name=name,
                        description=description,
                        proposed_amount=Decimal(10_000_000 * idx),
                    )
                    activity.full_clean()
                    activity.save()
                    activities.append(activity)

                rkat.status = status
                if status != RKATSubmission.Status.DRAFT:
                    rkat.submitted_at = timezone.now()
                rkat.save(update_fields=["status", "submitted_at", "updated_at"])

                if status in {
                    RKATSubmission.Status.FORWARDED,
                    RKATSubmission.Status.DECISION_RECORDED,
                    RKATSubmission.Status.ALLOCATION_ADJUSTMENT,
                    RKATSubmission.Status.READY,
                }:
                    RKATForwarding.objects.create(
                        rkat=rkat,
                        forwarded_by=users["admin"],
                        reference_number=f"TERUS/{year_name}/{program_code}",
                        notes="Simulasi penerusan RKAT ke Lembaga.",
                    )

                if status in {
                    RKATSubmission.Status.DECISION_RECORDED,
                    RKATSubmission.Status.ALLOCATION_ADJUSTMENT,
                    RKATSubmission.Status.READY,
                }:
                    proposed = rkat.proposed_total
                    approved = proposed if status == RKATSubmission.Status.READY else proposed * Decimal("0.90")
                    RKATInstitutionDecision.objects.create(
                        rkat=rkat,
                        decision_status=(
                            RKATInstitutionDecision.DecisionStatus.APPROVED
                            if approved == proposed
                            else RKATInstitutionDecision.DecisionStatus.PARTIAL
                        ),
                        proposed_total=proposed,
                        approved_total=approved,
                        decision_date=date(int(year_name), 6, 15),
                        reference_number=f"SK-RKAT/{year_name}/{program_code}",
                        recorded_by=users["admin"],
                        notes="Keputusan simulasi Lembaga.",
                    )

                if status in {
                    RKATSubmission.Status.ALLOCATION_ADJUSTMENT,
                    RKATSubmission.Status.READY,
                }:
                    final_amount = (
                        rkat.proposed_total
                        if status == RKATSubmission.Status.READY
                        else rkat.proposed_total * Decimal("0.90")
                    )
                    allocation = ProgramBudgetAllocation.objects.create(
                        rkat=rkat,
                        final_amount=final_amount,
                        determined_by=users["admin"],
                        notes="Pagu simulasi.",
                    )
                    per_activity = (final_amount / Decimal("3")).quantize(Decimal("0.01"))
                    for activity in activities:
                        ActivityAllocation.objects.create(
                            budget_allocation=allocation,
                            activity=activity,
                            amount=per_activity,
                        )

                result[(program_code, year_name)] = {
                    "rkat": rkat,
                    "activities": activities,
                }
        return result

    def create_fund_requests(self, users, programs, years, rkat_map):
        # Sembilan status FundRequest yang tersedia pada model.
        status_specs = [
            (FundRequest.Status.DRAFT, "Pengajuan kegiatan seminar"),
            (FundRequest.Status.SUBMITTED, "Pengadaan bahan pembelajaran"),
            (FundRequest.Status.REVISION, "Kegiatan penelitian mahasiswa"),
            (FundRequest.Status.FACULTY_CHECKED, "Workshop peningkatan kompetensi"),
            (FundRequest.Status.FORWARDED, "Pengabdian kepada masyarakat"),
            (FundRequest.Status.APPROVED, "Seminar nasional program studi"),
            (FundRequest.Status.PARTIALLY_APPROVED, "Pengadaan perangkat praktikum"),
            (FundRequest.Status.REJECTED, "Kegiatan kunjungan industri"),
            (FundRequest.Status.CANCELLED, "Pelatihan sertifikasi mahasiswa"),
            (FundRequest.Status.APPROVED, "Konferensi ilmiah program studi"),
            (FundRequest.Status.PARTIALLY_APPROVED, "Pengembangan laboratorium pembelajaran"),
            (FundRequest.Status.APPROVED, "Program pendampingan akademik"),
            (FundRequest.Status.PARTIALLY_APPROVED, "Kegiatan pengabdian kolaboratif"),
        ]
        program_items = list(programs.items())
        requests = []
        now = timezone.now()

        for idx, (status, subject) in enumerate(status_specs, start=1):
            program_code, program = program_items[(idx - 1) % len(program_items)]
            ready_rkat = rkat_map[(program_code, "2026")]
            activity = ready_rkat["activities"][(idx - 1) % 3]
            requested = Decimal(
                4_000_000 + ((idx - 1) % 3) * 1_500_000
            )
            approved = None
            if status == FundRequest.Status.APPROVED:
                approved = requested
            elif status == FundRequest.Status.PARTIALLY_APPROVED:
                approved = (requested * Decimal("0.70")).quantize(Decimal("0.01"))

            request = FundRequest.objects.create(
                study_program=program,
                budget_year=years["2026"],
                request_number=f"PD/2026/{idx:03d}",
                subject=subject,
                letter_date=date(2026, min(idx, 12), 5),
                created_by=users[f"prodi{((idx - 1) % 4) + 1}"],
                status=status,
                submitted_at=None if status in {
                    FundRequest.Status.DRAFT,
                    FundRequest.Status.CANCELLED,
                } else now - timedelta(days=idx * 2),
                notes=f"Data dummy untuk pengujian status {status}.",
            )

            request_activity = FundRequestActivity(
                fund_request=request,
                rkat_activity=activity,
                requested_amount=requested,
                approved_amount=approved,
                notes="Aktivitas berasal dari RKAT 2026 berstatus READY.",
            )
            request_activity.full_clean()
            request_activity.save()

            RequestCostItem.objects.create(
                request_activity=request_activity,
                description="Honorarium narasumber / tenaga ahli",
                quantity=Decimal("1"),
                unit="paket",
                unit_price=requested * Decimal("0.40"),
            )
            RequestCostItem.objects.create(
                request_activity=request_activity,
                description="Konsumsi dan perlengkapan kegiatan",
                quantity=Decimal("1"),
                unit="paket",
                unit_price=requested * Decimal("0.60"),
            )

            self.create_request_history(request, status, users)

            if status in {
                FundRequest.Status.APPROVED,
                FundRequest.Status.PARTIALLY_APPROVED,
                FundRequest.Status.REJECTED,
            }:
                decision_status = {
                    FundRequest.Status.APPROVED: FundRequestInstitutionDecision.DecisionStatus.APPROVED,
                    FundRequest.Status.PARTIALLY_APPROVED: FundRequestInstitutionDecision.DecisionStatus.PARTIAL,
                    FundRequest.Status.REJECTED: FundRequestInstitutionDecision.DecisionStatus.REJECTED,
                }[status]
                FundRequestInstitutionDecision.objects.create(
                    fund_request=request,
                    decision_status=decision_status,
                    decision_date=date(2026, 8, 10),
                    reference_number=f"SK-PD/2026/{idx:03d}",
                    recorded_by=users["admin"],
                    notes="Keputusan simulasi Lembaga.",
                )

            requests.append(request)

        return requests

    def create_request_history(self, request, status, users):
        sequence = {
            FundRequest.Status.DRAFT: [],
            FundRequest.Status.SUBMITTED: [
                (ApprovalHistory.Action.SUBMIT, "Diajukan ke Fakultas."),
            ],
            FundRequest.Status.REVISION: [
                (ApprovalHistory.Action.SUBMIT, "Pengajuan dikirim."),
                (ApprovalHistory.Action.REVISION, "Dikembalikan untuk revisi."),
            ],
            FundRequest.Status.FACULTY_CHECKED: [
                (ApprovalHistory.Action.SUBMIT, "Pengajuan dikirim."),
                (ApprovalHistory.Action.CHECK, "Diperiksa Fakultas."),
            ],
            FundRequest.Status.FORWARDED: [
                (ApprovalHistory.Action.SUBMIT, "Pengajuan dikirim."),
                (ApprovalHistory.Action.CHECK, "Diperiksa Fakultas."),
                (ApprovalHistory.Action.FORWARD, "Diteruskan ke Lembaga."),
            ],
            FundRequest.Status.APPROVED: [
                (ApprovalHistory.Action.SUBMIT, "Pengajuan dikirim."),
                (ApprovalHistory.Action.CHECK, "Diperiksa Fakultas."),
                (ApprovalHistory.Action.FORWARD, "Diteruskan ke Lembaga."),
                (ApprovalHistory.Action.RECORD_APPROVAL, "Disetujui Lembaga."),
            ],
            FundRequest.Status.PARTIALLY_APPROVED: [
                (ApprovalHistory.Action.SUBMIT, "Pengajuan dikirim."),
                (ApprovalHistory.Action.CHECK, "Diperiksa Fakultas."),
                (ApprovalHistory.Action.FORWARD, "Diteruskan ke Lembaga."),
                (ApprovalHistory.Action.RECORD_APPROVAL, "Disetujui sebagian."),
            ],
            FundRequest.Status.REJECTED: [
                (ApprovalHistory.Action.SUBMIT, "Pengajuan dikirim."),
                (ApprovalHistory.Action.CHECK, "Diperiksa Fakultas."),
                (ApprovalHistory.Action.FORWARD, "Diteruskan ke Lembaga."),
                (ApprovalHistory.Action.REJECT, "Pengajuan ditolak."),
            ],
            FundRequest.Status.CANCELLED: [
                (ApprovalHistory.Action.CANCEL, "Pengajuan dibatalkan."),
            ],
        }
        program = request.study_program
        faculty_username = "fakultas1" if program.faculty.code == "FAK-01" else "fakultas2"
        for action, notes in sequence[status]:
            actor = users[faculty_username] if action in {
                ApprovalHistory.Action.CHECK,
                ApprovalHistory.Action.REVISION,
                ApprovalHistory.Action.FORWARD,
            } else users["admin"] if action in {
                ApprovalHistory.Action.RECORD_APPROVAL,
                ApprovalHistory.Action.REJECT,
            } else request.created_by
            ApprovalHistory.objects.create(
                fund_request=request,
                action=action,
                actor=actor,
                notes=notes,
            )

    def create_disbursements(self, users, requests):
        for request in requests:
            if request.status not in {
                FundRequest.Status.APPROVED,
                FundRequest.Status.PARTIALLY_APPROVED,
            }:
                continue
            approved = request.approved_total
            InstitutionDisbursement.objects.create(
                fund_request=request,
                amount=approved,
                disbursed_at=date(2026, 9, 1),
                reference_number=f"CAIR/{request.request_number}",
                recorded_by=users["admin"],
                notes="Pencairan dummy dari Lembaga ke Fakultas.",
            )
            FacultyHandover.objects.create(
                fund_request=request,
                amount=approved,
                handed_over_at=date(2026, 9, 3),
                reference_number=f"SERAH/{request.request_number}",
                recorded_by=(
                    users["fakultas1"]
                    if request.study_program.faculty.code == "FAK-01"
                    else users["fakultas2"]
                ),
                received_by=f"Ketua Program Studi {request.study_program.name}",
                notes="Penyerahan dummy dari Fakultas ke Prodi.",
            )

    def create_realizations(self, users, requests):
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF"
        )
        evidence_path = default_storage.save(
            "realizations/evidence/dummy-bukti-realisasi.pdf",
            ContentFile(pdf_bytes),
        )
        for request in requests:
            if request.status not in {
                FundRequest.Status.APPROVED,
                FundRequest.Status.PARTIALLY_APPROVED,
            }:
                continue
            request_activity = request.activities.first()
            amount = request_activity.approved_amount or Decimal("0")
            realization_amount = (amount * Decimal("0.45")).quantize(Decimal("0.01"))
            Realization.objects.create(
                request_activity=request_activity,
                realization_date=date(2026, 9, 15),
                description="Realisasi tahap pertama kegiatan.",
                amount=realization_amount,
                evidence=evidence_path,
                recorded_by=request.created_by,
                notes="Bukti PDF dummy untuk pengujian lokal, bukan bukti transaksi nyata.",
            )
            Realization.objects.create(
                request_activity=request_activity,
                realization_date=date(2026, 9, 20),
                description="Realisasi tahap kedua kegiatan.",
                amount=realization_amount,
                evidence=evidence_path,
                recorded_by=request.created_by,
                notes="Bukti PDF dummy untuk pengujian lokal, bukan bukti transaksi nyata.",
            )

    def create_accountability(self, users, requests):
        eligible = [
            request for request in requests
            if request.status in {
                FundRequest.Status.APPROVED,
                FundRequest.Status.PARTIALLY_APPROVED,
            }
        ]
        report_statuses = [
            AccountabilityReport.Status.DRAFT,
            AccountabilityReport.Status.SUBMITTED,
            AccountabilityReport.Status.REVISION,
            AccountabilityReport.Status.ACCEPTED,
        ]
        for request, status in zip(eligible, report_statuses):
            faculty_username = (
                "fakultas1"
                if request.study_program.faculty.code == "FAK-01"
                else "fakultas2"
            )
            report = AccountabilityReport.objects.create(
                fund_request=request,
                status=status,
                prepared_by=request.created_by,
                submitted_at=(
                    timezone.now() - timedelta(days=3)
                    if status in {
                        AccountabilityReport.Status.SUBMITTED,
                        AccountabilityReport.Status.REVISION,
                        AccountabilityReport.Status.ACCEPTED,
                    }
                    else None
                ),
                reviewed_by=(
                    users[faculty_username]
                    if status in {
                        AccountabilityReport.Status.REVISION,
                        AccountabilityReport.Status.ACCEPTED,
                    }
                    else None
                ),
                reviewed_at=(
                    timezone.now() - timedelta(days=1)
                    if status in {
                        AccountabilityReport.Status.REVISION,
                        AccountabilityReport.Status.ACCEPTED,
                    }
                    else None
                ),
                notes="LPJ dummy untuk pengujian alur pertanggungjawaban.",
                revision_notes=(
                    "Mohon lengkapi bukti pengeluaran."
                    if status == AccountabilityReport.Status.REVISION
                    else ""
                ),
            )
            if status in {
                AccountabilityReport.Status.SUBMITTED,
                AccountabilityReport.Status.REVISION,
                AccountabilityReport.Status.ACCEPTED,
            }:
                AccountabilityHistory.objects.create(
                    report=report,
                    action=AccountabilityHistory.Action.SUBMIT,
                    actor=request.created_by,
                    notes="LPJ dikirim ke Fakultas.",
                )
            if status == AccountabilityReport.Status.REVISION:
                AccountabilityHistory.objects.create(
                    report=report,
                    action=AccountabilityHistory.Action.REVISION,
                    actor=users[faculty_username],
                    notes="LPJ dikembalikan untuk revisi.",
                )
            if status == AccountabilityReport.Status.ACCEPTED:
                AccountabilityHistory.objects.create(
                    report=report,
                    action=AccountabilityHistory.Action.ACCEPT,
                    actor=users[faculty_username],
                    notes="LPJ diterima Fakultas.",
                )
