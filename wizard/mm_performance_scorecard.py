# -*- coding: utf-8 -*-
"""Performance Scorecard - who did best / worst.

Commission is branch-level (one shared POS login per branch), so employees
are judged on behaviour only: counted violations (Approved + Closed) and the
customer complaints among them. Branches also get their sales.

- Employee score = 100 - the employee's violation points in the period
  (floor 0). Ties - common, most staff have no mistakes - go first to a
  spotless record (never any violation), then to the longer run without a
  violation, then fewer complaints. The spotless check matters: employees
  were imported recently, so "days since added" would otherwise lose to
  someone whose last mistake is older than the import.
- Branch score = Sales weight x sales score + Mistakes weight x mistakes
  score (weights in Settings, default 70 / 30):
  * sales score = achieved / target of the branch's sales targets
    (pos_sales_target_commission) inside the period, capped at 100; a
    branch without a target is scored on mistakes alone ("No target");
  * mistakes score = 100 - violation points per employee of the branch, so
    a big branch is not punished for having more staff.
  Ties go to fewer violations, then fewer customer complaints. Complaint
  response speed is NOT used for branches: one central Customer Service
  team calls customers back for every branch, so it is not the branch's
  doing - it is measured per Customer Service person instead.
- Each ranking is also computed for the previous period of the same length
  to show the rank movement.
"""
import base64
import re
from collections import defaultdict
from datetime import date

from dateutil.relativedelta import relativedelta
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.mm_branch_violation import COUNTED_STATES

PERIODS = [
    ("this_month", "This Month"),
    ("last_month", "Last Month"),
    ("this_quarter", "This Quarter"),
    ("this_year", "This Year"),
    ("last_year", "Last Year"),
    ("custom", "Custom"),
]
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
REBUILD_FIELDS = {"period", "date_from", "date_to", "branch_ids", "company_id"}


def period_dates(period, today, date_from=None, date_to=None):
    if period == "this_month":
        start = today.replace(day=1)
        return start, start + relativedelta(months=1, days=-1)
    if period == "last_month":
        start = today.replace(day=1) - relativedelta(months=1)
        return start, start + relativedelta(months=1, days=-1)
    if period == "this_quarter":
        start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
        return start, start + relativedelta(months=3, days=-1)
    if period == "this_year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    if period == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    return date_from or today.replace(day=1), date_to or today


def previous_period(start, end):
    """Same length, immediately before; whole months compare with whole
    months (Sept -> Aug, a year -> the year before)."""
    if start.day == 1 and (end + relativedelta(days=1)).day == 1:
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        return start - relativedelta(months=months), start - relativedelta(days=1)
    length = (end - start).days + 1
    return start - relativedelta(days=length), start - relativedelta(days=1)


