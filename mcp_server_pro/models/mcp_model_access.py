# -*- coding: utf-8 -*-
import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


class McpModelAccess(models.Model):
    """Least-privilege grant of one Odoo model to one API key.

    This is the real access-control surface. Even if the run-as user can see
    100 models, the key can only touch the models explicitly granted here,
    only with the CRUD verbs ticked, only the allowlisted fields, and only
    records matching the domain filter.
    """

    _name = "mcp.model.access"
    _description = "MCP Key Model Access"
    _order = "model_id"

    key_id = fields.Many2one(
        "mcp.api.key", required=True, ondelete="cascade", index=True)
    model_id = fields.Many2one(
        "ir.model", required=True, ondelete="cascade",
        domain=[("transient", "=", False)])
    model_name = fields.Char(related="model_id.model", store=True, index=True)

    can_read = fields.Boolean(default=True)
    can_create = fields.Boolean(default=False)
    can_write = fields.Boolean(default=False)
    can_unlink = fields.Boolean(default=False)

    field_allow_list = fields.Text(
        string="Field Allowlist",
        help="Comma/newline separated field names. Empty = all fields the "
             "run-as user can read. Applies to read AND write.")
    domain_filter = fields.Char(
        string="Domain Filter", default="[]",
        help="Odoo domain ANDed into every query. e.g. "
             "[('state','=','done')]. Hard-limits which records are visible.")

    max_rows = fields.Integer(
        default=500,
        help="Maximum rows returned per read/search call for this model.")

    _sql_constraints = [
        ("key_model_uniq", "unique(key_id, model_id)",
         "Each model can be granted only once per key."),
    ]

    @api.constrains("domain_filter")
    def _check_domain(self):
        for rec in self:
            if not rec.domain_filter:
                continue
            try:
                dom = safe_eval(rec.domain_filter)
                if not isinstance(dom, list):
                    raise ValueError("not a list")
            except Exception as e:
                raise ValidationError(
                    _("Invalid domain filter on %s: %s")
                    % (rec.model_name, e))

    def get_allowed_fields(self):
        self.ensure_one()
        if not self.field_allow_list:
            return None  # None => all readable fields
        return [
            f.strip() for f in self.field_allow_list.replace(",", "\n").split("\n")
            if f.strip()
        ]

    def get_domain(self):
        self.ensure_one()
        try:
            return safe_eval(self.domain_filter or "[]")
        except Exception:
            return []
