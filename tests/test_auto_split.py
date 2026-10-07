# -*- coding: utf-8 -*-
from datetime import date

from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

SEPTEMBER = (date(2026, 9, 1), date(2026, 9, 30))


@tagged("post_install", "-at_install")
class TestAutoSplit(TransactionCase):
    """King Road: 1,000 SAR branch commission, 5 cashiers, 200 each."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for xmlid in ("group_pos_target_manager", "group_pos_target_approve"):
            cls.env.user.groups_id += cls.env.ref("pos_sales_target_commission." + xmlid)
        cls.branch = cls.env["company.branch"].create({
            "name": "MMA King Road", "code": "MMA-KR", "company_id": cls.env.company.id})
        cls.plan = cls.env["pos.commission.plan"].create({
            "name": "MMA Plan", "company_id": cls.env.company.id,
            "distribution_method": "equal",
            "mm_auto_split": True,
            "tier_ids": [Command.create({
                "achievement_from": 0.0, "is_open_ended": True,
                "commission_type": "percentage", "rate": 100.0,
            })],
        })
        cls.cashiers = cls.env["hr.employee"].create([
            {"name": "MMA Cashier %s" % i, "branch_id": cls.branch.id} for i in range(1, 6)])
        cls.officer = new_test_user(
            cls.env, login="mma_officer", groups="mm_branch_violation.group_violation_officer")
        cls.manager = new_test_user(
            cls.env, login="mma_manager", groups="mm_branch_violation.group_violation_manager")
        cls.vtype = cls.env["mm.violation.type"].create({"name": "MMA ten", "points": 10.0})
        cls.source = cls.env["mm.violation.source"].create({"name": "MMA source"})

    def _target(self, amount=1000.0, plan=None, **vals):
        target = self.env["pos.sales.target"].create(dict({
            "target_level": "branch", "branch_id": self.branch.id,
            "period_type": "custom", "date_from": SEPTEMBER[0], "date_to": SEPTEMBER[1],
            "commission_plan_id": (plan or self.plan).id,
            "line_ids": [Command.create({
                "indicator_type": "net_sales_refunds", "is_primary": True,
                "target_value": amount,
            })],
        }, **vals))
        target.write({"net_sales": amount})
        return target

    def _refresh(self, target):
        """What Confirm / Start / Refresh KPIs / the hourly job call."""
        target._pstc_ensure_commission_result()
        return target.commission_result_id

    def _violation(self, employee):
        violation = self.env["mm.branch.violation"].with_user(self.officer).create({
            "date": date(2026, 9, 10), "branch_id": self.branch.id,
            "note_type": "operational", "entering_department": "operations",
            "employee_id": employee.id, "source_id": self.source.id,
            "violation_type_id": self.vtype.id,
        })
        violation.with_user(self.officer).action_submit()
        violation.with_user(self.manager).action_approve()
        return violation

    # ------------------------------------------------------------------
    def test_branch_commission_split_equally(self):
        self._violation(self.cashiers[2])
        result = self._refresh(self._target())
        self.assertEqual(result.distribution_method, "equal", "taken from the plan")
        self.assertEqual(result.employee_distribution_state, "calculated")
        self.assertEqual(result.employee_line_ids.employee_id, self.cashiers)
        self.assertEqual(set(result.employee_line_ids.mapped("final_commission_amount")), {200.0})
        line3 = result.employee_line_ids.filtered(lambda l: l.employee_id == self.cashiers[2])
        self.assertEqual(line3.deduction_percent, 10.0)
        self.assertEqual(line3.net_commission_amount, 180.0)
        self.assertEqual(result.total_net_commission, 980.0)

    def test_no_rerun_when_nothing_changed(self):
        target = self._target()
        result = self._refresh(target)
        messages = len(result.message_ids)
        self._refresh(target)   # e.g. the hourly job
        self.assertEqual(len(result.message_ids), messages, "no new split, no chatter noise")

    def test_follows_changes_until_submitted(self):
        target = self._target()
        result = self._refresh(target)
        new_cashier = self.env["hr.employee"].create({"name": "MMA New", "branch_id": self.branch.id})
        self._refresh(target)
        self.assertIn(new_cashier, result.employee_line_ids.employee_id)
        self.assertAlmostEqual(sum(result.employee_line_ids.mapped("final_commission_amount")), 1000.0)
        self.assertEqual(result.undistributed_difference, 0.0)

        result.action_submit_distribution_review()
        late_joiner = self.env["hr.employee"].create({"name": "MMA Late", "branch_id": self.branch.id})
        self._refresh(target)
        self.assertNotIn(late_joiner, result.employee_line_ids.employee_id,
                         "a submitted split is not changed underneath the reviewer")
        self.assertEqual(result.employee_distribution_state, "under_review")

    def test_manual_percentage_only_loads(self):
        plan = self.plan.copy({"name": "MMA Manual", "distribution_method": "manual_percentage"})
        result = self._refresh(self._target(plan=plan))
        self.assertEqual(len(result.employee_line_ids), 5)
        self.assertEqual(result.employee_distribution_state, "draft", "needs the % typed in first")

    def test_off_unless_enabled_on_plan(self):
        plan = self.plan.copy({"name": "MMA Off", "mm_auto_split": False})
        result = self._refresh(self._target(plan=plan))
        self.assertFalse(result.employee_line_ids)
        self.assertEqual(result.employee_distribution_state, "draft")

    def test_only_branch_targets(self):
        target = self._target(target_level="company", branch_id=False)
        result = self._refresh(target)
        self.assertFalse(result.employee_line_ids)
