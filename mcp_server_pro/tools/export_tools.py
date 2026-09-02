# -*- coding: utf-8 -*-
"""Server-side dataset export to CSV / XLSX (returned as base64)."""
import base64
import csv
import io
import logging

from odoo import api, models, _
from odoo.exceptions import UserError, AccessError

_logger = logging.getLogger(__name__)


class McpToolExport(models.AbstractModel):
    _name = "mcp.tool.export"
    _description = "MCP Export Tools"

    def export_dataset(self, model_name, args, access=None, key=None):
        Model = self.env[model_name]
        if key:
            Model = Model.with_user(key.user_id)
            access = key.model_access_ids.filtered(
                lambda a: a.model_name == model_name)[:1]
        domain = list(args.get("domain") or [])
        if access:
            domain += access.get_domain()

        # Field allowlist enforcement.
        fields_req = args.get("fields")
        if access:
            allowed = access.get_allowed_fields()
            if allowed is not None:
                if fields_req:
                    blocked = set(fields_req) - set(allowed)
                    if blocked:
                        raise AccessError(_("Fields not allowed: %s")
                                          % ", ".join(blocked))
                else:
                    fields_req = allowed
        if not fields_req:
            fields_req = ["id", "display_name"]

        cap = int(self.env["ir.config_parameter"].sudo().get_param(
            "mcp_server_pro.export_row_cap", "50000"))
        rows = Model.search_read(domain, fields_req, limit=cap)

        fmt = args.get("format", "csv")
        if fmt == "csv":
            content = self._to_csv(fields_req, rows)
            mimetype = "text/csv"
            ext = "csv"
        elif fmt == "xlsx":
            content = self._to_xlsx(fields_req, rows, model_name)
            mimetype = ("application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet")
            ext = "xlsx"
        else:
            raise UserError(_("Unsupported format: %s") % fmt)

        return {
            "model": model_name,
            "format": fmt,
            "row_count": len(rows),
            "filename": "%s_export.%s" % (model_name.replace(".", "_"), ext),
            "mimetype": mimetype,
            "content_b64": base64.b64encode(content).decode("ascii"),
        }

    def _to_csv(self, headers, rows):
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({h: self._flatten(r.get(h)) for h in headers})
        return buf.getvalue().encode("utf-8-sig")

    def _to_xlsx(self, headers, rows, model_name):
        try:
            import xlsxwriter
        except ImportError:
            raise UserError(_(
                "xlsxwriter not installed on the server. Use CSV format or "
                "install python3-xlsxwriter."))
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {"in_memory": True})
        ws = wb.add_worksheet(model_name[:31])
        bold = wb.add_format({"bold": True})
        for c, h in enumerate(headers):
            ws.write(0, c, h, bold)
        for r_i, row in enumerate(rows, start=1):
            for c_i, h in enumerate(headers):
                ws.write(r_i, c_i, self._flatten(row.get(h)))
        wb.close()
        return buf.getvalue()

    @staticmethod
    def _flatten(value):
        if isinstance(value, (list, tuple)):
            # m2o -> (id, name); take name
            if len(value) == 2 and isinstance(value[0], int):
                return value[1]
            return ", ".join(str(v) for v in value)
        if value is False or value is None:
            return ""
        return value
