# -*- coding: utf-8 -*-
from odoo import fields, models


class MmViolationCancelWizard(models.TransientModel):
    _name = "mm.violation.cancel.wizard"
    _description = "Cancel Branch Violation"

    violation_ids = fields.Many2many("mm.branch.violation", required=True)
    reason = fields.Text(string="Cancellation Reason", required=True)

    def action_confirm(self):
        self.ensure_one()
        self.violation_ids.action_cancel(self.reason)
        return {"type": "ir.actions.act_window_close"}
