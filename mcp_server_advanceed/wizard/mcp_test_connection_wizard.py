# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class McpTestConnectionWizard(models.TransientModel):
    _name = "mcp.test.connection.wizard"
    _description = "MCP Test Connection Wizard"

    key_id = fields.Many2one("mcp.api.key", required=True)
    new_token = fields.Char(readonly=True)
    endpoint_url = fields.Char(readonly=True)
    sample_config = fields.Text(readonly=True, string="Claude Desktop Config")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        key_id = self.env.context.get("default_key_id") \
            or self.env.context.get("active_id")
        if key_id:
            key = self.env["mcp.api.key"].browse(key_id)
            base = self.env["ir.config_parameter"].sudo().get_param(
                "web.base.url")
            res["key_id"] = key.id
            res["endpoint_url"] = "%s/mcp" % base
        return res

    def action_issue_token(self):
        self.ensure_one()
        raw = self.key_id.action_generate_token()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        config = (
            '{\n'
            '  "mcpServers": {\n'
            '    "odoo": {\n'
            '      "url": "%s/mcp",\n'
            '      "headers": { "Authorization": "Bearer %s" }\n'
            '    }\n'
            '  }\n'
            '}'
        ) % (base, raw)
        self.write({"new_token": raw, "endpoint_url": "%s/mcp" % base,
                    "sample_config": config})
        return {
            "type": "ir.actions.act_window",
            "res_model": "mcp.test.connection.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
