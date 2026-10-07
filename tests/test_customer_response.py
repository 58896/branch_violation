# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged, new_test_user

from .common import MmViolationCommon


@tagged("post_install", "-at_install")
class TestCustomerResponse(MmViolationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cs = new_test_user(
            cls.env, login="mmv_cs",
            groups="mm_branch_violation.group_violation_customer_service")
        cls.cs2 = new_test_user(
            cls.env, login="mmv_cs2",
            groups="mm_branch_violation.group_violation_customer_service")
        cls.call_source = cls.env["mm.violation.source"].create({
            "name": "MMV call", "requires_response": True,
        })
        cls.company.mm_response_deadline_hours = 24
        cls.activity_type = cls.env.ref(
            "mm_branch_violation.mail_activity_type_customer_response")

    def _complaint(self, user=None, **overrides):
        overrides.setdefault("source_id", self.call_source.id)
        overrides.setdefault("customer_name", "Ahmed")
        overrides.setdefault("customer_phone", "0500000000")
        return self._create(user=user or self.officer, **overrides)

    def _response_activities(self, rec):
        return rec.activity_ids.filtered(
            lambda a: a.activity_type_id == self.activity_type)

    # ------------------------------------------------------------ detection
    def test_needs_response_from_source_or_note_type(self):
        self.assertFalse(self._create(user=self.officer).needs_response)
        self.assertFalse(self._create(user=self.officer).response_state)
        by_source = self._complaint()
        self.assertTrue(by_source.needs_response)
        self.assertEqual(by_source.response_state, "pending")
        by_type = self._create(user=self.officer, note_type="customer_complaint")
        self.assertTrue(by_type.needs_response)
        self.assertEqual(by_type.response_state, "pending")

    def test_deadline_uses_company_hours(self):
        rec = self._complaint()
        self.assertEqual(rec.response_deadline,
                         rec.complaint_received_at + timedelta(hours=24))
        self.company.mm_response_deadline_hours = 48
        later = self._complaint()
        self.assertEqual(later.response_deadline,
                         later.complaint_received_at + timedelta(hours=48))
        # the old complaint keeps its deadline
        self.assertEqual(rec.response_deadline,
                         rec.complaint_received_at + timedelta(hours=24))

    # ------------------------------------------------------------- workflow
    def test_contact_then_resolve(self):
        rec = self._complaint()
        self.assertEqual(self._response_activities(rec).user_id, self.cs | self.cs2)
        # due today so it shows on the activity counter straight away
        self.assertEqual(set(self._response_activities(rec).mapped("date_deadline")),
                         {fields.Date.context_today(rec)})
        rec.with_user(self.cs).action_response_contacted()
        self.assertEqual(rec.response_state, "contacted")
        self.assertEqual(rec.response_contacted_by_id, self.cs)
        self.assertFalse(rec.response_late)
        self.assertFalse(self._response_activities(rec))
        with self.assertRaises(UserError):
            rec.with_user(self.cs).action_response_resolved()  # no action text
        rec.with_user(self.cs).response_action = "Apologised, free meal voucher"
        rec.with_user(self.cs).action_response_resolved()
        self.assertEqual(rec.response_state, "resolved")
        self.assertEqual(rec.response_closed_by_id, self.cs)
        rec.with_user(self.cs).action_response_reopen()
        self.assertEqual(rec.response_state, "contacted")
        self.assertFalse(rec.response_closed_by_id)

    def test_resolve_on_first_call_sets_contact(self):
        rec = self._complaint(response_action="Refunded on the call")
        rec.with_user(self.cs).action_response_unresolved()
        self.assertEqual(rec.response_state, "unresolved")
        self.assertEqual(rec.response_contacted_by_id, self.cs)
        self.assertTrue(rec.response_contacted_date)

    def test_response_independent_of_approval(self):
        rec = self._complaint()
        rec.with_user(self.officer).action_submit()
        rec.with_user(self.manager).action_approve()
        self.assertEqual(rec.response_state, "pending")
        rec.with_user(self.manager).action_cancel("not the employee's fault")
        # still followed up after cancellation
        rec.with_user(self.cs).response_action = "Called, explained"
        rec.with_user(self.cs).action_response_resolved()
        self.assertEqual(rec.response_state, "resolved")
        self.assertEqual(rec.state, "cancelled")

    def test_assign_moves_reminder(self):
        rec = self._complaint()
        rec.with_user(self.cs).response_user_id = self.cs2
        self.assertEqual(self._response_activities(rec).user_id, self.cs2)

    # ----------------------------------------------------------- access
    def test_officer_cannot_respond(self):
        rec = self._complaint()
        with self.assertRaises(AccessError):
            rec.with_user(self.officer2).action_response_contacted()
        rec.with_user(self.officer).action_submit()
        with self.assertRaises(AccessError):
            rec.with_user(self.officer).response_action = "x"

    def test_customer_service_limited_to_response_fields(self):
        rec = self._complaint()
        rec.with_user(self.officer).action_submit()
        rec.with_user(self.cs).customer_phone = "0511111111"
        with self.assertRaises(AccessError):
            rec.with_user(self.cs).description = "not a response field"
        with self.assertRaises(UserError):
            rec.with_user(self.cs).write({"response_state": "resolved"})
        with self.assertRaises(AccessError):
            rec.with_user(self.cs).action_approve()
        # another officer's draft stays closed to customer service
        draft = self._create(user=self.officer)
        with self.assertRaises(AccessError):
            draft.with_user(self.cs).description = "not mine"
        with self.assertRaises(AccessError):
            draft.with_user(self.cs).action_submit()
        # and a non-complaint has no response fields to edit
        draft.with_user(self.officer).action_submit()
        with self.assertRaises(UserError):
            draft.with_user(self.cs).response_action = "x"

    # ---------------------------------------------------------- overdue
    def test_overdue_and_cron(self):
        rec = self._complaint()
        rec.with_context(mm_violation_workflow_write=True).write({
            "complaint_received_at": fields.Datetime.now() - timedelta(hours=30),
        })
        self.assertTrue(rec.response_overdue)
        Violation = self.env["mm.branch.violation"]
        self.assertIn(rec, Violation.search([("response_overdue", "=", True)]))
        self.assertNotIn(rec, Violation.search([("response_overdue", "=", False)]))
        Violation._cron_mm_response_overdue()
        self.assertTrue(rec.response_overdue_notified)
        manager_acts = self._response_activities(rec).filtered(
            lambda a: a.user_id == self.manager)
        self.assertEqual(len(manager_acts), 1)
        # second run: no duplicate reminder
        Violation._cron_mm_response_overdue()
        self.assertEqual(len(self._response_activities(rec).filtered(
            lambda a: a.user_id == self.manager)), 1)
        rec.with_user(self.cs).action_response_contacted()
        self.assertTrue(rec.response_late)
        self.assertFalse(rec.response_overdue)
        self.assertFalse(self._response_activities(rec))
