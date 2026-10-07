# -*- coding: utf-8 -*-
"""Data for the Violations Dashboard (Manager screen, OWL client action).

One call - ``get_dashboard_data(params)`` - returns every figure of the
screen for the filters the manager picked. Records are searched as the
current user (record rules apply) with ONE domain built by
``_mm_dash_domain``; the same domain is returned to the screen so every
drill-down list opens exactly the records behind the number clicked.

Violation volumes are small (tens to a few thousand a month), so the
figures are aggregated in Python from one search per period instead of a
dozen read_group calls - simpler, and every breakdown shares the same rows.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .mm_branch_violation import COUNTED_STATES, MANAGER_GROUP

FILTER_M2O = {
    "branch_ids": "branch_id",
    "employee_ids": "employee_id",
    "type_ids": "violation_type_id",
    "source_ids": "source_id",
    "job_ids": "job_id",
}
FILTER_SELECTION = {
    "note_types": "note_type",
    "departments": "entering_department",
    "states": "state",
    "response_states": "response_state",
}
LANGS = {"ar": "ar_001", "en": "en_US"}
# drafts older than this are "stuck": logged but never sent for approval
STALE_DRAFT_DAYS = 2
MATRIX_TYPES = 8
TOP_EMPLOYEES = 15
RECENT_ROWS = 10


class MmViolationDashboard(models.AbstractModel):
    _name = "mm.violation.dashboard"
    _description = "Branch Violations Dashboard"

    # ------------------------------------------------------------------
    # filters
    # ------------------------------------------------------------------
    @api.model
    def _mm_dash_filters(self, params):
        """Clean the screen's filter dict: ids as ints, lists as lists."""
        today = fields.Date.context_today(self)
        f = {
            "date_from": params.get("date_from") or (today - timedelta(days=29)).isoformat(),
            "date_to": params.get("date_to") or today.isoformat(),
            "complaints_only": bool(params.get("complaints_only")),
            "repeat_only": bool(params.get("repeat_only")),
            "search": (params.get("search") or "").strip(),
        }
        for key in FILTER_M2O:
            f[key] = [int(i) for i in params.get(key) or []]
        for key in FILTER_SELECTION:
            f[key] = [str(v) for v in params.get(key) or []]
        if "states" not in params:
            f["states"] = list(COUNTED_STATES)
        if f["date_from"] > f["date_to"]:
            f["date_from"], f["date_to"] = f["date_to"], f["date_from"]
        return f

    @api.model
    def _mm_dash_domain(self, f, dates=True, states=True, date_from=None, date_to=None):
        """ORM domain for the filters. ``dates`` / ``states`` off: the same
        filters without that part (the status funnel shows every state, the
        to-do strip every date)."""
        domain = [("company_id", "in", self.env.companies.ids)]
        if dates:
            domain += [("date", ">=", date_from or f["date_from"]),
                       ("date", "<=", date_to or f["date_to"])]
        for key, field in FILTER_M2O.items():
            if f[key]:
                domain.append((field, "in", f[key]))
        for key, field in FILTER_SELECTION.items():
            if key == "states" and not states:
                continue
            if f[key]:
                domain.append((field, "in", f[key]))
        if f["complaints_only"]:
            domain.append(("needs_response", "=", True))
        if f["search"]:
            s = f["search"]
            domain += ["|", "|", "|", "|", "|",
                       ("name", "ilike", s), ("employee_id.name", "ilike", s),
                       ("employee_number", "ilike", s), ("description", "ilike", s),
                       ("customer_name", "ilike", s), ("customer_phone", "ilike", s)]
        return domain

    @staticmethod
    def _mm_prev_range(date_from, date_to):
        """The period of the same length just before."""
        d_from, d_to = date.fromisoformat(date_from), date.fromisoformat(date_to)
        span = (d_to - d_from).days + 1
        p_to = d_from - timedelta(days=1)
        return (p_to - timedelta(days=span - 1)).isoformat(), p_to.isoformat()

    def _mm_search(self, domain, f):
        Violation = self.env["mm.branch.violation"]
        records = Violation.search(domain)
        if f["repeat_only"]:
            # employees with 2+ counted violations in THIS set's period
            counts = Counter(v.employee_id.id for v in records if v.state in COUNTED_STATES)
            repeat = {e for e, n in counts.items() if n >= 2}
            records = records.filtered(lambda v: v.employee_id.id in repeat)
        # names of employees / branches are read as superuser: a violation
        # manager usually has no HR rights (see the model's related fields)
        return records.sudo()

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, params):
        if not (self.env.user.has_group(MANAGER_GROUP) or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Only Violation Managers can open the violations dashboard."))
        lang = LANGS.get(params.get("lang"), self.env.lang)
        self = self.with_context(lang=lang)
        f = self._mm_dash_filters(params)
        p_from, p_to = self._mm_prev_range(f["date_from"], f["date_to"])
        granularity = params.get("granularity") or self._mm_auto_granularity(f)

        cur = self._mm_search(self._mm_dash_domain(f), f)
        prev = self._mm_search(self._mm_dash_domain(f, date_from=p_from, date_to=p_to), f)
        # every state, same filters: for the status funnel
        pipeline = self._mm_search(self._mm_dash_domain(f, states=False), f)

        counted = cur.filtered(lambda v: v.state in COUNTED_STATES) if f["states"] else cur
        return {
            "filters": f,
            "prev_range": [p_from, p_to],
            "granularity": granularity,
            "domain": self._mm_dash_domain(f),
            # the status funnel: every state
            "domain_funnel": self._mm_dash_domain(f, states=False),
            # the to-do strip: no period, no status
            "domain_open": self._mm_dash_domain(f, dates=False, states=False),
            "options": self._mm_options(),
            "kpis": self._mm_kpis(cur),
            "kpis_prev": self._mm_kpis(prev),
            "trend": self._mm_trend(cur, prev, f, granularity),
            "branches": self._mm_by_branch(cur, prev),
            "matrix": self._mm_matrix(cur),
            "types": self._mm_by_m2o(cur, prev, "violation_type_id"),
            "sources": self._mm_by_m2o(cur, prev, "source_id"),
            "jobs": self._mm_by_m2o(cur, prev, "job_id"),
            "note_types": self._mm_by_selection(cur, prev, "note_type"),
            "departments": self._mm_by_selection(cur, prev, "entering_department"),
            "employees": self._mm_by_employee(cur, prev),
            "weekdays": self._mm_weekdays(cur),
            "funnel": dict(Counter(pipeline.mapped("state"))),
            "complaints": self._mm_complaints(cur),
            "actions": self._mm_actions(f),
            "recent": self._mm_recent(cur),
            "headline_counted": len(counted),
        }

    @staticmethod
    def _mm_auto_granularity(f):
        span = (date.fromisoformat(f["date_to"]) - date.fromisoformat(f["date_from"])).days + 1
        return "day" if span <= 31 else "week" if span <= 120 else "month"

    # ------------------------------------------------------------------
    # blocks
    # ------------------------------------------------------------------
    def _mm_options(self):
        """Everything the filter panel can pick from."""
        companies = self.env.companies.ids
        branches = self.env["company.branch"].sudo().search(
            [("company_id", "in", companies)], order="name")
        Violation = self.env["mm.branch.violation"].sudo()
        used = Violation.search([("company_id", "in", companies)])
        employees = used.employee_id.sorted("name")
        return {
            "branches": [{"id": b.id, "name": b.display_name} for b in branches],
            "employees": [{
                "id": e.id, "name": e.name,
                "number": e.mm_employee_number or "",
                "branch": e.branch_id.display_name or "",
            } for e in employees],
            "types": [{"id": t.id, "name": t.name, "points": t.points}
                      for t in self.env["mm.violation.type"].with_context(active_test=False).search([])],
            "sources": [{"id": s.id, "name": s.name}
                        for s in self.env["mm.violation.source"].with_context(active_test=False).search([])],
            "jobs": [{"id": j.id, "name": j.name} for j in used.job_id.sorted("name")],
            "can_approve": self.env.user.has_group(MANAGER_GROUP),
        }

    @staticmethod
    def _mm_hours(start, end):
        return (end - start).total_seconds() / 3600.0 if start and end else None

    def _mm_kpis(self, recs):
        counted = recs.filtered(lambda v: v.state in COUNTED_STATES)
        per_emp = Counter(v.employee_id.id for v in counted)
        complaints = recs.filtered("needs_response")
        resolved = len(complaints.filtered(lambda v: v.response_state == "resolved"))
        closed = len(complaints.filtered(lambda v: v.response_state in ("resolved", "unresolved")))
        on_time = len(complaints.filtered(lambda v: v.response_contacted_date and not v.response_late))
        late = len(complaints.filtered("response_late"))
        overdue = len(complaints.filtered("response_overdue"))
        due = on_time + late + overdue
        contact_hours = [h for h in (self._mm_hours(v.complaint_received_at, v.response_contacted_date)
                                     for v in complaints) if h is not None and h >= 0]
        approve_hours = [h for h in (self._mm_hours(v.submitted_date, v.approved_date)
                                     for v in counted) if h is not None and h >= 0]
        points = sum(counted.mapped("points"))
        return {
            "logged": len(recs),
            "violations": len(counted),
            "points": round(points, 2),
            "avg_points": round(points / len(counted), 2) if counted else 0,
            "employees": len(per_emp),
            "branches": len(counted.branch_id),
            "repeat": sum(1 for n in per_emp.values() if n >= 2),
            "repeat_pct": round(100.0 * sum(1 for n in per_emp.values() if n >= 2) / len(per_emp), 1)
                          if per_emp else None,
            "complaints": len(complaints),
            "resolved": resolved,
            "resolved_pct": round(100.0 * resolved / closed, 1) if closed else None,
            "on_time": on_time,
            "late": late,
            "overdue": overdue,
            "on_time_pct": round(100.0 * on_time / due, 1) if due else None,
            "avg_contact_hours": round(sum(contact_hours) / len(contact_hours), 1) if contact_hours else None,
            "avg_approve_hours": round(sum(approve_hours) / len(approve_hours), 1) if approve_hours else None,
            "cancelled": len(recs.filtered(lambda v: v.state == "cancelled")),
        }

    @staticmethod
    def _mm_bucket(day, granularity):
        if granularity == "month":
            return day.strftime("%Y-%m")
        if granularity == "week":
            monday = day - timedelta(days=day.weekday())
            return monday.isoformat()
        return day.isoformat()

    def _mm_buckets(self, date_from, date_to, granularity):
        labels, day, end = [], date.fromisoformat(date_from), date.fromisoformat(date_to)
        while day <= end:
            label = self._mm_bucket(day, granularity)
            if not labels or labels[-1] != label:
                labels.append(label)
            day += timedelta(days=1)
        return labels

    def _mm_trend(self, cur, prev, f, granularity):
        """Count per bucket and category (zero-filled) + points, with the
        previous period laid on the same axis, bucket by bucket."""
        labels = self._mm_buckets(f["date_from"], f["date_to"], granularity)
        p_from, p_to = self._mm_prev_range(f["date_from"], f["date_to"])
        prev_labels = self._mm_buckets(p_from, p_to, granularity)
        index = {b: i for i, b in enumerate(labels)}
        prev_index = {b: i for i, b in enumerate(prev_labels)}
        by_type = defaultdict(lambda: [0] * len(labels))
        total, points = [0] * len(labels), [0.0] * len(labels)
        complaints = [0] * len(labels)
        for v in cur:
            i = index.get(self._mm_bucket(v.date, granularity))
            if i is None:
                continue
            by_type[v.note_type][i] += 1
            total[i] += 1
            points[i] += v.points
            if v.needs_response:
                complaints[i] += 1
        prev_total = [0] * len(labels)
        for v in prev:
            i = prev_index.get(self._mm_bucket(v.date, granularity))
            if i is not None and i < len(labels):
                prev_total[i] += 1
        return {
            "labels": labels,
            "by_note_type": dict(by_type),
            "total": total,
            "points": [round(p, 2) for p in points],
            "complaints": complaints,
            "prev_total": prev_total,
        }

    def _mm_by_branch(self, cur, prev):
        prev_count = Counter(v.branch_id.id for v in prev)
        branches = cur.branch_id
        headcount = Counter(e.branch_id.id for e in self.env["hr.employee"].sudo().search(
            [("branch_id", "in", branches.ids)])) if branches else Counter()
        rows = []
        for branch in branches:
            recs = cur.filtered(lambda v: v.branch_id == branch)
            types = Counter(v.violation_type_id.name for v in recs)
            staff = headcount.get(branch.id, 0)
            rows.append({
                "id": branch.id,
                "name": branch.display_name,
                "count": len(recs),
                "points": round(sum(recs.mapped("points")), 2),
                "complaints": len(recs.filtered("needs_response")),
                "employees": len(recs.employee_id),
                "headcount": staff,
                "per_employee": round(len(recs) / staff, 2) if staff else None,
                "prev": prev_count.get(branch.id, 0),
                "top_type": types.most_common(1)[0][0] if types else "",
                "last_date": max(recs.mapped("date")).isoformat(),
            })
        rows.sort(key=lambda r: (-r["count"], -r["points"], r["name"]))
        return rows

    def _mm_matrix(self, cur):
        """Branch x violation type counts, for the heatmap: the most
        frequent types as columns, the rest folded into "Other"."""
        type_counts = Counter(v.violation_type_id.id for v in cur)
        top = [t for t, _n in type_counts.most_common(MATRIX_TYPES)]
        Type = self.env["mm.violation.type"]
        cols = [{"id": t, "name": Type.browse(t).name} for t in top]
        has_other = len(type_counts) > len(top)
        rows, peak = [], 0
        for branch in cur.branch_id:
            recs = cur.filtered(lambda v: v.branch_id == branch)
            c = Counter(v.violation_type_id.id for v in recs)
            cells = [c.get(t, 0) for t in top]
            if has_other:
                cells.append(sum(n for t, n in c.items() if t not in top))
            peak = max([peak] + cells)
            rows.append({"id": branch.id, "name": branch.display_name, "cells": cells, "total": len(recs)})
        rows.sort(key=lambda r: (-r["total"], r["name"]))
        return {"cols": cols, "has_other": has_other, "rows": rows, "peak": peak}

    def _mm_by_m2o(self, cur, prev, field):
        prev_count = Counter(v[field].id for v in prev)
        rows = []
        for rec in cur[field]:
            recs = cur.filtered(lambda v: v[field] == rec)
            rows.append({
                "id": rec.id, "name": rec.display_name, "count": len(recs),
                "points": round(sum(recs.mapped("points")), 2),
                "prev": prev_count.get(rec.id, 0),
            })
        rows.sort(key=lambda r: (-r["count"], r["name"]))
        return rows

    def _mm_by_selection(self, cur, prev, field):
        prev_count = Counter(prev.mapped(field))
        rows = []
        for key, n in Counter(cur.mapped(field)).most_common():
            recs = cur.filtered(lambda v: v[field] == key)
            rows.append({"key": key, "count": n,
                         "points": round(sum(recs.mapped("points")), 2),
                         "prev": prev_count.get(key, 0)})
        return rows

    def _mm_by_employee(self, cur, prev):
        prev_count = Counter(v.employee_id.id for v in prev)
        today = fields.Date.context_today(self)
        rows = []
        for emp in cur.employee_id:
            recs = cur.filtered(lambda v: v.employee_id == emp)
            per_type = Counter(v.violation_type_id.name for v in recs)
            top_type, top_n = per_type.most_common(1)[0]
            last = max(recs.mapped("date"))
            rows.append({
                "id": emp.id,
                "name": emp.name,
                "number": emp.mm_employee_number or "",
                "branch": emp.branch_id.display_name or recs[0].branch_id.display_name,
                "job": emp.job_id.name or "",
                "count": len(recs),
                "points": round(sum(recs.mapped("points")), 2),
                "kinds": len(per_type),
                "top_type": top_type,
                "same_type_repeat": top_n if top_n >= 2 else 0,
                "last_date": last.isoformat(),
                "days_since": (today - last).days,
                "prev": prev_count.get(emp.id, 0),
            })
        rows.sort(key=lambda r: (-r["count"], -r["points"], r["name"]))
        return rows[:TOP_EMPLOYEES]

    @staticmethod
    def _mm_weekdays(cur):
        """Sunday = 0 .. Saturday = 6 (JS getDay order)."""
        out = [0] * 7
        for v in cur:
            out[(v.date.weekday() + 1) % 7] += 1
        return out

    def _mm_complaints(self, cur):
        complaints = cur.filtered("needs_response")
        reps = defaultdict(lambda: {"complaints": 0, "on_time": 0, "late": 0, "overdue": 0,
                                    "resolved": 0, "hours": []})
        for v in complaints:
            user = v.response_user_id or v.response_contacted_by_id or v.response_closed_by_id
            r = reps[user.id or 0]
            r["name"] = user.name or ""
            r["complaints"] += 1
            if v.response_contacted_date and not v.response_late:
                r["on_time"] += 1
            if v.response_late:
                r["late"] += 1
            if v.response_overdue:
                r["overdue"] += 1
            if v.response_state == "resolved":
                r["resolved"] += 1
            h = self._mm_hours(v.complaint_received_at, v.response_contacted_date)
            if h is not None and h >= 0:
                r["hours"].append(h)
        by_rep = []
        for uid, r in reps.items():
            due = r["on_time"] + r["late"] + r["overdue"]
            hours = r.pop("hours")
            by_rep.append(dict(r, user_id=uid,
                               on_time_pct=round(100.0 * r["on_time"] / due) if due else None,
                               avg_hours=round(sum(hours) / len(hours), 1) if hours else None))
        by_rep.sort(key=lambda r: -r["complaints"])
        now = fields.Datetime.now()
        overdue = complaints.filtered("response_overdue").sorted("response_deadline")
        return {
            "states": dict(Counter(complaints.mapped("response_state"))),
            "by_rep": by_rep,
            "overdue": [{
                "id": v.id, "name": v.name, "customer": v.customer_name or "",
                "phone": v.customer_phone or "", "branch": v.branch_id.display_name,
                "hours_late": round(self._mm_hours(v.response_deadline, now) or 0, 1),
            } for v in overdue[:8]],
        }

    def _mm_actions(self, f):
        """Open work right now, whatever the period: same branch / employee
        / type filters, but no date and no status filter."""
        base = self._mm_dash_domain(f, dates=False, states=False)
        Violation = self.env["mm.branch.violation"]
        today = fields.Date.context_today(self)
        review = Violation.search(base + [("state", "=", "under_review")], order="submitted_date")
        stale_limit = fields.Datetime.now() - timedelta(days=STALE_DRAFT_DAYS)
        oldest = review[:1].submitted_date
        return {
            "to_review": len(review),
            "to_review_days": (today - oldest.date()).days if oldest else 0,
            "stale_drafts": Violation.search_count(
                base + [("state", "=", "draft"), ("create_date", "<", stale_limit)]),
            "overdue": Violation.search_count(base + [("response_overdue", "=", True)]),
            "awaiting": Violation.search_count(base + [("response_state", "=", "pending")]),
            "contacted": Violation.search_count(base + [("response_state", "=", "contacted")]),
            "pending_deductions": Violation.search_count(base + [("mm_deduction_status", "=", "pending")]),
            "stale_draft_days": STALE_DRAFT_DAYS,
        }

    @staticmethod
    def _mm_recent(cur):
        return [{
            "id": v.id, "name": v.name, "date": v.date.isoformat(),
            "branch": v.branch_id.display_name, "employee": v.employee_id.name,
            "type": v.violation_type_id.name, "points": v.points,
            "state": v.state, "note_type": v.note_type,
            "response_state": v.response_state or "",
        } for v in cur[:RECENT_ROWS]]
