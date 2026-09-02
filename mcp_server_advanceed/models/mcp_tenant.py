# -*- coding: utf-8 -*-
import logging
import secrets

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class McpTenant(models.Model):
    """A multi-tenant workspace.

    Each tenant is a hard isolation boundary: API keys, audit logs, jobs,
    generated modules and portal pages all belong to exactly one tenant.
    Tenants optionally pin to a set of allowed companies (res.company) so a
    key issued in tenant A can never reach company B's data, even if Odoo
    ACLs would otherwise allow it. This is enforced in the request pipeline
    on top of Odoo's own multi-company rules.
    """

    _name = "mcp.tenant"
    _description = "MCP Tenant / Workspace"
    _order = "name"

    name = fields.Char(required=True, index=True)
    slug = fields.Char(
        required=True, index=True, copy=False,
        help="URL-safe identifier used in portal page namespacing and routing.")
    active = fields.Boolean(default=True)
    description = fields.Text()

    company_ids = fields.Many2many(
        "res.company", string="Allowed Companies",
        help="Hard boundary: keys in this tenant can only touch these "
             "companies. Empty = all companies the key's user can see.")

    api_key_ids = fields.One2many("mcp.api.key", "tenant_id", string="API Keys")
    api_key_count = fields.Integer(compute="_compute_counts")
    audit_log_count = fields.Integer(compute="_compute_counts")
    job_count = fields.Integer(compute="_compute_counts")

    # Per-tenant default rate limits (keys may override downward only).
    rate_limit_per_sec = fields.Integer(
        default=20, string="Rate Limit / sec",
        help="Default per-second request ceiling for keys in this tenant.")
    rate_limit_per_min = fields.Integer(
        default=600, string="Rate Limit / min")

    # Tenant-wide kill switch — disables every key instantly.
    is_suspended = fields.Boolean(
        string="Suspended", default=False,
        help="Emergency kill switch. When set, every request from any key "
             "in this tenant is rejected immediately.")

    _sql_constraints = [
        ("slug_uniq", "unique(slug)", "Tenant slug must be unique."),
    ]

    @api.depends("api_key_ids")
    def _compute_counts(self):
        key_data = self.env["mcp.api.key"].read_group(
            [("tenant_id", "in", self.ids)], ["tenant_id"], ["tenant_id"])
        key_map = {d["tenant_id"][0]: d["tenant_id_count"] for d in key_data}
        log_data = self.env["mcp.audit.log"].read_group(
            [("tenant_id", "in", self.ids)], ["tenant_id"], ["tenant_id"])
        log_map = {d["tenant_id"][0]: d["tenant_id_count"] for d in log_data}
        job_data = self.env["mcp.job"].read_group(
            [("tenant_id", "in", self.ids)], ["tenant_id"], ["tenant_id"])
        job_map = {d["tenant_id"][0]: d["tenant_id_count"] for d in job_data}
        for rec in self:
            rec.api_key_count = key_map.get(rec.id, 0)
            rec.audit_log_count = log_map.get(rec.id, 0)
            rec.job_count = job_map.get(rec.id, 0)

    @api.constrains("slug")
    def _check_slug(self):
        for rec in self:
            if not rec.slug.replace("-", "").replace("_", "").isalnum():
                raise ValidationError(
                    _("Slug may only contain letters, digits, '-' and '_'."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("slug") and vals.get("name"):
                base = "".join(
                    c for c in vals["name"].lower().replace(" ", "-")
                    if c.isalnum() or c in "-_")
                vals["slug"] = base or "tenant-%s" % secrets.token_hex(3)
        return super().create(vals_list)

    def action_view_keys(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("API Keys"),
            "res_model": "mcp.api.key",
            "view_mode": "list,form",
            "domain": [("tenant_id", "=", self.id)],
            "context": {"default_tenant_id": self.id},
        }

    def action_view_audit_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Audit Logs"),
            "res_model": "mcp.audit.log",
            "view_mode": "list,form",
            "domain": [("tenant_id", "=", self.id)],
        }
