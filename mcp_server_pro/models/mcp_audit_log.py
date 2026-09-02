# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class McpAuditLog(models.Model):
    _name = "mcp.audit.log"
    _description = "MCP Audit Log"
    _order = "create_date desc"
    _rec_name = "tool_name"

    tenant_id = fields.Many2one("mcp.tenant", index=True, ondelete="cascade")
    key_id = fields.Many2one("mcp.api.key", index=True, ondelete="set null")
    key_name = fields.Char(help="Snapshot — survives key deletion.")
    user_id = fields.Many2one("res.users", index=True)

    tool_name = fields.Char(index=True)
    model_name = fields.Char(index=True)
    method = fields.Char(help="JSON-RPC method / tool verb invoked.")

    request_payload = fields.Text()
    response_payload = fields.Text()

    status = fields.Selection(
        [("ok", "Success"),
         ("denied", "Denied"),
         ("error", "Error"),
         ("rate_limited", "Rate Limited")],
        index=True)
    error_message = fields.Text()

    remote_ip = fields.Char(index=True)
    duration_ms = fields.Integer(help="Server processing time in milliseconds.")
    record_count = fields.Integer(help="Rows read/affected.")

    @api.model
    def log(self, vals):
        """Create an audit entry. Best-effort; never raises into the pipeline."""
        try:
            # Truncate oversized payloads defensively.
            for f in ("request_payload", "response_payload"):
                if vals.get(f) and len(vals[f]) > 100000:
                    vals[f] = vals[f][:100000] + "...[truncated]"
            return self.sudo().create(vals)
        except Exception as e:  # pragma: no cover
            _logger.warning("MCP: failed to write audit log: %s", e)
            return False

    @api.model
    def _cron_apply_retention(self):
        """Delete logs older than the configured retention window."""
        days = int(self.env["ir.config_parameter"].sudo().get_param(
            "mcp_server_pro.audit_retention_days", "90"))
        if days <= 0:
            return
        cutoff = fields.Datetime.now() - timedelta(days=days)
        old = self.search([("create_date", "<", cutoff)])
        if old:
            count = len(old)
            old.unlink()
            _logger.info("MCP: purged %s audit logs older than %s days",
                         count, days)
