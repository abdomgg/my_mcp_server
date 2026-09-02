# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mcp_redis_url = fields.Char(
        string="Redis URL",
        config_parameter="mcp_server_pro.redis_url",
        help="redis://host:6379/0 — optional. Falls back to in-memory.")
    mcp_audit_retention_days = fields.Integer(
        string="Audit Retention (days)", default=90,
        config_parameter="mcp_server_pro.audit_retention_days")
    mcp_default_max_rows = fields.Integer(
        string="Default Max Rows", default=500,
        config_parameter="mcp_server_pro.default_max_rows")
    mcp_export_row_cap = fields.Integer(
        string="Export Row Cap", default=50000,
        config_parameter="mcp_server_pro.export_row_cap")
    mcp_enable_module_builder = fields.Boolean(
        string="Enable Module Builder Globally", default=True,
        config_parameter="mcp_server_pro.enable_module_builder")
    mcp_capture_payloads_default = fields.Boolean(
        string="Capture Payloads by Default", default=True,
        config_parameter="mcp_server_pro.capture_payloads_default")
