# -*- coding: utf-8 -*-
"""Branch / employee violation log.

Workflow: draft -> under_review -> approved -> closed, plus cancelled.

- Only ``approved`` and ``closed`` violations count toward deductions.
- ``closed`` is set when the commission distribution that counted the
  violation is approved (see pos_commission_employee_line.py). Closed
  records are read-only for everyone.
- A violation approved after its commission period was already settled is
  "carried forward" to the employee's next open period.
- Workflow fields can only change through the action methods (they write
  with ``MM_WORKFLOW_CTX``); a plain ``write()`` touching them is refused.
- Officers can only edit their own drafts: the record rule limits write
  access, and ``write()`` re-checks the state so the lock does not depend
  on the view.

Customer response (complaints from a customer: the source has "Needs
Customer Response" ticked, or the note type is Customer Complaint) has its
own status, independent of the approval workflow above: Awaiting Response
-> Customer Contacted -> Resolved / Unresolved. Customer Service sets it
with buttons, and may edit the customer/response fields in any state. The
deadline is the company's "Customer Response Deadline (Hours)" counted from
when the complaint was received; an hourly cron reminds the managers of
complaints still not contacted after it.
"""
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .pos_commission_employee_line import period_label

OFFICER_GROUP = "mm_branch_violation.group_violation_officer"
MANAGER_GROUP = "mm_branch_violation.group_violation_manager"
CUSTOMER_SERVICE_GROUP = "mm_branch_violation.group_violation_customer_service"
RESPONSE_ACTIVITY = "mm_branch_violation.mail_activity_type_customer_response"

# Context key the workflow actions (and the commission hooks) use to write
# the workflow fields below. Never set it from a view or RPC-reachable default.
MM_WORKFLOW_CTX = "mm_violation_workflow_write"

NOTE_TYPES = [
    ("financial", "Financial"),
    ("operational", "Operational"),
    ("customer_complaint", "Customer Complaint"),
    ("government", "Government Violation"),
]
ENTERING_DEPARTMENTS = [
    ("finance", "Finance"),
    ("operations", "Operations"),
    ("customer_service", "Customer Service"),
]
VIOLATION_STATES = [
    ("draft", "New"),
    ("under_review", "Under Review"),
    ("approved", "Approved"),
    ("closed", "Closed"),
    ("cancelled", "Cancelled"),
]
COUNTED_STATES = ("approved", "closed")
DEDUCTION_STATUSES = [
    ("none", "Not Counted"),
    ("pending", "Pending Deduction"),
    ("counted", "In an Open Commission"),
    ("deducted", "Deducted"),
]

RESPONSE_STATES = [
    ("pending", "Awaiting Response"),
    ("contacted", "Customer Contacted"),
    ("resolved", "Resolved"),
    ("unresolved", "Unresolved"),
]
# set only by the response buttons / cron
RESPONSE_WORKFLOW_FIELDS = {
    "response_state", "response_contacted_by_id", "response_contacted_date",
    "response_closed_by_id", "response_closed_date",
    "response_overdue_notified",
}
# Customer Service may edit these on any complaint, whatever its state
RESPONSE_EDIT_FIELDS = {
    "customer_name", "customer_phone", "response_user_id", "response_action",
}

WORKFLOW_FIELDS = {
    "state", "submitted_by_id", "submitted_date", "approved_by_id",
    "approved_date", "cancel_reason",
    "mm_commission_line_id", "mm_deduction_period_id", "mm_is_carried",
    "mm_carried_from_date_from", "mm_carried_from_date_to",
} | RESPONSE_WORKFLOW_FIELDS
# changes that move a violation between commission lines
COMMISSION_REFRESH_FIELDS = {"employee_id", "date", "points", "violation_type_id"}


