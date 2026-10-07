# -*- coding: utf-8 -*-
"""Monthly violation deductions for Jisr.

Commission is only calculated per branch in Odoo today, and each employee's
incentive is paid through Jisr. So once a month Finance takes the approved
violations of that month, gets one row per employee with the total
deduction % (capped at 100), exports it for Jisr, and confirms the month:
the violations become Closed and can never be deducted again.

- A violation approved after its month was confirmed is carried forward to
  the next month that is still open (see mm_branch_violation.py).
- Confirming a month and approving an employee commission distribution
  (pos_commission_employee_line.py) both close violations, and neither
  counts a Closed violation again, so the two ways never double-deduct.
"""
import base64
import calendar
import io
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

try:
    import xlsxwriter
except ImportError:  # pragma: no cover - declared in external_dependencies
    xlsxwriter = None

MANAGER_GROUP = "mm_branch_violation.group_violation_manager"


class MmViolationDeductionPeriod(models.Model):
    _name = "mm.violation.deduction.period"
    _description = "Monthly Violation Deductions"
    _inherit = ["mail.thread"]
    _order = "date_from desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_dates", store=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
    )
    month = fields.Selection(
        [(str(i), calendar.month_name[i]) for i in range(1, 13)], required=True,
        default=lambda self: str((fields.Date.context_today(self) - relativedelta(months=1)).month),
    )
    year = fields.Integer(
        required=True,
        default=lambda self: (fields.Date.context_today(self) - relativedelta(months=1)).year,
    )
    date_from = fields.Date(compute="_compute_dates", store=True, index=True)
    date_to = fields.Date(compute="_compute_dates", store=True)
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed")],
        default="draft", required=True, tracking=True, copy=False,
    )
    line_ids = fields.One2many(
        "mm.violation.deduction.line", "period_id", string="Employees", copy=False,
    )
    employee_count = fields.Integer(compute="_compute_totals")
    violation_count = fields.Integer(compute="_compute_totals")
    missing_number_count = fields.Integer(
        string="Missing Employee Numbers", compute="_compute_totals",
    )
    computed_date = fields.Datetime(string="Last Refreshed", readonly=True, copy=False)
    confirmed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    confirmed_date = fields.Datetime(readonly=True, copy=False)

    _sql_constraints = [
        ("month_company_uniq", "unique(company_id, year, month)",
         "There is already a deduction record for this month."),
    ]

    @api.depends("month", "year")
    def _compute_dates(self):
        for period in self:
            if not (period.month and period.year):
                period.date_from = period.date_to = period.name = False
                continue
            start = date(period.year, int(period.month), 1)
            period.date_from = start
            period.date_to = start + relativedelta(months=1, days=-1)
            period.name = start.strftime("%Y-%m")

    @api.depends("line_ids.violation_count", "line_ids.employee_number")
    def _compute_totals(self):
        for period in self:
            period.employee_count = len(period.line_ids)
            period.violation_count = sum(period.line_ids.mapped("violation_count"))
            period.missing_number_count = len(period.line_ids.filtered(lambda l: not l.employee_number))

    # ------------------------------------------------------------------
    def write(self, vals):
        if {"month", "year", "company_id"} & set(vals) and \
                any(p.state == "confirmed" for p in self):
            raise UserError(_("A confirmed month cannot be changed. Reset it to Draft first."))
        res = super().write(vals)
        if {"month", "year"} & set(vals):
            self.filtered(lambda p: p.state == "draft")._mm_refresh()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        periods = super().create(vals_list)
        periods._mm_refresh()
        return periods

    def unlink(self):
        if any(p.state == "confirmed" for p in self):
            raise UserError(_("A confirmed month cannot be deleted. Reset it to Draft first."))
        return super().unlink()

    def _mm_check_manager(self):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(_("Only a Violation Manager can do this."))

    # ------------------------------------------------------------------
    # Which violations count
    # ------------------------------------------------------------------
    def _mm_counted_violations(self):
        """Approved (not yet deducted) violations for this month: dated in
        the month, plus violations carried forward from an earlier
        confirmed month, when this is the earliest open month after it."""
        self.ensure_one()
        Violation = self.env["mm.branch.violation"].sudo()
        base = [("company_id", "=", self.company_id.id), ("state", "=", "approved")]
        regular = Violation.search(base + [
            ("mm_is_carried", "=", False),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ])
        carried = Violation.search(base + [
            ("mm_is_carried", "=", True),
            ("mm_carried_from_date_to", "<", self.date_from),
        ])
        if carried:
            earlier_open = self.search([
                ("company_id", "=", self.company_id.id),
                ("state", "=", "draft"),
                ("date_from", "<", self.date_from),
                ("id", "!=", self.id),
            ])
            carried = carried.filtered(lambda v: not any(
                p.date_from > v.mm_carried_from_date_to for p in earlier_open))
        return regular | carried

    def _mm_refresh(self):
        for period in self.sudo().filtered(lambda p: p.state == "draft" and p.date_from):
            violations = period._mm_counted_violations()
            by_employee = {}
            for violation in violations:
                by_employee.setdefault(violation.employee_id, self.env["mm.branch.violation"].sudo())
                by_employee[violation.employee_id] |= violation
            existing = {line.employee_id: line for line in period.line_ids}
            for employee, emp_violations in by_employee.items():
                vals = {"violation_ids": [(6, 0, emp_violations.ids)]}
                if employee in existing:
                    existing.pop(employee).write(vals)
                else:
                    self.env["mm.violation.deduction.line"].sudo().create(
                        dict(vals, period_id=period.id, employee_id=employee.id))
            for line in existing.values():  # nothing left for these employees
                line.unlink()
            period.computed_date = fields.Datetime.now()

    @api.model
    def _mm_refresh_open_periods(self, companies):
        if companies:
            self.sudo().search([
                ("company_id", "in", companies.ids), ("state", "=", "draft"),
            ])._mm_refresh()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_refresh(self):
        for period in self:
            if period.state != "draft":
                raise UserError(_("%s is confirmed: its figures are final.", period.name))
        self._mm_refresh()
        return True

    def action_confirm(self):
        self._mm_check_manager()
        for period in self:
            if period.state != "draft":
                raise UserError(_("%s is already confirmed.", period.name))
            earlier_draft = self.search([
                ("company_id", "=", period.company_id.id), ("state", "=", "draft"),
                ("date_from", "<", period.date_from), ("id", "not in", self.ids)])
            if earlier_draft:
                raise UserError(_(
                    "Confirm the earlier month(s) first: %s",
                    ", ".join(earlier_draft.mapped("name"))))
        self._mm_refresh()
        now = fields.Datetime.now()
        for period in self.sudo().sorted("date_from"):
            violations = period.line_ids.violation_ids.filtered(lambda v: v.state == "approved")
            violations._mm_workflow_write({"state": "closed", "mm_deduction_period_id": period.id})
            for violation in violations:
                violation.message_post(body=_(
                    "Deducted in the monthly deductions of %s. Closed.", period.name))
            period.write({"state": "confirmed", "confirmed_by_id": self.env.user.id,
                          "confirmed_date": now})
            period.message_post(body=_(
                "Month confirmed: %(v)s violations of %(e)s employees closed.",
                v=len(violations), e=len(period.line_ids)))
        return True

    def action_reset_draft(self):
        self._mm_check_manager()
        for period in self:
            if period.state != "confirmed":
                raise UserError(_("%s is not confirmed.", period.name))
            later_confirmed = self.search([
                ("company_id", "=", period.company_id.id), ("state", "=", "confirmed"),
                ("date_from", ">", period.date_from), ("id", "not in", self.ids)])
            if later_confirmed:
                raise UserError(_(
                    "Reset the later confirmed month(s) first: %s",
                    ", ".join(later_confirmed.mapped("name"))))
        Violation = self.env["mm.branch.violation"].sudo()
        for period in self.sudo():
            closed = Violation.search([("mm_deduction_period_id", "=", period.id)])
            closed._mm_workflow_write({"state": "approved", "mm_deduction_period_id": False})
            for violation in closed:
                violation.message_post(body=_(
                    "Monthly deductions of %s reset to Draft: violation back to Approved.",
                    period.name))
            period.write({"state": "draft", "confirmed_by_id": False, "confirmed_date": False})
            period.message_post(body=_("Reset to Draft: %s violations released.", len(closed)))
        self._mm_refresh()
        return True

    def action_export_jisr(self):
        self.ensure_one()
        if self.state == "draft":
            self._mm_refresh()
        if not xlsxwriter:
            raise UserError(_("The xlsxwriter Python library is not installed."))
        lines = self.line_ids.sudo().sorted(lambda l: l.employee_id.name or "")
        if not lines:
            raise UserError(_("No approved violations in %s.", self.name))
        missing = lines.filtered(lambda l: not l.employee_number)
        if missing:
            raise UserError(_(
                "These employees have no Employee Number, so Jisr cannot match "
                "them. Fill it on the employee (or run Import Employee Numbers) "
                "first:\n%s", "\n".join(missing.employee_id.mapped("name"))))

        buf = io.BytesIO()
        book = xlsxwriter.Workbook(buf, {"in_memory": True})
        sheet = book.add_worksheet("Jisr")
        if (self.env.user.lang or "").startswith("ar"):
            sheet.right_to_left()
        head = book.add_format({"bold": True, "bg_color": "#DDDDDD", "border": 1})
        pct = book.add_format({"num_format": "0.00"})
        headers = [_("Employee Number"), _("Employee Name"), _("Branch"), _("Month"),
                   _("Violations"), _("Deduction %")]
        for col, title in enumerate(headers):
            sheet.write(0, col, title, head)
        for row, line in enumerate(lines, start=1):
            sheet.write_string(row, 0, line.employee_number or "")
            sheet.write_string(row, 1, line.employee_id.name or "")
            sheet.write_string(row, 2, line.branch_names or "")
            sheet.write_string(row, 3, self.name)
            sheet.write_number(row, 4, line.violation_count)
            sheet.write_number(row, 5, line.deduction_percent, pct)
        sheet.set_column(0, 0, 16)
        sheet.set_column(1, 2, 34)
        sheet.set_column(3, 5, 14)
        sheet.freeze_panes(1, 0)
        book.close()

        attachment = self.env["ir.attachment"].create({
            "name": "jisr_violation_deductions_%s.xlsx" % self.name,
            "datas": base64.b64encode(buf.getvalue()),
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        })
        self.message_post(body=_("Exported for Jisr by %s.", self.env.user.display_name),
                          attachment_ids=attachment.ids)
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }


