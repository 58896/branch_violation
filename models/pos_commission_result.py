# -*- coding: utf-8 -*-
import base64
import io

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .pos_commission_employee_line import LOCKED_DISTRIBUTION_STATES, period_label

try:
    import xlsxwriter
except ImportError:  # pragma: no cover - declared in external_dependencies
    xlsxwriter = None

VIEW_COMMISSION_GROUP = "pos_sales_target_commission.group_pos_target_view_commission"


class PosCommissionResult(models.Model):
    _inherit = "pos.commission.result"

    total_violation_deduction = fields.Monetary(
        string="Violation Deductions", currency_field="currency_id",
        compute="_compute_violation_totals", store=True,
    )
    total_net_commission = fields.Monetary(
        string="Net Commission (after violations)", currency_field="currency_id",
        compute="_compute_violation_totals", store=True,
    )

    @api.depends("employee_line_ids.deduction_amount", "employee_line_ids.net_commission_amount")
    def _compute_violation_totals(self):
        for result in self:
            lines = result.employee_line_ids
            result.total_violation_deduction = sum(lines.mapped("deduction_amount"))
            result.total_net_commission = sum(lines.mapped("net_commission_amount"))

    # ------------------------------------------------------------------
    # Hooks into the distribution workflow (pos_sales_target_commission)
    # ------------------------------------------------------------------
    def _pstc_run_distribution(self, reason=None):
        res = super()._pstc_run_distribution(reason)
        self.employee_line_ids._mm_refresh_violation_deductions()
        return res

    def action_submit_distribution_review(self):
        self.employee_line_ids._mm_refresh_violation_deductions()
        return super().action_submit_distribution_review()

    def action_approve_distribution(self):
        # final refresh while the lines are still open, then freeze
        self.employee_line_ids._mm_refresh_violation_deductions()
        res = super().action_approve_distribution()
        self.employee_line_ids._mm_lock_violation_deductions()
        return res

    def action_cancel_distribution(self):
        locked = self.employee_line_ids.filtered("mm_deduction_locked")
        res = super().action_cancel_distribution()
        locked._mm_release_violation_deductions()
        return res

    def action_mm_refresh_violations(self):
        self.ensure_one()
        if self.employee_distribution_state in LOCKED_DISTRIBUTION_STATES:
            raise UserError(_("The distribution is approved: its violation deductions are locked."))
        self.employee_line_ids._mm_refresh_violation_deductions()
        return True

    # ------------------------------------------------------------------
    # Export for Jisr
    # ------------------------------------------------------------------
    def _mm_jisr_rows(self):
        self.ensure_one()
        rows = []
        for line in self.employee_line_ids.sudo().sorted(lambda l: l.employee_id.name or ""):
            rows.append({
                "number": line.employee_id.mm_employee_number or "",
                "name": line.employee_id.name or "",
                "branch": line.branch_id.display_name or "",
                "period": period_label(line.date_from, line.date_to),
                "gross": line.final_commission_amount,
                "percent": line.deduction_percent,
                "deduction": line.deduction_amount,
                "net": line.net_commission_amount,
            })
        return rows

    def action_mm_export_jisr(self):
        self.ensure_one()
        if not self.env.user.has_group(VIEW_COMMISSION_GROUP):
            raise UserError(_("Exporting commissions requires the View Commission permission."))
        if self.employee_distribution_state not in LOCKED_DISTRIBUTION_STATES:
            raise UserError(_(
                "Only an Approved or Transferred distribution can be exported for Jisr."))
        if not xlsxwriter:
            raise UserError(_("The xlsxwriter Python library is not installed."))
        rows = self._mm_jisr_rows()
        if not rows:
            raise UserError(_("This distribution has no employee lines."))
        missing = [r["name"] for r in rows if not r["number"]]
        if missing:
            raise UserError(_(
                "These employees have no Employee Number, so Jisr cannot match "
                "them. Fill it on the employee (or run Import Employee Numbers) "
                "first:\n%s", "\n".join(missing)))

        buf = io.BytesIO()
        book = xlsxwriter.Workbook(buf, {"in_memory": True})
        sheet = book.add_worksheet("Jisr")
        if (self.env.user.lang or "").startswith("ar"):
            sheet.right_to_left()
        head = book.add_format({"bold": True, "bg_color": "#DDDDDD", "border": 1})
        digits = self.currency_id.decimal_places if self.currency_id else 2
        money = book.add_format({"num_format": "#,##0." + "0" * digits if digits else "#,##0"})
        pct = book.add_format({"num_format": "0.00"})
        headers = [
            _("Employee Number"), _("Employee Name"), _("Branch"), _("Period"),
            _("Gross Commission"), _("Violation %"), _("Deduction Amount"),
            _("Net Commission"),
        ]
        for col, title in enumerate(headers):
            sheet.write(0, col, title, head)
        for idx, row in enumerate(rows, start=1):
            sheet.write_string(idx, 0, row["number"])
            sheet.write_string(idx, 1, row["name"])
            sheet.write_string(idx, 2, row["branch"])
            sheet.write_string(idx, 3, row["period"])
            sheet.write_number(idx, 4, row["gross"], money)
            sheet.write_number(idx, 5, row["percent"], pct)
            sheet.write_number(idx, 6, row["deduction"], money)
            sheet.write_number(idx, 7, row["net"], money)
        sheet.set_column(0, 0, 16)
        sheet.set_column(1, 2, 32)
        sheet.set_column(3, 7, 16)
        sheet.freeze_panes(1, 0)
        book.close()

        filename = "jisr_commission_%s_%s.xlsx" % (
            (self.branch_id.code or "branch").replace("/", "-"),
            period_label(self.target_id.date_from, self.target_id.date_to).replace(" → ", "_"))
        attachment = self.env["ir.attachment"].create({
            "name": filename,
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
