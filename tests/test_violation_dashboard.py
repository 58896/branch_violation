# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MmViolationCommon


@tagged("post_install", "-at_install")
class TestViolationDashboard(MmViolationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Employee = cls.env["hr.employee"]
        cls.branch_b = cls.env["company.branch"].create({
            "name": "MMV Dash B", "code": "MMV-DB", "company_id": cls.company.id})
        cls.emp_a = Employee.create({"name": "MMV Dash A", "company_id": cls.company.id,
                                     "branch_id": cls.branch.id})
        cls.emp_b = Employee.create({"name": "MMV Dash B", "company_id": cls.company.id,
                                     "branch_id": cls.branch_b.id})
        cls.today = fields.Date.context_today(cls.env["mm.branch.violation"])
        cls.Dash = cls.env["mm.violation.dashboard"].with_user(cls.manager)

    def _approved(self, employee, branch, vtype, on=None, **extra):
        rec = self._create(user=self.officer, employee_id=employee.id, branch_id=branch.id,
                           violation_type_id=vtype.id, date=on or self.today, **extra)
        rec.with_user(self.officer).action_submit()
        rec.with_user(self.manager).action_approve()
        return rec

    def _data(self, **params):
        params.setdefault("date_from", (self.today - timedelta(days=6)).isoformat())
        params.setdefault("date_to", self.today.isoformat())
        params.setdefault("branch_ids", [self.branch.id, self.branch_b.id])
        params.setdefault("lang", "en")
        return self.Dash.get_dashboard_data(params)

    def test_officer_cannot_open(self):
        with self.assertRaises(AccessError):
            self.env["mm.violation.dashboard"].with_user(self.officer).get_dashboard_data({})

    def test_counts_breakdowns_and_previous_period(self):
        self._approved(self.emp_a, self.branch, self.type_5)
        self._approved(self.emp_a, self.branch, self.type_5)
        self._approved(self.emp_a, self.branch, self.type_10)
        self._approved(self.emp_b, self.branch_b, self.type_10)
        # previous 7-day period
        self._approved(self.emp_b, self.branch_b, self.type_10, on=self.today - timedelta(days=8))
        # a draft does not count while the status filter is "approved"
        self._create(user=self.officer, employee_id=self.emp_b.id, branch_id=self.branch_b.id)

        d = self._data()
        k = d["kpis"]
        self.assertEqual(k["violations"], 4)
        self.assertEqual(k["logged"], 4)
        self.assertEqual(k["points"], 30.0)
        self.assertEqual(k["employees"], 2)
        # emp_a has 3: one repeat offender out of two employees
        self.assertEqual(k["repeat"], 1)
        self.assertEqual(k["repeat_pct"], 50.0)
        self.assertEqual(d["kpis_prev"]["violations"], 1)

        branches = {r["id"]: r for r in d["branches"]}
        self.assertEqual(branches[self.branch.id]["count"], 3)
        self.assertEqual(branches[self.branch_b.id]["prev"], 1)
        self.assertEqual(d["branches"][0]["id"], self.branch.id, "most violations first")

        top = d["employees"][0]
        self.assertEqual(top["id"], self.emp_a.id)
        self.assertEqual(top["count"], 3)
        self.assertEqual(top["same_type_repeat"], 2)

        types = {r["id"]: r["count"] for r in d["types"]}
        self.assertEqual(types, {self.type_5.id: 2, self.type_10.id: 2})
        self.assertEqual(sum(d["trend"]["total"]), 4)
        self.assertEqual(sum(d["trend"]["prev_total"]), 1)
        self.assertEqual(sum(d["weekdays"]), 4)
        # matrix: branch x type
        row = next(r for r in d["matrix"]["rows"] if r["id"] == self.branch.id)
        self.assertEqual(sum(row["cells"]), 3)
        # the funnel shows every state, the draft included
        self.assertEqual(d["funnel"].get("draft"), 1)
        self.assertEqual(d["funnel"].get("approved"), 4)

    def test_filters(self):
        self._approved(self.emp_a, self.branch, self.type_5)
        self._approved(self.emp_b, self.branch_b, self.type_10)
        self._approved(self.emp_b, self.branch_b, self.type_10)

        self.assertEqual(self._data(branch_ids=[self.branch_b.id])["kpis"]["violations"], 2)
        self.assertEqual(self._data(type_ids=[self.type_5.id])["kpis"]["violations"], 1)
        self.assertEqual(self._data(employee_ids=[self.emp_a.id])["kpis"]["violations"], 1)
        self.assertEqual(self._data(search="MMV Dash B")["kpis"]["violations"], 2)
        # repeat offenders only: emp_b (2) stays, emp_a (1) goes
        d = self._data(repeat_only=True)
        self.assertEqual(d["kpis"]["violations"], 2)
        self.assertEqual([e["id"] for e in d["employees"]], [self.emp_b.id])
        # every status
        self._create(user=self.officer, employee_id=self.emp_a.id)
        self.assertEqual(self._data(states=[])["kpis"]["logged"], 4)
        self.assertEqual(self._data(states=["draft"])["kpis"]["logged"], 1)

    def test_drill_down_domain_matches_figures(self):
        self._approved(self.emp_a, self.branch, self.type_5)
        self._approved(self.emp_b, self.branch_b, self.type_10)
        d = self._data(branch_ids=[self.branch.id])
        records = self.env["mm.branch.violation"].with_user(self.manager).search(d["domain"])
        self.assertEqual(len(records), d["kpis"]["logged"])

    def test_action_strip_ignores_period(self):
        old = self._create(user=self.officer, employee_id=self.emp_a.id,
                           date=self.today - timedelta(days=90))
        old.with_user(self.officer).action_submit()
        d = self._data()
        self.assertEqual(d["actions"]["to_review"], 1)
        self.assertEqual(
            self.env["mm.branch.violation"].search_count(d["domain_open"] + [("state", "=", "under_review")]), 1)

    def test_complaints(self):
        complaint_source = self.env["mm.violation.source"].create(
            {"name": "MMV call", "requires_response": True})
        rec = self._approved(self.emp_a, self.branch, self.type_5, source_id=complaint_source.id,
                             customer_name="MMV Customer")
        d = self._data()
        self.assertEqual(d["kpis"]["complaints"], 1)
        self.assertEqual(d["complaints"]["states"], {"pending": 1})
        rec.with_user(self.manager).action_response_contacted()
        rec.with_user(self.manager).response_action = "Apologised, voucher given"
        rec.with_user(self.manager).action_response_resolved()
        d = self._data(complaints_only=True)
        self.assertEqual(d["kpis"]["violations"], 1)
        self.assertEqual(d["kpis"]["resolved_pct"], 100.0)
        self.assertEqual(d["kpis"]["on_time_pct"], 100.0)

    def test_empty_period(self):
        d = self._data(date_from="2001-01-01", date_to="2001-01-31")
        self.assertEqual(d["kpis"]["logged"], 0)
        self.assertEqual(d["branches"], [])
        self.assertEqual(len(d["trend"]["labels"]), len(d["trend"]["total"]))