class MmViolationDeductionLine(models.Model):
    _name = "mm.violation.deduction.line"
    _description = "Monthly Violation Deduction - Employee"
    _order = "deduction_percent desc, id"

    period_id = fields.Many2one(
        "mm.violation.deduction.period", required=True, ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(related="period_id.company_id", store=True)
    state = fields.Selection(related="period_id.state", store=True)
    employee_id = fields.Many2one("hr.employee", required=True, index=True)
    employee_number = fields.Char(related="employee_id.mm_employee_number", string="Employee Number")
    violation_ids = fields.Many2many(
        "mm.branch.violation", "mm_deduction_line_violation_rel",
        "line_id", "violation_id", string="Violations",
    )
    violation_count = fields.Integer(
        string="Violation Count", compute="_compute_figures", store=True,
    )
    total_points = fields.Float(
        string="Total Points", compute="_compute_figures", store=True, digits=(6, 2),
    )
    deduction_percent = fields.Float(
        string="Deduction %", compute="_compute_figures", store=True, digits=(5, 2),
        help="Total points of the employee's violations in the month, capped at 100%.",
    )
    branch_names = fields.Char(string="Branch", compute="_compute_figures", store=True)
    has_carried = fields.Boolean(string="Includes Carried", compute="_compute_figures", store=True)

    _sql_constraints = [
        ("period_employee_uniq", "unique(period_id, employee_id)",
         "An employee can only appear once per month."),
    ]

    @api.depends("violation_ids.points", "violation_ids.branch_id", "violation_ids.mm_is_carried")
    def _compute_figures(self):
        for line in self:
            violations = line.violation_ids
            total = sum(violations.mapped("points"))
            line.violation_count = len(violations)
            line.total_points = total
            line.deduction_percent = min(total, 100.0)
            line.branch_names = ", ".join(violations.branch_id.mapped("display_name"))
            line.has_carried = any(violations.mapped("mm_is_carried"))

    def action_view_violations(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "mm_branch_violation.mm_branch_violation_action")
        action.update({
            "name": _("Violations - %s", self.employee_id.sudo().name),
            "domain": [("id", "in", self.violation_ids.ids)],
            "context": {"create": False},
        })
        return action
