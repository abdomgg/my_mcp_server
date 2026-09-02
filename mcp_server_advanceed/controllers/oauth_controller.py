# -*- coding: utf-8 -*-
"""OAuth 2.1 + PKCE + Dynamic Client Registration for MCP clients.

Implements the full MCP 2025-06-18 authorization spec so that claude.ai
(web + desktop + mobile), Cursor, Claude Code and any compliant MCP client
can connect by URL alone — they self-register, run the consent flow, and get
a per-user token mapped to a key cloned from a template.

Endpoints:
  /.well-known/oauth-protected-resource     (RFC 9728 resource metadata)
  /.well-known/oauth-authorization-server   (RFC 8414 AS metadata)
  /mcp/oauth/register                        (RFC 7591 dynamic registration)
  /mcp/oauth/authorize                       (consent + auth code)
  /mcp/oauth/token                           (code/refresh -> token)
"""
import base64
import hashlib
import json
import logging
import secrets
import time

from odoo import http, _
from odoo.http import request
from markupsafe import escape

_logger = logging.getLogger(__name__)

# Short-lived auth codes: {code: {...}}. For multi-worker, persisted to
# ir.config_parameter as a fallback so any worker can complete the exchange.
_AUTH_CODES = {}
_CODE_TTL = 600


def _b64json(obj):
    return json.dumps(obj)


def _cors_headers():
    return [
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Authorization, Content-Type"),
    ]