class MmPerformanceScorecard(models.TransientModel):
    _name = "mm.performance.scorecard"
    _description = "Performance Scorecard"

    name = fields.Char(default=lambda self: _("Performance Scorecard"), readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
    )
    period = fields.Selection(PERIODS, required=True, default="this_month")
    date_from = fields.Date(
        compute="_compute_dates", store=True, readonly=False, required=True, precompute=True,
    )
    date_to = fields.Date(
        compute="_compute_dates", store=True, readonly=False, required=True, precompute=True,
    )
    branch_ids = fields.Many2many(
        "company.branch", string="Branches",
        domain="[('company_id', '=', company_id)]",
        help="Leave empty for all branches.",
    )
    language = fields.Selection(
        [("en_US", "English"), ("ar_001", "Arabic")], string="PDF Language", required=True,
        default=lambda self: "ar_001" if (self.env.user.lang or "").startswith("ar") else "en_US",
    )
    branch_line_ids = fields.One2many(
        "mm.performance.scorecard.branch", "scorecard_id", readonly=True)
    employee_line_ids = fields.One2many(
        "mm.performance.scorecard.employee", "scorecard_id", readonly=True)
    service_line_ids = fields.One2many(
        "mm.performance.scorecard.service", "scorecard_id", readonly=True)
    weight_sales = fields.Integer(readonly=True)
    weight_mistakes = fields.Integer(readonly=True)
    previous_label = fields.Char(readonly=True)
    summary_html = fields.Html(compute="_compute_summary_html", sanitize=False)

    @api.depends("period")
    def _compute_dates(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.date_from, rec.date_to = period_dates(
                rec.period, today, rec.date_from, rec.date_to)

    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._mm_build()
        return records

    def write(self, vals):
        res = super().write(vals)
        if REBUILD_FIELDS & set(vals):
            self._mm_build()
        return res

    @api.model
    def action_open(self):
        scorecard = self.create({})
        return scorecard._mm_form_action()

    def _mm_form_action(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Performance Scorecard"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_refresh(self):
        self._mm_build()
        return True

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------
    def _mm_violations(self, date_from, date_to):
        domain = [
            ("company_id", "=", self.company_id.id),
            ("state", "in", list(COUNTED_STATES)),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
        ]
        if self.branch_ids:
            domain.append(("branch_id", "in", self.branch_ids.ids))
        # searched with the user's rights, read as sudo (hr.employee
        # refuses non-public fields to users without HR rights)
        return self.env["mm.branch.violation"].search(domain).sudo()

    def _mm_staff(self):
        """Active employees of the company in a branch (in the filter)."""
        domain = [("company_id", "=", self.company_id.id), ("branch_id", "!=", False)]
        if self.branch_ids:
            domain.append(("branch_id", "in", self.branch_ids.ids))
        return self.env["hr.employee"].sudo().search(domain)

    def _mm_last_violation_dates(self, employees, up_to):
        """Latest counted violation on or before ``up_to`` per employee."""
        if not employees:
            return {}
        groups = self.env["mm.branch.violation"].sudo()._read_group(
            [("employee_id", "in", employees.ids),
             ("state", "in", list(COUNTED_STATES)),
             ("date", "<=", up_to)],
            ["employee_id"], ["date:max"])
        return {employee.id: last for employee, last in groups}

    def _mm_sales(self, date_from, date_to):
        """{branch id: (target, achieved)} from the branch sales targets of
        the period. Targets inside the period are summed (a year = its 12
        monthly targets); when there are none, a target overlapping the
        period is used instead (a yearly target seen from one month).
        Read as sudo: violation managers need no POS Targets rights for a
        branch total."""
        Target = self.env["pos.sales.target"].sudo()
        base = [("company_id", "=", self.company_id.id),
                ("target_level", "=", "branch"),
                ("branch_id", "!=", False),
                ("state", "not in", ("draft", "cancelled")),
                ("target_amount", ">", 0)]
        if self.branch_ids:
            base.append(("branch_id", "in", self.branch_ids.ids))
        inside = Target.search(base + [("date_from", ">=", date_from), ("date_to", "<=", date_to)])
        overlapping = Target.search(base + [("date_from", "<=", date_to), ("date_to", ">=", date_from)])
        sales = {}
        for branch in overlapping.branch_id:
            targets = inside.filtered(lambda t: t.branch_id == branch) or \
                overlapping.filtered(lambda t: t.branch_id == branch)
            sales[branch.id] = (sum(targets.mapped("target_amount")),
                                sum(targets.mapped("achieved_amount")))
        return sales

    def _mm_rank(self, date_from, date_to):
        """(branch rows, employee rows), each sorted best first."""
        violations = self._mm_violations(date_from, date_to)
        staff = self._mm_staff()
        employees = staff | violations.employee_id
        sales = self._mm_sales(date_from, date_to)
        w_sales = self.company_id.mm_score_weight_sales / 100.0
        w_mistakes = self.company_id.mm_score_weight_mistakes / 100.0

        emp = {e.id: {"employee": e, "violations": 0, "points": 0.0, "complaints": 0,
                      "branches": defaultdict(int)} for e in employees}
        branches = {}
        headcount = defaultdict(int)
        for e in staff:
            headcount[e.branch_id.id] += 1
            branches[e.branch_id.id] = e.branch_id
        br = defaultdict(lambda: {"violations": 0, "points": 0.0, "complaints": 0,
                                  "resolved": 0, "unresolved": 0, "late": 0, "open": 0})
        for v in violations:
            row = emp[v.employee_id.id]
            row["violations"] += 1
            row["points"] += v.points
            row["branches"][v.branch_id] += 1
            branches[v.branch_id.id] = v.branch_id
            b = br[v.branch_id.id]
            b["violations"] += 1
            b["points"] += v.points
            if v.needs_response:
                row["complaints"] += 1
                b["complaints"] += 1
                if v.response_state == "resolved":
                    b["resolved"] += 1
                elif v.response_state == "unresolved":
                    b["unresolved"] += 1
                else:
                    b["open"] += 1
                if v.response_late or v.response_overdue:
                    b["late"] += 1

        # --- employees -------------------------------------------------
        until = min(date_to, fields.Date.context_today(self))
        last = self._mm_last_violation_dates(employees, until)
        emp_rows = []
        for row in emp.values():
            e = row["employee"]
            since = last.get(e.id) or (e.create_date and e.create_date.date()) or date_from
            branch = e.branch_id or (max(row["branches"], key=row["branches"].get)
                                     if row["branches"] else e.branch_id)
            emp_rows.append({
                "employee_id": e.id,
                "branch_id": branch.id,
                "job_id": e.job_id.id,
                "violations": row["violations"],
                "points": row["points"],
                "complaints": row["complaints"],
                "clean_days": max((until - since).days, 0),
                "spotless": e.id not in last,
                "score": max(100.0 - row["points"], 0.0),
                "_name": e.name or "",
            })
        emp_rows.sort(key=lambda r: (-r["score"], r["violations"], not r["spotless"], -r["clean_days"],
                                     r["complaints"], r["_name"]))

        # --- branches --------------------------------------------------
        for branch in self.env["company.branch"].browse(list(sales)):
            branches.setdefault(branch.id, branch)
        br_rows = []
        for branch_id, branch in branches.items():
            b = br[branch_id]
            count = headcount.get(branch_id, 0)
            per_employee = b["points"] / count if count else b["points"]
            mistakes_score = max(100.0 - per_employee, 0.0)
            target, achieved = sales.get(branch_id, (0.0, 0.0))
            sales_rate = 100.0 * achieved / target if target else 0.0
            sales_score = min(sales_rate, 100.0)
            if target and w_sales:
                score = w_sales * sales_score + w_mistakes * mistakes_score
            else:
                score = mistakes_score
            closed = b["resolved"] + b["unresolved"]
            br_rows.append({
                "branch_id": branch_id,
                "headcount": count,
                "violations": b["violations"],
                "points": b["points"],
                "points_per_employee": per_employee,
                "has_target": bool(target),
                "sales_target": target,
                "sales_achieved": achieved,
                "sales_rate": sales_rate,
                "sales_score": sales_score,
                "mistakes_score": mistakes_score,
                "score": score,
                "complaints": b["complaints"],
                "complaints_resolved": b["resolved"],
                "complaints_open": b["open"],
                "complaints_late": b["late"],
                "resolved_pct": 100.0 * b["resolved"] / closed if closed else 0.0,
                "_name": branch.display_name or "",
            })
        br_rows.sort(key=lambda r: (-r["score"], r["violations"], r["complaints"], r["_name"]))
        return br_rows, emp_rows

    @api.model
    def _mm_movement(self, rank, previous):
        """(trend, places moved up - negative = down)"""
        if not previous:
            return "new", 0
        if previous == rank:
            return "same", 0
        return ("up" if previous > rank else "down"), previous - rank

    def _mm_build(self):
        Branch = self.env["mm.performance.scorecard.branch"]
        Employee = self.env["mm.performance.scorecard.employee"]
        for rec in self:
            if rec.date_from > rec.date_to:
                raise UserError(_("The start date must be before the end date."))
            rec.branch_line_ids.unlink()
            rec.employee_line_ids.unlink()
            br_rows, emp_rows = rec._mm_rank(rec.date_from, rec.date_to)
            prev_from, prev_to = previous_period(rec.date_from, rec.date_to)
            prev_br, prev_emp = rec._mm_rank(prev_from, prev_to)
            prev_br_rank = {r["branch_id"]: i for i, r in enumerate(prev_br, 1)}
            # an employee absent from the previous ranking is "new"
            prev_emp_rank = {r["employee_id"]: i for i, r in enumerate(prev_emp, 1)}

            branch_vals = []
            for i, row in enumerate(br_rows, 1):
                trend, delta = rec._mm_movement(i, prev_br_rank.get(row["branch_id"]))
                vals = {k: v for k, v in row.items() if not k.startswith("_")}
                vals.update(scorecard_id=rec.id, rank=i, medal=MEDALS.get(i, ""),
                            trend=trend, rank_delta=delta,
                            # a branch level with the leader is never "worst"
                            is_bottom=len(br_rows) > 3 and i > len(br_rows) - 3
                            and row["score"] < br_rows[0]["score"])
                branch_vals.append(vals)
            Branch.create(branch_vals)

            employee_vals = []
            for i, row in enumerate(emp_rows, 1):
                trend, delta = rec._mm_movement(i, prev_emp_rank.get(row["employee_id"]))
                vals = {k: v for k, v in row.items() if not k.startswith("_")}
                vals.update(scorecard_id=rec.id, rank=i, medal=MEDALS.get(i, ""),
                            trend=trend, rank_delta=delta,
                            is_employee_of_month=i == 1,
                            is_bottom=len(emp_rows) > 5 and i > len(emp_rows) - 5
                            and row["violations"] > 0)
                employee_vals.append(vals)
            Employee.create(employee_vals)
            rec.service_line_ids.unlink()
            self.env["mm.performance.scorecard.service"].create([
                dict(row, scorecard_id=rec.id, rank=i)
                for i, row in enumerate(rec._mm_service_rows(), 1)])
            rec.write({
                "previous_label": "%s → %s" % (prev_from, prev_to),
                "weight_sales": rec.company_id.mm_score_weight_sales,
                "weight_mistakes": rec.company_id.mm_score_weight_mistakes,
            })

    def _mm_service_rows(self):
        """One row per Customer Service person: the complaints of the
        period (any approval status - the response is independent of it),
        credited to the assignee, else to who contacted / closed it."""
        self.ensure_one()
        domain = [("company_id", "=", self.company_id.id),
                  ("needs_response", "=", True),
                  ("date", ">=", self.date_from),
                  ("date", "<=", self.date_to)]
        if self.branch_ids:
            domain.append(("branch_id", "in", self.branch_ids.ids))
        complaints = self.env["mm.branch.violation"].search(domain).sudo()
        rows = defaultdict(lambda: {"complaints": 0, "contacted": 0, "on_time": 0,
                                    "overdue": 0, "open": 0, "resolved": 0,
                                    "unresolved": 0, "hours": 0.0})
        for c in complaints:
            user = c.response_user_id or c.response_contacted_by_id or c.response_closed_by_id
            row = rows[user.id]
            row["complaints"] += 1
            if c.response_contacted_date:
                row["contacted"] += 1
                if not c.response_late:
                    row["on_time"] += 1
                if c.complaint_received_at:
                    row["hours"] += max(
                        (c.response_contacted_date - c.complaint_received_at).total_seconds(), 0) / 3600
            elif c.response_overdue:
                row["overdue"] += 1
            if c.response_state in ("pending", "contacted"):
                row["open"] += 1
            elif c.response_state == "resolved":
                row["resolved"] += 1
            elif c.response_state == "unresolved":
                row["unresolved"] += 1
        result = []
        for user_id, row in rows.items():
            due = row["contacted"] + row["overdue"]
            closed = row["resolved"] + row["unresolved"]
            result.append({
                "user_id": user_id or False,
                "complaints": row["complaints"],
                "contacted": row["contacted"],
                "on_time": row["on_time"],
                "late": row["contacted"] - row["on_time"],
                "overdue": row["overdue"],
                "open_count": row["open"],
                "resolved": row["resolved"],
                "unresolved": row["unresolved"],
                "on_time_pct": 100.0 * row["on_time"] / due if due else 0.0,
                "resolved_pct": 100.0 * row["resolved"] / closed if closed else 0.0,
                "avg_hours": row["hours"] / row["contacted"] if row["contacted"] else 0.0,
            })
        # unassigned last; then on time, resolved, faster
        result.sort(key=lambda r: (not r["user_id"], -r["on_time_pct"], -r["resolved_pct"],
                                   r["avg_hours"], -r["complaints"]))
        return result

    # ------------------------------------------------------------------
    # Highlights
    # ------------------------------------------------------------------
    def _mm_highlights(self):
        """The headline cards, shared by the screen and the PDF."""
        self.ensure_one()
        branches = self.branch_line_ids.sorted("rank")
        employees = self.employee_line_ids.sorted("rank")
        clean = employees.filtered(lambda line: not line.violations)
        worst_branch = branches[-1:] if len(branches) > 1 and \
            branches[-1:].score < branches[:1].score else branches.browse()
        worst_employee = self._mm_most_mistakes(1)
        top = employees[:1]
        tied = employees.filtered(lambda line: (
            line.score, line.violations, line.spotless,
            line.spotless or line.clean_days) == (
            top.score, top.violations, top.spotless,
            top.spotless or top.clean_days)) if top else employees
        top_branch = branches[:1]
        tied_branches = branches.filtered(
            lambda line: (line.score, line.violations, line.complaints) == (
                top_branch.score, top_branch.violations, top_branch.complaints))
        return {
            "tied_first": len(tied),
            "tied_branches": len(tied_branches),
            "best_branch": branches[:1],
            "worst_branch": worst_branch,
            "employee_of_month": employees[:1],
            "worst_employee": worst_employee,
            "clean": len(clean),
            "staff": len(employees),
            "violations": sum(branches.mapped("violations")),
            "complaints": sum(branches.mapped("complaints")),
        }

    def _mm_most_mistakes(self, limit):
        return self.employee_line_ids.filtered("violations").sorted(
            lambda line: (-line.points, -line.violations, line.rank))[:limit]

    @api.depends("branch_line_ids", "employee_line_ids")
    def _compute_summary_html(self):
        for rec in self:
            h = rec._mm_highlights()
            cards = []

            def card(title, icon, color, main, sub):
                cards.append(Markup(
                    '<div class="col-lg-3 col-md-6 mb-3"><div class="card h-100 border-0 shadow-sm" '
                    'style="border-top: 4px solid %s !important;"><div class="card-body">'
                    '<div class="text-muted small text-uppercase fw-bold">%s %s</div>'
                    '<div class="fs-4 fw-bold mt-1">%s</div><div class="text-muted small">%s</div>'
                    '</div></div></div>') % (color, icon, title, main, sub))

            best = h["best_branch"]
            if h["tied_branches"] > 1:
                card(_("Best Branch"), "🏆", "#27AE60",
                     _("%s branches tied", h["tied_branches"]),
                     _("Score %(score)s · %(count)s violations each", score=round(best.score, 1),
                       count=best.violations))
            else:
                card(_("Best Branch"), "🏆", "#27AE60",
                     best.branch_id.display_name if best else "—",
                     best._mm_summary() if best else "")
            eom = h["employee_of_month"]
            card(_("Employee of the Period"), "⭐", "#C9950C",
                 eom.employee_id.name if eom else "—",
                 eom._mm_clean_label(with_branch=True) if eom else "")
            worst = h["worst_branch"]
            card(_("Branch Needing Attention"), "⚠️", "#E74C3C",
                 worst.branch_id.display_name if worst else _("None"),
                 worst._mm_summary() if worst else _("No branch stands out"))
            card(_("Employees Without Mistakes"), "✅", "#2F80ED",
                 "%s / %s" % (h["clean"], h["staff"]),
                 _("%(v)s violations · %(c)s customer complaints",
                   v=h["violations"], c=h["complaints"]))
            note = ""
            if h["tied_first"] > 1:
                note = Markup('<div class="alert alert-warning py-2 small">%s</div>') % _(
                    "%(n)s employees share first place with exactly the same record: "
                    "they are listed alphabetically. Pick the Employee of the Period "
                    "among them.", n=h["tied_first"])
            elif eom and h["clean"] > 1:
                note = Markup('<div class="text-muted small">%s</div>') % _(
                    "%(n)s employees had no mistakes in this period: ties go to a "
                    "spotless record, then the longest run without a violation.",
                    n=h["clean"])
            rec.summary_html = Markup('<div class="row">%s</div>%s') % (
                Markup("").join(cards), note)

    # ------------------------------------------------------------------
    # PDF
    # ------------------------------------------------------------------
    def _mm_file_name(self):
        self.ensure_one()
        return re.sub(r'[\\/*?:"<>|]', "_", "Performance_Scorecard_%s_%s.pdf" % (
            self.date_from, self.date_to))

    def action_print_pdf(self):
        self.ensure_one()
        content, _fmt = self.env["ir.actions.report"].with_context(lang=self.language)._render_qweb_pdf(
            "mm_branch_violation.action_report_mm_performance_scorecard", [self.id])
        attachment = self.env["ir.attachment"].create({
            "name": self._mm_file_name(),
            "datas": base64.b64encode(content),
            "res_model": self._name, "res_id": self.id,
            "mimetype": "application/pdf",
        })
        return {"type": "ir.actions.act_url",
                "url": "/web/content/%s?download=true" % attachment.id, "target": "self"}

    def _mm_report_meta(self):
        self.ensure_one()
        period_label = dict(self._fields["period"]._description_selection(self.env))[self.period]
        return {
            "company": self.company_id,
            "rtl": (self.env.context.get("lang") or "").startswith("ar"),
            "period_label": "%s (%s → %s)" % (period_label, self.date_from, self.date_to),
            "branches_label": ", ".join(self.branch_ids.mapped("display_name")) or _("All branches"),
            "generated_by": self.env.user.name,
            "generated_on": fields.Datetime.context_timestamp(
                self, fields.Datetime.now()).strftime("%Y-%m-%d %H:%M"),
        }


TRENDS = [("up", "Up"), ("down", "Down"), ("same", "Same"), ("new", "New")]


def _rank_change_label(trend, delta, new_label):
    if trend == "new":
        return new_label
    if trend == "up":
        return "▲ %s" % delta
    if trend == "down":
        return "▼ %s" % -delta
    return "=" if trend == "same" else ""


class MmPerformanceScorecardBranch(models.TransientModel):
    _name = "mm.performance.scorecard.branch"
    _description = "Performance Scorecard - Branch"
    _order = "rank"

    scorecard_id = fields.Many2one("mm.performance.scorecard", required=True, ondelete="cascade")
    rank = fields.Integer()
    medal = fields.Char(string=" ")
    branch_id = fields.Many2one("company.branch", string="Branch")
    headcount = fields.Integer(string="Employees")
    violations = fields.Integer()
    points = fields.Float(string="Points", digits=(16, 2))
    points_per_employee = fields.Float(string="Points / Employee", digits=(16, 2))
    score = fields.Float(digits=(16, 1))
    complaints = fields.Integer(string="Complaints")
    has_target = fields.Boolean(string="Has Sales Target")
    sales_target = fields.Float(string="Sales Target", digits=(16, 0))
    sales_achieved = fields.Float(string="Sales Achieved", digits=(16, 0))
    sales_rate = fields.Float(string="Target Achieved %", digits=(16, 1))
    sales_score = fields.Float(string="Sales Score", digits=(16, 1))
    mistakes_score = fields.Float(string="Mistakes Score", digits=(16, 1))
    sales_label = fields.Char(string="Target Achieved", compute="_compute_sales_label")
    complaints_resolved = fields.Integer(string="Resolved")
    complaints_open = fields.Integer(string="Open")
    complaints_late = fields.Integer(string="Late Responses")
    resolved_pct = fields.Float(string="Resolved %", digits=(16, 0))
    trend = fields.Selection(TRENDS)
    rank_delta = fields.Integer()
    rank_change = fields.Char(string="vs Previous", compute="_compute_rank_change")

    @api.depends("trend", "rank_delta")
    def _compute_rank_change(self):
        # computed at display time: the label follows the reader's language
        new = _("New")  # here, not in the helper: _() needs self for the language
        for line in self:
            line.rank_change = _rank_change_label(line.trend, line.rank_delta, new)
    is_bottom = fields.Boolean()

    @api.depends("has_target", "sales_rate")
    def _compute_sales_label(self):
        for line in self:
            line.sales_label = ("%.1f%%" % line.sales_rate) if line.has_target else _("No target")

    def _mm_summary(self):
        self.ensure_one()
        if self.has_target:
            return _("Score %(score)s · sales %(sales)s · %(count)s violations",
                     score=round(self.score, 1), sales=self.sales_label, count=self.violations)
        return _("Score %(score)s · no sales target · %(count)s violations",
                 score=round(self.score, 1), count=self.violations)


class MmPerformanceScorecardEmployee(models.TransientModel):
    _name = "mm.performance.scorecard.employee"
    _description = "Performance Scorecard - Employee"
    _order = "rank"

    scorecard_id = fields.Many2one("mm.performance.scorecard", required=True, ondelete="cascade")
    rank = fields.Integer()
    medal = fields.Char(string=" ")
    employee_id = fields.Many2one("hr.employee", string="Employee")
    branch_id = fields.Many2one("company.branch", string="Branch")
    job_id = fields.Many2one("hr.job", string="Job Position")
    violations = fields.Integer()
    points = fields.Float(string="Points", digits=(16, 2))
    complaints = fields.Integer(string="Complaints")
    spotless = fields.Boolean(
        string="Never Any Mistake",
        help="No approved violation ever, up to the end of the period.")
    clean_days = fields.Integer(
        string="Days Without Mistake",
        help="Days since the employee's last approved violation (or since "
             "the employee was added, if never).",
    )
    score = fields.Float(digits=(16, 1))
    trend = fields.Selection(TRENDS)
    rank_delta = fields.Integer()
    rank_change = fields.Char(string="vs Previous", compute="_compute_rank_change")

    @api.depends("trend", "rank_delta")
    def _compute_rank_change(self):
        # computed at display time: the label follows the reader's language
        new = _("New")  # here, not in the helper: _() needs self for the language
        for line in self:
            line.rank_change = _rank_change_label(line.trend, line.rank_delta, new)
    is_employee_of_month = fields.Boolean(string="Employee of the Period")
    is_bottom = fields.Boolean()
    clean_label = fields.Char(string="Without Mistake", compute="_compute_clean_label")

    def _mm_clean_label(self, with_branch=False):
        self.ensure_one()
        if self.spotless:
            text = _("never had a mistake")
        else:
            text = _("%s days without a mistake", self.clean_days)
        if with_branch and self.branch_id:
            return "%s · %s" % (self.branch_id.display_name, text)
        return text

    @api.depends("spotless", "clean_days")
    def _compute_clean_label(self):
        for line in self:
            line.clean_label = _("Never") if line.spotless else str(line.clean_days)


class MmPerformanceScorecardService(models.TransientModel):
    _name = "mm.performance.scorecard.service"
    _description = "Performance Scorecard - Customer Service"
    _order = "rank"

    scorecard_id = fields.Many2one("mm.performance.scorecard", required=True, ondelete="cascade")
    rank = fields.Integer()
    user_id = fields.Many2one("res.users", string="Customer Service")
    complaints = fields.Integer()
    contacted = fields.Integer()
    on_time = fields.Integer(string="Contacted On Time")
    late = fields.Integer(string="Contacted Late")
    overdue = fields.Integer(string="Overdue Now",
                             help="Deadline passed and the customer is still not contacted.")
    open_count = fields.Integer(string="Still Open")
    resolved = fields.Integer()
    unresolved = fields.Integer()
    on_time_pct = fields.Float(
        string="On Time %", digits=(16, 0),
        help="Contacted on time / (contacted + overdue).")
    resolved_pct = fields.Float(
        string="Resolved %", digits=(16, 0),
        help="Resolved / (resolved + unresolved).")
    avg_hours = fields.Float(string="Avg Hours to Contact", digits=(16, 1))
    name_label = fields.Char(string="Name", compute="_compute_name_label")

    @api.depends("user_id")
    def _compute_name_label(self):
        for line in self:
            line.name_label = line.user_id.name or _("Unassigned")
