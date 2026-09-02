# -*- coding: utf-8 -*-
"""Schema cache for model/field metadata.

Model discovery is the hottest read path for agents (they describe models
before acting). We cache field metadata per (model, run-as user) for a short
TTL using Odoo's ormcache, and bust it whenever ir.model / ir.model.fields /
ir.model.access change.
"""
import logging

from odoo import api, models, tools

_logger = logging.getLogger(__name__)

CACHE_TTL = 300  # seconds (mirrors the 5-minute intelligent cache claim)


class McpSchemaCache(models.AbstractModel):
    _name = "mcp.schema.cache"
    _description = "MCP Schema Cache"

    @tools.ormcache("model_name", "uid")
    def _cached_fields(self, model_name, uid):
        """Return field metadata dict for a model as a given user.

        ormcache makes this shared across requests in a worker; combined with
        the registry signal it is effectively cross-worker coherent because
        any schema-affecting write clears the registry cache everywhere.
        """
        Model = self.env[model_name].with_user(uid)
        fg = Model.fields_get(
            attributes=["string", "type", "required", "readonly",
                        "relation", "selection", "help"])
        return fg

    def describe_model(self, model_name, uid, allowed_fields=None):
        """Public entry: cached field metadata, optionally filtered."""
        fg = self._cached_fields(model_name, uid)
        if allowed_fields is not None:
            fg = {k: v for k, v in fg.items() if k in allowed_fields}
        return fg

    @api.model
    def clear(self):
        self.env.registry.clear_cache()
        _logger.debug("MCP: schema cache cleared")
