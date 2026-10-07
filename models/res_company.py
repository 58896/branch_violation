# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    mm_response_deadline_hours = fields.Integer(
        string="Customer Response Deadline (Hours)", default=24,
        help="Hours Customer Service has to contact a complaining customer "
             "back. Applies to complaints received after the change.",
    )

    mm_score_weight_sales = fields.Integer(
        string="Branch Score: Sales Weight (%)", default=70,
        help="Share of the branch score that comes from its sales target "
             "achievement. Sales + Mistakes must equal 100.",
    )
    mm_score_weight_mistakes = fields.Integer(
        string="Branch Score: Mistakes Weight (%)", default=30,
        help="Share of the branch score that comes from its violations "
             "(customer complaints included). Sales + Mistakes must equal 100.",
    )

    _sql_constraints = [
        ("mm_score_weights_total",
         "CHECK(mm_score_weight_sales >= 0 AND mm_score_weight_mistakes >= 0 "
         "AND mm_score_weight_sales + mm_score_weight_mistakes = 100)",
         "The branch score weights (Sales + Mistakes) must add up to 100."),
        ("mm_response_deadline_hours_positive",
         "CHECK(mm_response_deadline_hours > 0)",
         "The customer response deadline must be at least 1 hour."),
    ]
