# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MmViolationCommon


@tagged("post_install", "-at_install")
class TestPerformanceScorecard(MmViolationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Branch = cls.env["company.branch"]
        Employee = cls.env["hr.employee"]
        cls.big = Branch.create({"name": "MMV Big", "code": "MMV-BIG", "company_id": cls.company.id})
        cls.small = Branch.create({"name": "MMV Small", "code": "MMV-SML", "company_id": cls.company.id})
        cls.big_staff = Employee.create([
            {"name": "MMV Big %s" % i, "company_id": cls.company.id, "branch_id": cls.big.id}
            for i in range(4)])
        cls.small_staff = Employee.create([
            {"name": "MMV Small %s" % i, "company_id": cls.company.id, "branch_id": cls.small.id}
            for i in range(2)])
        cls.today = fields.Date.context_today(cls.env["mm.branch.violation"])

    def _approved(self, employee, branch, violation_type, on=None):
        rec = self._create(user=self.officer, employee_id=employee.id, branch_id=branch.id,
                           violation_type_id=violation_type.id, date=on or self.today)
        rec.with_user(self.officer).action_submit()
        rec.with_user(self.manager).action_approve()
        return rec

    def _scorecard(self, **vals):
        vals.setdefault("branch_ids", [(6, 0, (self.big | self.small).ids)])
        return self.env["mm.performance.scorecard"].with_user(self.manager).create(vals)

    def test_branch_score_is_per_employee(self):
        # big: 10 points over 4 staff = 2.5 / employee; small: 5 over 2 = 2.5
        # -> same score, fewer violations wins: small (1) beats big (2)
        self._approved(self.big_staff[0], self.big, self.type_5)
        self._approved(self.big_staff[1], self.big, self.type_5)
        self._approved(self.small_staff[0], self.small, self.type_5)
        card = self._scorecard()
        lines = card.branch_line_ids.sorted("rank")
        self.assertEqual(lines.mapped("branch_id"), self.small | self.big)
        self.assertEqual(lines[0].branch_id, self.small)
        self.assertAlmostEqual(lines[1].points_per_employee, 2.5)
        self.assertAlmostEqual(lines[1].score, 97.5)
        self.assertEqual(lines[0].medal, "🥇")

    def test_employee_ranking_and_employee_of_month(self):
        self._approved(self.big_staff[0], self.big, self.type_10)
        self._approved(self.big_staff[1], self.big, self.type_5)
        # an old mistake: still clean this month, but a shorter clean run
        last_year = self.today - relativedelta(years=1)
        self._approved(self.small_staff[1], self.small, self.type_5, on=last_year)
        card = self._scorecard()
        lines = card.employee_line_ids.sorted("rank")
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[-1].employee_id, self.big_staff[0])
        self.assertEqual(lines[-1].score, 90.0)
        self.assertEqual(lines[-2].employee_id, self.big_staff[1])
        eom = lines.filtered("is_employee_of_month")
        self.assertEqual(eom, lines[0])
        self.assertEqual(eom.violations, 0)
        self.assertNotEqual(eom.employee_id, self.small_staff[1])
        highlights = card._mm_highlights()
        self.assertEqual(highlights["clean"], 4)
        self.assertEqual(highlights["worst_employee"].employee_id, self.big_staff[0])
        self.assertEqual(highlights["worst_branch"].branch_id, self.big)
        self.assertIn("MMV Small", card.summary_html)
        self.assertTrue(eom.spotless)
        self.assertEqual(eom.clean_label, "Never")
        # big 2/3 and small 0 have the same spotless record: a shared first place
        self.assertEqual(highlights["tied_first"], 3)
        self.assertIn("share first place", card.summary_html)

    def test_only_counted_violations(self):
        draft = self._create(user=self.officer, employee_id=self.big_staff[0].id,
                             branch_id=self.big.id)
        card = self._scorecard()
        self.assertFalse(sum(card.employee_line_ids.mapped("violations")))
        draft.with_user(self.officer).action_submit()
        draft.with_user(self.manager).action_approve()
        card.action_refresh()
        self.assertEqual(sum(card.employee_line_ids.mapped("violations")), 1)

    def test_movement_vs_previous_period(self):
        last_month = self.today.replace(day=1) - relativedelta(days=1)
        self._approved(self.small_staff[0], self.small, self.type_10, on=last_month)
        self._approved(self.big_staff[0], self.big, self.type_10)
        self._approved(self.big_staff[1], self.big, self.type_10)
        card = self._scorecard(period="this_month")
        small = card.branch_line_ids.filtered(lambda l: l.branch_id == self.small)
        big = card.branch_line_ids.filtered(lambda l: l.branch_id == self.big)
        self.assertEqual((small.rank, small.trend), (1, "up"))
        self.assertEqual((big.rank, big.trend), (2, "down"))
        # changing the period rebuilds the ranking
        card.period = "last_month"
        small = card.branch_line_ids.filtered(lambda l: l.branch_id == self.small)
        self.assertEqual(small.violations, 1)

    def test_no_mistakes_no_worst(self):
        third = self.env["company.branch"].create(
            {"name": "MMV Third", "code": "MMV-3", "company_id": self.company.id})
        fourth = self.env["company.branch"].create(
            {"name": "MMV Fourth", "code": "MMV-4", "company_id": self.company.id})
        self.env["hr.employee"].create([
            {"name": "MMV T", "company_id": self.company.id, "branch_id": third.id},
            {"name": "MMV F", "company_id": self.company.id, "branch_id": fourth.id}])
        card = self._scorecard(branch_ids=[(6, 0, (self.big | self.small | third | fourth).ids)])
        self.assertFalse(card.branch_line_ids.filtered("is_bottom"))
        self.assertEqual(card._mm_highlights()["tied_branches"], 4)
        self.assertFalse(card._mm_highlights()["worst_branch"])
        self.assertIn("4 branches tied", card.summary_html)

    def _target(self, branch, amount, achieved, date_from, date_to, state="in_progress"):
        target = self.env["pos.sales.target"].create({
            "target_level": "branch", "branch_id": branch.id,
            "period_type": "custom", "date_from": date_from, "date_to": date_to,
            "line_ids": [Command.create({
                "indicator_type": "net_sales_refunds", "is_primary": True,
                "target_value": amount,
            })],
        })
        target.write({"net_sales": achieved})
        self.env.cr.execute("UPDATE pos_sales_target SET state = %s WHERE id = %s",
                            (state, target.id))
        target.invalidate_recordset(["state"])
        self.assertEqual(target.target_amount, amount)
        self.assertEqual(target.achieved_amount, achieved)
        return target

    def test_sales_weighted_branch_score(self):
        start = self.today.replace(day=1)
        end = start + relativedelta(months=1, days=-1)
        # big: 90% of target, one 10-point mistake over 4 staff (mistakes 97.5)
        self._target(self.big, 1000.0, 900.0, start, end)
        self._approved(self.big_staff[0], self.big, self.type_10)
        # small: 50% of target, no mistakes
        self._target(self.small, 1000.0, 500.0, start, end)
        self.company.write({"mm_score_weight_sales": 70, "mm_score_weight_mistakes": 30})
        card = self._scorecard(period="this_month")
        big = card.branch_line_ids.filtered(lambda l: l.branch_id == self.big)
        small = card.branch_line_ids.filtered(lambda l: l.branch_id == self.small)
        self.assertAlmostEqual(big.sales_rate, 90.0)
        self.assertAlmostEqual(big.mistakes_score, 97.5)
        self.assertAlmostEqual(big.score, 0.7 * 90 + 0.3 * 97.5, delta=0.051)
        self.assertAlmostEqual(small.score, 0.7 * 50 + 0.3 * 100, delta=0.051)
        self.assertEqual(big.rank, 1, "sales outweigh one mistake at 70/30")
        # mistakes only: the clean branch wins
        self.company.write({"mm_score_weight_sales": 0, "mm_score_weight_mistakes": 100})
        card.action_refresh()
        small = card.branch_line_ids.filtered(lambda l: l.branch_id == self.small)
        self.assertEqual(small.rank, 1)
        self.assertEqual(card.weight_sales, 0)

    def test_sales_over_target_capped_and_no_target(self):
        start = self.today.replace(day=1)
        end = start + relativedelta(months=1, days=-1)
        self._target(self.big, 1000.0, 1500.0, start, end)
        # a draft target does not count
        self._target(self.small, 1000.0, 1000.0, start, end, state="draft")
        card = self._scorecard(period="this_month")
        big = card.branch_line_ids.filtered(lambda l: l.branch_id == self.big)
        small = card.branch_line_ids.filtered(lambda l: l.branch_id == self.small)
        self.assertAlmostEqual(big.sales_rate, 150.0)
        self.assertAlmostEqual(big.score, 100.0)
        self.assertFalse(small.has_target)
        self.assertEqual(small.sales_label, "No target")
        self.assertAlmostEqual(small.score, 100.0, msg="mistakes only")

    def test_year_sums_monthly_targets(self):
        year = self.today.year
        jan = (self.today.replace(month=1, day=1), self.today.replace(month=1, day=31))
        feb = (self.today.replace(month=2, day=1), self.today.replace(month=2, day=28))
        self._target(self.big, 1000.0, 1000.0, *jan)
        self._target(self.big, 1000.0, 600.0, *feb)
        card = self._scorecard(period="this_year")
        big = card.branch_line_ids.filtered(lambda l: l.branch_id == self.big)
        self.assertAlmostEqual(big.sales_target, 2000.0)
        self.assertAlmostEqual(big.sales_rate, 80.0)
        self.assertEqual(year, card.date_from.year)

    def test_weights_must_total_100(self):
        with self.assertRaises(Exception), self.env.cr.savepoint():
            self.company.write({"mm_score_weight_sales": 60, "mm_score_weight_mistakes": 30})
            self.env.flush_all()

    def test_customer_service_ranking(self):
        from odoo.tests import new_test_user
        cs = new_test_user(self.env, login="mmv_sc_cs",
                           groups="mm_branch_violation.group_violation_customer_service")
        cs2 = new_test_user(self.env, login="mmv_sc_cs2",
                            groups="mm_branch_violation.group_violation_customer_service")
        source = self.env["mm.violation.source"].create({"name": "MMV call sc", "requires_response": True})

        def complaint(branch, user=None):
            return self._create(user=self.officer, branch_id=branch.id,
                                employee_id=(self.big_staff[0] if branch == self.big else self.small_staff[0]).id,
                                source_id=source.id, response_user_id=user and user.id,
                                response_action="called")
        # cs: two complaints from two branches, both handled on time
        a, b = complaint(self.big, cs), complaint(self.small, cs)
        a.with_user(cs).action_response_resolved()
        b.with_user(cs).action_response_contacted()
        # cs2: one overdue, never contacted
        c = complaint(self.big, cs2)
        c.with_context(mm_violation_workflow_write=True).write({
            "complaint_received_at": fields.Datetime.now() - relativedelta(days=3)})
        # unassigned, contacted by cs2 late
        d = complaint(self.small)
        d.with_context(mm_violation_workflow_write=True).write({
            "complaint_received_at": fields.Datetime.now() - relativedelta(days=3)})
        d.with_user(cs2).action_response_contacted()
        card = self._scorecard()
        rows = card.service_line_ids.sorted("rank")
        self.assertEqual(rows[0].user_id, cs)
        self.assertEqual((rows[0].complaints, rows[0].on_time_pct, rows[0].resolved_pct),
                         (2, 100.0, 100.0))
        row2 = rows.filtered(lambda r: r.user_id == cs2)
        # c (assigned, overdue) + d (credited to who contacted it, late)
        self.assertEqual((row2.complaints, row2.overdue, row2.late, row2.on_time_pct),
                         (2, 1, 1, 0.0))

    def test_managers_only(self):
        with self.assertRaises(AccessError):
            self.env["mm.performance.scorecard"].with_user(self.officer).create({})

    def test_pdf_renders(self):
        self._approved(self.big_staff[0], self.big, self.type_5)
        card = self._scorecard()
        html = self.env["ir.actions.report"].with_user(self.manager)._render_qweb_html(
            "mm_branch_violation.action_report_mm_performance_scorecard", card.ids)[0]
        self.assertIn(b"MMV Big", html)
        self.assertIn(b"Performance Scorecard", html)
        action = self.env["mm.performance.scorecard"].with_user(self.manager).action_open()
        self.assertEqual(action["res_model"], "mm.performance.scorecard")
