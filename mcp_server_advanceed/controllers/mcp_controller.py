# -*- coding: utf-8 -*-
"""The /mcp endpoint.

Speaks MCP over JSON-RPC 2.0 (Streamable HTTP, stateless JSON). Handles:
  initialize, tools/list, tools/call, ping

Request pipeline for every tools/call:
  1. Authenticate token (hash lookup, key active, not expired, IP allowed)
  2. Tenant kill-switch + suspension check
  3. Rate limit (distributed, per effective key limits)
  4. Dispatch to the tool layer (scopes enforced there)
  5. Audit log (status, duration, payloads if enabled)
"""
import json
import logging
import time

from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import AccessError, UserError, ValidationError

from ..models.mcp_api_key import _hash_token

_logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "odoo-mcp-server-pro", "version": "18.0.2.0.0"}


def _jsonrpc_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


class McpController(http.Controller):

    @http.route("/mcp", type="http", auth="none",
                methods=["POST", "GET", "OPTIONS"],
                csrf=False, save_session=False)
    def mcp_endpoint(self, **kwargs):
        started = time.time()

        # CORS preflight.
        if request.httprequest.method == "OPTIONS":
            return request.make_response("", headers=self._cors())

        # A bare GET is a discovery probe — answer with the 401 challenge so
        # the client knows where to find OAuth metadata.
        if request.httprequest.method == "GET":
            return self._auth_challenge()

        try:
            body = json.loads(request.httprequest.get_data() or b"{}")
        except Exception:
            return self._http_json(_jsonrpc_error(None, -32700, "Parse error"))

        # For methods that require auth, if NO token was supplied at all,
        # return a real 401 + WWW-Authenticate so claude.ai starts OAuth.
        probe = body[0] if isinstance(body, list) and body else body
        method = probe.get("method") if isinstance(probe, dict) else None
        if method not in (None, "initialize", "ping",
                          "notifications/initialized"):
            auth = self._authenticate()
            if auth.get("no_token"):
                return self._auth_challenge()

        # Batch support: a list of JSON-RPC calls.
        if isinstance(body, list):
            return self._http_json([self._handle_one(m, started) for m in body])
        return self._http_json(self._handle_one(body, started))

    def _auth_challenge(self):
        """401 with WWW-Authenticate pointing at our protected-resource doc."""
        base = request.env["ir.config_parameter"].sudo().get_param(
            "web.base.url")
        www_auth = ('Bearer resource_metadata='
                    '"%s/.well-known/oauth-protected-resource"' % base)
        return request.make_response(
            json.dumps({"error": "unauthorized"}),
            status=401,
            headers=[("Content-Type", "application/json"),
                     ("WWW-Authenticate", www_auth)] + self._cors())

    @staticmethod
    def _cors():
        return [
            ("Access-Control-Allow-Origin", "*"),
            ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
            ("Access-Control-Allow-Headers", "Authorization, Content-Type"),
        ]

    # ------------------------------------------------------------------ #
    def _handle_one(self, msg, started):
        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}

        # Methods that don't require auth.
        if method == "initialize":
            return _jsonrpc_result(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            })
        if method == "ping":
            return _jsonrpc_result(req_id, {})
        if method == "notifications/initialized":
            return _jsonrpc_result(req_id, {})

        # Authenticate for everything else.
        auth = self._authenticate()
        if auth.get("error"):
            return _jsonrpc_error(req_id, -32001, auth["error"])
        key = auth["key"]
        token = auth["token"]

        if method == "tools/list":
            tools = request.env["mcp.dispatcher"].sudo().list_tools(key)
            return _jsonrpc_result(req_id, {"tools": tools})

        if method == "tools/call":
            return self._handle_tool_call(req_id, key, token, params, started)

        return _jsonrpc_error(req_id, -32601, "Method not found: %s" % method)

    # ------------------------------------------------------------------ #
    def _authenticate(self):
        """Resolve the bearer token to an active key+token, with gates."""
        header = request.httprequest.headers.get("Authorization", "")
        raw = None
        if header.lower().startswith("bearer "):
            raw = header[7:].strip()
        if not raw:
            raw = request.httprequest.headers.get("X-MCP-Token")
        if not raw:
            return {"error": "Missing bearer token.", "no_token": True}

        token = request.env["mcp.token"].sudo().search(
            [("token_hash", "=", _hash_token(raw)),
             ("is_refresh", "=", False)], limit=1)
        if not token:
            return {"error": "Invalid token."}
        if not token.is_valid_now():
            return {"error": "Token revoked or expired."}

        key = token.key_id
        if not key.active:
            return {"error": "Key disabled."}
        if key.is_expired:
            return {"error": "Key expired."}
        if key.tenant_id.is_suspended:
            return {"error": "Tenant suspended."}

        remote_ip = request.httprequest.remote_addr
        if not key.check_ip_allowed(remote_ip):
            return {"error": "IP not allowed."}

        return {"key": key, "token": token}

    # ------------------------------------------------------------------ #
    def _handle_tool_call(self, req_id, key, token, params, started):
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        remote_ip = request.httprequest.remote_addr
        Audit = request.env["mcp.audit.log"].sudo()

        # --- Rate limit -------------------------------------------------
        per_sec, per_min = key.get_effective_rate_limits()
        rl_key = "%s:%s" % (key.tenant_id.id, key.id)
        allowed, retry_after = request.env["mcp.rate.limiter"].sudo().check(
            rl_key, per_sec, per_min)
        if not allowed:
            Audit.log({
                "tenant_id": key.tenant_id.id, "key_id": key.id,
                "key_name": key.name, "user_id": key.user_id.id,
                "tool_name": tool_name, "method": "tools/call",
                "status": "rate_limited", "remote_ip": remote_ip,
                "duration_ms": int((time.time() - started) * 1000),
            })
            return _jsonrpc_error(
                req_id, -32002, "Rate limit exceeded",
                {"retry_after": retry_after})

        # --- Dispatch ---------------------------------------------------
        capture = key.capture_payloads
        try:
            result = request.env["mcp.dispatcher"].sudo().dispatch(
                key, tool_name, arguments)
            status = "ok"
            error_msg = None
        except (AccessError,) as e:
            result, status, error_msg = None, "denied", str(e)
        except (UserError, ValidationError) as e:
            result, status, error_msg = None, "error", str(e)
        except Exception as e:  # pragma: no cover
            _logger.exception("MCP tool %s crashed", tool_name)
            result, status, error_msg = None, "error", "Internal error: %s" % e

        duration = int((time.time() - started) * 1000)

        # --- Bookkeeping ------------------------------------------------
        key.sudo().write({
            "last_used": fields.Datetime.now(),
            "request_count": key.request_count + 1,
        })
        token.sudo().last_used = fields.Datetime.now()

        # --- Audit ------------------------------------------------------
        record_count = 0
        if isinstance(result, dict):
            record_count = (result.get("count")
                            or len(result.get("records", []))
                            or len(result.get("created", []))
                            or 0)
        Audit.log({
            "tenant_id": key.tenant_id.id, "key_id": key.id,
            "key_name": key.name, "user_id": key.user_id.id,
            "tool_name": tool_name, "method": "tools/call",
            "model_name": arguments.get("model"),
            "status": status,
            "error_message": error_msg,
            "remote_ip": remote_ip,
            "duration_ms": duration,
            "record_count": record_count,
            "request_payload": json.dumps(arguments, default=str)
            if capture else None,
            "response_payload": json.dumps(result, default=str)[:50000]
            if (capture and result is not None) else None,
        })

        if status != "ok":
            code = -32003 if status == "denied" else -32000
            return _jsonrpc_error(req_id, code, error_msg)

        # MCP tools/call result shape: content array.
        return _jsonrpc_result(req_id, {
            "content": [{"type": "text",
                         "text": json.dumps(result, default=str)}],
            "isError": False,
        })

    # ------------------------------------------------------------------ #
    @staticmethod
    def _http_json(payload):
        return request.make_response(
            json.dumps(payload, default=str),
            headers=[("Content-Type", "application/json"),
                     ("Access-Control-Allow-Origin", "*")])
