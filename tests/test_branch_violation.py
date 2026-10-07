# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MmViolationCommon


@tagged("post_install", "-at_install")
class TestBranchViolation(MmViolationCommon):

    # ---------------------------------------------------------------- points
    def test_points_default_from_type(self):
        violation = self._create(user=self.officer)
        self.assertEqual(violation.points, 5.0)
        self.assertTrue(violation.name.startswith("VIO/"))
        violation.with_user(self.officer).violation_type_id = self.type_10
        self.assertEqual(violation.points, 10.0)

    def test_officer_cannot_override_points(self):
        with self.assertRaises(AccessError):
            self._create(user=self.officer, points=1.0)
        violation = self._create(user=self.officer)
        with self.assertRaises(AccessError):
            violation.with_user(self.officer).points = 2.0

    def test_manager_can_override_points(self):
        violation = self._create(user=self.manager, points=7.5)
        self.assertEqual(violation.points, 7.5)
        violation.with_user(self.manager).action_submit()
        violation.with_user(self.manager2).points = 3.0
        self.assertEqual(violation.points, 3.0)

    # ------------------------------------------------------------ edit lock
    def test_officer_edit_lock_after_submit(self):
        violation = self._create(user=self.officer)
        violation.with_user(self.officer).description = "draft edit is fine"
        violation.with_user(self.officer).action_submit()
        self.assertEqual(violation.state, "under_review")
        self.assertEqual(violation.submitted_by_id, self.officer)
        with self.assertRaises(AccessError):
            violation.with_user(self.officer).description = "too late"
        # the lock is in write(), not only in the record rule: a manager
        # still can
        violation.with_user(self.manager).description = "manager edit"

    def test_officer_cannot_edit_other_officers_draft(self):
        violation = self._create(user=self.officer)
        with self.assertRaises(AccessError):
            violation.with_user(self.officer2).description = "not mine"
        with self.assertRaises(AccessError):
            violation.with_user(self.officer2).action_submit()

    def test_workflow_fields_only_through_buttons(self):
        violation = self._create(user=self.officer)
        with self.assertRaises(UserError):
            violation.with_user(self.officer).write({"state": "approved"})
        with self.assertRaises(UserError):
            violation.with_user(self.manager).write({"state": "approved"})
        with self.assertRaises(UserError):
            self._create(user=self.manager, state="approved")

    def test_officer_cannot_delete(self):
        violation = self._create(user=self.officer)
        with self.assertRaises(AccessError):
            violation.with_user(self.officer).unlink()

    def test_manager_deletes_drafts_only(self):
        violation = self._create(user=self.officer)
        violation.with_user(self.officer).action_submit()
        with self.assertRaises(UserError):
            violation.with_user(self.manager).unlink()
        draft = self._create(user=self.officer)
        draft.with_user(self.manager).unlink()
        self.assertFalse(draft.exists())

    # ------------------------------------------------------------ four-eyes
    def test_officer_cannot_approve(self):
        violation = self._create(user=self.officer)
        violation.with_user(self.officer).action_submit()
        with self.assertRaises(AccessError):
            violation.with_user(self.officer).action_approve()

    def test_submitter_cannot_approve(self):
        violation = self._create(user=self.manager)
        violation.with_user(self.manager).action_submit()
        with self.assertRaises(UserError):
            violation.with_user(self.manager).action_approve()
        violation.with_user(self.manager2).action_approve()
        self.assertEqual(violation.state, "approved")
        self.assertEqual(violation.approved_by_id, self.manager2)
        self.assertTrue(violation.approved_date)

    def test_employee_cannot_approve_own_violation(self):
        violation = self._create(user=self.officer, employee_id=self.manager_employee.id)
        violation.with_user(self.officer).action_submit()
        with self.assertRaises(UserError):
            violation.with_user(self.manager2).action_approve()
        violation.with_user(self.manager).action_approve()
        self.assertEqual(violation.state, "approved")

    def test_admin_gets_no_exception(self):
        admin = self.env.ref("base.user_admin")
        admin.groups_id = [(4, self.env.ref("mm_branch_violation.group_violation_manager").id)]
        violation = self._create(user=admin)
        violation.with_user(admin).action_submit()
        with self.assertRaises(UserError):
            violation.with_user(admin).action_approve()

    def test_submit_schedules_activity_for_other_managers(self):
        violation = self._create(user=self.manager)
        violation.with_user(self.manager).action_submit()
        users = violation.activity_ids.user_id
        self.assertIn(self.manager2, users)
        self.assertNotIn(self.manager, users, "the submitter reviews nothing of their own")
        violation.with_user(self.manager2).action_approve()
        self.assertFalse(violation.activity_ids)

    # --------------------------------------------------- cancel / reset / closed
    def test_cancel_requires_reason_and_reset(self):
        violation = self._create(user=self.officer)
        violation.with_user(self.officer).action_submit()
        with self.assertRaises(UserError):
            violation.with_user(self.manager).action_cancel("  ")
        with self.assertRaises(AccessError):
            violation.with_user(self.officer).action_cancel("reason")
        wizard = self.env["mm.violation.cancel.wizard"].with_user(self.manager).create({
            "violation_ids": [(6, 0, violation.ids)], "reason": "Duplicate entry",
        })
        wizard.action_confirm()
        self.assertEqual(violation.state, "cancelled")
        self.assertEqual(violation.cancel_reason, "Duplicate entry")
        with self.assertRaises(UserError):
            violation.with_user(self.manager).description = "edit cancelled"
        violation.with_user(self.manager).action_reset_draft()
        self.assertEqual(violation.state, "draft")
        self.assertFalse(violation.submitted_by_id)
        self.assertFalse(violation.cancel_reason)

    def test_reset_not_allowed_from_approved(self):
        violation = self._create(user=self.officer)
        violation.with_user(self.officer).action_submit()
        violation.with_user(self.manager).action_approve()
        with self.assertRaises(UserError):
            violation.with_user(self.manager).action_reset_draft()

    def test_closed_is_readonly_for_everyone(self):
        violation = self._create(user=self.officer)
        violation.with_user(self.officer).action_submit()
        violation.with_user(self.manager).action_approve()
        violation.sudo()._mm_workflow_write({"state": "closed"})  # what distribution approval does
        for user in (self.officer, self.manager):
            with self.assertRaises(UserError):
                violation.with_user(user).description = "change"
        with self.assertRaises(UserError):
            violation.with_user(self.manager).points = 1.0
        with self.assertRaises(UserError):
            violation.with_user(self.manager).action_cancel("late")
        with self.assertRaises(UserError):
            violation.sudo().write({"notes": "even superuser"})

    # ------------------------------------------------------------- employee
    def test_employee_month_totals(self):
        today = fields.Date.context_today(self.employee)
        approved = self._create(user=self.officer, violation_type_id=self.type_10.id)
        approved.with_user(self.officer).action_submit()
        approved.with_user(self.manager).action_approve()
        self._create(user=self.officer)  # draft: not counted
        old = self._create(user=self.officer, date=today.replace(day=1) - relativedelta(days=1))
        old.with_user(self.officer).action_submit()
        old.with_user(self.manager).action_approve()  # last month: not counted
        self.env.invalidate_all()
        self.assertEqual(self.employee.mm_violation_count, 1)
        self.assertEqual(self.employee.mm_violation_points, 10.0)
        action = self.employee.action_view_mm_violations()
        self.assertEqual(action["domain"], [("employee_id", "=", self.employee.id)])

    def test_counted_domain(self):
        violation = self._create(user=self.officer)
        Violation = self.env["mm.branch.violation"]
        today = violation.date
        domain = Violation._mm_counted_domain(self.employee, today, today)
        self.assertFalse(Violation.search(domain))
        violation.with_user(self.officer).action_submit()
        violation.with_user(self.manager).action_approve()
        self.assertEqual(Violation.search(domain), violation)

    def test_multi_company_isolation(self):
        other = self.env["res.company"].create({"name": "MMV Other Co"})
        outsider = self.env["res.users"].create({
            "name": "MMV outsider", "login": "mmv_outsider",
            "company_id": other.id, "company_ids": [(6, 0, other.ids)],
            "groups_id": [(6, 0, [self.env.ref("mm_branch_violation.group_violation_manager").id])],
        })
        violation = self._create(user=self.officer)
        self.assertFalse(
            self.env["mm.branch.violation"].with_user(outsider).search([("id", "=", violation.id)]))

    def test_officer_reads_employee_number_without_hr_rights(self):
        self.employee.mm_employee_number = "MMV-208"
        violation = self._create(user=self.officer)
        self.assertFalse(self.officer.has_group("hr.group_hr_user"))
        self.assertEqual(violation.with_user(self.officer).employee_number, "MMV-208")
        found = self.env["mm.branch.violation"].with_user(self.officer).search(
            [("employee_number", "=", "MMV-208")])
        self.assertEqual(found, violation)

    # -------------------------------------------------------- employee picker
    def test_employee_picker_not_filtered_by_branch(self):
        """Any employee of the company can be picked, whatever the branch."""
        other_branch = self.env["company.branch"].create({
            "name": "Other Branch MMV", "code": "MMV-OTHER",
            "company_id": self.company.id,
        })
        there = self.env["hr.employee"].create({
            "name": "MMV Other Cashier", "branch_id": other_branch.id,
        })
        violation = self._create(user=self.officer, employee_id=there.id)
        self.assertEqual(violation.employee_id, there)
        self.assertEqual(violation.branch_id, self.branch)

        violation = self.env["mm.branch.violation"].new({
            "branch_id": self.branch.id, "employee_id": self.employee.id,
        })
        violation.branch_id = other_branch
        self.assertEqual(violation.employee_id, self.employee)
