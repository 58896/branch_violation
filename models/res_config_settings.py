# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mm_response_deadline_hours = fields.Integer(
        related="company_id.mm_response_deadline_hours", readonly=False,
    )
    mm_score_weight_sales = fields.Integer(
        related="company_id.mm_score_weight_sales", readonly=False,
    )
    mm_score_weight_mistakes = fields.Integer(
        related="company_id.mm_score_weight_mistakes", readonly=False,
    )

    @api.onchange("mm_score_weight_sales")
    def _onchange_mm_score_weight_sales(self):
        if 0 <= self.mm_score_weight_sales <= 100:
            self.mm_score_weight_mistakes = 100 - self.mm_score_weight_sales
