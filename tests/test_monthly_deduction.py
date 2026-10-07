# -*- coding: utf-8 -*-
import base64
import io
from datetime import date

import openpyxl

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MmViolationCommon


@tagged("post_install", "-at_install")
class TestMonthlyDeduction(MmViolationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee.mm_employee_number = "MMV-1"
        cls.employee2 = cls.env["hr.employee"].create({
            "name": "MMV Seller", "mm_employee_number": "MMV-2"})
        cls.Period = cls.env["mm.violation.deduction.period"]
        # no other open months in the database may take our carried violations
        cls.Period.search([("state", "=", "draft")]).unlink()

    def _approved(self, day, employee=None, vtype=None, points=None):
        vals = {"date": day, "violation_type_id": (vtype or self.type_5).id}
        if employee:
            vals["employee_id"] = employee.id
        if points is not None:
            violation = self._create(user=self.manager, points=points, **vals)
            violation.with_user(self.manager).action_submit()
            violation.with_user(self.manager2).action_approve()
        else:
            violation = self._create(user=self.officer, **vals)
            violation.with_user(self.officer).action_submit()
            violation.with_user(self.manager).action_approve()
        return violation.sudo()

    def _month(self, year, month):
        return self.Period.with_user(self.manager).create({"year": year, "month": str(month)})

    def _line(self, period, employee=None):
        return period.line_ids.filtered(lambda l: l.employee_id == (employee or self.employee))

    # ------------------------------------------------------------------
    def test_one_line_per_employee_with_cap(self):
        self._approved(date(2031, 5, 3), vtype=self.type_10)
        self._approved(date(2031, 5, 20))
        self._approved(date(2031, 5, 21), employee=self.employee2, points=60.0)
        self._approved(date(2031, 5, 22), employee=self.employee2, points=60.0)
        self._approved(date(2031, 6, 1))                       # next month
        self._create(user=self.officer, date=date(2031, 5, 4))  # draft: not counted
        may = self._month(2031, 5)
        self.assertEqual(may.name, "2031-05")
        self.assertEqual(len(may.line_ids), 2)
        line = self._line(may)
        self.assertEqual(line.violation_count, 2)
        self.assertEqual(line.deduction_percent, 15.0)
        self.assertEqual(line.branch_names, self.branch.display_name)
        capped = self._line(may, self.employee2)
        self.assertEqual(capped.total_points, 120.0)
        self.assertEqual(capped.deduction_percent, 100.0)

    def test_open_month_follows_approvals(self):
        may = self._month(2031, 5)
        self.assertFalse(may.line_ids)
        violation = self._approved(date(2031, 5, 3))
        self.assertEqual(self._line(may).deduction_percent, 5.0)
        self.assertEqual(violation.mm_deduction_status, "counted")
        violation.with_user(self.manager).points = 8.0
        self.assertEqual(self._line(may).deduction_percent, 8.0)
        violation.with_user(self.manager).action_cancel("wrong branch")
        self.assertFalse(may.line_ids)

    def test_confirm_closes_and_never_deducts_twice(self):
        violation = self._approved(date(2031, 5, 3))
        may = self._month(2031, 5)
        with self.assertRaises(AccessError):
            may.with_user(self.officer).action_confirm()
        may.with_user(self.manager).action_confirm()
        self.assertEqual(may.state, "confirmed")
        self.assertEqual(violation.state, "closed")
        self.assertEqual(violation.mm_deduction_period_id, may)
        self.assertEqual(violation.mm_deduction_status, "deducted")
        with self.assertRaises(UserError):
            violation.with_user(self.manager).action_cancel("too late")
        june = self._month(2031, 6)
        self.assertFalse(june.line_ids)
        with self.assertRaises(UserError):
            may.with_user(self.manager).unlink()

    def test_late_approval_carried_to_next_open_month(self):
        may = self._month(2031, 5)
        may.with_user(self.manager).action_confirm()
        late = self._approved(date(2031, 5, 10), vtype=self.type_10)
        self.assertTrue(late.mm_is_carried)
        self.assertEqual(late.mm_carried_from_label, "2031-05")
        self.assertEqual(late.state, "approved", "confirmed month untouched")
        self.assertFalse(may.line_ids.violation_ids & late)
        self.assertEqual(late.mm_deduction_status, "pending", "no open month yet")

        june = self._month(2031, 6)
        july = self._month(2031, 7)
        self.assertEqual(self._line(june).violation_ids, late)
        self.assertTrue(self._line(june).has_carried)
        self.assertFalse(july.line_ids, "only the earliest open month takes it")
        june.with_user(self.manager).action_confirm()
        self.assertEqual(late.state, "closed")
        self.assertEqual(late.mm_deduction_period_id, june)

    def test_confirm_in_order(self):
        self._month(2031, 5)
        june = self._month(2031, 6)
        with self.assertRaises(UserError):
            june.with_user(self.manager).action_confirm()

    def test_reset_releases(self):
        violation = self._approved(date(2031, 5, 3))
        may = self._month(2031, 5)
        may.with_user(self.manager).action_confirm()
        june = self._month(2031, 6)
        june.with_user(self.manager).action_confirm()
        with self.assertRaises(UserError):
            may.with_user(self.manager).action_reset_draft()   # June is later
        june.with_user(self.manager).action_reset_draft()
        may.with_user(self.manager).action_reset_draft()
        self.assertEqual(may.state, "draft")
        self.assertEqual(violation.state, "approved")
        self.assertFalse(violation.mm_deduction_period_id)
        self.assertEqual(self._line(may).violation_ids, violation)

    def test_unique_month(self):
        self._month(2031, 5)
        with self.assertRaises(Exception):
            with self.cr.savepoint():
                self._month(2031, 5)

    def test_export_for_jisr(self):
        self._approved(date(2031, 5, 3), vtype=self.type_10)
        no_number = self.env["hr.employee"].create({"name": "MMV No Number"})
        self._approved(date(2031, 5, 4), employee=no_number)
        may = self._month(2031, 5)
        self.assertEqual(may.missing_number_count, 1)
        with self.assertRaises(UserError):
            may.action_export_jisr()
        no_number.mm_employee_number = "MMV-3"
        action = may.action_export_jisr()
        attachment = self.env["ir.attachment"].browse(int(action["url"].split("/")[3].split("?")[0]))
        rows = list(openpyxl.load_workbook(io.BytesIO(base64.b64decode(attachment.datas))).active
                    .iter_rows(values_only=True))
        self.assertEqual(len(rows), 3)
        by_number = {r[0]: r for r in rows[1:]}
        self.assertEqual(by_number["MMV-1"][1:], (
            "MMV Cashier", self.branch.display_name, "2031-05", 1, 10.0))
        self.assertEqual(by_number["MMV-3"][4:], (1, 5.0))
