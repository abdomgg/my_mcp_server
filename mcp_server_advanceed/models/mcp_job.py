# -*- coding: utf-8 -*-
import json
import logging
import traceback

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class McpJob(models.Model):
    """Background job for heavy/long-running MCP operations.

    submit_job enqueues; an ir.cron drains the queue. Status and results are
    queryable via get_job_status / get_job_result. Jobs are tenant-scoped.
    """

    _name = "mcp.job"
    _description = "MCP Background Job"
    _order = "create_date desc"

    name = fields.Char(required=True)
    tenant_id = fields.Many2one("mcp.tenant", index=True, ondelete="cascade")
    key_id = fields.Many2one("mcp.api.key", index=True, ondelete="set null")
    user_id = fields.Many2one("res.users")

    job_type = fields.Selection(
        [("batch_create", "Batch Create"),
         ("batch_update", "Batch Update"),
         ("batch_delete", "Batch Delete"),
         ("export", "Export"),
         ("custom", "Custom")],
        required=True)
    model_name = fields.Char()

    payload = fields.Text(help="JSON job input.")
    result = fields.Text(help="JSON job output.")

    state = fields.Selection(
        [("queued", "Queued"),
         ("running", "Running"),
         ("done", "Done"),
         ("failed", "Failed"),
         ("cancelled", "Cancelled")],
        default="queued", index=True, required=True)
    progress = fields.Integer(default=0, help="0-100.")
    error_message = fields.Text()

    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)

    def action_cancel(self):
        self.filtered(lambda j: j.state in ("queued", "running")).write(
            {"state": "cancelled"})

    @api.model
    def _cron_run_jobs(self, limit=10):
        """Drain the queue. Each job runs as its key's run-as user."""
        jobs = self.search([("state", "=", "queued")], limit=limit,
                            order="create_date asc")
        for job in jobs:
            job._run()

    def _run(self):
        self.ensure_one()
        if self.state != "queued":
            return
        self.write({"state": "running", "started_at": fields.Datetime.now()})
        self.env.cr.commit()  # checkpoint so status is visible mid-run
        try:
            run_uid = self.key_id.user_id.id if self.key_id else self.env.uid
            executor = self.with_user(run_uid)
            result = executor._dispatch()
            self.write({
                "state": "done",
                "progress": 100,
                "result": json.dumps(result, default=str),
                "finished_at": fields.Datetime.now(),
            })
        except Exception as e:
            _logger.exception("MCP job %s failed", self.id)
            self.write({
                "state": "failed",
                "error_message": "%s\n%s" % (e, traceback.format_exc()),
                "finished_at": fields.Datetime.now(),
            })

    def _dispatch(self):
        """Execute job body. Delegates to the CRUD tool layer."""
        self.ensure_one()
        payload = json.loads(self.payload or "{}")
        Crud = self.env["mcp.tool.crud"]
        model = self.model_name
        if self.job_type == "batch_create":
            return Crud.batch_create(model, payload.get("records", []),
                                     access=None, job=self)
        if self.job_type == "batch_update":
            return Crud.batch_update(model, payload.get("updates", []),
                                     access=None, job=self)
        if self.job_type == "batch_delete":
            return Crud.batch_delete(model, payload.get("ids", []),
                                     access=None, job=self)
        if self.job_type == "export":
            return self.env["mcp.tool.export"].export_dataset(
                model, payload, access=None)
        return {"error": "unknown job_type"}
