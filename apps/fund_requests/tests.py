from django.shortcuts import render

# Create your views here.

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.master_data.models import Faculty, StudyProgram, BudgetYear
from apps.accounts.models import UserProfile
from apps.rkat.models import (
    RKATSubmission,
    RKATActivity,
    ProgramBudgetAllocation,
    ActivityAllocation,
)
from apps.fund_requests.models import (
    FundRequest,
    FundRequestActivity,
)
from apps.fund_requests.services import submit_fund_request


User = get_user_model()


class FundRequestServiceTests(TestCase):

    def setUp(self):
        # Pengguna Prodi
        self.user = User.objects.create_user(
            username="prodi_test",
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
            user=self.user,
            role=UserProfile.Role.PRODI,
            study_program=self.study_program,
        )

        # Tahun anggaran
        self.budget_year = BudgetYear.objects.create(
            name="2027",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 12, 31),
            is_active=True,
        )

        # RKAT yang sudah siap digunakan
        self.rkat = RKATSubmission.objects.create(
            study_program=self.study_program,
            budget_year=self.budget_year,
            submission_number="RKAT-2027-001",
            status="READY",
        )

        self.activity = RKATActivity.objects.create(
            rkat=self.rkat,
            name="Pengembangan Kurikulum",
            description="Kegiatan pengembangan kurikulum",
            proposed_amount=Decimal("5000000"),
        )

        # Pagu dan alokasi aktivitas
        self.budget_allocation = ProgramBudgetAllocation.objects.create(
            rkat=self.rkat,
            final_amount=Decimal("5000000"),
            determined_by=self.user,
        )

        ActivityAllocation.objects.create(
            budget_allocation=self.budget_allocation,
            activity=self.activity,
            amount=Decimal("5000000"),
        )

    def create_fund_request(self, number, amount, status="DRAFT"):
        fund_request = FundRequest.objects.create(
            study_program=self.study_program,
            budget_year=self.budget_year,
            request_number=number,
            subject="Pengajuan Pengembangan Kurikulum",
            created_by=self.user,
            status=status,
        )

        FundRequestActivity.objects.create(
            fund_request=fund_request,
            rkat_activity=self.activity,
            requested_amount=Decimal(str(amount)),
        )

        return fund_request

    def test_prodi_can_submit_request_within_allocation(self):
        fund_request = self.create_fund_request(
            "REQ-001",
            "3000000",
        )

        result = submit_fund_request(
            fund_request.id,
            self.user,
        )

        result.refresh_from_db()

        self.assertEqual(
            result.status,
            FundRequest.Status.SUBMITTED,
        )

        self.assertIsNotNone(result.submitted_at)

    def test_request_cannot_exceed_activity_allocation(self):
        fund_request = self.create_fund_request(
            "REQ-002",
            "6000000",
        )

        with self.assertRaises(ValidationError):
            submit_fund_request(
                fund_request.id,
                self.user,
            )

        fund_request.refresh_from_db()

        self.assertEqual(
            fund_request.status,
            FundRequest.Status.DRAFT,
        )
