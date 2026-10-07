# -*- coding: utf-8 -*-
from odoo import fields, models


class MmViolationType(models.Model):
    _name = "mm.violation.type"
    _description = "Violation Type"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    points = fields.Float(
        string="Deduction Points (%)", required=True, default=5.0, digits=(5, 2),
        help="Percentage deducted from the employee's commission for each "
             "approved violation of this type. Proposed on every new "
             "violation; only a Violation Manager can override it there.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("points_range", "CHECK(points >= 0 AND points <= 100)",
         "Deduction points must be between 0 and 100."),
    ]
