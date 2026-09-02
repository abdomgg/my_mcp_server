# -*- coding: utf-8 -*-
import logging
import secrets

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class McpOAuthClient(models.Model):
    """OAuth client registration for one-click connect from MCP clients.

    Supports both manual registration (admin creates a client and shares the
    id/secret) and Dynamic Client Registration (claude.ai/Cursor self-register
    via /mcp/oauth/register). Each successful authorization issues a per-user
    token bound to a key cloned from a template, so team members get their own
    audited connection without sharing static keys.
    """

    _name = "mcp.oauth.client"
    _description = "MCP OAuth Client"
    _order = "name"

    name = fields.Char(required=True)
    tenant_id = fields.Many2one("mcp.tenant", required=True, ondelete="cascade")
    client_id = fields.Char(
        required=True, copy=False, default=lambda s: secrets.token_urlsafe(16),
        readonly=True)
    client_secret = fields.Char(
        copy=False, default=lambda s: secrets.token_urlsafe(32), readonly=True,
        groups="mcp_server_pro.group_mcp_admin")
    redirect_uris = fields.Text(
        help="Newline-separated allowed redirect URIs (e.g. "
             "https://claude.ai/api/mcp/auth_callback). Dynamically "
             "registered clients fill this automatically.")
    active = fields.Boolean(default=True)
    is_dynamic = fields.Boolean(
        string="Self-Registered", default=False, readonly=True,
        help="Created automatically via Dynamic Client Registration.")

    # Template key cloned per user on first authorization.
    template_key_id = fields.Many2one(
        "mcp.api.key", string="Template Key",
        help="New per-user keys are cloned from this key's scopes. If empty, "
             "the tenant's first key is used.")

    require_pkce = fields.Boolean(default=True)

    def get_redirect_uris(self):
        self.ensure_one()
        return [u.strip() for u in (self.redirect_uris or "").split("\n")
                if u.strip()]

    @api.model
    def _default_template_key(self, tenant):
        """Pick a template key for DCR clients: the tenant's first key."""
        if not tenant:
            return self.env["mcp.api.key"]
        return self.env["mcp.api.key"].sudo().search(
            [("tenant_id", "=", tenant.id), ("active", "=", True)],
            limit=1, order="create_date asc")

    def _issue_user_key(self, user):
        """Clone the template key for a specific user (idempotent per user)."""
        self.ensure_one()
        template = self.template_key_id
        if not template:
            raise ValueError("OAuth client has no template key.")
        # Reuse an existing cloned key for this user+client if present.
        existing = self.env["mcp.api.key"].sudo().search([
            ("tenant_id", "=", self.tenant_id.id),
            ("user_id", "=", user.id),
            ("oauth_client_id", "=", self.id),
        ], limit=1)
        if existing:
            return existing
        key = template.sudo().copy({
            "name": "%s — %s" % (self.name, user.name),
            "user_id": user.id,
            "oauth_client_id": self.id,
        })
        for acc in template.model_access_ids:
            acc.sudo().copy({"key_id": key.id})
        return key
