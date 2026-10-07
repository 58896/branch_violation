# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, new_test_user


class MmViolationCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.branch = cls.env["company.branch"].create({
            "name": "Test Branch MMV",
            "code": "MMV-TEST",
            "company_id": cls.company.id,
        })
        cls.officer = new_test_user(
            cls.env, login="mmv_officer", groups="mm_branch_violation.group_violation_officer")
        cls.officer2 = new_test_user(
            cls.env, login="mmv_officer2", groups="mm_branch_violation.group_violation_officer")
        cls.manager = new_test_user(
            cls.env, login="mmv_manager", groups="mm_branch_violation.group_violation_manager")
        cls.manager2 = new_test_user(
            cls.env, login="mmv_manager2", groups="mm_branch_violation.group_violation_manager")
        cls.employee = cls.env["hr.employee"].create({
            "name": "MMV Cashier", "company_id": cls.company.id,
        })
        # an employee who is also a violation manager
        cls.manager_employee = cls.env["hr.employee"].create({
            "name": "MMV Manager Employee", "company_id": cls.company.id,
            "user_id": cls.manager2.id,
        })
        cls.type_5 = cls.env["mm.violation.type"].create({"name": "MMV five", "points": 5.0})
        cls.type_10 = cls.env["mm.violation.type"].create({"name": "MMV ten", "points": 10.0})
        cls.source = cls.env["mm.violation.source"].create({"name": "MMV source"})

    def _vals(self, **overrides):
        vals = {
            "branch_id": self.branch.id,
            "note_type": "operational",
            "entering_department": "operations",
            "employee_id": self.employee.id,
            "source_id": self.source.id,
            "violation_type_id": self.type_5.id,
        }
        vals.update(overrides)
        return vals

    def _create(self, user=None, **overrides):
        Violation = self.env["mm.branch.violation"]
        if user:
            Violation = Violation.with_user(user)
        return Violation.create(self._vals(**overrides))
