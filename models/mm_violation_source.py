# -*- coding: utf-8 -*-
from odoo import fields, models


class MmViolationSource(models.Model):
    _name = "mm.violation.source"
    _description = "Violation Source"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    requires_response = fields.Boolean(
        string="Needs Customer Response",
        help="Violations from this source come from a customer (call, "
             "Google, social media...): Customer Service must contact the "
             "customer back before the response deadline.",
    )
