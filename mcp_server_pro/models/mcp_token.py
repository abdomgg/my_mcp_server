# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class McpToken(models.Model):
    """Individual token belonging to an API key.

    A key can have multiple active tokens simultaneously, enabling
    zero-downtime rotation: issue a new token, distribute it, then let the
    old one expire after its grace period.
    """

    _name = "mcp.token"
    _description = "MCP Token"
    _order = "version desc"

    key_id = fields.Many2one(
        "mcp.api.key", required=True, ondelete="cascade", index=True)
    token_hash = fields.Char(
        required=True, index=True,
        help="SHA-256 of the raw token. Raw value is shown only once.")
    refresh_hash = fields.Char(
        index=True,
        help="SHA-256 of the refresh token, when this row is a refresh token.")
    is_refresh = fields.Boolean(
        default=False, index=True,
        help="True for OAuth refresh tokens (not usable as access tokens).")
    version = fields.Integer(default=1)
    state = fields.Selection(
        [("active", "Active"),
         ("grace", "Grace (rotating out)"),
         ("revoked", "Revoked"),
         ("expired", "Expired")],
        default="active", required=True, index=True)
    grace_until = fields.Datetime(
        help="During grace, the token still authenticates but a rotation "
             "warning is logged.")
    created_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_used = fields.Datetime(readonly=True)

    @api.model
    def _hash(self, raw):
        import hashlib
        return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

    def is_valid_now(self):
        self.ensure_one()
        now = fields.Datetime.now()
        if self.state == "active":
            return True
        if self.state == "grace":
            return not self.grace_until or self.grace_until > now
        return False

    def revoke(self):
        self.write({"state": "revoked"})

    @api.model
    def _cron_expire_tokens(self):
        """Move grace tokens past their window to expired; notify upcoming."""
        now = fields.Datetime.now()
        expired = self.search([
            ("state", "=", "grace"),
            ("grace_until", "!=", False),
            ("grace_until", "<", now),
        ])
        if expired:
            expired.write({"state": "expired"})
            _logger.info("MCP: expired %s grace tokens", len(expired))

        # Notify keys with tokens expiring within 3 days (via key expiration).
        soon = now + timedelta(days=3)
        keys = self.env["mcp.api.key"].search([
            ("expiration_date", "!=", False),
            ("expiration_date", ">", now),
            ("expiration_date", "<", soon),
        ])
        for key in keys:
            key.message_post(
                body=_("API key '%s' expires on %s (within 3 days). "
                       "Rotate now to avoid downtime.")
                % (key.name, key.expiration_date))
