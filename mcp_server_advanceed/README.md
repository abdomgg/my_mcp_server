# Odoo MCP Server (PRO)

**More Than Just MCP Server · Build Modules · Connect Claude, ChatGPT & AI Agents**

Turn your Odoo instance into a governed, multi-tenant AI tool server. A single
stable endpoint (`POST /mcp`) exposes Odoo to any MCP-capable client — Claude
Desktop/Web, ChatGPT, Gemini, Cursor, n8n, LangChain, crewAI — through your
own security layer.

## Why this over a basic MCP bridge

| Capability | Basic bridge | **MCP Server (PRO)** |
|---|---|---|
| CRUD over MCP | ✅ | ✅ |
| Per-model scopes | partial | ✅ field allowlist + domain filter + row caps |
| Multi-tenant isolation | ❌ | ✅ hard company/data boundary per tenant |
| Token rotation | ❌ | ✅ multi-token, grace periods, zero downtime |
| Rate limiting | ❌ | ✅ distributed (Redis) + in-memory fallback |
| Audit trail | basic | ✅ full payload capture + retention policy |
| BI/analytics tools | ❌ | ✅ pivot, timeseries, top-N, cohort, funnel |
| Server-side export | ❌ | ✅ CSV / XLSX |
| Batch + async jobs | ❌ | ✅ background job queue |
| AI Module Builder | ❌ | ✅ spec → validated → installable ZIP |
| One-click OAuth connect | ❌ | ✅ OAuth 2.1 + PKCE, per-user keys |
| Public data pages | ❌ | ✅ live server-rendered portal pages |

## Install

1. Copy `mcp_server_pro` into your Odoo 18 addons path.
2. Update the apps list and install **Odoo MCP Server (PRO)**.
3. (Optional) Set a Redis URL in **MCP Server → Configuration → Settings**
   for distributed rate limiting and OAuth code storage.

## Quick start

1. **MCP Server → Tenants** — a *Default Workspace* exists; optionally pin it
   to specific companies for a hard data boundary.
2. **MCP Server → Auth & Tokens → API Keys** — create a key:
   - Choose the **Run As User** (all ops use this user's ACLs).
   - Add **Model Access** rows (deny-by-default): tick CRUD verbs, set a field
     allowlist and a domain filter per model.
   - Tick capabilities (BI, export, batch, module builder).
3. Click **Rotate Token** (or use the Test Connection wizard) to mint a token.
   **Copy it — it is shown only once.**

### Connect Claude Desktop

```json
{
  "mcpServers": {
    "odoo": {
      "url": "https://YOUR-ODOO/mcp",
      "headers": { "Authorization": "Bearer mcp_xxx" }
    }
  }
}
```

### Protocol

- Transport: Streamable HTTP, stateless JSON-RPC 2.0.
- Methods: `initialize`, `ping`, `tools/list`, `tools/call`.
- Auth: `Authorization: Bearer <token>` or `X-MCP-Token: <token>`.

## Tool catalog

- **CRUD**: `list_models`, `describe_model`, `search_records`, `read_records`,
  `create_record`, `update_records`, `delete_records`
- **BI**: `pivot`, `timeseries`, `topn`, `cohort`, `funnel`
- **Export**: `export_dataset` (csv/xlsx)
- **Batch/Async**: `batch_create/update/delete`, `submit_job`, `get_job_status`,
  `get_job_result`, `cancel_job`
- **Builder**: `generate_module_spec`, `build_module_zip`, `install_module_zip`

Each tool is advertised in `tools/list` **only if** the key's capabilities and
scopes permit it.

## Security model

Every call: authenticate token → tenant kill-switch/suspension → rate limit →
dispatch (scope + field + domain enforced) → audit. Keys can only ever
*narrow* the run-as user's permissions, never widen them. The AI runs
externally; this module never bypasses Odoo's own ACLs or record rules.

---

© systm — OPL-1. Generated module output is LGPL-3.