class MmBranchViolation(models.Model):
    _name = "mm.branch.violation"
    _description = "Branch Violation"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(
        string="Reference", default="/", copy=False, readonly=True, index=True,
    )
    date = fields.Date(
        required=True, index=True, tracking=True,
        default=fields.Date.context_today,
    )
    company_id = fields.Many2one(
        "res.company", required=True, index=True,
        default=lambda self: self.env.company,
    )
    branch_id = fields.Many2one(
        "company.branch", string="Branch", required=True, index=True,
        tracking=True, check_company=True, ondelete="restrict",
        default=lambda self: self.env.user.default_branch_id,
    )
    note_type = fields.Selection(NOTE_TYPES, required=True, tracking=True)
    entering_department = fields.Selection(
        ENTERING_DEPARTMENTS, string="Entering Department", required=True,
    )
    employee_id = fields.Many2one(
        "hr.employee", string="Employee", required=True, index=True,
        tracking=True, check_company=True, ondelete="restrict",
    )
    employee_number = fields.Char(
        related="employee_id.mm_employee_number", store=True,
        string="Employee Number",
    )
    job_id = fields.Many2one(
        related="employee_id.job_id", store=True, string="Job Position",
    )
    # related fields read as superuser: officers usually have no HR rights,
    # and hr.employee refuses non-public fields to them.
    employee_user_id = fields.Many2one(related="employee_id.user_id")
    source_id = fields.Many2one(
        "mm.violation.source", string="Source", required=True,
        ondelete="restrict",
    )
    violation_type_id = fields.Many2one(
        "mm.violation.type", string="Violation", required=True, index=True,
        tracking=True, ondelete="restrict",
    )
    points = fields.Float(
        string="Deduction Points (%)", digits=(5, 2), tracking=True,
        compute="_compute_points", store=True, readonly=False, precompute=True,
        help="Proposed from the violation type. Only a Violation Manager "
             "can change it.",
    )
    description = fields.Text()
    notes = fields.Text()

    state = fields.Selection(
        VIOLATION_STATES, default="draft", required=True, index=True,
        tracking=True, copy=False,
    )
    submitted_by_id = fields.Many2one(
        "res.users", string="Submitted By", readonly=True, copy=False,
    )
    submitted_date = fields.Datetime(readonly=True, copy=False)
    approved_by_id = fields.Many2one(
        "res.users", string="Approved By", readonly=True, copy=False,
    )
    approved_date = fields.Datetime(readonly=True, copy=False)
    cancel_reason = fields.Text(readonly=True, copy=False, tracking=True)

    # ---- commission deduction ----------------------------------------
    mm_commission_line_id = fields.Many2one(
        "pos.commission.employee.line", string="Deducted On", readonly=True,
        copy=False, index=True, ondelete="set null",
        help="The commission line whose approval closed this violation.",
    )
    mm_commission_line_ids = fields.Many2many(
        "pos.commission.employee.line", "mm_commission_line_violation_rel",
        "violation_id", "line_id", string="Counted On", readonly=True, copy=False,
    )
    mm_deduction_period_id = fields.Many2one(
        "mm.violation.deduction.period", string="Deducted In Month", readonly=True,
        copy=False, index=True, ondelete="set null",
        help="The confirmed monthly deductions (for Jisr) that closed this violation.",
    )
    mm_deduction_line_ids = fields.Many2many(
        "mm.violation.deduction.line", "mm_deduction_line_violation_rel",
        "violation_id", "line_id", string="Monthly Deduction Lines", readonly=True, copy=False,
    )
    mm_is_carried = fields.Boolean(
        string="Carried Forward", readonly=True, copy=False, index=True,
        help="Approved after its own commission period was already settled, "
             "so it is deducted in the employee's next open period instead.",
    )
    mm_carried_from_date_from = fields.Date(
        string="Carried Period Start", readonly=True, copy=False)
    mm_carried_from_date_to = fields.Date(
        string="Carried Period End", readonly=True, copy=False)
    mm_carried_from_label = fields.Char(
        string="Carried From", compute="_compute_mm_carried_from_label",
    )
    mm_deduction_status = fields.Selection(
        DEDUCTION_STATUSES, string="Deduction", compute="_compute_mm_deduction_status",
        store=True, index=True,
    )

    # ---- customer response ---------------------------------------------
    needs_response = fields.Boolean(
        string="Needs Customer Response", compute="_compute_needs_response",
        store=True, index=True,
    )
    customer_name = fields.Char(tracking=True)
    customer_phone = fields.Char(tracking=True)
    complaint_received_at = fields.Datetime(
        string="Complaint Received", default=fields.Datetime.now, copy=False,
        help="When the customer complained. The response deadline counts "
             "from here.",
    )
    response_deadline = fields.Datetime(
        string="Response Deadline", compute="_compute_response_deadline",
        store=True, copy=False,
    )
    response_state = fields.Selection(
        RESPONSE_STATES, string="Response Status",
        compute="_compute_response_state", store=True, index=True,
        tracking=True, copy=False,
    )
    response_user_id = fields.Many2one(
        "res.users", string="Assigned To", tracking=True, copy=False,
        domain=lambda self: [("groups_id", "in", self.env.ref(CUSTOMER_SERVICE_GROUP).ids)],
        help="Customer Service person who must call the customer back. "
             "Empty: every Customer Service user gets the reminder.",
    )
    response_action = fields.Text(
        string="Action Taken", copy=False,
        help="What was said / done for the customer.",
    )
    response_contacted_by_id = fields.Many2one(
        "res.users", string="Contacted By", readonly=True, copy=False,
    )
    response_contacted_date = fields.Datetime(
        string="Contacted On", readonly=True, copy=False,
    )
    response_closed_by_id = fields.Many2one(
        "res.users", string="Closed By", readonly=True, copy=False,
        help="Who marked the complaint Resolved or Unresolved.",
    )
    response_closed_date = fields.Datetime(
        string="Closed On", readonly=True, copy=False,
    )
    response_late = fields.Boolean(
        string="Late Response", compute="_compute_response_late", store=True,
        help="The customer was contacted after the deadline.",
    )
    response_overdue = fields.Boolean(
        string="Overdue", compute="_compute_response_overdue",
        search="_search_response_overdue",
        help="Deadline passed and the customer has not been contacted yet.",
    )
    response_overdue_notified = fields.Boolean(readonly=True, copy=False)

    is_readonly = fields.Boolean(compute="_compute_access_flags")
    can_edit_points = fields.Boolean(compute="_compute_access_flags")
    can_edit_response = fields.Boolean(compute="_compute_access_flags")

    _sql_constraints = [
        ("points_range", "CHECK(points >= 0 AND points <= 100)",
         "Deduction points must be between 0 and 100."),
    ]

    # ------------------------------------------------------------------
    @api.depends("violation_type_id")
    def _compute_points(self):
        for rec in self:
            rec.points = rec.violation_type_id.points

    @api.depends("mm_carried_from_date_from", "mm_carried_from_date_to")
    def _compute_mm_carried_from_label(self):
        for rec in self:
            rec.mm_carried_from_label = period_label(
                rec.mm_carried_from_date_from, rec.mm_carried_from_date_to)

    @api.depends("state", "mm_commission_line_ids", "mm_deduction_line_ids")
    def _compute_mm_deduction_status(self):
        for rec in self:
            if rec.state == "closed":
                rec.mm_deduction_status = "deducted"
            elif rec.state != "approved":
                rec.mm_deduction_status = "none"
            elif rec.sudo().mm_commission_line_ids or rec.sudo().mm_deduction_line_ids:
                rec.mm_deduction_status = "counted"
            else:
                rec.mm_deduction_status = "pending"

    @api.depends("state", "needs_response")
    @api.depends_context("uid")
    def _compute_access_flags(self):
        is_manager = self.env.user.has_group(MANAGER_GROUP)
        is_cs = self.env.user.has_group(CUSTOMER_SERVICE_GROUP)
        for rec in self:
            locked = rec.state in ("closed", "cancelled") or (
                rec.state != "draft" and not is_manager)
            rec.is_readonly = locked
            rec.can_edit_points = is_manager and not locked
            rec.can_edit_response = (is_cs and rec.needs_response) or not locked

    @api.depends("source_id.requires_response", "note_type")
    def _compute_needs_response(self):
        for rec in self:
            rec.needs_response = bool(
                rec.source_id.requires_response
                or rec.note_type == "customer_complaint")

    @api.depends("needs_response")
    def _compute_response_state(self):
        for rec in self:
            if not rec.needs_response:
                rec.response_state = False
            elif not rec.response_state:
                rec.response_state = "pending"

    @api.depends("needs_response", "complaint_received_at", "company_id")
    def _compute_response_deadline(self):
        # the hours are read when the complaint is received: changing the
        # setting later does not move existing deadlines
        for rec in self:
            if rec.needs_response and rec.complaint_received_at:
                hours = rec.company_id.mm_response_deadline_hours or 24
                rec.response_deadline = rec.complaint_received_at + timedelta(hours=hours)
            else:
                rec.response_deadline = False

    @api.depends("response_contacted_date", "response_deadline")
    def _compute_response_late(self):
        for rec in self:
            rec.response_late = bool(
                rec.response_deadline and rec.response_contacted_date
                and rec.response_contacted_date > rec.response_deadline)

    @api.depends("response_state", "response_deadline")
    def _compute_response_overdue(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.response_overdue = bool(
                rec.response_state == "pending" and rec.response_deadline
                and rec.response_deadline < now)

    def _search_response_overdue(self, operator, value):
        if operator not in ("=", "!="):
            raise UserError(_("Unsupported search on Overdue."))
        now = fields.Datetime.now()
        if (operator == "=") == bool(value):
            return [("response_state", "=", "pending"),
                    ("response_deadline", "<", now)]
        return ["|", ("response_state", "!=", "pending"),
                "|", ("response_deadline", "=", False),
                ("response_deadline", ">=", now)]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(MM_WORKFLOW_CTX):
            for vals in vals_list:
                touched = {f for f in WORKFLOW_FIELDS if vals.get(f)}
                if vals.get("state") == "draft":
                    touched.discard("state")
                if touched:
                    raise UserError(_(
                        "A violation is always created as New. Use the "
                        "workflow buttons to change its status."))
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "mm.branch.violation",
                    sequence_date=vals.get("date") or fields.Date.context_today(self),
                ) or "/"
        records = super().create(vals_list)
        records._mm_check_points_override()
        records.filtered("needs_response")._mm_schedule_response_activities()
        return records

    def write(self, vals):
        if self.env.context.get(MM_WORKFLOW_CTX):
            return super().write(vals)
        # chatter/activity bookkeeping is not a business edit
        touched = {k for k in vals if not k.startswith(("message_", "activity_"))}
        if not touched:
            return super().write(vals)
        if touched & WORKFLOW_FIELDS:
            raise UserError(_(
                "The status and approval fields can only be changed with the "
                "workflow buttons."))
        if touched <= RESPONSE_EDIT_FIELDS and \
                self.env.user.has_group(CUSTOMER_SERVICE_GROUP):
            # Customer Service follows the customer up whatever the
            # approval status: only the response fields, only complaints
            if not all(self.mapped("needs_response")):
                raise UserError(_(
                    "Only customer complaints have a customer response."))
            res = super().write(vals)
            if "response_user_id" in touched:
                self._mm_reassign_response_activities()
            return res
        if any(rec.state == "closed" for rec in self):
            raise UserError(_(
                "Closed violations are locked: the commission period that "
                "counted them has already been settled."))
        if any(rec.state == "cancelled" for rec in self):
            raise UserError(_(
                "Cancelled violations cannot be edited. Reset them to New first."))
        if not self.env.user.has_group(MANAGER_GROUP) and \
                any(rec.state != "draft" for rec in self):
            raise AccessError(_(
                "Only New violations can be edited. Once submitted, only a "
                "Violation Manager can change them."))
        # Customer Service may write any record (for the response fields):
        # everything else stays limited to one's own drafts
        if not self.env.su and not self.env.user.has_group(MANAGER_GROUP) and \
                any(rec.create_uid != self.env.user for rec in self):
            raise AccessError(_("You can only edit the violations you created."))
        old_employees = self.employee_id
        was_needing = {rec.id for rec in self if rec.needs_response}
        res = super().write(vals)
        newly_needing = self.filtered(
            lambda r: r.needs_response and r.id not in was_needing)
        newly_needing._mm_schedule_response_activities()
        if "response_user_id" in touched:
            (self - newly_needing).filtered("needs_response")._mm_reassign_response_activities()
        if {"points", "violation_type_id"} & touched:
            self._mm_check_points_override()
        if COMMISSION_REFRESH_FIELDS & touched and \
                any(rec.state == "approved" for rec in self):
            self._mm_refresh_commission_lines(old_employees)
        return res

    def unlink(self):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(_("Only a Violation Manager can delete violations."))
        if any(rec.state != "draft" for rec in self):
            raise UserError(_(
                "Only New violations can be deleted. Cancel the others instead."))
        return super().unlink()

    def _mm_check_points_override(self):
        if self.env.su or self.env.user.has_group(MANAGER_GROUP):
            return
        for rec in self:
            if rec.violation_type_id and \
                    rec.points != rec.violation_type_id.points:
                raise AccessError(_(
                    "Only a Violation Manager can change the deduction "
                    "points of a violation."))

    def _mm_workflow_write(self, vals):
        return self.with_context(**{MM_WORKFLOW_CTX: True}).write(vals)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def _mm_check_group(self, group, message):
        if not self.env.user.has_group(group):
            raise AccessError(message)

    def action_submit(self):
        self._mm_check_group(OFFICER_GROUP, _(
            "Only Violation Officers can submit violations."))
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only New violations can be submitted."))
        # Officers may only submit what they may edit (their own drafts)
        self.check_access("write")
        if not self.env.user.has_group(MANAGER_GROUP) and \
                any(rec.create_uid != self.env.user for rec in self):
            raise AccessError(_("You can only submit the violations you created."))
        self._mm_workflow_write({
            "state": "under_review",
            "submitted_by_id": self.env.user.id,
            "submitted_date": fields.Datetime.now(),
        })
        self._mm_schedule_review_activities()
        return True

    def action_approve(self):
        self._mm_check_group(MANAGER_GROUP, _(
            "Only a Violation Manager can approve violations."))
        user = self.env.user
        for rec in self:
            if rec.state != "under_review":
                raise UserError(_("Only violations Under Review can be approved."))
            if rec.submitted_by_id == user:
                raise UserError(_(
                    "%s: you submitted this violation, so another manager "
                    "must approve it (four-eyes rule).", rec.name))
            if rec.employee_user_id == user:
                raise UserError(_(
                    "%s: you cannot approve a violation recorded against "
                    "yourself.", rec.name))
        self._mm_workflow_write({
            "state": "approved",
            "approved_by_id": user.id,
            "approved_date": fields.Datetime.now(),
        })
        self.sudo().activity_feedback(
            ["mail.mail_activity_data_todo"],
            feedback=_("Approved by %s", user.display_name))
        self._mm_carry_if_period_settled()
        self._mm_refresh_commission_lines()
        return True

    def action_open_cancel_wizard(self):
        self._mm_check_group(MANAGER_GROUP, _(
            "Only a Violation Manager can cancel violations."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Cancel Violation"),
            "res_model": "mm.violation.cancel.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_violation_ids": self.ids},
        }

    def action_cancel(self, reason):
        self._mm_check_group(MANAGER_GROUP, _(
            "Only a Violation Manager can cancel violations."))
        reason = (reason or "").strip()
        if not reason:
            raise UserError(_("A cancellation reason is required."))
        for rec in self:
            if rec.state == "closed":
                raise UserError(_(
                    "%s is Closed: its commission period is already settled, "
                    "so it cannot be cancelled.", rec.name))
            if rec.state == "cancelled":
                raise UserError(_("%s is already cancelled.", rec.name))
        self._mm_workflow_write({"state": "cancelled", "cancel_reason": reason})
        self.sudo().activity_unlink(["mail.mail_activity_data_todo"])
        self._mm_refresh_commission_lines()
        return True

    def action_reset_draft(self):
        self._mm_check_group(MANAGER_GROUP, _(
            "Only a Violation Manager can reset violations to New."))
        for rec in self:
            if rec.state not in ("under_review", "cancelled"):
                raise UserError(_(
                    "Only violations Under Review or Cancelled can be reset to New."))
        self._mm_workflow_write({
            "state": "draft",
            "submitted_by_id": False,
            "submitted_date": False,
            "approved_by_id": False,
            "approved_date": False,
            "cancel_reason": False,
            "mm_is_carried": False,
            "mm_carried_from_date_from": False,
            "mm_carried_from_date_to": False,
        })
        self.sudo().activity_unlink(["mail.mail_activity_data_todo"])
        self._mm_refresh_commission_lines()
        return True

    def _mm_review_users(self):
        """Managers who can approve this violation: same company, and
        neither the submitter nor the employee concerned."""
        self.ensure_one()
        managers = self.env.ref(MANAGER_GROUP).sudo().users.filtered(
            lambda u: u.active and not u.share and self.company_id in u.company_ids)
        return managers - self.submitted_by_id - self.employee_user_id

    def _mm_schedule_review_activities(self):
        for rec in self:
            for manager in rec._mm_review_users():
                rec.sudo().activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=manager.id,
                    summary=_("Review violation %s", rec.name),
                    note=_("%(emp)s - %(type)s (%(points)s%%)",
                           emp=rec.employee_id.sudo().name,
                           type=rec.violation_type_id.name,
                           points=rec.points),
                )

    # ------------------------------------------------------------------
    # Customer response
    # ------------------------------------------------------------------
    def _mm_check_response(self, allowed_states, message):
        self._mm_check_group(CUSTOMER_SERVICE_GROUP, _(
            "Only Customer Service can update the customer response."))
        for rec in self:
            if not rec.needs_response:
                raise UserError(_(
                    "%s is not a customer complaint: it has no customer "
                    "response.", rec.name))
            if rec.response_state not in allowed_states:
                raise UserError(message)

    def action_response_contacted(self):
        self._mm_check_response(("pending",), _(
            "Only complaints Awaiting Response can be marked as contacted."))
        self._mm_workflow_write({
            "response_state": "contacted",
            "response_contacted_by_id": self.env.user.id,
            "response_contacted_date": fields.Datetime.now(),
        })
        self._mm_done_response_activities(_("Customer contacted by %s",
                                            self.env.user.display_name))
        return True

    def _mm_close_response(self, state):
        self._mm_check_response(("pending", "contacted"), _(
            "This complaint is already closed. Reopen it first."))
        for rec in self:
            if not (rec.response_action or "").strip():
                raise UserError(_(
                    "%s: write the Action Taken before closing the "
                    "complaint.", rec.name))
        now = fields.Datetime.now()
        user = self.env.user
        for rec in self:
            vals = {
                "response_state": state,
                "response_closed_by_id": user.id,
                "response_closed_date": now,
            }
            # closed on the first call: that call is the contact too
            if not rec.response_contacted_date:
                vals.update(response_contacted_by_id=user.id,
                            response_contacted_date=now)
            rec._mm_workflow_write(vals)
        self._mm_done_response_activities(
            _("Complaint closed by %s", user.display_name))
        return True

    def action_response_resolved(self):
        return self._mm_close_response("resolved")

    def action_response_unresolved(self):
        return self._mm_close_response("unresolved")

    def action_response_reopen(self):
        self._mm_check_response(("resolved", "unresolved"), _(
            "Only Resolved or Unresolved complaints can be reopened."))
        self._mm_workflow_write({
            "response_state": "contacted",
            "response_closed_by_id": False,
            "response_closed_date": False,
        })
        return True

    def _mm_response_users(self):
        """Who gets the 'call the customer back' reminder: the assignee,
        else every Customer Service user of the company who is not a
        manager (managers get the overdue reminder instead), else the
        managers."""
        self.ensure_one()
        if self.response_user_id:
            return self.response_user_id
        company_users = self.env.ref(CUSTOMER_SERVICE_GROUP).sudo().users.filtered(
            lambda u: u.active and not u.share and self.company_id in u.company_ids)
        managers = company_users.filtered(lambda u: u.has_group(MANAGER_GROUP))
        return (company_users - managers) or managers

    def _mm_received_date(self, user):
        """The to-do is due the day the complaint came in (in the user's
        timezone), not on the response deadline: Odoo's activity counter
        only shows what is due today or overdue, and a callback is urgent.
        The deadline itself is tracked by response_deadline / the cron."""
        tz = pytz.timezone(user.tz or "UTC")
        received = self.complaint_received_at or fields.Datetime.now()
        return pytz.utc.localize(received).astimezone(tz).date()

    def _mm_schedule_response_activities(self):
        for rec in self.filtered(lambda r: r.response_state == "pending"):
            for user in rec._mm_response_users():
                rec.sudo().activity_schedule(
                    RESPONSE_ACTIVITY,
                    date_deadline=rec._mm_received_date(user),
                    user_id=user.id,
                    summary=_("Call the customer back: %s", rec.name),
                    note=_("%(customer)s %(phone)s - branch %(branch)s",
                           customer=rec.customer_name or "",
                           phone=rec.customer_phone or "",
                           branch=rec.branch_id.name),
                )

    def _mm_reassign_response_activities(self):
        pending = self.filtered(lambda r: r.response_state == "pending")
        pending.sudo().activity_unlink([RESPONSE_ACTIVITY])
        pending._mm_schedule_response_activities()

    def _mm_done_response_activities(self, feedback):
        self.sudo().activity_feedback([RESPONSE_ACTIVITY], feedback=feedback)

    @api.model
    def _cron_mm_response_overdue(self):
        overdue = self.sudo().search([
            ("response_overdue", "=", True),
            ("response_overdue_notified", "=", False),
        ])
        Group = self.env.ref(MANAGER_GROUP).sudo()
        for rec in overdue:
            managers = Group.users.filtered(
                lambda u: u.active and not u.share and rec.company_id in u.company_ids)
            for manager in managers:
                rec.activity_schedule(
                    RESPONSE_ACTIVITY,
                    date_deadline=fields.Date.context_today(rec),
                    user_id=manager.id,
                    summary=_("Overdue: customer not contacted (%s)", rec.name),
                )
            rec.message_post(body=_(
                "Response deadline passed: the customer has not been "
                "contacted yet."))
            rec._mm_workflow_write({"response_overdue_notified": True})
        return True

    # ------------------------------------------------------------------
    # Commission deduction
    # ------------------------------------------------------------------
    def _mm_refresh_commission_lines(self, employees=None):
        employees = (employees or self.env["hr.employee"]) | self.employee_id
        self.env["pos.commission.employee.line"]._mm_refresh_for_employees(employees)
        self.env["mm.violation.deduction.period"]._mm_refresh_open_periods(self.company_id)

    def _mm_carry_if_period_settled(self):
        """Approved after the commission of its own period was approved:
        never touch that paid period, carry to the next open one."""
        Line = self.env["pos.commission.employee.line"].sudo()
        Period = self.env["mm.violation.deduction.period"].sudo()
        for rec in self.sudo():
            # settled either by a confirmed month for Jisr, or by an
            # approved employee commission distribution
            settled = Period.search([
                ("company_id", "=", rec.company_id.id),
                ("state", "=", "confirmed"),
                ("date_from", "<=", rec.date),
                ("date_to", ">=", rec.date),
            ], limit=1) or Line.search([
                ("employee_id", "=", rec.employee_id.id),
                ("mm_deduction_locked", "=", True),
                ("date_from", "<=", rec.date),
                ("date_to", ">=", rec.date),
            ], order="date_to desc", limit=1)
            if not settled:
                continue
            rec._mm_workflow_write({
                "mm_is_carried": True,
                "mm_carried_from_date_from": settled.date_from,
                "mm_carried_from_date_to": settled.date_to,
            })
            rec.message_post(body=_(
                "Approved after the commission for %s was already settled: it "
                "will be deducted in the employee's next open commission period.",
                rec.mm_carried_from_label))

    @api.model
    def _mm_counted_domain(self, employee, date_from, date_to):
        """Violations that count toward an employee's deduction for a
        period (the employee smart button)."""
        return [
            ("employee_id", "in", employee.ids),
            ("state", "in", list(COUNTED_STATES)),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
        ]
