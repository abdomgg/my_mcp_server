# -*- coding: utf-8 -*-
import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


class McpPortalPage(models.Model):
    """A server-rendered public page backed by live Odoo data.

    Rendered at /mcp-page/<tenant_slug>/<slug>. The page definition is a JSON
    spec of widgets (kpi tiles, charts, tables) each bound to a model + domain
    + measure. Data is fetched server-side at render time so it always
    reflects current Odoo state. Bound to an API key for access control.
    """

    _name = "mcp.portal.page"
    _description = "MCP Portal Page"
    _order = "create_date desc"

    name = fields.Char(required=True)
    slug = fields.Char(required=True, index=True, copy=False)
    tenant_id = fields.Many2one(
        "mcp.tenant", required=True, index=True, ondelete="cascade")
    key_id = fields.Many2one(
        "mcp.api.key", string="Data Access Key", required=True,
        help="Page queries run with this key's permissions and scopes.")

    is_published = fields.Boolean(default=False)
    is_public = fields.Boolean(
        string="Public (no login)", default=True,
        help="If off, requires an authenticated Odoo session.")

    layout_json = fields.Text(
        string="Layout Spec (JSON)",
        help="Widget definitions: kpi/chart/table with model, domain, measure.")
    theme = fields.Selection(
        [("light", "Light"), ("dark", "Dark"), ("brand", "Brand")],
        default="light")

    allow_export = fields.Boolean(string="Show Export Buttons", default=True)
    view_count = fields.Integer(readonly=True, default=0)

    _sql_constraints = [
        ("tenant_slug_uniq", "unique(tenant_id, slug)",
         "Slug must be unique within a tenant."),
    ]

    @api.constrains("layout_json")
    def _check_layout(self):
        for rec in self:
            if not rec.layout_json:
                continue
            try:
                spec = json.loads(rec.layout_json)
                if not isinstance(spec.get("widgets"), list):
                    raise ValueError("missing 'widgets' list")
            except Exception as e:
                raise ValidationError(_("Invalid layout JSON: %s") % e)

    def get_public_url(self):
        self.ensure_one()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return "%s/mcp-page/%s/%s" % (base, self.tenant_id.slug, self.slug)

    def action_open_page(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/mcp-page/%s/%s" % (self.tenant_id.slug, self.slug),
            "target": "new",
        }

    def render_widget_data(self):
        """Compute data for every widget. Called by the controller at render."""
        self.ensure_one()
        spec = json.loads(self.layout_json or '{"widgets": []}')
        out = []
        for w in spec.get("widgets", []):
            out.append(self._compute_widget(w))
        return out

    def _compute_widget(self, w):
        wtype = w.get("type")
        model = w.get("model")
        if not model or model not in self.env:
            return {**w, "error": "unknown model"}
        # Enforce the key's access to this model.
        access = self.key_id.model_access_ids.filtered(
            lambda a: a.model_name == model and a.can_read)
        if not access:
            return {**w, "error": "access denied"}
        domain = safe_eval(w.get("domain", "[]")) + access[0].get_domain()
        Model = self.env[model].with_user(self.key_id.user_id)
        if wtype == "kpi":
            measure = w.get("measure", "__count")
            if measure == "__count":
                val = Model.search_count(domain)
            else:
                data = Model.read_group(domain, [measure], [])
                val = data[0].get(measure) if data else 0
            return {**w, "value": val}
        if wtype == "table":
            fields_ = w.get("fields", ["display_name"])
            rows = Model.search_read(
                domain, fields_, limit=w.get("limit", 50))
            return {**w, "rows": rows}
        if wtype == "chart":
            groupby = w.get("groupby")
            measure = w.get("measure", "__count")
            data = Model.read_group(
                domain, [measure] if measure != "__count" else [], [groupby])
            return {**w, "series": data}
        return {**w, "error": "unknown widget type"}
