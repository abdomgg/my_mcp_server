# -*- coding: utf-8 -*-
import base64
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class McpGeneratedModule(models.Model):
    """A module produced by the AI Module Builder.

    Lifecycle: spec (validated JSON) -> built (ZIP generated) ->
    installed (deployed into Odoo). Every transition is audited.
    """

    _name = "mcp.generated.module"
    _description = "MCP Generated Module"
    _order = "create_date desc"

    name = fields.Char(required=True, help="Technical module name (e.g. theme_park).")
    display_name_ = fields.Char(string="Title")
    tenant_id = fields.Many2one("mcp.tenant", index=True, ondelete="cascade")
    key_id = fields.Many2one("mcp.api.key", ondelete="set null")

    spec_json = fields.Text(string="Module Spec (JSON)")
    state = fields.Selection(
        [("spec", "Spec"),
         ("validated", "Validated"),
         ("built", "Built"),
         ("installed", "Installed"),
         ("failed", "Failed")],
        default="spec", required=True, index=True)

    zip_file = fields.Binary(string="Module ZIP", attachment=True)
    zip_filename = fields.Char()
    validation_report = fields.Text()
    odoo_version = fields.Char(default="18.0")

    summary = fields.Text(help="Human-readable description of what was generated.")
    model_count = fields.Integer()
    view_count = fields.Integer()

    def action_build_zip(self):
        self.ensure_one()
        builder = self.env["mcp.tool.builder"]
        result = builder.build_module_zip(self.spec_json)
        if not result.get("ok"):
            self.write({"state": "failed",
                        "validation_report": result.get("error")})
            raise UserError(result.get("error"))
        self.write({
            "zip_file": result["zip_b64"],
            "zip_filename": "%s.zip" % self.name,
            "state": "built",
        })

    def action_install(self):
        self.ensure_one()
        if self.state != "built":
            raise UserError(_("Build the ZIP before installing."))
        self.env["mcp.tool.builder"].install_module_zip(
            self.name, self.zip_file)
        self.write({"state": "installed"})

    def action_download(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/?model=mcp.generated.module&id=%s&field=zip_file"
                   "&filename_field=zip_filename&download=true" % self.id,
            "target": "self",
        }
