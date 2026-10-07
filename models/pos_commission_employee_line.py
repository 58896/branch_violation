# -*- coding: utf-8 -*-
"""Violation deduction on each employee commission line.

Rules (agreed with Operations/Finance, 2026-09-29):

- deduction % for a line = sum of points of the employee's counted
  violations, capped at 100. The money stays with the company: nothing is
  redistributed to colleagues, and ``final_commission_amount`` (which must
  reconcile with the branch commission) is never touched.
- Counted on a line (employee E, period P):
  * approved, not-carried violations of E dated inside P;
  * violations already Closed by another of E's lines for the same P
    (employee in two branches: the same % applies to every line);
  * carried violations of E, on E's earliest open period after the period
    they were carried from.
- The figures refresh while the distribution is open and freeze when it is
  approved (``mm_deduction_locked``); the counted violations become Closed
  and point to the line that closed them. Cancelling the distribution
  releases them again.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

# distribution states whose figures are final / being paid
LOCKED_DISTRIBUTION_STATES = ("approved", "transferred")


def period_label(date_from, date_to):
    """'2026-09' for a calendar month, otherwise 'from → to'."""
    if not (date_from and date_to):
        return ""
    if date_from.day == 1 and date_to == date_from + relativedelta(months=1, days=-1):
        return date_from.strftime("%Y-%m")
    return "%s → %s" % (date_from, date_to)


class PosCommissionEmployeeLine(models.Model):
    _inherit = "pos.commission.employee.line"

    mm_violation_ids = fields.Many2many(
        "mm.branch.violation", "mm_commission_line_violation_rel",
        "line_id", "violation_id", string="Counted Violations",
        readonly=True, copy=False,
    )
    mm_violation_count = fields.Integer(
        string="Violations", compute="_compute_mm_violation_count",
    )
    deduction_percent = fields.Float(
        string="Violation %", digits=(5, 2), readonly=True, copy=False,
        help="Sum of the counted violations' points, capped at 100%.",
    )
    deduction_amount = fields.Monetary(
        string="Violation Deduction", currency_field="currency_id",
        compute="_compute_violation_deduction", store=True,
    )
    net_commission_amount = fields.Monetary(
        string="Net Commission", currency_field="currency_id",
        compute="_compute_violation_deduction", store=True,
        help="Final commission minus the violation deduction. This is what "
             "the employee is paid.",
    )
    mm_violation_note = fields.Text(string="Violation Details", readonly=True, copy=False)
    mm_deduction_locked = fields.Boolean(
        string="Deduction Locked", readonly=True, copy=False,
        help="Set when the distribution is approved: the violation figures "
             "no longer change.",
    )

    @api.depends("mm_violation_ids")
    def _compute_mm_violation_count(self):
        for line in self:
            line.mm_violation_count = len(line.mm_violation_ids)

    @api.depends("final_commission_amount", "deduction_percent", "currency_id")
    def _compute_violation_deduction(self):
        for line in self:
            gross = line.final_commission_amount or 0.0
            pct = min(max(line.deduction_percent or 0.0, 0.0), 100.0)
            amount = gross * pct / 100.0
            if line.currency_id:
                amount = line.currency_id.round(amount)
            amount = min(amount, gross) if gross > 0 else 0.0
            line.deduction_amount = amount
            line.net_commission_amount = gross - amount

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._mm_refresh_violation_deductions()
        return lines

    # ------------------------------------------------------------------
    def _mm_is_open(self):
        self.ensure_one()
        return not self.mm_deduction_locked and \
            self.commission_result_id.employee_distribution_state not in LOCKED_DISTRIBUTION_STATES

    @api.model
    def _mm_open_lines_domain(self, employees):
        return [
            ("employee_id", "in", employees.ids),
            ("mm_deduction_locked", "=", False),
            ("commission_result_id.employee_distribution_state", "not in",
             list(LOCKED_DISTRIBUTION_STATES) + ["cancelled"]),
        ]

    @api.model
    def _mm_refresh_for_employees(self, employees):
        if employees:
            self.sudo().search(self._mm_open_lines_domain(employees))._mm_refresh_violation_deductions()

    def _mm_counted_violations(self):
        self.ensure_one()
        Violation = self.env["mm.branch.violation"].sudo()
        employee, date_from, date_to = self.employee_id, self.date_from, self.date_to
        if not (employee and date_from and date_to):
            return Violation
        regular = Violation.search([
            ("employee_id", "=", employee.id),
            ("state", "=", "approved"),
            ("mm_is_carried", "=", False),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
        ])
        closed_same_period = Violation.search([
            ("employee_id", "=", employee.id),
            ("state", "=", "closed"),
            ("mm_commission_line_id", "!=", self.id),
            ("mm_commission_line_id.date_from", "=", date_from),
            ("mm_commission_line_id.date_to", "=", date_to),
        ])
        carried = Violation.search([
            ("employee_id", "=", employee.id),
            ("state", "=", "approved"),
            ("mm_is_carried", "=", True),
            ("mm_carried_from_date_to", "<", date_from),
        ])
        if carried:
            open_lines = self.sudo().search(self._mm_open_lines_domain(employee))
            carried = carried.filtered(lambda v: min(
                open_lines.filtered(lambda l: l.date_from and l.date_from > v.mm_carried_from_date_to)
                .mapped("date_from") or [date_from]) == date_from)
        return regular | closed_same_period | carried

    def _mm_refresh_violation_deductions(self):
        """Recompute the counted violations of the still-open lines."""
        for line in self.sudo().filtered(lambda l: l._mm_is_open()):
            violations = line._mm_counted_violations()
            total = sum(violations.mapped("points"))
            line.write({
                "mm_violation_ids": [(6, 0, violations.ids)],
                "deduction_percent": min(total, 100.0),
                "mm_violation_note": line._mm_build_note(violations, total),
            })

    def _mm_build_note(self, violations, total):
        if not violations:
            return False
        rows = []
        for violation in violations.sorted(lambda v: (v.date, v.id)):
            text = "%s  %s  %s  %s%%" % (
                violation.name, violation.date, violation.violation_type_id.name, violation.points)
            if violation.mm_is_carried:
                text += "  " + _("(carried from %s)", violation.mm_carried_from_label)
            if violation.branch_id != self.branch_id:
                text += "  " + _("[branch: %s]", violation.branch_id.display_name)
            rows.append(text)
        if total > 100.0:
            rows.append(_("Total %(total)s%% capped at 100%%.", total=total))
        return "\n".join(rows)

    def _mm_lock_violation_deductions(self):
        """Freeze the figures and close the counted violations. Called when
        the distribution is approved."""
        for line in self.sudo():
            line.mm_deduction_locked = True
            to_close = line.mm_violation_ids.filtered(lambda v: v.state == "approved")
            to_close._mm_workflow_write({"state": "closed", "mm_commission_line_id": line.id})
            for violation in to_close:
                violation.message_post(body=_(
                    "Deducted on commission line %(line)s (%(period)s). Closed.",
                    line=line.name, period=period_label(line.date_from, line.date_to)))

    def _mm_release_violation_deductions(self):
        """Undo _mm_lock_violation_deductions (distribution cancelled)."""
        lines = self.sudo()
        lines.write({"mm_deduction_locked": False})
        Violation = self.env["mm.branch.violation"].sudo()
        closed = Violation.search([("mm_commission_line_id", "in", lines.ids)])
        for violation in closed:
            # still counted on another settled line of the same employee
            # (other branch, same period): that line now holds it
            other = self.sudo().search([
                ("mm_violation_ids", "in", violation.id),
                ("mm_deduction_locked", "=", True),
                ("id", "not in", lines.ids),
            ], limit=1)
            if other:
                violation._mm_workflow_write({"mm_commission_line_id": other.id})
            else:
                violation._mm_workflow_write({"state": "approved", "mm_commission_line_id": False})
                violation.message_post(body=_(
                    "Commission distribution cancelled: violation released back "
                    "to Approved."))
        self._mm_refresh_for_employees(closed.employee_id)

    def action_view_mm_violations(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "mm_branch_violation.mm_branch_violation_action")
        action.update({
            "name": _("Violations counted - %s", self.employee_id.sudo().name),
            "domain": [("id", "in", self.mm_violation_ids.ids)],
            "context": {"create": False},
        })
        return action
