# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models, _


class McpKeyRotateWizard(models.TransientModel):
    _name = "mcp.key.rotate.wizard"
    _description = "MCP Key Rotation Wizard"

    key_id = fields.Many2one("mcp.api.key", required=True)
    grace_days = fields.Integer(
        default=7, string="Grace Period (days)",
        help="Existing tokens keep working for this long, then expire.")
    new_token = fields.Char(readonly=True, string="New Token (copy now!)")
    done = fields.Boolean(default=False)

    def action_rotate(self):
        self.ensure_one()
        # Move current active tokens into grace.
        grace_until = fields.Datetime.now() + timedelta(days=self.grace_days)
        self.key_id.token_ids.filtered(
            lambda t: t.state == "active").write({
                "state": "grace", "grace_until": grace_until})
        # Issue a new active token.
        raw = self.key_id.action_generate_token()
        self.write({"new_token": raw, "done": True})
        self.key_id.message_post(
            body=_("Token rotated. Old tokens enter a %s-day grace period.")
            % self.grace_days)
        return {
            "type": "ir.actions.act_window",
            "res_model": "mcp.key.rotate.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
