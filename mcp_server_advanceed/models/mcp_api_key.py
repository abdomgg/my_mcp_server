# -*- coding: utf-8 -*-
import hashlib
import ipaddress
import logging
import secrets

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)

TOKEN_PREFIX = "mcp_"


def _hash_token(raw):
    """SHA-256 hash. Raw tokens are never stored, only shown once at creation."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class McpApiKey(models.Model):
    _name = "mcp.api.key"
    _description = "MCP API Key"
    _order = "create_date desc"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True, tracking=True)
    tenant_id = fields.Many2one(
        "mcp.tenant", required=True, ondelete="cascade", index=True,
        tracking=True)
    active = fields.Boolean(default=True, tracking=True)

    # The key runs *as* this Odoo user. Every read/write goes through that
    # user's ACLs and record rules. Scopes can only narrow, never widen.
    user_id = fields.Many2one(
        "res.users", string="Run As User", required=True, tracking=True,
        help="All operations execute with this user's permissions. "
             "Scopes and allowlists further restrict but never elevate.")

    description = fields.Text()

    # --- Multi-token rotation (zero downtime) -----------------------------
    token_ids = fields.One2many("mcp.token", "key_id", string="Tokens")
    active_token_count = fields.Integer(compute="_compute_token_count")

    # --- Scopes -----------------------------------------------------------
    model_access_ids = fields.One2many(
        "mcp.model.access", "key_id", string="Model Access",
        help="Per-model CRUD grants with field allowlists and domain filters. "
             "If empty, the key has NO model access (deny by default).")

    allow_bi_tools = fields.Boolean(string="Allow BI / Reporting", default=True)
    allow_export = fields.Boolean(string="Allow Exports", default=False)
    allow_module_builder = fields.Boolean(
        string="Allow Module Builder", default=False,
        help="DANGEROUS: lets the AI generate and install Odoo modules. "
             "Off by default.")
    allow_batch_async = fields.Boolean(string="Allow Batch/Async", default=True)
    allow_portal_pages = fields.Boolean(string="Allow Portal Pages", default=False)

    # --- Network controls -------------------------------------------------
    ip_allow_list = fields.Text(
        string="IP Allow List",
        help="Newline/comma separated CIDRs or IPs. Empty = allow all.")
    ip_deny_list = fields.Text(
        string="IP Deny List",
        help="Newline/comma separated CIDRs or IPs. Checked before allow.")

    # --- Rate limiting (override tenant downward only) --------------------
    rate_limit_per_sec = fields.Integer(
        string="Rate Limit / sec",
        help="Optional per-key override. 0 = inherit tenant default.")
    rate_limit_per_min = fields.Integer(
        string="Rate Limit / min",
        help="Optional per-key override. 0 = inherit tenant default.")

    # --- Audit / lifecycle ------------------------------------------------
    last_used = fields.Datetime(readonly=True)
    request_count = fields.Integer(readonly=True, default=0)
    expiration_date = fields.Datetime(
        tracking=True,
        help="Hard cutoff. After this the key is rejected entirely.")
    is_expired = fields.Boolean(compute="_compute_is_expired", store=False)

    capture_payloads = fields.Boolean(
        string="Capture Payloads in Audit", default=True,
        help="Store full request/response bodies in audit logs. Disable for "
             "PII-sensitive keys.")

    oauth_client_id = fields.Many2one(
        "mcp.oauth.client", string="Issued via OAuth Client", readonly=True,
        ondelete="set null",
        help="Set when this key was auto-created by an OAuth authorization.")

    @api.depends("token_ids.state")
    def _compute_token_count(self):
        for rec in self:
            rec.active_token_count = len(
                rec.token_ids.filtered(lambda t: t.state == "active"))

    @api.depends("expiration_date")
    def _compute_is_expired(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.is_expired = bool(rec.expiration_date and rec.expiration_date < now)

    @api.constrains("rate_limit_per_sec", "rate_limit_per_min")
    def _check_rate_override(self):
        for rec in self:
            t = rec.tenant_id
            if rec.rate_limit_per_sec and rec.rate_limit_per_sec > t.rate_limit_per_sec:
                raise ValidationError(_(
                    "Per-key rate limit (/sec) cannot exceed the tenant "
                    "default of %s. Keys may only narrow limits.")
                    % t.rate_limit_per_sec)
            if rec.rate_limit_per_min and rec.rate_limit_per_min > t.rate_limit_per_min:
                raise ValidationError(_(
                    "Per-key rate limit (/min) cannot exceed the tenant "
                    "default of %s.") % t.rate_limit_per_min)

    # --- Token issuance ---------------------------------------------------
    def action_generate_token(self):
        """Issue a new active token. Returns the raw value once (wizard)."""
        self.ensure_one()
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        self.env["mcp.token"].create({
            "key_id": self.id,
            "token_hash": _hash_token(raw),
            "state": "active",
            "version": (max(self.token_ids.mapped("version") or [0]) + 1),
        })
        # Stash raw token in context for the wizard to display once.
        return raw

    def get_effective_rate_limits(self):
        self.ensure_one()
        per_sec = self.rate_limit_per_sec or self.tenant_id.rate_limit_per_sec
        per_min = self.rate_limit_per_min or self.tenant_id.rate_limit_per_min
        return per_sec, per_min

    def _issue_refresh_token(self):
        """Mint a refresh token (stored hashed on a token row). Returns raw."""
        self.ensure_one()
        raw = "mcpr_" + secrets.token_urlsafe(32)
        Token = self.env["mcp.token"]
        Token.create({
            "key_id": self.id,
            "token_hash": Token._hash(raw),     # placeholder; not an access tok
            "refresh_hash": Token._hash(raw),
            "state": "active",
            "is_refresh": True,
            "version": (max(self.token_ids.mapped("version") or [0]) + 1),
        })
        return raw

    # --- Network gate -----------------------------------------------------
    @staticmethod
    def _parse_cidrs(text):
        out = []
        if not text:
            return out
        for chunk in text.replace(",", "\n").split("\n"):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                out.append(ipaddress.ip_network(chunk, strict=False))
            except ValueError:
                _logger.warning("MCP: invalid CIDR/IP in key rule: %s", chunk)
        return out

    def check_ip_allowed(self, remote_ip):
        self.ensure_one()
        if not remote_ip:
            return True
        try:
            ip = ipaddress.ip_address(remote_ip)
        except ValueError:
            return False
        for net in self._parse_cidrs(self.ip_deny_list):
            if ip in net:
                return False
        allow = self._parse_cidrs(self.ip_allow_list)
        if allow:
            return any(ip in net for net in allow)
        return True

    def action_open_rotate_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Rotate Token"),
            "res_model": "mcp.key.rotate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_key_id": self.id},
        }
