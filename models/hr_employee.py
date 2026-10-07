# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import _, fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    mm_employee_number = fields.Char(
        string="Employee Number", index=True, copy=False, tracking=True,
        help="Employee number from Jisr (the HR/payroll system). Used to "
             "identify employees on violations and in the Jisr export.",
    )
    # Totals are never stored: the deduction period is decided by the
    # commission side, so these only show the current month at a glance.
    mm_violation_count = fields.Integer(
        string="Violations This Month", compute="_compute_mm_violations",
        groups="mm_branch_violation.group_violation_officer",
    )
    mm_violation_points = fields.Float(
        string="Violation Points This Month", compute="_compute_mm_violations",
        groups="mm_branch_violation.group_violation_officer",
    )

    _sql_constraints = [
        ("mm_employee_number_company_uniq",
         "unique(company_id, mm_employee_number)",
         "This employee number is already used by another employee of the "
         "same company."),
    ]

    def _compute_mm_violations(self):
        today = fields.Date.context_today(self)
        date_from = today.replace(day=1)
        date_to = date_from + relativedelta(months=1, days=-1)
        Violation = self.env["mm.branch.violation"]
        data = {
            employee.id: (count, points)
            for employee, count, points in Violation._read_group(
                Violation._mm_counted_domain(self, date_from, date_to),
                ["employee_id"], ["__count", "points:sum"],
            )
        }
        for employee in self:
            count, points = data.get(employee.id, (0, 0.0))
            employee.mm_violation_count = count
            employee.mm_violation_points = points

    def action_view_mm_violations(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "mm_branch_violation.mm_branch_violation_action")
        action.update({
            "name": _("Violations - %s", self.name),
            "domain": [("employee_id", "=", self.id)],
            "context": {
                "default_employee_id": self.id,
                "search_default_filter_this_month": 1,
                "search_default_filter_counted": 1,
            },
        })
        return action


class HrEmployeePublic(models.Model):
    _inherit = "hr.employee.public"

    # Public so Violation Officers without HR rights can see and search it.
    mm_employee_number = fields.Char(string="Employee Number", readonly=True)
    # Public so officers without HR rights can read an employee's branch.
    branch_id = fields.Many2one("company.branch", readonly=True)
