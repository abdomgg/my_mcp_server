# -*- coding: utf-8 -*-
{
    "name": "Odoo MCP Server (Advanced)",
    "version": "18.0.3.1.0",
    "category": "Productivity/AI",
    "summary": "More Than Just MCP Server — Build Modules · Connect Claude, ChatGPT & AI Agents to live Odoo data, with multi-tenant isolation, scoped keys, BI tools and an AI module builder.",
    "description": """
Odoo MCP Server (advanced)
=====================

Turn Odoo into a governed, multi-tenant AI tool server.

A single stable endpoint (POST /mcp) exposes your Odoo instance to any
MCP-capable client (Claude Desktop/Web, ChatGPT, Gemini, Cursor, n8n,
LangChain, crewAI) with:

* Per-key scopes + Odoo ACLs + record rules (least privilege by default)
* Multi-tenant workspace isolation (hard data boundaries per tenant)
* Field allowlists, domain filters, IP allow/deny lists
* Distributed (Redis) rate limiting with in-memory fallback
* Zero-downtime token rotation with grace periods
* Schema caching with automatic invalidation
* Full audit logs with payload capture and retention policy
* BI/analytics tools: pivot, timeseries, top-N, cohort, funnel, export
* AI Module Builder: spec -> validated -> installable ZIP
* Server-side CSV/XLSX exports
* Batch + async background jobs

This is an integration gateway. AI runs externally and calls approved
tools through Odoo's own security layer. It does NOT bypass Odoo security.
""",
    "author": "Abdulfattah",
    "license": "OPL-1",
    "support": "abdogabr354@gmail.com",
    "price": 180.00,
    "currency": "USD",
    "depends": [
        "base",
        "web",
        "mail",
    ],
    "external_dependencies": {
        "python": [],
    },
    "data": [
        "security/mcp_security.xml",
        "security/ir.model.access.csv",
        "data/mcp_data.xml",
        "data/ir_cron.xml",
        "views/mcp_tenant_views.xml",
        "views/mcp_api_key_views.xml",
        "views/mcp_model_access_views.xml",
        "views/mcp_audit_log_views.xml",
        "views/mcp_job_views.xml",
        "views/mcp_generated_module_views.xml",
        "views/mcp_portal_page_views.xml",
        "views/mcp_oauth_client_views.xml",
        "views/mcp_config_settings_views.xml",
        "wizard/mcp_key_rotate_wizard_views.xml",
        "wizard/mcp_test_connection_wizard_views.xml",
        "views/mcp_dashboard_views.xml",
        "views/mcp_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mcp_server_advanceed/static/src/scss/mcp_dashboard.scss",
            "mcp_server_advanceed/static/src/js/mcp_dashboard.js",
            "mcp_server_advanceed/static/src/dashboard/dashboard.scss",
            "mcp_server_advanceed/static/src/dashboard/dashboard.js",
            "mcp_server_advanceed/static/src/dashboard/dashboard.xml",
        ],
    },
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": True,
    "auto_install": False,
}
