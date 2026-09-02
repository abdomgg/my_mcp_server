# -*- coding: utf-8 -*-
"""Dashboard aggregation — feeds the OWL control-plane view.

One JSON-RPC entry point (get_dashboard) returns everything the cockpit
needs in a single round trip: headline counters, time-bucketed request
volume, status mix, tool leaderboard, slowest tools, busiest keys, and the
live tail of recent calls. All computed with read_group for speed.
"""
import logging
from collections import OrderedDict
from datetime import datetime, timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class McpDashboard(models.AbstractModel):
    _name = "mcp.dashboard"
    _description = "MCP Dashboard Data"

    @api.model
    def get_dashboard(self, tenant_id=None, hours=24):
        Audit = self.env["mcp.audit.log"].sudo()
        now = fields.Datetime.now()
        since = now - timedelta(hours=hours)

        base = [("create_date", ">=", since)]
        if tenant_id:
            base.append(("tenant_id", "=", tenant_id))

        return {
            "generated_at": fields.Datetime.to_string(now),
            "window_hours": hours,
            "counters": self._counters(base, tenant_id),
            "volume": self._volume_series(base, hours),
            "status_mix": self._status_mix(base),
            "top_tools": self._top_tools(base),
            "slowest_tools": self._slowest_tools(base),
            "busy_keys": self._busy_keys(base),
            "recent": self._recent(base),
            "tenants": self._tenant_health(),
        }

    # ------------------------------------------------------------------ #
    def _counters(self, base, tenant_id):
        Audit = self.env["mcp.audit.log"].sudo()
        Key = self.env["mcp.api.key"].sudo()
        Tenant = self.env["mcp.tenant"].sudo()

        total = Audit.search_count(base)
        ok = Audit.search_count(base + [("status", "=", "ok")])
        denied = Audit.search_count(base + [("status", "=", "denied")])
        errored = Audit.search_count(base + [("status", "=", "error")])
        limited = Audit.search_count(base + [("status", "=", "rate_limited")])

        # avg duration
        grp = Audit.read_group(base, ["duration_ms:avg"], [])
        avg_ms = round(grp[0].get("duration_ms") or 0, 1) if grp else 0

        # rows touched
        rec_grp = Audit.read_group(base, ["record_count:sum"], [])
        records = int(rec_grp[0].get("record_count") or 0) if rec_grp else 0

        key_dom = [("active", "=", True)]
        tenant_dom = []
        if tenant_id:
            key_dom.append(("tenant_id", "=", tenant_id))
            tenant_dom.append(("id", "=", tenant_id))

        success_rate = round((ok / total * 100), 1) if total else 100.0

        return {
            "total_requests": total,
            "ok": ok,
            "denied": denied,
            "error": errored,
            "rate_limited": limited,
            "success_rate": success_rate,
            "avg_ms": avg_ms,
            "records_touched": records,
            "active_keys": Key.search_count(key_dom),
            "tenants": Tenant.search_count(tenant_dom),
            "suspended_tenants": Tenant.search_count(
                tenant_dom + [("is_suspended", "=", True)]),
        }

    def _volume_series(self, base, hours):
        """Bucket request volume by hour (or day for long windows)."""
        Audit = self.env["mcp.audit.log"].sudo()
        interval = "hour" if hours <= 48 else "day"
        gb = "create_date:%s" % interval
        data = Audit.read_group(base, ["__count"], [gb], lazy=False)
        series = []
        for row in data:
            series.append({
                "label": row.get(gb),
                "count": row.get("__count", 0),
            })
        return {"interval": interval, "points": series}

    def _status_mix(self, base):
        Audit = self.env["mcp.audit.log"].sudo()
        data = Audit.read_group(base, ["__count"], ["status"], lazy=False)
        return [{"status": d.get("status") or "unknown",
                 "count": d.get("__count", 0)} for d in data]

    def _top_tools(self, base, limit=8):
        Audit = self.env["mcp.audit.log"].sudo()
        data = Audit.read_group(base, ["__count"], ["tool_name"], lazy=False)
        data = [d for d in data if d.get("tool_name")]
        data.sort(key=lambda d: d.get("__count", 0), reverse=True)
        return [{"tool": d["tool_name"], "count": d.get("__count", 0)}
                for d in data[:limit]]

    def _slowest_tools(self, base, limit=6):
        Audit = self.env["mcp.audit.log"].sudo()
        data = Audit.read_group(
            base, ["duration_ms:avg", "__count"], ["tool_name"], lazy=False)
        data = [d for d in data if d.get("tool_name")]
        data.sort(key=lambda d: d.get("duration_ms") or 0, reverse=True)
        return [{"tool": d["tool_name"],
                 "avg_ms": round(d.get("duration_ms") or 0, 1),
                 "count": d.get("__count", 0)} for d in data[:limit]]

    def _busy_keys(self, base, limit=6):
        Audit = self.env["mcp.audit.log"].sudo()
        data = Audit.read_group(base, ["__count"], ["key_name"], lazy=False)
        data = [d for d in data if d.get("key_name")]
        data.sort(key=lambda d: d.get("__count", 0), reverse=True)
        return [{"key": d["key_name"], "count": d.get("__count", 0)}
                for d in data[:limit]]

    def _recent(self, base, limit=12):
        Audit = self.env["mcp.audit.log"].sudo()
        recs = Audit.search_read(
            base, ["create_date", "tool_name", "model_name", "status",
                   "duration_ms", "key_name", "record_count"],
            limit=limit, order="create_date desc")
        return recs

    def _tenant_health(self):
        Tenant = self.env["mcp.tenant"].sudo()
        out = []
        for t in Tenant.search([]):
            out.append({
                "id": t.id,
                "name": t.name,
                "suspended": t.is_suspended,
                "keys": t.api_key_count,
                "rate_sec": t.rate_limit_per_sec,
                "rate_min": t.rate_limit_per_min,
            })
        return out