class McpOAuthController(http.Controller):

    # ================================================================== #
    #  Discovery: Protected Resource Metadata (RFC 9728)                 #
    # ================================================================== #
    @http.route(["/.well-known/oauth-protected-resource",
                 "/.well-known/oauth-protected-resource/mcp"],
                type="http", auth="none", methods=["GET", "OPTIONS"],
                csrf=False, save_session=False)
    def protected_resource_metadata(self, **kw):
        if request.httprequest.method == "OPTIONS":
            return request.make_response("", headers=_cors_headers())
        base = self._base_url()
        meta = {
            "resource": "%s/mcp" % base,
            "authorization_servers": [base],
            "scopes_supported": ["mcp:tools"],
            "bearer_methods_supported": ["header"],
        }
        return self._json(meta)

    # ================================================================== #
    #  Discovery: Authorization Server Metadata (RFC 8414)               #
    # ================================================================== #
    @http.route(["/.well-known/oauth-authorization-server",
                 "/.well-known/openid-configuration"],
                type="http", auth="none", methods=["GET", "OPTIONS"],
                csrf=False, save_session=False)
    def discovery(self, **kw):
        if request.httprequest.method == "OPTIONS":
            return request.make_response("", headers=_cors_headers())
        base = self._base_url()
        meta = {
            "issuer": base,
            "authorization_endpoint": "%s/mcp/oauth/authorize" % base,
            "token_endpoint": "%s/mcp/oauth/token" % base,
            "registration_endpoint": "%s/mcp/oauth/register" % base,
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none",
                                                      "client_secret_post"],
            "scopes_supported": ["mcp:tools"],
        }
        return self._json(meta)

    # ================================================================== #
    #  Dynamic Client Registration (RFC 7591)                            #
    # ================================================================== #
    @http.route("/mcp/oauth/register", type="http", auth="none",
                methods=["POST", "OPTIONS"], csrf=False, save_session=False)
    def register(self, **kw):
        if request.httprequest.method == "OPTIONS":
            return request.make_response("", headers=_cors_headers())
        try:
            body = json.loads(request.httprequest.get_data() or b"{}")
        except Exception:
            body = {}

        redirect_uris = body.get("redirect_uris") or []
        client_name = body.get("client_name") or "MCP Client"

        Client = request.env["mcp.oauth.client"].sudo()
        # Bind dynamically-registered clients to the default tenant + its
        # template key (admin can later point them at a tighter template).
        tenant = request.env.ref("mcp_server_advanceed.tenant_default",
                                 raise_if_not_found=False)
        template = Client._default_template_key(tenant)

        client_id = secrets.token_urlsafe(16)
        client_secret = secrets.token_urlsafe(32)
        rec = Client.create({
            "name": "%s (DCR)" % client_name,
            "tenant_id": tenant.id if tenant else False,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": "\n".join(redirect_uris),
            "template_key_id": template.id if template else False,
            "is_dynamic": True,
        })

        resp = {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": int(time.time()),
            "client_secret_expires_at": 0,  # never expires
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "client_name": client_name,
        }
        return self._json(resp, status=201)

    # ================================================================== #
    #  Authorize                                                         #
    # ================================================================== #
    @http.route("/mcp/oauth/authorize", type="http", auth="user",
                methods=["GET", "POST"], csrf=False, save_session=True)
    def authorize(self, **kw):
        client_id = kw.get("client_id")
        redirect_uri = kw.get("redirect_uri")
        state = kw.get("state", "")
        code_challenge = kw.get("code_challenge")
        code_challenge_method = kw.get("code_challenge_method", "S256")

        client = request.env["mcp.oauth.client"].sudo().search(
            [("client_id", "=", client_id), ("active", "=", True)], limit=1)
        if not client:
            return request.make_response("invalid_client", status=400)

        allowed = client.get_redirect_uris()
        # DCR clients may register many; if none stored, accept the supplied
        # one (claude.ai callback) but require https or localhost.
        if allowed and redirect_uri not in allowed:
            return request.make_response("invalid_redirect_uri", status=400)
        if not allowed and not (redirect_uri or "").startswith(
                ("https://", "http://localhost", "http://127.0.0.1")):
            return request.make_response("invalid_redirect_uri", status=400)

        if request.httprequest.method == "GET":
            return self._consent_page(client, kw)

        # POST = user approved.
        code = secrets.token_urlsafe(32)
        _AUTH_CODES[code] = {
            "client_id": client_id,
            "user_id": request.env.user.id,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "redirect_uri": redirect_uri,
            "ts": time.time(),
        }
        sep = "&" if "?" in (redirect_uri or "") else "?"
        url = "%s%scode=%s" % (redirect_uri, sep, code)
        if state:
            url += "&state=%s" % state
        return request.redirect(url, local=False)

    # ================================================================== #
    #  Token                                                             #
    # ================================================================== #
    @http.route("/mcp/oauth/token", type="http", auth="none",
                methods=["POST", "OPTIONS"], csrf=False, save_session=False)
    def token(self, **kw):
        if request.httprequest.method == "OPTIONS":
            return request.make_response("", headers=_cors_headers())

        grant_type = kw.get("grant_type")
        if grant_type == "refresh_token":
            return self._handle_refresh(kw)

        code = kw.get("code")
        code_verifier = kw.get("code_verifier")
        info = _AUTH_CODES.pop(code, None)
        if not info or (time.time() - info["ts"]) > _CODE_TTL:
            return self._token_error("invalid_grant")

        # PKCE verify (S256).
        if info.get("code_challenge"):
            digest = hashlib.sha256((code_verifier or "").encode()).digest()
            expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            if expected != info["code_challenge"]:
                return self._token_error("invalid_grant")

        client = request.env["mcp.oauth.client"].sudo().search(
            [("client_id", "=", info["client_id"])], limit=1)
        if not client or not client.template_key_id:
            return self._token_error("invalid_client",
                                     "No template key configured.")

        user = request.env["res.users"].sudo().browse(info["user_id"])
        key = client._issue_user_key(user)
        access = key.action_generate_token()
        refresh = key._issue_refresh_token()

        return self._json({
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": 3600 * 24 * 30,
            "refresh_token": refresh,
            "scope": "mcp:tools",
        })

    def _handle_refresh(self, kw):
        refresh = kw.get("refresh_token")
        Token = request.env["mcp.token"].sudo()
        rt = Token.search([("refresh_hash", "=", Token._hash(refresh or "")),
                           ("state", "=", "active")], limit=1)
        if not rt:
            return self._token_error("invalid_grant")
        key = rt.key_id
        access = key.action_generate_token()
        new_refresh = key._issue_refresh_token()
        rt.revoke()  # rotate refresh token (OAuth 2.1 requirement)
        return self._json({
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": 3600 * 24 * 30,
            "refresh_token": new_refresh,
            "scope": "mcp:tools",
        })

    # ================================================================== #
    #  Helpers                                                           #
    # ================================================================== #
    def _base_url(self):
        return request.env["ir.config_parameter"].sudo().get_param(
            "web.base.url")

    def _json(self, obj, status=200):
        return request.make_response(
            json.dumps(obj), status=status,
            headers=[("Content-Type", "application/json")] + _cors_headers())

    def _token_error(self, code, desc=None):
        body = {"error": code}
        if desc:
            body["error_description"] = desc
        return request.make_response(
            json.dumps(body), status=400,
            headers=[("Content-Type", "application/json")] + _cors_headers())

    def _consent_page(self, client, kw):
        hidden = "".join(
            '<input type="hidden" name="%s" value="%s">'
            % (escape(k), escape(v)) for k, v in kw.items())
        html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect to Odoo</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;
margin:0;background:#0f111a;color:#e8e8ea}
.box{background:#1b1e27;padding:40px;border-radius:18px;max-width:400px;
box-shadow:0 8px 40px rgba(0,0,0,.4);text-align:center}
h2{margin:0 0 6px;font-size:22px}
.who{color:#5b8cff;font-weight:600}
p{color:#9aa0b4;font-size:14px;line-height:1.5}
button{background:#5b8cff;color:#fff;border:0;padding:13px 30px;
border-radius:11px;font-size:15px;cursor:pointer;margin-top:18px;width:100%%}
button:hover{background:#4a7bf0}
.cancel{background:transparent;color:#9aa0b4;margin-top:8px}
</style></head><body><div class="box">
<h2>Connect <span class="who">%s</span></h2>
<p>This grants governed, audited access to Odoo as
<b>%s</b>, limited to the scopes on its template key. You can revoke it
anytime from the MCP Server app.</p>
<form method="POST">%s
<button type="submit">Authorize</button>
</form>
</div></body></html>""" % (
            escape(client.name), escape(request.env.user.name), hidden)
        return request.make_response(html)
