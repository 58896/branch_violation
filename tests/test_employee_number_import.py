# -*- coding: utf-8 -*-
import base64
import io

import openpyxl

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MmViolationCommon


@tagged("post_install", "-at_install")
class TestEmployeeNumberImport(MmViolationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Employee = cls.env["hr.employee"]
        cls.emp_exact = Employee.create({"name": "محمد دياب السيد دياب"})
        cls.emp_variant = Employee.create({"name": "أحمد عبدالله العسيري"})
        cls.emp_twin_a = Employee.create({"name": "سيد مصطفى"})
        cls.emp_twin_b = Employee.create({"name": "سيد مصطفى"})
        cls.emp_manual = Employee.create({"name": "Truncated In Jisr Full Name"})

    def _file(self, rows, header=("الرقم الوظيفي", "اسم الموظف", "المسمى الوظيفي", "الفرع")):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["Jisr export"])  # title row above the header
        sheet.append(list(header))
        for row in rows:
            sheet.append(list(row))
        buf = io.BytesIO()
        book.save(buf)
        return base64.b64encode(buf.getvalue())

    def _wizard(self, rows, **vals):
        # English: some assertions below look for message text
        return self.env["mm.employee.number.import"].with_user(self.manager).with_context(
            lang="en_US").create(
            dict({"file": self._file(rows), "filename": "jisr.xlsx"}, **vals))

    def _line(self, wizard, number):
        return wizard.line_ids.filtered(lambda l: l.file_employee_number == number)

    def test_match_review_apply(self):
        wizard = self._wizard([
            (208, "محمد دياب السيد دياب", "كاشير", "[MMV-TEST] Test Branch MMV"),
            ("647", "احمد عبد الله العسيرى", "بائع", "MMV-TEST"),   # spelling variant
            ("510", "سيد مصطفى", "محمص", ""),                       # two employees
            ("116", "مد ابراهيم", "كاشير", ""),                     # not found
            ("", "الإجمالي", "", ""),                               # total row: ignored
        ])
        wizard.action_read_file()
        self.assertEqual(wizard.state, "review")
        self.assertEqual(len(wizard.line_ids), 4)
        self.assertEqual(self._line(wizard, "208").employee_id, self.emp_exact)
        self.assertEqual(self._line(wizard, "208").branch_id, self.branch)
        self.assertEqual(self._line(wizard, "647").employee_id, self.emp_variant)
        twins = self._line(wizard, "510")
        self.assertEqual(twins.match_status, "ambiguous")
        self.assertEqual(twins.candidate_ids, self.emp_twin_a | self.emp_twin_b)
        missing = self._line(wizard, "116")
        self.assertEqual(missing.match_status, "unmatched")
        self.assertEqual(wizard.attention_count, 2)
        self.assertTrue(wizard.report_file, "report of rows needing attention")

        # review step: fix one by hand, skip the other
        twins.employee_id = self.emp_twin_b
        self.assertEqual(twins.match_status, "manual")
        missing.skip = True
        wizard.action_apply()
        self.assertEqual(wizard.state, "done")
        self.assertEqual(self.emp_exact.mm_employee_number, "208")
        self.assertEqual(self.emp_variant.mm_employee_number, "647")
        self.assertEqual(self.emp_twin_b.mm_employee_number, "510")
        self.assertFalse(self.emp_twin_a.mm_employee_number)
        self.assertFalse(self.emp_exact.job_id, "job not touched when the option is off")

    def test_rerun_matches_by_number(self):
        self.emp_manual.mm_employee_number = "900"
        wizard = self._wizard([("900", "Name spelled differently in Jisr", "", "")])
        wizard.action_read_file()
        self.assertEqual(self._line(wizard, "900").employee_id, self.emp_manual)

    def test_conflicts_block_apply(self):
        self.emp_manual.mm_employee_number = "208"
        wizard = self._wizard([
            ("208", "محمد دياب السيد دياب", "", ""),
            ("300", "محمد دياب السيد دياب", "", ""),
        ])
        wizard.action_read_file()
        line = self._line(wizard, "208")
        # an existing number wins over the name
        self.assertEqual(line.employee_id, self.emp_manual)
        self.assertFalse(line.issue)
        # picking someone else for a number another employee holds is flagged,
        # and so is the same employee on two rows
        line.employee_id = self.emp_exact
        self.assertIn(self.emp_manual.name, line.issue)
        self.assertIn("another row", line.issue)
        with self.assertRaises(UserError):
            wizard.action_apply()
        self._line(wizard, "300").skip = True
        self.assertNotIn("another row", line.issue)
        self.assertIn(self.emp_manual.name, line.issue)

    def test_update_job_and_branch(self):
        wizard = self._wizard(
            [("208", "محمد دياب السيد دياب", "MMV Roaster", "[MMV-TEST] Test Branch MMV")],
            update_job_branch=True)
        wizard.action_read_file()
        wizard.action_apply()
        self.assertEqual(self.emp_exact.job_id.name, "MMV Roaster")
        if "branch_id" in self.env["hr.employee"]._fields:
            self.assertEqual(self.emp_exact.branch_id, self.branch)
        # second import reuses the created job instead of duplicating it
        wizard2 = self._wizard(
            [("647", "أحمد عبدالله العسيري", "mmv roaster", "")], update_job_branch=True)
        wizard2.action_read_file()
        wizard2.action_apply()
        self.assertEqual(self.emp_variant.job_id, self.emp_exact.job_id)

    def test_unknown_branch_blocks_only_when_updating(self):
        rows = [("208", "محمد دياب السيد دياب", "", "Nowhere Branch")]
        wizard = self._wizard(rows)
        wizard.action_read_file()
        self.assertFalse(self._line(wizard, "208").issue)
        wizard = self._wizard(rows, update_job_branch=True)
        wizard.action_read_file()
        self.assertTrue(self._line(wizard, "208").issue)

    def test_bad_files(self):
        wizard = self._wizard([("1", "x")])
        wizard.file = self._file([("1", "x")], header=("foo", "bar"))
        with self.assertRaises(UserError):
            wizard.action_read_file()
        wizard.file = base64.b64encode(b"not an excel file")
        with self.assertRaises(UserError):
            wizard.action_read_file()

    def test_officer_cannot_import(self):
        wizard = self._wizard([("208", "محمد دياب السيد دياب", "", "")])
        with self.assertRaises(UserError):
            wizard.with_user(self.officer).action_read_file()
