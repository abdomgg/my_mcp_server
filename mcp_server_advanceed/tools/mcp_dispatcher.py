# -*- coding: utf-8 -*-
"""MCP tool dispatcher.

Maps MCP tool names to implementations and enforces per-call authorization
against the API key's model-access grants and capability flags. This is the
single chokepoint every tool call passes through.
"""
import logging

from odoo import api, models, _
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class McpDispatcher(models.AbstractModel):
    _name = "mcp.dispatcher"
    _description = "MCP Tool Dispatcher"

    # ------------------------------------------------------------------ #
    #  Tool catalog (advertised via tools/list)                          #
    # ------------------------------------------------------------------ #
    @api.model
    def list_tools(self, key):
        """Return the MCP tool definitions this key is allowed to use."""
        tools = []
        tools += self._crud_tool_defs()
        if key.allow_bi_tools:
            tools += self._bi_tool_defs()
        if key.allow_export:
            tools += self._export_tool_defs()
        if key.allow_batch_async:
            tools += self._batch_tool_defs()
        if key.allow_module_builder:
            tools += self._builder_tool_defs()
        return tools

    # ------------------------------------------------------------------ #
    #  Dispatch                                                          #
    # ------------------------------------------------------------------ #
    @api.model
    def dispatch(self, key, tool_name, arguments):
        """Route a tool call. Returns a JSON-serializable result.

        Raises AccessError / UserError which the controller maps to MCP
        error responses and audit entries.
        """
        arguments = arguments or {}
        handler = getattr(self, "_t_" + tool_name, None)
        if handler is None:
            raise UserError(_("Unknown tool: %s") % tool_name)
        return handler(key, arguments)

    # ---- access helper ------------------------------------------------- #
    def _resolve_access(self, key, model_name, verb):
        """Return the mcp.model.access record granting `verb` on model.

        verb in {read, create, write, unlink}. Raises AccessError if denied.
        """
        access = key.model_access_ids.filtered(
            lambda a: a.model_name == model_name)
        if not access:
            raise AccessError(_(
                "Key '%s' has no access to model '%s'.")
                % (key.name, model_name))
        access = access[0]
        flag = {"read": access.can_read, "create": access.can_create,
                "write": access.can_write, "unlink": access.can_unlink}.get(verb)
        if not flag:
            raise AccessError(_(
                "Key '%s' is not allowed to %s on '%s'.")
                % (key.name, verb, model_name))
        # Tenant company boundary check.
        if key.tenant_id.company_ids:
            allowed_companies = key.tenant_id.company_ids.ids
            user_companies = key.user_id.company_ids.ids
            if not set(user_companies) & set(allowed_companies):
                raise AccessError(_(
                    "Tenant company boundary blocks this key from the "
                    "run-as user's companies."))
        return access

    # ================================================================== #
    #  CRUD tools                                                        #
    # ================================================================== #
    def _t_search_records(self, key, args):
        access = self._resolve_access(key, args["model"], "read")
        return self.env["mcp.tool.crud"].search_records(
            args["model"], args.get("domain", []),
            args.get("fields"), args.get("limit"),
            args.get("offset", 0), args.get("order"), access=access, key=key)

    def _t_read_records(self, key, args):
        access = self._resolve_access(key, args["model"], "read")
        return self.env["mcp.tool.crud"].read_records(
            args["model"], args["ids"], args.get("fields"),
            access=access, key=key)

    def _t_create_record(self, key, args):
        access = self._resolve_access(key, args["model"], "create")
        return self.env["mcp.tool.crud"].create_record(
            args["model"], args["values"], access=access, key=key)

    def _t_update_records(self, key, args):
        access = self._resolve_access(key, args["model"], "write")
        return self.env["mcp.tool.crud"].update_records(
            args["model"], args["ids"], args["values"], access=access, key=key)

    def _t_delete_records(self, key, args):
        access = self._resolve_access(key, args["model"], "unlink")
        return self.env["mcp.tool.crud"].delete_records(
            args["model"], args["ids"], access=access, key=key)

    def _t_describe_model(self, key, args):
        access = self._resolve_access(key, args["model"], "read")
        return self.env["mcp.tool.crud"].describe_model(
            args["model"], access=access, key=key)

    def _t_list_models(self, key, args):
        return [{"model": a.model_name,
                 "crud": {"read": a.can_read, "create": a.can_create,
                          "write": a.can_write, "unlink": a.can_unlink}}
                for a in key.model_access_ids]

    # ================================================================== #
    #  BI tools                                                          #
    # ================================================================== #
    def _t_pivot(self, key, args):
        self._resolve_access(key, args["model"], "read")
        return self.env["mcp.tool.bi"].pivot(key, args)

    def _t_timeseries(self, key, args):
        self._resolve_access(key, args["model"], "read")
        return self.env["mcp.tool.bi"].timeseries(key, args)

    def _t_topn(self, key, args):
        self._resolve_access(key, args["model"], "read")
        return self.env["mcp.tool.bi"].topn(key, args)

    def _t_cohort(self, key, args):
        self._resolve_access(key, args["model"], "read")
        return self.env["mcp.tool.bi"].cohort(key, args)

    def _t_funnel(self, key, args):
        self._resolve_access(key, args["model"], "read")
        return self.env["mcp.tool.bi"].funnel(key, args)

    # ================================================================== #
    #  Export                                                            #
    # ================================================================== #
    def _t_export_dataset(self, key, args):
        if not key.allow_export:
            raise AccessError(_("Export not allowed for this key."))
        self._resolve_access(key, args["model"], "read")
        return self.env["mcp.tool.export"].export_dataset(
            args["model"], args, access=None, key=key)

    # ================================================================== #
    #  Batch / async                                                     #
    # ================================================================== #
    def _t_batch_create(self, key, args):
        access = self._resolve_access(key, args["model"], "create")
        return self.env["mcp.tool.crud"].batch_create(
            args["model"], args["records"], access=access, key=key)

    def _t_batch_update(self, key, args):
        access = self._resolve_access(key, args["model"], "write")
        return self.env["mcp.tool.crud"].batch_update(
            args["model"], args["updates"], access=access, key=key)

    def _t_batch_delete(self, key, args):
        access = self._resolve_access(key, args["model"], "unlink")
        return self.env["mcp.tool.crud"].batch_delete(
            args["model"], args["ids"], access=access, key=key)

    def _t_submit_job(self, key, args):
        import json
        if not key.allow_batch_async:
            raise AccessError(_("Async jobs not allowed for this key."))
        job = self.env["mcp.job"].create({
            "name": args.get("name", args["job_type"]),
            "tenant_id": key.tenant_id.id,
            "key_id": key.id,
            "user_id": key.user_id.id,
            "job_type": args["job_type"],
            "model_name": args.get("model"),
            "payload": json.dumps(args.get("payload", {})),
        })
        return {"job_id": job.id, "state": job.state}

    def _t_get_job_status(self, key, args):
        job = self.env["mcp.job"].search(
            [("id", "=", args["job_id"]), ("tenant_id", "=", key.tenant_id.id)])
        if not job:
            raise UserError(_("Job not found."))
        return {"job_id": job.id, "state": job.state, "progress": job.progress,
                "error": job.error_message}

    def _t_get_job_result(self, key, args):
        import json
        job = self.env["mcp.job"].search(
            [("id", "=", args["job_id"]), ("tenant_id", "=", key.tenant_id.id)])
        if not job:
            raise UserError(_("Job not found."))
        return {"job_id": job.id, "state": job.state,
                "result": json.loads(job.result or "null")}

    def _t_cancel_job(self, key, args):
        job = self.env["mcp.job"].search(
            [("id", "=", args["job_id"]), ("tenant_id", "=", key.tenant_id.id)])
        if not job:
            raise UserError(_("Job not found."))
        job.action_cancel()
        return {"job_id": job.id, "state": job.state}

    # ================================================================== #
    #  Module builder                                                    #
    # ================================================================== #
    def _t_generate_module_spec(self, key, args):
        if not key.allow_module_builder:
            raise AccessError(_("Module builder not allowed for this key."))
        return self.env["mcp.tool.builder"].generate_module_spec(args, key=key)

    def _t_build_module_zip(self, key, args):
        if not key.allow_module_builder:
            raise AccessError(_("Module builder not allowed for this key."))
        return self.env["mcp.tool.builder"].build_module_zip(
            args["spec"], key=key)

    def _t_install_module_zip(self, key, args):
        if not key.allow_module_builder:
            raise AccessError(_("Module builder not allowed for this key."))
        return self.env["mcp.tool.builder"].install_module_zip(
            args["module_name"], args["zip_b64"], key=key)

    # ================================================================== #
    #  Tool schema definitions (MCP tools/list payload)                  #
    # ================================================================== #
    def _crud_tool_defs(self):
        obj = {"type": "object"}
        return [
            {"name": "list_models",
             "description": "List Odoo models this key can access with their CRUD permissions.",
             "inputSchema": {**obj, "properties": {}}},
            {"name": "describe_model",
             "description": "Get field metadata (name, type, relation, required) for a model.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"}}, "required": ["model"]}},
            {"name": "search_records",
             "description": "Search records by Odoo domain. Returns matching rows with selected fields.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "domain": {"type": "array"},
                 "fields": {"type": "array", "items": {"type": "string"}},
                 "limit": {"type": "integer"},
                 "offset": {"type": "integer"},
                 "order": {"type": "string"}},
                 "required": ["model"]}},
            {"name": "read_records",
             "description": "Read specific records by ID.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "ids": {"type": "array", "items": {"type": "integer"}},
                 "fields": {"type": "array", "items": {"type": "string"}}},
                 "required": ["model", "ids"]}},
            {"name": "create_record",
             "description": "Create one record. Respects field allowlist.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "values": {"type": "object"}},
                 "required": ["model", "values"]}},
            {"name": "update_records",
             "description": "Update records by ID.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "ids": {"type": "array", "items": {"type": "integer"}},
                 "values": {"type": "object"}},
                 "required": ["model", "ids", "values"]}},
            {"name": "delete_records",
             "description": "Delete records by ID.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "ids": {"type": "array", "items": {"type": "integer"}}},
                 "required": ["model", "ids"]}},
        ]

    def _bi_tool_defs(self):
        obj = {"type": "object"}
        return [
            {"name": "pivot",
             "description": "Pivot-table aggregation: group rows/cols by fields and aggregate a measure.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "domain": {"type": "array"},
                 "rows": {"type": "array", "items": {"type": "string"}},
                 "measure": {"type": "string"},
                 "agg": {"type": "string", "enum": ["sum", "avg", "count", "min", "max"]}},
                 "required": ["model", "rows"]}},
            {"name": "timeseries",
             "description": "Date-bucketed trend of a measure with optional gap filling.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "domain": {"type": "array"},
                 "date_field": {"type": "string"},
                 "interval": {"type": "string", "enum": ["day", "week", "month", "quarter", "year"]},
                 "measure": {"type": "string"},
                 "fill_gaps": {"type": "boolean"}},
                 "required": ["model", "date_field", "interval"]}},
            {"name": "topn",
             "description": "Top-N records/groups ranked by a measure.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "domain": {"type": "array"},
                 "groupby": {"type": "string"},
                 "measure": {"type": "string"},
                 "n": {"type": "integer"}},
                 "required": ["model", "groupby"]}},
            {"name": "cohort",
             "description": "Cohort retention analysis across two date fields.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "domain": {"type": "array"},
                 "cohort_field": {"type": "string"},
                 "event_field": {"type": "string"},
                 "interval": {"type": "string"}},
                 "required": ["model", "cohort_field", "event_field"]}},
            {"name": "funnel",
             "description": "Conversion funnel across ordered stage values of a field.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "domain": {"type": "array"},
                 "stage_field": {"type": "string"},
                 "stages": {"type": "array", "items": {"type": "string"}}},
                 "required": ["model", "stage_field", "stages"]}},
        ]

    def _export_tool_defs(self):
        obj = {"type": "object"}
        return [
            {"name": "export_dataset",
             "description": "Export records to CSV or XLSX (base64). Respects field allowlist and row caps.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "domain": {"type": "array"},
                 "fields": {"type": "array", "items": {"type": "string"}},
                 "format": {"type": "string", "enum": ["csv", "xlsx"]}},
                 "required": ["model", "format"]}},
        ]

    def _batch_tool_defs(self):
        obj = {"type": "object"}
        return [
            {"name": "batch_create",
             "description": "Create many records in one call.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "records": {"type": "array"}},
                 "required": ["model", "records"]}},
            {"name": "batch_update",
             "description": "Update many records; each update has ids+values.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "updates": {"type": "array"}},
                 "required": ["model", "updates"]}},
            {"name": "batch_delete",
             "description": "Delete many records by ID.",
             "inputSchema": {**obj, "properties": {
                 "model": {"type": "string"},
                 "ids": {"type": "array"}},
                 "required": ["model", "ids"]}},
            {"name": "submit_job",
             "description": "Offload heavy batch work to a background job. Returns job_id.",
             "inputSchema": {**obj, "properties": {
                 "job_type": {"type": "string"},
                 "model": {"type": "string"},
                 "payload": {"type": "object"},
                 "name": {"type": "string"}},
                 "required": ["job_type"]}},
            {"name": "get_job_status",
             "description": "Poll a background job's status and progress.",
             "inputSchema": {**obj, "properties": {
                 "job_id": {"type": "integer"}}, "required": ["job_id"]}},
            {"name": "get_job_result",
             "description": "Fetch a completed job's result.",
             "inputSchema": {**obj, "properties": {
                 "job_id": {"type": "integer"}}, "required": ["job_id"]}},
            {"name": "cancel_job",
             "description": "Cancel a queued or running job.",
             "inputSchema": {**obj, "properties": {
                 "job_id": {"type": "integer"}}, "required": ["job_id"]}},
        ]

    def _builder_tool_defs(self):
        obj = {"type": "object"}
        return [
            {"name": "generate_module_spec",
             "description": "Produce a validated Odoo module spec (JSON) from a natural-language description.",
             "inputSchema": {**obj, "properties": {
                 "description": {"type": "string"},
                 "module_name": {"type": "string"}},
                 "required": ["description"]}},
            {"name": "build_module_zip",
             "description": "Generate an installable Odoo module ZIP (base64) from a validated spec.",
             "inputSchema": {**obj, "properties": {
                 "spec": {"type": "object"}}, "required": ["spec"]}},
            {"name": "install_module_zip",
             "description": "Install a generated module ZIP into Odoo.",
             "inputSchema": {**obj, "properties": {
                 "module_name": {"type": "string"},
                 "zip_b64": {"type": "string"}},
                 "required": ["module_name", "zip_b64"]}},
        ]
