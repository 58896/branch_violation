# -*- coding: utf-8 -*-
"""Customer response (v18.0.6.0.0).

- The source records are noupdate: tick "Needs Customer Response" on the
  customer-facing ones (call, Google, Instagram, WhatsApp) here.
- Existing violations got "Complaint Received" = upgrade time from the field
  default: use their creation time instead, and mark them as already
  notified so the first cron run does not flood the managers with overdue
  reminders for complaints logged before this feature existed.
"""
from odoo import SUPERUSER_ID, api

CUSTOMER_SOURCES = ("03", "06", "07", "08")


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Violation = env["mm.branch.violation"].with_context(
        active_test=False, mm_violation_workflow_write=True)
    for rec in Violation.search([]):
        rec.write({
            "complaint_received_at": rec.create_date,
            "response_overdue_notified": True,
        })
    for code in CUSTOMER_SOURCES:
        source = env.ref("mm_branch_violation.violation_source_%s" % code,
                         raise_if_not_found=False)
        if source:
            source.requires_response = True
