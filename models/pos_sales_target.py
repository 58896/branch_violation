# -*- coding: utf-8 -*-
"""Automatic employee split of the branch commission.

Opt-in per commission plan (``mm_auto_split``). Every time
pos_sales_target_commission refreshes a branch target's commission result
(Confirm / Start / Refresh KPIs / the hourly refresh job), the result is
split among the branch's employees on its own:

- the distribution method is taken from the commission plan (Equal by
  default) when the result has none yet;
- the branch's active employees are added (hr.employee.branch_id);
- the split is recalculated - only when the commission amount or the
  employee lines actually changed, so the hourly job does not rerun it
  (and post to the chatter) for nothing.

This stops as soon as the distribution is submitted for review: from then
on the numbers being checked do not move. Methods that need figures typed
in first (Manual Percentage) only get the employees loaded.
"""
import hashlib
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

AUTO_SPLIT_STATES = ("draft", "calculated")
MANUAL_INPUT_METHODS = ("manual_percentage",)
TARGET_LOCKED_STATES = ("approved", "closed", "cancelled")


class PosCommissionPlan(models.Model):
    _inherit = "pos.commission.plan"

    mm_auto_split = fields.Boolean(
        string="Split Automatically Among Branch Employees", tracking=True,
        help="When the branch commission is calculated or refreshed, add the "
             "branch's employees and split it with the Distribution Method "
             "below (violation deductions included), until the split is "
             "submitted for review. Note: a target whose split has employees "
             "can only be approved after its split is approved.",
    )


class PosSalesTarget(models.Model):
    _inherit = "pos.sales.target"

    def _pstc_ensure_commission_result(self):
        res = super()._pstc_ensure_commission_result()
        self._mm_auto_split_commission()
        return res

    def _mm_auto_split_commission(self):
        for target in self.sudo():
            result = target.commission_result_id
            if not result or not target.commission_plan_id.mm_auto_split \
                    or target.target_level != "branch" or not target.branch_id \
                    or target.state in TARGET_LOCKED_STATES \
                    or result.employee_distribution_state not in AUTO_SPLIT_STATES:
                continue
            try:
                with self.env.cr.savepoint():
                    result._mm_auto_split()
            except UserError as error:
                _logger.info("Auto split skipped for %s: %s", result.display_name, error)
                result.message_post(body=_(
                    "Automatic employee split skipped: %s", error.args[0]))


class PosCommissionResult(models.Model):
    _inherit = "pos.commission.result"

    mm_auto_split_signature = fields.Char(string="Automatic Split Fingerprint", readonly=True, copy=False)

    def _mm_split_signature(self):
        self.ensure_one()
        parts = [self.distribution_method or "", "%.4f" % (self.final_amount or 0.0)]
        for line in self.employee_line_ids.sorted("id"):
            parts.append("%s:%s:%s:%s" % (
                line.employee_id.id, int(line.is_excluded),
                line.eligible_from or "", line.eligible_to or ""))
        return hashlib.sha1("|".join(parts).encode()).hexdigest()

    def _mm_auto_split(self):
        self.ensure_one()
        plan = self.target_id.commission_plan_id
        if not self.distribution_method and plan:
            self.write({
                "distribution_method": plan.distribution_method or "equal",
                "working_time_basis": plan.working_time_basis,
                "hybrid_individual_pct": plan.hybrid_individual_pct,
                "hybrid_shared_pct": plan.hybrid_shared_pct,
                "hybrid_shared_basis": plan.hybrid_shared_basis,
            })
        self.action_load_eligible_employees()
        if not self.employee_line_ids or not self.distribution_method \
                or self.distribution_method in MANUAL_INPUT_METHODS:
            return
        signature = self._mm_split_signature()
        if self.employee_distribution_state == "calculated" and \
                signature == self.mm_auto_split_signature:
            return
        self._pstc_run_distribution(_("Automatic split"))
        self.mm_auto_split_signature = signature
