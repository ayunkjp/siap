
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.master_data.models import Faculty, StudyProgram, BudgetYear
from apps.accounts.models import UserProfile
from apps.rkat.models import RKATSubmission, RKATActivity
from apps.fund_requests.models import FundRequest, FundRequestActivity
from apps.disbursements.models import FacultyHandover
from apps.realizations.models import Realization
from apps.realizations.services import record_realization


User = get_user_model()


class RealizationServiceTests(TestCase):

    def setUp(self):
        self.prodi_user = User.objects.create_user(
            username="prodi_realization",
            password="test-password-123",
        )

        self.faculty = Faculty.objects.create(
            name="Fakultas Teknik",
            code="FT",
        )

        self.study_program = StudyProgram.objects.create(
            faculty=self.faculty,
            name="Teknik Informatika",
            code="TI",
        )

        UserProfile.objects.create(
            user=self.prodi_user,
            role=UserProfile.Role.PRODI,
            study_program=self.study_program,
        )

        self.budget_year = BudgetYear.objects.create(
            name="2027",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 12, 31),
        )

        self.rkat = RKATSubmission.objects.create(
            study_program=self.study_program,
            budget_year=self.budget_year,
            submission_number="RKAT-REAL-001",
            status="READY",
        )

        self.activity = RKATActivity.objects.create(
            rkat=self.rkat,
            name="Pengembangan Kurikulum",
            description="Kegiatan pengembangan kurikulum",
            proposed_amount=Decimal("5000000"),
        )

        self.fund_request = FundRequest.objects.create(
            study_program=self.study_program,
            budget_year=self.budget_year,
            request_number="REQ-REAL-001",
            subject="Pengajuan Pengembangan Kurikulum",
            created_by=self.prodi_user,
            status=FundRequest.Status.APPROVED,
        )

        self.request_activity = FundRequestActivity.objects.create(
            fund_request=self.fund_request,
            rkat_activity=self.activity,
            requested_amount=Decimal("5000000"),
            approved_amount=Decimal("5000000"),
        )

        # Dana yang benar-benar sudah diterima Prodi.
        self.handover = FacultyHandover.objects.create(
            fund_request=self.fund_request,
            amount=Decimal("3000000"),
            handed_over_at=date(2027, 2, 1),
            recorded_by=self.prodi_user,
            received_by="Ketua Prodi",
        )

    def make_evidence(self):
        return SimpleUploadedFile(
            "bukti.pdf",
            b"Contoh bukti transaksi untuk pengujian",
            content_type="application/pdf",
        )

    def create_realization(self, amount):
        return record_realization(
            activity_id=self.request_activity.id,
            realization_date=date(2027, 2, 5),
            description="Pengeluaran kegiatan",
            amount=amount,
            evidence=self.make_evidence(),
            actor=self.prodi_user,
        )

    def test_realization_within_limits(self):
        result = self.create_realization("1000000")

        self.assertEqual(
            result.amount,
            Decimal("1000000"),
        )

    def test_realization_cannot_exceed_approved_activity_amount(self):
        with self.assertRaises(ValidationError):
            self.create_realization("6000000")

    def test_realization_cannot_exceed_handover_amount(self):
        self.create_realization("2000000")

        with self.assertRaises(ValidationError):
            self.create_realization("1500000")

    def test_evidence_is_required(self):
        with self.assertRaises(ValidationError):
            record_realization(
                activity_id=self.request_activity.id,
                realization_date=date(2027, 2, 5),
                description="Pengeluaran tanpa bukti",
                amount="500000",
                evidence=None,
                actor=self.prodi_user,
            )
