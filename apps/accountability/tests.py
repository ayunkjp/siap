
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
from apps.realizations.models import Realization
from apps.accountability.models import AccountabilityReport
from apps.accountability.services import (
    create_accountability_report,
    submit_accountability_report,
    return_accountability_for_revision,
    accept_accountability_report,
)


User = get_user_model()


class AccountabilityServiceTests(TestCase):

    def setUp(self):
        self.prodi_user = User.objects.create_user(
            username="prodi_lpj",
            password="test-password-123",
        )

        self.faculty_user = User.objects.create_user(
            username="faculty_lpj",
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

        UserProfile.objects.create(
            user=self.faculty_user,
            role=UserProfile.Role.FAKULTAS,
            faculty=self.faculty,
        )

        self.budget_year = BudgetYear.objects.create(
            name="2027",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 12, 31),
        )

        self.rkat = RKATSubmission.objects.create(
            study_program=self.study_program,
            budget_year=self.budget_year,
            submission_number="RKAT-LPJ-001",
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
            request_number="REQ-LPJ-001",
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

    def create_realization(self):
        return Realization.objects.create(
            request_activity=self.request_activity,
            realization_date=date(2027, 2, 5),
            description="Pengeluaran kegiatan",
            amount=Decimal("1000000"),
            evidence=SimpleUploadedFile(
                "bukti.pdf",
                b"Contoh bukti transaksi",
                content_type="application/pdf",
            ),
            recorded_by=self.prodi_user,
        )

    def create_report(self):
        return create_accountability_report(
            request_id=self.fund_request.id,
            actor=self.prodi_user,
        )

    def test_cannot_submit_report_without_realization(self):
        report = self.create_report()

        with self.assertRaises(ValidationError):
            submit_accountability_report(
                report_id=report.id,
                actor=self.prodi_user,
            )

    def test_prodi_can_submit_report_with_realization(self):
        self.create_realization()
        report = self.create_report()

        result = submit_accountability_report(
            report_id=report.id,
            actor=self.prodi_user,
        )

        self.assertEqual(
            result.status,
            AccountabilityReport.Status.SUBMITTED,
        )

        self.assertIsNotNone(result.submitted_at)

    def test_faculty_can_return_report_for_revision(self):
        self.create_realization()
        report = self.create_report()

        submit_accountability_report(
            report_id=report.id,
            actor=self.prodi_user,
        )

        result = return_accountability_for_revision(
            report_id=report.id,
            actor=self.faculty_user,
            notes="Mohon lengkapi bukti transaksi.",
        )

        self.assertEqual(
            result.status,
            AccountabilityReport.Status.REVISION,
        )

    def test_faculty_can_accept_report(self):
        self.create_realization()
        report = self.create_report()

        submit_accountability_report(
            report_id=report.id,
            actor=self.prodi_user,
        )

        result = accept_accountability_report(
            report_id=report.id,
            actor=self.faculty_user,
            notes="Dokumen LPJ lengkap.",
        )

        self.assertEqual(
            result.status,
            AccountabilityReport.Status.ACCEPTED,
        )

    def test_faculty_cannot_review_other_faculty_report(self):
        self.create_realization()
        report = self.create_report()

        submit_accountability_report(
            report_id=report.id,
            actor=self.prodi_user,
        )

        other_user = User.objects.create_user(
            username="other_faculty",
            password="test-password-123",
        )

        other_faculty = Faculty.objects.create(
            name="Fakultas Ekonomi",
            code="FE",
        )

        UserProfile.objects.create(
            user=other_user,
            role=UserProfile.Role.FAKULTAS,
            faculty=other_faculty,
        )

        with self.assertRaises(ValidationError):
            accept_accountability_report(
                report_id=report.id,
                actor=other_user,
            )
