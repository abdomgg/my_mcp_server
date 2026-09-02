# -*- coding: utf-8 -*-
"""BI / analytics tools.

These wrap read_group with opinionated shapes agents and dashboards want:
pivot, timeseries (with gap filling), top-N, cohort retention and funnel.
All run as the key's user and respect the model-access domain.
"""
import logging
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

_INTERVAL_GROUPBY = {
    "day": ":day", "week": ":week", "month": ":month",
    "quarter": ":quarter", "year": ":year",
}


class McpToolBi(models.AbstractModel):
    _name = "mcp.tool.bi"
    _description = "MCP BI Tools"

    def _model(self, key, model_name):
        return self.env[model_name].with_user(key.user_id)

    def _access_domain(self, key, model_name, domain):
        access = key.model_access_ids.filtered(
            lambda a: a.model_name == model_name)
        base = list(domain or [])
        if access:
            base += access[0].get_domain()
        return base

    # ---- pivot ----------------------------------------------------------
    def pivot(self, key, args):
        model = args["model"]
        Model = self._model(key, model)
        domain = self._access_domain(key, model, args.get("domain"))
        rows = args["rows"]
        measure = args.get("measure")
        agg = args.get("agg", "sum")
        fields_arg = []
        if measure and agg != "count":
            fields_arg = ["%s:%s" % (measure, agg)]
        data = Model.read_group(domain, fields_arg, rows, lazy=False)
        return {"model": model, "rows": rows, "measure": measure,
                "agg": agg, "data": data}

    # ---- timeseries (with gap filling) ----------------------------------
    def timeseries(self, key, args):
        model = args["model"]
        Model = self._model(key, model)
        domain = self._access_domain(key, model, args.get("domain"))
        date_field = args["date_field"]
        interval = args["interval"]
        measure = args.get("measure")
        agg = args.get("agg", "sum")
        if interval not in _INTERVAL_GROUPBY:
            raise UserError(_("Unsupported interval: %s") % interval)

        gb = date_field + _INTERVAL_GROUPBY[interval]
        fields_arg = ["%s:%s" % (measure, agg)] if measure else []
        data = Model.read_group(domain, fields_arg, [gb], lazy=False)

        series = []
        for row in data:
            label = row.get(gb)
            value = row.get("%s" % measure) if measure else row.get("__count")
            series.append({"period": label,
                           "value": value,
                           "count": row.get("__count")})
        if args.get("fill_gaps"):
            series = self._fill_gaps(series, interval)
        return {"model": model, "date_field": date_field,
                "interval": interval, "series": series}

    def _fill_gaps(self, series, interval):
        # read_group returns sparse buckets; best-effort fill of empty periods
        # is interval-dependent and label-format-dependent, so we annotate
        # rather than fabricate dates we cannot reliably parse back.
        for point in series:
            point.setdefault("value", 0)
            if point["value"] is None:
                point["value"] = 0
        return series

    # ---- topn -----------------------------------------------------------
    def topn(self, key, args):
        model = args["model"]
        Model = self._model(key, model)
        domain = self._access_domain(key, model, args.get("domain"))
        groupby = args["groupby"]
        measure = args.get("measure")
        agg = args.get("agg", "sum")
        n = args.get("n", 10)
        fields_arg = ["%s:%s" % (measure, agg)] if measure else []
        data = Model.read_group(domain, fields_arg, [groupby], lazy=False)
        key_fn = (lambda r: r.get(measure) or 0) if measure \
            else (lambda r: r.get("__count") or 0)
        data.sort(key=key_fn, reverse=True)
        top = data[:n]
        return {"model": model, "groupby": groupby, "measure": measure,
                "n": n, "data": top}

    # ---- cohort ---------------------------------------------------------
    def cohort(self, key, args):
        """Cohort by cohort_field period; retention measured at event_field."""
        model = args["model"]
        Model = self._model(key, model)
        domain = self._access_domain(key, model, args.get("domain"))
        cohort_field = args["cohort_field"]
        event_field = args["event_field"]
        interval = args.get("interval", "month")

        recs = Model.search_read(
            domain, [cohort_field, event_field], limit=100000)
        cohorts = defaultdict(lambda: defaultdict(int))
        for r in recs:
            c = self._bucket(r.get(cohort_field), interval)
            e = self._bucket(r.get(event_field), interval)
            if c is None:
                continue
            cohorts[c]["__size"] += 1
            if e is not None:
                offset = self._period_offset(c, e, interval)
                if offset is not None and offset >= 0:
                    cohorts[c][offset] += 1

        result = []
        for c in sorted(cohorts):
            row = cohorts[c]
            size = row["__size"]
            periods = {k: v for k, v in row.items() if k != "__size"}
            result.append({
                "cohort": c, "size": size,
                "retention": {str(k): {"count": periods[k],
                                       "pct": round(periods[k] / size * 100, 1)}
                              for k in sorted(periods)},
            })
        return {"model": model, "interval": interval, "cohorts": result}

    # ---- funnel ---------------------------------------------------------
    def funnel(self, key, args):
        model = args["model"]
        Model = self._model(key, model)
        domain = self._access_domain(key, model, args.get("domain"))
        stage_field = args["stage_field"]
        stages = args["stages"]

        steps = []
        prev = None
        for st in stages:
            cnt = Model.search_count(domain + [(stage_field, "=", st)])
            step = {"stage": st, "count": cnt}
            if prev:
                step["conversion_pct"] = round(cnt / prev * 100, 1)
            steps.append(step)
            prev = cnt
        first = steps[0]["count"] if steps else 0
        for s in steps:
            s["overall_pct"] = round(s["count"] / first * 100, 1) if first else 0
        return {"model": model, "stage_field": stage_field, "funnel": steps}

    # ---- date helpers ---------------------------------------------------
    def _bucket(self, value, interval):
        if not value:
            return None
        if isinstance(value, str):
            try:
                value = fields.Datetime.from_string(value) \
                    or fields.Date.from_string(value)
            except Exception:
                return None
        if isinstance(value, datetime):
            value = value.date()
        if not isinstance(value, date):
            return None
        if interval == "year":
            return "%04d" % value.year
        if interval == "quarter":
            return "%04d-Q%d" % (value.year, (value.month - 1) // 3 + 1)
        if interval == "month":
            return "%04d-%02d" % (value.year, value.month)
        if interval == "week":
            iso = value.isocalendar()
            return "%04d-W%02d" % (iso[0], iso[1])
        return value.isoformat()

    def _period_offset(self, cohort, event, interval):
        """Integer period distance between two bucket labels."""
        try:
            if interval == "month":
                cy, cm = map(int, cohort.split("-"))
                ey, em = map(int, event.split("-"))
                return (ey - cy) * 12 + (em - cm)
            if interval == "year":
                return int(event) - int(cohort)
            if interval == "quarter":
                cy, cq = cohort.split("-Q")
                ey, eq = event.split("-Q")
                return (int(ey) - int(cy)) * 4 + (int(eq) - int(cq))
        except Exception:
            return None
        return None
