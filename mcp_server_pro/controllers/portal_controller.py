# -*- coding: utf-8 -*-
"""Public portal pages at /mcp-page/<tenant_slug>/<slug>.

Server-side rendered from live Odoo data via the page's bound key.
"""
import json
import logging

from odoo import http, _
from odoo.http import request

_logger = logging.getLogger(__name__)


class McpPortalController(http.Controller):

    @http.route("/mcp-page/<string:tenant_slug>/<string:slug>",
                type="http", auth="public", website=False, csrf=False)
    def render_portal_page(self, tenant_slug, slug, **kwargs):
        page = request.env["mcp.portal.page"].sudo().search([
            ("slug", "=", slug),
            ("tenant_id.slug", "=", tenant_slug),
            ("is_published", "=", True),
        ], limit=1)
        if not page:
            return request.not_found()
        if not page.is_public and request.env.user._is_public():
            return request.redirect("/web/login")

        page.sudo().view_count += 1
        widgets = page.sudo().render_widget_data()
        html = self._render_html(page, widgets)
        return request.make_response(
            html, headers=[("Content-Type", "text/html; charset=utf-8")])

    def _render_html(self, page, widgets):
        theme = {
            "light": ("#ffffff", "#1a1a1a", "#f5f5f7", "#0066ff"),
            "dark": ("#13151a", "#e8e8ea", "#1d2027", "#5b8cff"),
            "brand": ("#0b1f3a", "#ffffff", "#13294d", "#ffb400"),
        }.get(page.theme, ("#ffffff", "#1a1a1a", "#f5f5f7", "#0066ff"))
        bg, fg, card, accent = theme

        cards = []
        for w in widgets:
            if w.get("error"):
                cards.append(
                    '<div class="mcp-card"><div class="mcp-title">%s</div>'
                    '<div class="mcp-err">%s</div></div>'
                    % (self._esc(w.get("title", "")), self._esc(w["error"])))
                continue
            wtype = w.get("type")
            if wtype == "kpi":
                cards.append(
                    '<div class="mcp-card mcp-kpi"><div class="mcp-title">%s'
                    '</div><div class="mcp-value">%s</div></div>'
                    % (self._esc(w.get("title", "")),
                       self._esc(str(w.get("value", "")))))
            elif wtype == "table":
                cards.append(self._render_table(w))
            elif wtype == "chart":
                cards.append(self._render_chart(w))
        body = "\n".join(cards)

        return """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --bg:{bg}; --fg:{fg}; --card:{card}; --accent:{accent}; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
  header {{ padding:32px 24px 8px; }}
  h1 {{ margin:0; font-size:24px; font-weight:650; }}
  .sub {{ opacity:.6; font-size:13px; margin-top:4px; }}
  .grid {{ display:grid; gap:16px; padding:24px;
    grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); }}
  .mcp-card {{ background:var(--card); border-radius:14px; padding:20px;
    box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .mcp-title {{ font-size:13px; opacity:.65; margin-bottom:10px;
    text-transform:uppercase; letter-spacing:.04em; }}
  .mcp-kpi .mcp-value {{ font-size:34px; font-weight:700; color:var(--accent); }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:7px 8px;
    border-bottom:1px solid rgba(127,127,127,.18); }}
  th {{ opacity:.6; font-weight:600; }}
  .mcp-err {{ color:#d33; font-size:13px; }}
  footer {{ padding:16px 24px; opacity:.4; font-size:12px; }}
</style></head>
<body>
<header><h1>{title}</h1><div class="sub">Live data · Odoo MCP Server (PRO)</div></header>
<div class="grid">{body}</div>
<footer>Auto-refreshes on load · {tenant}</footer>
</body></html>""".format(
            title=self._esc(page.name), bg=bg, fg=fg, card=card,
            accent=accent, body=body, tenant=self._esc(page.tenant_id.name))

    def _render_table(self, w):
        rows = w.get("rows", [])
        if not rows:
            return ('<div class="mcp-card"><div class="mcp-title">%s</div>'
                    "<div>No data</div></div>" % self._esc(w.get("title", "")))
        headers = [k for k in rows[0].keys() if k != "id"]
        thead = "".join("<th>%s</th>" % self._esc(h) for h in headers)
        trows = ""
        for r in rows:
            tds = "".join("<td>%s</td>" % self._esc(self._cell(r.get(h)))
                          for h in headers)
            trows += "<tr>%s</tr>" % tds
        return ('<div class="mcp-card" style="grid-column:1/-1">'
                '<div class="mcp-title">%s</div>'
                "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>"
                % (self._esc(w.get("title", "")), thead, trows))

    def _render_chart(self, w):
        series = w.get("series", [])
        groupby = w.get("groupby", "")
        measure = w.get("measure", "__count")
        bars = ""
        vals = [(s.get(groupby), s.get(measure) or s.get("__count") or 0)
                for s in series]
        mx = max([v for _, v in vals], default=1) or 1
        for label, v in vals:
            pct = int(v / mx * 100)
            bars += (
                '<div style="margin:6px 0"><div style="font-size:12px;'
                'opacity:.7">%s — %s</div>'
                '<div style="background:var(--accent);height:10px;border-radius:5px;'
                'width:%d%%"></div></div>'
                % (self._esc(self._cell(label)), self._esc(str(v)), pct))
        return ('<div class="mcp-card" style="grid-column:1/-1">'
                '<div class="mcp-title">%s</div>%s</div>'
                % (self._esc(w.get("title", "")), bars))

    @staticmethod
    def _cell(value):
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return str(value[1])
        if value is False or value is None:
            return ""
        return str(value)

    @staticmethod
    def _esc(text):
        from markupsafe import escape
        return str(escape(text))
