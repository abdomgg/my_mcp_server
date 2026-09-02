# -*- coding: utf-8 -*-
"""CRUD tool implementations.

Every method runs as the key's run-as user and applies the model-access
record's domain filter, field allowlist and max-row cap on top of Odoo's
native ACLs and record rules.
"""
import logging

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class McpToolCrud(models.AbstractModel):
    _name = "mcp.tool.crud"
    _description = "MCP CRUD Tools"

    # ---- helpers --------------------------------------------------------
    def _model_as_user(self, model_name, key):
        return self.env[model_name].with_user(key.user_id)

    def _apply_field_filter(self, access, fields_req, model):
        allowed = access.get_allowed_fields() if access else None
        if allowed is None:
            return fields_req  # all allowed
        if fields_req:
            blocked = set(fields_req) - set(allowed)
            if blocked:
                raise AccessError(_(
                    "Fields not in allowlist: %s") % ", ".join(sorted(blocked)))
            return fields_req
        return allowed

    def _scrub_write_values(self, access, values):
        allowed = access.get_allowed_fields() if access else None
        if allowed is None:
            return values
        blocked = set(values) - set(allowed)
        if blocked:
            raise AccessError(_(
                "Cannot write fields outside allowlist: %s")
                % ", ".join(sorted(blocked)))
        return values

    def _effective_domain(self, access, domain):
        base = list(domain or [])
        if access:
            base = base + access.get_domain()
        return base

    def _row_cap(self, access, requested):
        cap = (access.max_rows if access else 0) or int(
            self.env["ir.config_parameter"].sudo().get_param(
                "mcp_server_pro.default_max_rows", "500"))
        if requested:
            return min(requested, cap)
        return cap

    # ---- read -----------------------------------------------------------
    def search_records(self, model_name, domain, fields_req, limit, offset,
                        order, access=None, key=None):
        Model = self._model_as_user(model_name, key)
        domain = self._effective_domain(access, domain)
        fields_req = self._apply_field_filter(access, fields_req, model_name)
        limit = self._row_cap(access, limit)
        rows = Model.search_read(
            domain, fields_req, offset=offset or 0, limit=limit, order=order)
        return {"model": model_name, "count": len(rows), "records": rows}

    def read_records(self, model_name, ids, fields_req, access=None, key=None):
        Model = self._model_as_user(model_name, key)
        fields_req = self._apply_field_filter(access, fields_req, model_name)
        # Re-filter ids against the access domain so the key can't read
        # records outside its slice by guessing IDs.
        domain = self._effective_domain(access, [("id", "in", ids)])
        recs = Model.search(domain)
        return {"model": model_name,
                "records": recs.read(fields_req)}

    def describe_model(self, model_name, access=None, key=None):
        allowed = access.get_allowed_fields() if access else None
        meta = self.env["mcp.schema.cache"].describe_model(
            model_name, key.user_id.id, allowed_fields=allowed)
        return {"model": model_name, "fields": meta}

    # ---- write ----------------------------------------------------------
    def create_record(self, model_name, values, access=None, key=None):
        Model = self._model_as_user(model_name, key)
        values = self._scrub_write_values(access, values)
        rec = Model.create(values)
        return {"model": model_name, "id": rec.id}

    def update_records(self, model_name, ids, values, access=None, key=None):
        Model = self._model_as_user(model_name, key)
        values = self._scrub_write_values(access, values)
        domain = self._effective_domain(access, [("id", "in", ids)])
        recs = Model.search(domain)
        if set(recs.ids) != set(ids):
            raise AccessError(_(
                "Some IDs are outside this key's permitted record set."))
        recs.write(values)
        return {"model": model_name, "updated": recs.ids}

    def delete_records(self, model_name, ids, access=None, key=None):
        Model = self._model_as_user(model_name, key)
        domain = self._effective_domain(access, [("id", "in", ids)])
        recs = Model.search(domain)
        if set(recs.ids) != set(ids):
            raise AccessError(_(
                "Some IDs are outside this key's permitted record set."))
        deleted = recs.ids
        recs.unlink()
        return {"model": model_name, "deleted": deleted}

    # ---- batch ----------------------------------------------------------
    def batch_create(self, model_name, records, access=None, key=None, job=None):
        Model = self._model_as_user(model_name, key) if key else self.env[model_name]
        scrubbed = [self._scrub_write_values(access, r) for r in records] \
            if access else records
        created = Model.create(scrubbed)
        return {"model": model_name, "created": created.ids,
                "count": len(created)}

    def batch_update(self, model_name, updates, access=None, key=None, job=None):
        Model = self._model_as_user(model_name, key) if key else self.env[model_name]
        results = []
        for i, upd in enumerate(updates):
            ids = upd["ids"]
            vals = self._scrub_write_values(access, upd["values"]) \
                if access else upd["values"]
            domain = self._effective_domain(access, [("id", "in", ids)])
            recs = Model.search(domain)
            recs.write(vals)
            results.append({"ids": recs.ids})
            if job:
                job.progress = int((i + 1) / len(updates) * 100)
        return {"model": model_name, "updated_batches": results}

    def batch_delete(self, model_name, ids, access=None, key=None, job=None):
        Model = self._model_as_user(model_name, key) if key else self.env[model_name]
        domain = self._effective_domain(access, [("id", "in", ids)])
        recs = Model.search(domain)
        deleted = recs.ids
        recs.unlink()
        return {"model": model_name, "deleted": deleted, "count": len(deleted)}
