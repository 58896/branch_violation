# -*- coding: utf-8 -*-
import base64
import io
from datetime import date

import openpyxl

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

JULY = (date(2026, 7, 1), date(2026, 7, 31))
AUGUST = (date(2026, 8, 1), date(2026, 8, 31))


@tagged("post_install", "-at_install")
class TestCommissionViolation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        for xmlid in ("group_pos_target_manager", "group_pos_target_approve",
                      "group_pos_commission_manager", "group_pos_target_view_commission"):
            cls.env.user.groups_id += cls.env.ref("pos_sales_target_commission." + xmlid)
        Branch = cls.env["company.branch"]
        cls.branch_a = Branch.create({"name": "MMC Branch A", "code": "MMC-A", "company_id": cls.company.id})
        cls.branch_b = Branch.create({"name": "MMC Branch B", "code": "MMC-B", "company_id": cls.company.id})
        cls.plan = cls.env["pos.commission.plan"].create({
            "name": "MMC Plan", "company_id": cls.company.id,
            "tier_ids": [Command.create({
                "achievement_from": 0.0, "is_open_ended": True,
                "commission_type": "percentage", "rate": 100.0,
            })],
        })
        Employee = cls.env["hr.employee"]
        cls.emp1 = Employee.create({"name": "MMC Violator", "branch_id": cls.branch_a.id,
                                    "mm_employee_number": "MMC-1"})
        cls.emp2 = Employee.create({"name": "MMC Colleague", "branch_id": cls.branch_a.id,
                                    "mm_employee_number": "MMC-2"})
        cls.officer = new_test_user(
            cls.env, login="mmc_officer", groups="mm_branch_violation.group_violation_officer")
        cls.manager = new_test_user(
            cls.env, login="mmc_manager", groups="mm_branch_violation.group_violation_manager")
        cls.manager2 = new_test_user(
            cls.env, login="mmc_manager2", groups="mm_branch_violation.group_violation_manager")
        cls.type_5 = cls.env["mm.violation.type"].create({"name": "MMC five", "points": 5.0})
        cls.type_10 = cls.env["mm.violation.type"].create({"name": "MMC ten", "points": 10.0})
        cls.source = cls.env["mm.violation.source"].create({"name": "MMC source"})

    # ------------------------------------------------------------------ helpers
    def _result(self, period, amount=1000.0, branch=None):
        branch = branch or self.branch_a
        target = self.env["pos.sales.target"].create({
            "target_level": "branch", "branch_id": branch.id,
            "period_type": "custom", "date_from": period[0], "date_to": period[1],
            "commission_plan_id": self.plan.id,
            "line_ids": [Command.create({
                "indicator_type": "net_sales_refunds", "is_primary": True,
                "target_value": amount,
            })],
        })
        target.write({"net_sales": amount})
        target._pstc_ensure_commission_result()
        result = target.commission_result_id
        result.distribution_method = "equal"
        result.action_load_eligible_employees()
        return result

    def _calculate(self, result):
        result.action_calculate_distribution()
        return result

    def _approve(self, result):
        if result.employee_distribution_state in ("draft", "cancelled"):
            result.action_calculate_distribution()
        result.action_submit_distribution_review()
        result.action_approve_distribution()
        return result

    def _violation(self, day, vtype=None, employee=None, approve=True, points=None):
        vals = {
            "date": day,
            "branch_id": self.branch_a.id,
            "note_type": "operational",
            "entering_department": "operations",
            "employee_id": (employee or self.emp1).id,
            "source_id": self.source.id,
            "violation_type_id": (vtype or self.type_5).id,
        }
        if points is not None:
            violation = self.env["mm.branch.violation"].with_user(self.manager).create(
                dict(vals, points=points))
        else:
            violation = self.env["mm.branch.violation"].with_user(self.officer).create(vals)
        violation.with_user(violation.create_uid).action_submit()
        if approve:
            approver = self.manager2 if violation.create_uid == self.manager else self.manager
            violation.with_user(approver).action_approve()
        return violation.sudo()

    def _line(self, result, employee=None):
        return result.employee_line_ids.filtered(lambda l: l.employee_id == (employee or self.emp1))

    # ------------------------------------------------------------------ tests
    def test_deduction_only_on_violators_share(self):
        self._violation(date(2026, 7, 5), self.type_5)
        self._violation(date(2026, 7, 20), self.type_10)
        self._violation(date(2026, 8, 2), self.type_10)   # other period: ignored
        result = self._calculate(self._result(JULY))
        line1, line2 = self._line(result), self._line(result, self.emp2)
        self.assertEqual(line1.final_commission_amount, 500.0, "gross share untouched")
        self.assertEqual(line1.deduction_percent, 15.0)
        self.assertEqual(line1.deduction_amount, 75.0)
        self.assertEqual(line1.net_commission_amount, 425.0)
        self.assertEqual(len(line1.mm_violation_ids), 2)
        self.assertEqual(line2.deduction_percent, 0.0)
        self.assertEqual(line2.net_commission_amount, 500.0, "colleague not affected")
        self.assertEqual(result.undistributed_difference, 0.0, "distribution still reconciles")
        self.assertEqual(result.total_violation_deduction, 75.0)
        self.assertEqual(result.total_net_commission, 925.0)

    def test_deduction_capped_at_100(self):
        self._violation(date(2026, 7, 5), points=60.0)
        self._violation(date(2026, 7, 6), points=60.0)
        line = self._line(self._calculate(self._result(JULY)))
        self.assertEqual(line.deduction_percent, 100.0)
        self.assertEqual(line.deduction_amount, 500.0)
        self.assertEqual(line.net_commission_amount, 0.0, "never negative")
        # 2 violation rows + the "capped at 100%" row
        self.assertEqual(len(line.mm_violation_note.splitlines()), 3)

    def test_rounding_uses_company_currency(self):
        self._violation(date(2026, 7, 5), points=3.33)
        line = self._line(self._calculate(self._result(JULY, amount=1000.02)))
        # 500.01 * 3.33% = 16.650333 -> 16.65
        self.assertEqual(line.deduction_percent, 3.33)
        self.assertEqual(line.deduction_amount, line.currency_id.round(line.final_commission_amount * 3.33 / 100))
        self.assertEqual(line.net_commission_amount, line.final_commission_amount - line.deduction_amount)

    def test_live_refresh_while_open(self):
        result = self._calculate(self._result(JULY))
        line = self._line(result)
        self.assertEqual(line.deduction_percent, 0.0)
        violation = self._violation(date(2026, 7, 5), self.type_10)
        self.assertEqual(line.deduction_percent, 10.0, "approving refreshes open lines")
        violation.with_user(self.manager).points = 7.0
        self.assertEqual(line.deduction_percent, 7.0)
        violation.with_user(self.manager).action_cancel("wrong employee")
        self.assertEqual(line.deduction_percent, 0.0)
        draft = self._violation(date(2026, 7, 6), approve=False)
        self.assertEqual(line.deduction_percent, 0.0, "under review does not count")
        self.assertEqual(draft.mm_deduction_status, "none")

    def test_lock_on_approval_and_no_double_deduction(self):
        violation = self._violation(date(2026, 7, 5), self.type_10)
        self.assertEqual(violation.mm_deduction_status, "pending")
        july = self._approve(self._result(JULY))
        line = self._line(july)
        self.assertTrue(line.mm_deduction_locked)
        self.assertEqual(violation.state, "closed")
        self.assertEqual(violation.mm_commission_line_id, line)
        self.assertEqual(violation.mm_deduction_status, "deducted")
        with self.assertRaises(UserError):
            violation.with_user(self.manager).action_cancel("too late")
        # a later period never counts it again
        august = self._calculate(self._result(AUGUST))
        self.assertEqual(self._line(august).deduction_percent, 0.0)
        # the locked line keeps its figures even if a new violation appears
        self.assertEqual(line.deduction_percent, 10.0)

    def test_cancel_distribution_releases(self):
        violation = self._violation(date(2026, 7, 5))
        july = self._approve(self._result(JULY))
        july.action_cancel_distribution()
        self.assertEqual(violation.state, "approved")
        self.assertFalse(violation.mm_commission_line_id)
        self.assertFalse(self._line(july).mm_deduction_locked)
        self._approve(july)
        self.assertEqual(violation.state, "closed")
        self.assertEqual(violation.mm_commission_line_id, self._line(july))

    def test_late_violation_carried_to_next_open_period(self):
        july = self._approve(self._result(JULY))
        late = self._violation(date(2026, 7, 15), self.type_10)
        self.assertTrue(late.mm_is_carried)
        self.assertEqual(late.mm_carried_from_label, "2026-07")
        self.assertEqual(late.state, "approved", "paid period untouched")
        self.assertEqual(self._line(july).deduction_percent, 0.0)
        self.assertEqual(late.mm_deduction_status, "pending", "no future line yet")
        pending = self.env["mm.branch.violation"].search([("mm_deduction_status", "=", "pending")])
        self.assertIn(late, pending)

        august = self._calculate(self._result(AUGUST))
        line = self._line(august)
        self.assertEqual(line.deduction_percent, 10.0)
        self.assertIn("2026-07", line.mm_violation_note, "note names the original period")
        self.assertEqual(late.mm_deduction_status, "counted")
        self._approve(august)
        self.assertEqual(late.state, "closed")
        self.assertEqual(late.mm_commission_line_id, line)

    def test_carried_only_on_earliest_open_period(self):
        self._approve(self._result(JULY))
        late = self._violation(date(2026, 7, 15))
        august = self._calculate(self._result(AUGUST))
        september = self._calculate(self._result((date(2026, 9, 1), date(2026, 9, 30))))
        self.assertEqual(self._line(august).mm_violation_ids, late)
        self.assertFalse(self._line(september).mm_violation_ids)

    def test_employee_in_two_branches_same_percent(self):
        violation = self._violation(date(2026, 7, 5), self.type_10)
        result_a = self._calculate(self._result(JULY))
        result_b = self._result(JULY, amount=600.0, branch=self.branch_b)
        self.env["pos.commission.employee.line"].create({
            "commission_result_id": result_b.id, "employee_id": self.emp1.id})
        self._calculate(result_b)
        line_a, line_b = self._line(result_a), self._line(result_b)
        self.assertEqual(line_a.deduction_percent, 10.0)
        self.assertEqual(line_b.deduction_percent, 10.0)
        self.assertIn("MMC-A", line_b.mm_violation_note, "note says where it happened")

        self._approve(result_a)
        self.assertEqual(violation.mm_commission_line_id, line_a)
        result_b.action_recalculate_distribution()
        self.assertEqual(line_b.deduction_percent, 10.0, "still counted after A closed it")
        self._approve(result_b)
        self.assertEqual(line_b.deduction_amount, 60.0)
        # cancelling A hands the violation to B instead of releasing it
        result_a.action_cancel_distribution()
        self.assertEqual(violation.state, "closed")
        self.assertEqual(violation.mm_commission_line_id, line_b)

    def test_export_for_jisr(self):
        self._violation(date(2026, 7, 5), self.type_10)
        result = self._calculate(self._result(JULY))
        with self.assertRaises(UserError):
            result.action_mm_export_jisr()   # not approved yet
        self._approve(result)
        self.emp2.mm_employee_number = False
        with self.assertRaises(UserError):
            result.action_mm_export_jisr()   # missing employee number
        self.emp2.mm_employee_number = "MMC-2"
        action = result.action_mm_export_jisr()
        attachment = self.env["ir.attachment"].browse(int(action["url"].split("/")[3].split("?")[0]))
        book = openpyxl.load_workbook(io.BytesIO(base64.b64decode(attachment.datas)))
        rows = list(book.active.iter_rows(values_only=True))
        self.assertEqual(len(rows), 3)
        by_number = {r[0]: r for r in rows[1:]}
        self.assertEqual(by_number["MMC-1"][1:], (
            "MMC Violator", self.branch_a.display_name, "2026-07", 500.0, 10.0, 50.0, 450.0))
        self.assertEqual(by_number["MMC-2"][4:], (500.0, 0.0, 0.0, 500.0))

    def test_deduction_fields_are_protected(self):
        violation = self._violation(date(2026, 7, 5))
        with self.assertRaises(UserError):
            violation.with_user(self.manager).write({"mm_is_carried": True})
        with self.assertRaises(UserError):
            violation.with_user(self.manager).write({"mm_commission_line_id": False})

    def test_reset_clears_carry(self):
        self._approve(self._result(JULY))
        late = self._violation(date(2026, 7, 15))
        self.assertTrue(late.mm_is_carried)
        late.with_user(self.manager).action_cancel("typo")
        late.with_user(self.manager).action_reset_draft()
        self.assertFalse(late.mm_is_carried)
