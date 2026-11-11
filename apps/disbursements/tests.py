
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.master_data.models import Faculty, StudyProgram, BudgetYear
from apps.accounts.models import UserProfile
from apps.rkat.models import RKATSubmission, RKATActivity
from apps.fund_requests.models import FundRequest, FundRequestActivity
from apps.disbursements.models import (
    InstitutionDisbursement,
    FacultyHandover,
)
from apps.disbursements.services import (
    record_institution_disbursement,
    record_faculty_handover,
    get_disbursement_summary,
)


User = get_user_model()


class DisbursementServiceTests(TestCase):

    def setUp(self):
        self.faculty_user = User.objects.create_user(
            username="faculty_test",
            password="test-password-123",
        )

        self.prodi_user = User.objects.create_user(
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
            user=self.faculty_user,
            role=UserProfile.Role.FAKULTAS,
            faculty=self.faculty,
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
            is_active=True,
        )

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

        self.fund_request = FundRequest.objects.create(
            study_program=self.study_program,
            budget_year=self.budget_year,
            request_number="REQ-2027-001",
            subject="Pengajuan Pengembangan Kurikulum",
            created_by=self.prodi_user,
            status=FundRequest.Status.APPROVED,
        )

        FundRequestActivity.objects.create(
            fund_request=self.fund_request,
            rkat_activity=self.activity,
            requested_amount=Decimal("5000000"),
            approved_amount=Decimal("5000000"),
        )

    def test_faculty_can_record_institution_disbursement(self):
        result = record_institution_disbursement(
            request_id=self.fund_request.id,
            amount="3000000",
            disbursed_at=date(2027, 2, 1),
            actor=self.faculty_user,
        )

        self.assertEqual(
            result.amount,
            Decimal("3000000"),
        )

    def test_institution_disbursement_cannot_exceed_approval(self):
        with self.assertRaises(ValidationError):
            record_institution_disbursement(
                request_id=self.fund_request.id,
                amount="6000000",
                disbursed_at=date(2027, 2, 1),
                actor=self.faculty_user,
            )

    def test_handover_cannot_exceed_received_funds(self):
        record_institution_disbursement(
            request_id=self.fund_request.id,
            amount="3000000",
            disbursed_at=date(2027, 2, 1),
            actor=self.faculty_user,
        )

        with self.assertRaises(ValidationError):
            record_faculty_handover(
                request_id=self.fund_request.id,
                amount="4000000",
                handed_over_at=date(2027, 2, 2),
                actor=self.faculty_user,
                received_by="Ketua Prodi",
            )

    def test_handover_within_received_funds(self):
        record_institution_disbursement(
            request_id=self.fund_request.id,
            amount="3000000",
            disbursed_at=date(2027, 2, 1),
            actor=self.faculty_user,
        )

        result = record_faculty_handover(
            request_id=self.fund_request.id,
            amount="2000000",
            handed_over_at=date(2027, 2, 2),
            actor=self.faculty_user,
            received_by="Ketua Prodi",
        )

        self.assertEqual(
            result.amount,
            Decimal("2000000"),
        )

    def test_disbursement_summary(self):
        record_institution_disbursement(
            request_id=self.fund_request.id,
            amount="3000000",
            disbursed_at=date(2027, 2, 1),
            actor=self.faculty_user,
        )

        record_faculty_handover(
            request_id=self.fund_request.id,
            amount="2000000",
            handed_over_at=date(2027, 2, 2),
            actor=self.faculty_user,
            received_by="Ketua Prodi",
        )

        summary = get_disbursement_summary(self.fund_request)

        self.assertEqual(
            summary["remaining_at_institution"],
            Decimal("2000000"),
        )

        self.assertEqual(
            summary["remaining_at_faculty"],
            Decimal("1000000"),
        )
