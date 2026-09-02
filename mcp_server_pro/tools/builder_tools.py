# -*- coding: utf-8 -*-
"""AI Module Builder.

Three tools:
  * generate_module_spec  — normalize + validate a module spec (JSON)
  * build_module_zip      — render a complete, installable Odoo 18 module ZIP
  * install_module_zip    — drop into addons path and install

The generator is deterministic and safe: it never executes model code at
build time, validates identifiers, and produces standard Odoo 18 artifacts
(models, security CSV, record-rule XML, menus, list/form views).
"""
import base64
import io
import logging
import os
import re
import zipfile

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MODEL_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

_FIELD_TYPES = {
    "char": "fields.Char", "text": "fields.Text", "html": "fields.Html",
    "integer": "fields.Integer", "float": "fields.Float",
    "monetary": "fields.Monetary", "boolean": "fields.Boolean",
    "date": "fields.Date", "datetime": "fields.Datetime",
    "selection": "fields.Selection", "many2one": "fields.Many2one",
    "one2many": "fields.One2many", "many2many": "fields.Many2many",
    "binary": "fields.Binary",
}


class McpToolBuilder(models.AbstractModel):
    _name = "mcp.tool.builder"
    _description = "MCP Module Builder Tools"

    # ================================================================== #
    #  Spec generation / validation                                      #
    # ================================================================== #
    def generate_module_spec(self, args, key=None):
        """Validate/normalize a spec. The *AI client* supplies structure;
        this method is the guardrail that makes it installable.

        Accepts either a full 'spec' object or a 'description' (in which case
        we return a scaffold the client can fill — we do not call an LLM
        server-side; the calling agent is the LLM)."""
        spec = args.get("spec")
        if not spec:
            # Provide a minimal scaffold derived from the module_name.
            name = self._slug(args.get("module_name") or "new_module")
            spec = {
                "name": name,
                "title": args.get("description", name).strip()[:64] or name,
                "summary": args.get("description", "")[:200],
                "models": [],
                "menus": [],
            }
        report = self._validate_spec(spec)
        record = None
        if key:
            record = self.env["mcp.generated.module"].create({
                "name": spec["name"],
                "display_name_": spec.get("title"),
                "tenant_id": key.tenant_id.id,
                "key_id": key.id,
                "spec_json": self._dumps(spec),
                "state": "validated" if report["ok"] else "spec",
                "validation_report": self._dumps(report),
                "summary": spec.get("summary"),
                "model_count": len(spec.get("models", [])),
            })
        return {"ok": report["ok"], "spec": spec, "report": report,
                "record_id": record.id if record else None}

    def _validate_spec(self, spec):
        errors, warnings = [], []
        if not isinstance(spec, dict):
            return {"ok": False, "errors": ["spec must be an object"]}
        name = spec.get("name", "")
        if not _IDENT_RE.match(name):
            errors.append("module name must be snake_case: %r" % name)
        for m in spec.get("models", []):
            mname = m.get("name", "")
            if not _MODEL_RE.match(mname):
                errors.append("invalid model name: %r" % mname)
            fields_ = m.get("fields", [])
            if not fields_:
                warnings.append("model %s has no fields" % mname)
            has_name = any(f.get("name") == "name" for f in fields_)
            if not has_name:
                warnings.append(
                    "model %s has no 'name' field (rec_name fallback)" % mname)
            for f in fields_:
                fn = f.get("name", "")
                ft = f.get("type", "")
                if not _IDENT_RE.match(fn):
                    errors.append("invalid field name %r on %s" % (fn, mname))
                if ft not in _FIELD_TYPES:
                    errors.append("unknown field type %r on %s.%s"
                                  % (ft, mname, fn))
                if ft in ("many2one", "one2many", "many2many") \
                        and not f.get("relation"):
                    errors.append("relational field %s.%s missing 'relation'"
                                  % (mname, fn))
                if ft == "selection" and not f.get("selection"):
                    errors.append("selection field %s.%s missing 'selection'"
                                  % (mname, fn))
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    # ================================================================== #
    #  ZIP build                                                          #
    # ================================================================== #
    def build_module_zip(self, spec, key=None):
        if isinstance(spec, str):
            spec = self._loads(spec)
        report = self._validate_spec(spec)
        if not report["ok"]:
            return {"ok": False, "error": "Spec invalid: %s"
                    % "; ".join(report["errors"]), "report": report}

        name = spec["name"]
        files = self._render_module(spec)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, content in files.items():
                zf.writestr("%s/%s" % (name, path), content)
        zip_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return {"ok": True, "module_name": name, "zip_b64": zip_b64,
                "file_list": sorted(files.keys()),
                "model_count": len(spec.get("models", []))}

    def _render_module(self, spec):
        """Return {relative_path: file_content_str} for the whole module."""
        name = spec["name"]
        models_ = spec.get("models", [])
        files = {}

        # __init__.py
        files["__init__.py"] = "from . import models\n"
        # manifest
        files["__manifest__.py"] = self._render_manifest(spec)
        # models/__init__.py
        model_modules = [self._model_module_name(m["name"]) for m in models_]
        files["models/__init__.py"] = "".join(
            "from . import %s\n" % mm for mm in model_modules) or "# no models\n"
        # each model
        for m in models_:
            mod_file = self._model_module_name(m["name"])
            files["models/%s.py" % mod_file] = self._render_model(m)
        # security
        files["security/ir.model.access.csv"] = self._render_access_csv(spec)
        rules = self._render_record_rules(spec)
        if rules:
            files["security/security.xml"] = rules
        # views
        files["views/views.xml"] = self._render_views(spec)
        # menus
        if spec.get("menus") or models_:
            files["views/menus.xml"] = self._render_menus(spec)
        # readme
        files["README.md"] = self._render_readme(spec)
        return files

    # ---- renderers ------------------------------------------------------
    def _render_manifest(self, spec):
        data_files = ["security/ir.model.access.csv"]
        if self._render_record_rules(spec):
            data_files.append("security/security.xml")
        data_files.append("views/views.xml")
        if spec.get("menus") or spec.get("models"):
            data_files.append("views/menus.xml")
        data_repr = ",\n        ".join('"%s"' % d for d in data_files)
        return (
            "# -*- coding: utf-8 -*-\n"
            "{\n"
            '    "name": %r,\n'
            '    "version": "18.0.1.0.0",\n'
            '    "summary": %r,\n'
            '    "category": "Uncategorized",\n'
            '    "author": "Odoo MCP Server (PRO) — generated",\n'
            '    "license": "LGPL-3",\n'
            '    "depends": ["base"],\n'
            '    "data": [\n        %s\n    ],\n'
            '    "installable": True,\n'
            '    "application": %s,\n'
            "}\n"
        ) % (spec.get("title") or spec["name"],
             spec.get("summary", ""),
             data_repr,
             repr(bool(spec.get("application", True))))

    def _render_model(self, m):
        mname = m["name"]
        cls = "".join(p.capitalize() for p in re.split(r"[._]", mname))
        lines = [
            "# -*- coding: utf-8 -*-",
            "from odoo import api, fields, models",
            "",
            "",
            "class %s(models.Model):" % cls,
            "    _name = %r" % mname,
            "    _description = %r" % (m.get("title") or mname),
        ]
        rec_name = None
        field_lines = []
        for f in m.get("fields", []):
            field_lines.append("    " + self._render_field(f))
            if f["name"] == "name":
                rec_name = "name"
        if rec_name:
            lines.append("    _rec_name = 'name'")
        if m.get("order"):
            lines.append("    _order = %r" % m["order"])
        lines.append("")
        lines.extend(field_lines or ["    # no fields defined"])
        lines.append("")
        return "\n".join(lines)

    def _render_field(self, f):
        ftype = _FIELD_TYPES[f["type"]]
        kwargs = []
        if f.get("string"):
            kwargs.append("string=%r" % f["string"])
        if f["type"] in ("many2one", "one2many", "many2many"):
            kwargs.append("comodel_name=%r" % f["relation"])
            if f["type"] == "one2many" and f.get("inverse_name"):
                kwargs.append("inverse_name=%r" % f["inverse_name"])
        if f["type"] == "selection":
            sel = [(str(s[0]), str(s[1])) if isinstance(s, (list, tuple))
                   else (str(s), str(s)) for s in f["selection"]]
            kwargs.append("selection=%r" % sel)
        if f.get("required"):
            kwargs.append("required=True")
        if f.get("readonly"):
            kwargs.append("readonly=True")
        if f.get("default") is not None:
            kwargs.append("default=%r" % f["default"])
        if f.get("help"):
            kwargs.append("help=%r" % f["help"])
        return "%s = %s(%s)" % (f["name"], ftype, ", ".join(kwargs))

    def _render_access_csv(self, spec):
        header = ("id,name,model_id:id,group_id:id,perm_read,perm_write,"
                  "perm_create,perm_unlink")
        rows = [header]
        for m in spec.get("models", []):
            mid = m["name"].replace(".", "_")
            rows.append(
                "access_%s_user,%s.user,model_%s,base.group_user,1,1,1,1"
                % (mid, mid, mid))
        return "\n".join(rows) + "\n"

    def _render_record_rules(self, spec):
        rules = []
        for m in spec.get("models", []):
            if m.get("company_rule"):
                mid = m["name"].replace(".", "_")
                rules.append(
                    '    <record id="rule_%s_company" model="ir.rule">\n'
                    '      <field name="name">%s multi-company</field>\n'
                    '      <field name="model_id" ref="model_%s"/>\n'
                    '      <field name="domain_force">'
                    "[('company_id','in',company_ids)]</field>\n"
                    "    </record>" % (mid, m["name"], mid))
        if not rules:
            return ""
        return ('<?xml version="1.0" encoding="utf-8"?>\n<odoo>\n'
                + "\n".join(rules) + "\n</odoo>\n")

    def _render_views(self, spec):
        chunks = []
        for m in spec.get("models", []):
            mname = m["name"]
            mid = mname.replace(".", "_")
            field_names = [f["name"] for f in m.get("fields", [])]
            list_fields = "\n".join(
                '          <field name="%s"/>' % fn for fn in field_names)
            form_fields = "\n".join(
                '            <field name="%s"/>' % fn for fn in field_names)
            chunks.append(
                '  <record id="view_%s_list" model="ir.ui.view">\n'
                '    <field name="name">%s.list</field>\n'
                '    <field name="model">%s</field>\n'
                '    <field name="arch" type="xml">\n'
                "      <list>\n%s\n      </list>\n"
                "    </field>\n"
                "  </record>\n"
                '  <record id="view_%s_form" model="ir.ui.view">\n'
                '    <field name="name">%s.form</field>\n'
                '    <field name="model">%s</field>\n'
                '    <field name="arch" type="xml">\n'
                "      <form>\n        <sheet>\n          <group>\n"
                "%s\n          </group>\n        </sheet>\n      </form>\n"
                "    </field>\n"
                "  </record>\n"
                '  <record id="action_%s" model="ir.actions.act_window">\n'
                '    <field name="name">%s</field>\n'
                '    <field name="res_model">%s</field>\n'
                '    <field name="view_mode">list,form</field>\n'
                "  </record>"
                % (mid, mname, mname, list_fields,
                   mid, mname, mname, form_fields,
                   mid, (m.get("title") or mname), mname))
        return ('<?xml version="1.0" encoding="utf-8"?>\n<odoo>\n'
                + "\n".join(chunks) + "\n</odoo>\n")

    def _render_menus(self, spec):
        name = spec["name"]
        title = spec.get("title") or name
        root_id = "menu_%s_root" % name
        items = [
            '  <menuitem id="%s" name="%s" sequence="10"/>' % (root_id, title)
        ]
        for i, m in enumerate(spec.get("models", [])):
            mid = m["name"].replace(".", "_")
            items.append(
                '  <menuitem id="menu_%s" name="%s" parent="%s" '
                'action="action_%s" sequence="%d"/>'
                % (mid, (m.get("title") or m["name"]), root_id, mid, (i + 1) * 10))
        return ('<?xml version="1.0" encoding="utf-8"?>\n<odoo>\n'
                + "\n".join(items) + "\n</odoo>\n")

    def _render_readme(self, spec):
        return "# %s\n\n%s\n\nGenerated by Odoo MCP Server (PRO).\n" % (
            spec.get("title") or spec["name"], spec.get("summary", ""))

    # ================================================================== #
    #  Install                                                           #
    # ================================================================== #
    def install_module_zip(self, module_name, zip_b64, key=None):
        """Extract the ZIP into a writable addons path and install it.

        Guarded: requires MCP admin (or sudo) and a configured, writable
        addons path. On read-only deployments this raises with guidance to
        download the ZIP and install out-of-band.
        """
        if not (self.env.su or self.env.user.has_group(
                "mcp_server_pro.group_mcp_admin")):
            raise UserError(_("Only MCP admins may install modules."))

        addons_path = self.env["ir.config_parameter"].sudo().get_param(
            "mcp_server_pro.addons_path")
        if not addons_path or not os.path.isdir(addons_path) \
                or not os.access(addons_path, os.W_OK):
            raise UserError(_(
                "No writable addons path configured. Set "
                "'mcp_server_pro.addons_path' to a directory on Odoo's "
                "addons_path, or download the ZIP and install manually."))

        if not _IDENT_RE.match(module_name):
            raise UserError(_("Invalid module name."))

        target = os.path.join(addons_path, module_name)
        raw = base64.b64decode(zip_b64)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            # Path-traversal guard.
            for member in zf.namelist():
                norm = os.path.normpath(member)
                if norm.startswith("..") or os.path.isabs(norm):
                    raise UserError(_("Unsafe path in ZIP: %s") % member)
            zf.extractall(addons_path)

        # Update module list and install.
        self.env["ir.module.module"].sudo().update_list()
        mod = self.env["ir.module.module"].sudo().search(
            [("name", "=", module_name)], limit=1)
        if not mod:
            raise UserError(_(
                "Module %s not found after extraction.") % module_name)
        mod.button_immediate_install()
        return {"ok": True, "module": module_name,
                "state": mod.state, "path": target}

    def _model_module_name(self, model_name):
        return model_name.replace(".", "_")

    @staticmethod
    def _slug(text):
        s = re.sub(r"[^a-z0-9_]", "_", (text or "").lower())
        s = re.sub(r"_+", "_", s).strip("_")
        return s or "module"

    def _dumps(self, obj):
        import json
        return json.dumps(obj, indent=2, default=str)

    def _loads(self, text):
        import json
        return json.loads(text)
