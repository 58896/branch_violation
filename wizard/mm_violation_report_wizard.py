# -*- coding: utf-8 -*-
"""Branch Violations report - PDF (management) and Excel (analysis).

One wizard collects the filters; ``_mm_collect()`` builds every section
once, and both outputs render from that same dict so the PDF and the Excel
can never disagree. Styling follows pos_sales_target_commission's report
family (same navy header, IBM Plex Sans Arabic, badge colours, RTL).

Risk levels on the employee ranking use the capped deduction %:
critical >= 30, warning >= 15, otherwise low.
"""
import base64
import io
import re
from collections import OrderedDict, defaultdict
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.mm_branch_violation import ENTERING_DEPARTMENTS, NOTE_TYPES

try:
    import xlsxwriter
except ImportError:  # pragma: no cover - declared in external_dependencies
    xlsxwriter = None

PERIODS = [
    ("this_month", "This Month"),
    ("last_month", "Last Month"),
    ("this_quarter", "This Quarter"),
    ("this_year", "This Year"),
    ("custom", "Custom"),
]
STATUS_FILTERS = [
    ("counted", "Counted (Approved + Closed)"),
    ("pending", "Pending Approval (New + Under Review)"),
    ("cancelled", "Cancelled"),
    ("all", "All (except Cancelled)"),
]
STATUS_DOMAINS = {
    "counted": [("state", "in", ("approved", "closed"))],
    "pending": [("state", "in", ("draft", "under_review"))],
    "cancelled": [("state", "=", "cancelled")],
    "all": [("state", "!=", "cancelled")],
}
LEVEL_BADGES = {"critical": "pstc-badge-red", "warning": "pstc-badge-orange", "low": "pstc-badge-green"}
STATE_BADGES = {"draft": "pstc-badge-blue", "under_review": "pstc-badge-orange",
                "approved": "pstc-badge-green", "closed": "pstc-badge-grey",
                "cancelled": "pstc-badge-red"}
CRITICAL_PCT = 30.0
WARNING_PCT = 15.0

# report palette (same family as pos_sales_target_commission)
NAVY = "#1B2A4A"
GOLD = "#C9950C"
RED = "#C0392B"
ORANGE = "#E07C1F"
GREEN = "#27AE60"
BLUE = "#2F80ED"
GREY = "#6B7280"
BORDER = "#E5E7EB"
ZEBRA = "#FAFBFC"
LIGHT = "#F5F6FA"
# Excel data bars sit behind the numbers: light tints keep dark text readable
BAR_TINTS = {NAVY: "#A9B8D6", GOLD: "#F1D48A", RED: "#F2A8A0", BLUE: "#A7C8F7", GREEN: "#A6DDB9"}
TYPE_COLORS = [NAVY, BLUE, GOLD, ORANGE, GREEN, "#8E44AD", "#16A085", RED]


class MmViolationReportWizard(models.TransientModel):
    _name = "mm.violation.report.wizard"
    _description = "Branch Violations Report"

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
    employee_ids = fields.Many2many(
        "hr.employee", string="Employees",
        domain="[('company_id', '=', company_id)]",
        help="Leave empty for all employees.",
    )
    violation_type_ids = fields.Many2many(
        "mm.violation.type", string="Violations",
        context={"active_test": False},
        help="Leave empty for all violation types.",
    )
    source_ids = fields.Many2many(
        "mm.violation.source", string="Sources",
        context={"active_test": False},
        help="Leave empty for all sources.",
    )
    job_ids = fields.Many2many(
        "hr.job", string="Job Positions",
        help="Leave empty for all job positions.",
    )
    note_type = fields.Selection(NOTE_TYPES, help="Leave empty for all note types.")
    entering_department = fields.Selection(
        ENTERING_DEPARTMENTS, help="Leave empty for all departments.",
    )
    status_filter = fields.Selection(
        STATUS_FILTERS, string="Status", required=True, default="counted",
    )
    include_details = fields.Boolean(
        string="Include Full Violation List", default=True,
    )
    language = fields.Selection(
        [("en_US", "English"), ("ar_001", "Arabic")], required=True,
        default=lambda self: "ar_001" if (self.env.user.lang or "").startswith("ar") else "en_US",
    )

    @api.depends("period")
    def _compute_dates(self):
        today = fields.Date.context_today(self)
        for wizard in self:
            if wizard.period == "this_month":
                start = today.replace(day=1)
                end = start + relativedelta(months=1, days=-1)
            elif wizard.period == "last_month":
                start = today.replace(day=1) - relativedelta(months=1)
                end = start + relativedelta(months=1, days=-1)
            elif wizard.period == "this_quarter":
                start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
                end = start + relativedelta(months=3, days=-1)
            elif wizard.period == "this_year":
                start, end = date(today.year, 1, 1), date(today.year, 12, 31)
            else:
                start = wizard.date_from or today.replace(day=1)
                end = wizard.date_to or today
            wizard.date_from, wizard.date_to = start, end

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _mm_domain(self, date_from=None, date_to=None, status=None):
        """Violations matching the wizard's filters - by default for its own
        period and status; the summary reuses it for the previous period
        and for the still-pending count."""
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("The start date must be before the end date."))
        domain = [
            ("company_id", "=", self.company_id.id),
            ("date", ">=", date_from or self.date_from),
            ("date", "<=", date_to or self.date_to),
        ] + STATUS_DOMAINS[status or self.status_filter]
        for field, records in (("branch_id", self.branch_ids), ("employee_id", self.employee_ids),
                               ("violation_type_id", self.violation_type_ids),
                               ("source_id", self.source_ids), ("job_id", self.job_ids)):
            if records:
                domain.append((field, "in", records.ids))
        if self.note_type:
            domain.append(("note_type", "=", self.note_type))
        if self.entering_department:
            domain.append(("entering_department", "=", self.entering_department))
        return domain

    def _mm_filters_label(self):
        parts = []
        for label, records in ((_("Branches"), self.branch_ids), (_("Employees"), self.employee_ids),
                               (_("Violations"), self.violation_type_ids),
                               (_("Sources"), self.source_ids), (_("Job Positions"), self.job_ids)):
            if records:
                parts.append("%s: %s" % (label, ", ".join(records.sudo().mapped("display_name"))))
        selections = (("note_type", _("Note Type")), ("entering_department", _("Entering Department")))
        for field, label in selections:
            if self[field]:
                parts.append("%s: %s" % (label, dict(self._fields[field]._description_selection(self.env))[self[field]]))
        parts.append("%s: %s" % (_("Status"), dict(
            self._fields["status_filter"]._description_selection(self.env))[self.status_filter]))
        return " | ".join(parts)

    def _mm_previous_period(self):
        """Same length, immediately before. Whole months compare with the
        same number of whole months (Sept -> Aug, Q3 -> Q2)."""
        self.ensure_one()
        start, end = self.date_from, self.date_to
        if start.day == 1 and (end + relativedelta(days=1)).day == 1:
            months = (end.year - start.year) * 12 + end.month - start.month + 1
            prev_start = start - relativedelta(months=months)
            return prev_start, start - relativedelta(days=1)
        length = (end - start).days + 1
        return start - relativedelta(days=length), start - relativedelta(days=1)

    @api.model
    def _mm_delta(self, current, previous):
        """Arrow text + colour. More violations is always worse here."""
        if not previous:
            if not current:
                return {"text": "—", "color": GREY, "arrow": ""}
            return {"text": _("▲ New"), "color": RED, "arrow": "up"}
        change = 100.0 * (current - previous) / previous
        if abs(change) < 0.05:
            return {"text": _("= No change"), "color": GREY, "arrow": ""}
        arrow = "▲" if change > 0 else "▼"
        return {"text": "%s %.0f%%" % (arrow, abs(change)),
                "color": RED if change > 0 else GREEN,
                "arrow": "up" if change > 0 else "down"}

    def _mm_headline(self, violations):
        """The six headline figures for a set of (sudo) violations."""
        points_by_emp = defaultdict(float)
        for v in violations:
            points_by_emp[v.employee_id.id] += v.points
        total = sum(violations.mapped("points"))
        return {
            "violations": len(violations),
            "points": total,
            "employees": len(points_by_emp),
            "branches": len(violations.branch_id),
            "avg_points": total / len(points_by_emp) if points_by_emp else 0.0,
            "critical": len([p for p in points_by_emp.values() if min(p, 100.0) >= CRITICAL_PCT]),
        }

    @api.model
    def _mm_level(self, pct):
        if pct >= CRITICAL_PCT:
            return "critical", _("Critical"), RED
        if pct >= WARNING_PCT:
            return "warning", _("Warning"), ORANGE
        return "low", _("Low"), GREEN

    def _mm_collect(self):
        """Every section of the report, from one search."""
        self.ensure_one()
        # search with the user's own rights (record rules), read as sudo:
        # hr.employee refuses non-public fields to users without HR rights
        violations = self.env["mm.branch.violation"].search(
            self._mm_domain(), order="date, id").sudo()
        state_labels = dict(
            self.env["mm.branch.violation"]._fields["state"]._description_selection(self.env))
        total_points = sum(violations.mapped("points"))
        count = len(violations)

        def share(value, total):
            return round(100.0 * value / total, 1) if total else 0.0

        # --- by employee ---------------------------------------------
        by_emp = OrderedDict()
        for v in violations:
            row = by_emp.setdefault(v.employee_id, {"violations": 0, "points": 0.0, "branches": set()})
            row["violations"] += 1
            row["points"] += v.points
            row["branches"].add(v.branch_id.display_name)
        employees = []
        for employee, row in by_emp.items():
            pct = min(row["points"], 100.0)
            level, level_label, color = self._mm_level(pct)
            employees.append({
                "number": employee.mm_employee_number or "",
                "name": employee.name,
                "job": employee.job_id.name or "",
                "branch": ", ".join(sorted(row["branches"])),
                "violations": row["violations"],
                "points": row["points"],
                "deduction": pct,
                "level": level, "level_label": level_label, "color": color,
                "badge": LEVEL_BADGES[level],
            })
        employees.sort(key=lambda r: (-r["points"], -r["violations"], r["name"]))
        for rank, row in enumerate(employees, start=1):
            row["rank"] = rank

        # --- by branch -----------------------------------------------
        by_branch = OrderedDict()
        for v in violations:
            row = by_branch.setdefault(v.branch_id, {"violations": 0, "points": 0.0,
                                                    "employees": set(), "types": defaultdict(int)})
            row["violations"] += 1
            row["points"] += v.points
            row["employees"].add(v.employee_id.id)
            row["types"][v.violation_type_id.name] += 1
        branches = []
        max_branch = max([r["points"] for r in by_branch.values()] or [0])
        for branch, row in by_branch.items():
            top_type = max(row["types"].items(), key=lambda kv: kv[1])[0] if row["types"] else ""
            branches.append({
                "name": branch.display_name, "violations": row["violations"],
                "points": row["points"], "employees": len(row["employees"]),
                "avg": row["points"] / len(row["employees"]) if row["employees"] else 0.0,
                "top_type": top_type, "share": share(row["points"], total_points),
                "bar": share(row["points"], max_branch),
            })
        branches.sort(key=lambda r: (-r["points"], -r["violations"]))

        # --- by violation type ---------------------------------------
        by_type = OrderedDict()
        for v in violations:
            row = by_type.setdefault(v.violation_type_id, {"violations": 0, "points": 0.0, "employees": set()})
            row["violations"] += 1
            row["points"] += v.points
            row["employees"].add(v.employee_id.id)
        max_type = max([r["violations"] for r in by_type.values()] or [0])
        types = sorted(({
            "name": vtype.name, "default_points": vtype.points,
            "violations": row["violations"], "points": row["points"],
            "employees": len(row["employees"]),
            "share": share(row["violations"], count), "bar": share(row["violations"], max_type),
        } for vtype, row in by_type.items()), key=lambda r: (-r["violations"], -r["points"]))
        for idx, row in enumerate(types):
            row["color"] = TYPE_COLORS[idx % len(TYPE_COLORS)]

        # --- by source / note type -----------------------------------
        undefined = _("Undefined")

        def simple_group(key_func):
            groups = OrderedDict()
            for v in violations:
                row = groups.setdefault(key_func(v), {"violations": 0, "points": 0.0})
                row["violations"] += 1
                row["points"] += v.points
            peak = max([r["violations"] for r in groups.values()] or [0])
            return sorted(({"name": name or undefined, "violations": row["violations"],
                            "points": row["points"], "share": share(row["violations"], count),
                            "bar": share(row["violations"], peak)}
                           for name, row in groups.items()), key=lambda r: -r["violations"])
        note_labels = dict(self.env["mm.branch.violation"]._fields["note_type"]._description_selection(self.env))
        sources = simple_group(lambda v: v.source_id.name)
        note_types = simple_group(lambda v: note_labels.get(v.note_type))

        # --- monthly trend -------------------------------------------
        months = OrderedDict()
        month_start = self.date_from.replace(day=1)
        while month_start <= self.date_to:
            months[month_start.strftime("%Y-%m")] = {"violations": 0, "points": 0.0, "employees": set()}
            month_start += relativedelta(months=1)
        for v in violations:
            row = months.get(v.date.strftime("%Y-%m"))
            if row is not None:
                row["violations"] += 1
                row["points"] += v.points
                row["employees"].add(v.employee_id.id)
        peak = max([r["violations"] for r in months.values()] or [0])
        trend = [{"month": m, "violations": r["violations"], "points": r["points"],
                  "employees": len(r["employees"]), "bar": share(r["violations"], peak)}
                 for m, r in months.items()]

        # --- details -------------------------------------------------
        details = [{
            "ref": v.name, "date": v.date, "branch": v.branch_id.display_name,
            "employee": v.employee_id.name, "number": v.employee_id.mm_employee_number or "",
            "job": v.job_id.name or "", "type": v.violation_type_id.name,
            "source": v.source_id.name, "note_type": note_labels.get(v.note_type, ""),
            "points": v.points, "state": state_labels.get(v.state, v.state),
            "state_key": v.state, "badge": STATE_BADGES.get(v.state, ""),
            "carried": v.mm_is_carried,
            "description": v.description or "",
        } for v in violations] if self.include_details else []

        critical = [e for e in employees if e["level"] == "critical"]

        # --- comparison with the previous period -----------------------
        Violation = self.env["mm.branch.violation"]
        prev_from, prev_to = self._mm_previous_period()
        previous = self._mm_headline(Violation.search(
            self._mm_domain(prev_from, prev_to)).sudo())
        current = self._mm_headline(violations)
        deltas = {key: self._mm_delta(current[key], previous[key]) for key in current}
        # only meaningful when the report counts approved violations: the
        # other status choices already include (or exclude) the pending ones
        pending = Violation.search_count(self._mm_domain(status="pending")) \
            if self.status_filter == "counted" else 0
        previous_label = "%s  →  %s" % (prev_from, prev_to)

        # --- key insights (plain sentences for management) -------------
        insights = []
        if count:
            insights.append(_(
                "%(v)s violations (%(p)s points) recorded for %(e)s employees in %(b)s branches.",
                v=count, p=self._mm_num(total_points), e=len(employees), b=len(branches)))
            if previous["violations"]:
                change = 100.0 * (count - previous["violations"]) / previous["violations"]
                if change > 0.05:
                    insights.append(_(
                        "Violations went UP %(c).0f%% compared with the previous period (%(a)s → %(b)s).",
                        c=change, a=previous["violations"], b=count))
                elif change < -0.05:
                    insights.append(_(
                        "Violations went DOWN %(c).0f%% compared with the previous period (%(a)s → %(b)s).",
                        c=-change, a=previous["violations"], b=count))
                else:
                    insights.append(_("Same number of violations as the previous period (%s).", count))
            else:
                insights.append(_("No violations in the previous period to compare with."))
            top = branches[0]
            insights.append(_(
                "%(b)s has the most points: %(p)s (%(s)s%% of the total).",
                b=top["name"], p=self._mm_num(top["points"]), s=top["share"]))
            top_type = types[0]
            insights.append(_(
                "Most common mistake: %(t)s - %(n)s times (%(s)s%% of violations).",
                t=top_type["name"], n=top_type["violations"], s=top_type["share"]))
            if critical:
                names = ", ".join(e["name"] for e in critical[:3])
                more = len(critical) - 3
                more_text = _(" and %s more", more) if more > 0 else ""
                insights.append(_(
                    "%(n)s employee(s) at CRITICAL level (30%%+ deduction): %(names)s%(more)s.",
                    n=len(critical), names=names, more=more_text))
            else:
                insights.append(_("No employee reached the critical level (30%+ deduction)."))
        if pending:
            insights.append(_(
                "%s violation(s) in this period are still waiting for approval and are not counted yet.",
                pending))

        return {
            "meta": {
                "company": self.company_id,
                "title": _("Branch Violations Report"),
                "period_label": "%s  →  %s" % (self.date_from, self.date_to),
                "filters_label": self._mm_filters_label(),
                "generated_by": self.env.user.name,
                "generated_on": fields.Datetime.context_timestamp(
                    self, fields.Datetime.now()).strftime("%Y-%m-%d %H:%M"),
                "rtl": self.language == "ar_001",
            },
            "kpis": {
                "violations": count,
                "points": total_points,
                "employees": len(employees),
                "branches": len(branches),
                "avg_points": total_points / len(employees) if employees else 0.0,
                "critical": len(critical),
                "top_branch": branches[0]["name"] if branches else "",
                "top_type": types[0]["name"] if types else "",
            },
            "previous": previous,
            "previous_label": previous_label,
            "deltas": deltas,
            "pending": pending,
            "insights": insights,
            "branches": branches,
            "types": types,
            "sources": sources,
            "note_types": note_types,
            "employees": employees,
            "trend": trend,
            "show_trend": len(trend) > 1,
            "details": details,
            "colors": {"navy": NAVY, "gold": GOLD, "red": RED, "orange": ORANGE,
                       "green": GREEN, "blue": BLUE, "grey": GREY},
        }

    @api.model
    def _mm_num(self, value):
        return ("%.2f" % value).rstrip("0").rstrip(".") if value else "0"

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def _mm_file_name(self, ext):
        self.ensure_one()
        base = "Branch_Violations_%s_%s" % (self.date_from, self.date_to)
        return "%s.%s" % (re.sub(r'[\\/*?:"<>|]', "_", base), ext)

    def _mm_download(self, content, ext):
        attachment = self.env["ir.attachment"].create({
            "name": self._mm_file_name(ext),
            "datas": base64.b64encode(content),
            "res_model": self._name, "res_id": self.id,
            "mimetype": "application/pdf" if ext == "pdf" else
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        })
        return {"type": "ir.actions.act_url",
                "url": "/web/content/%s?download=true" % attachment.id, "target": "self"}

    def action_print_pdf(self):
        self.ensure_one()
        self._mm_domain()  # validate dates before rendering
        content, _fmt = self.env["ir.actions.report"].with_context(lang=self.language)._render_qweb_pdf(
            "mm_branch_violation.action_report_mm_violation", [self.id])
        return self._mm_download(content, "pdf")

    def action_export_xlsx(self):
        self.ensure_one()
        if not xlsxwriter:
            raise UserError(_("The xlsxwriter Python library is not installed."))
        data = self.with_context(lang=self.language)._mm_collect()
        return self._mm_download(self.with_context(lang=self.language)._mm_build_xlsx(data), "xlsx")

    # ------------------------------------------------------------------
    # Excel
    # ------------------------------------------------------------------
    def _mm_build_xlsx(self, data):
        buf = io.BytesIO()
        book = xlsxwriter.Workbook(buf, {
            "in_memory": True,
            "default_format_properties": {"font_name": "IBM Plex Sans Arabic", "font_size": 10},
        })
        rtl = data["meta"]["rtl"]
        align = "right" if rtl else "left"
        fmt = {
            "title": book.add_format({"bold": True, "font_size": 16, "font_color": NAVY, "align": align}),
            "subtitle": book.add_format({"font_size": 10, "font_color": GREY, "align": align}),
            "head": book.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": NAVY,
                                     "border": 1, "border_color": NAVY, "align": "center",
                                     "valign": "vcenter", "text_wrap": True}),
            "cell": book.add_format({"border": 1, "border_color": BORDER, "align": align, "valign": "vcenter"}),
            "zebra": book.add_format({"border": 1, "border_color": BORDER, "bg_color": ZEBRA,
                                      "align": align, "valign": "vcenter"}),
            "int": book.add_format({"border": 1, "border_color": BORDER, "num_format": "#,##0", "align": "center"}),
            "num": book.add_format({"border": 1, "border_color": BORDER, "num_format": "#,##0.00", "align": "center"}),
            "pct": book.add_format({"border": 1, "border_color": BORDER, "num_format": '0.0"%"', "align": "center"}),
            "total_label": book.add_format({"bold": True, "bg_color": "#EDEFF5", "top": 2,
                                            "top_color": NAVY, "border_color": BORDER, "align": align}),
            "total_int": book.add_format({"bold": True, "bg_color": "#EDEFF5", "top": 2, "top_color": NAVY,
                                          "num_format": "#,##0", "align": "center"}),
            "total_num": book.add_format({"bold": True, "bg_color": "#EDEFF5", "top": 2, "top_color": NAVY,
                                          "num_format": "#,##0.00", "align": "center"}),
            "kpi_label": book.add_format({"font_size": 9, "font_color": GREY, "bg_color": LIGHT,
                                          "top": 5, "top_color": NAVY, "align": "center"}),
            "kpi_value": book.add_format({"bold": True, "font_size": 18, "font_color": NAVY,
                                          "bg_color": LIGHT, "align": "center", "num_format": "#,##0.##"}),
            "meta_label": book.add_format({"bold": True, "font_color": NAVY, "align": align}),
            "meta": book.add_format({"font_color": "#4B5563", "align": align, "text_wrap": True}),
        }
        levels = {
            key: book.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": color,
                                  "border": 1, "border_color": BORDER, "align": "center"})
            for key, color in (("critical", RED), ("warning", ORANGE), ("low", GREEN))
        }
        states = {
            key: book.add_format({"font_color": color, "bold": True, "border": 1,
                                  "border_color": BORDER, "align": "center"})
            for key, color in (("draft", BLUE), ("under_review", ORANGE), ("approved", GREEN),
                               ("closed", GREY), ("cancelled", RED))
        }

        def sheet_with_title(name, title, widths):
            sheet = book.add_worksheet(name[:31])
            if rtl:
                sheet.right_to_left()
            sheet.hide_gridlines(2)
            # printing: A4 landscape, all columns on one page width
            sheet.set_paper(9)
            sheet.set_landscape()
            sheet.fit_to_pages(1, 0)
            sheet.set_margins(left=0.4, right=0.4, top=0.5, bottom=0.5)
            sheet.set_footer("&L%s&R&P / &N" % data["meta"]["company"].name.replace("&", "&&"))
            for col, width in enumerate(widths):
                sheet.set_column(col, col, width)
            last = max(len(widths) - 1, 1)
            sheet.merge_range(0, 0, 0, last, title, fmt["title"])
            sheet.merge_range(1, 0, 1, last, "%s   |   %s" % (
                data["meta"]["company"].name, data["meta"]["period_label"]), fmt["subtitle"])
            sheet.set_row(0, 26)
            return sheet

        total_label = _("Total")

        def table(sheet, first_row, headers, rows, kinds, totals=None, bars=(), main=True):
            # main=False: a second table on the same sheet - Excel allows one
            # frozen header and one autofilter per sheet, keep the first one
            sheet.set_row(first_row, 30)
            for col, head in enumerate(headers):
                sheet.write(first_row, col, head, fmt["head"])
            for idx, row in enumerate(rows):
                r = first_row + 1 + idx
                for col, (value, kind) in enumerate(zip(row, kinds)):
                    if kind == "text":
                        sheet.write_string(r, col, str(value or ""), fmt["zebra" if idx % 2 else "cell"])
                    elif isinstance(kind, dict):
                        sheet.write_string(r, col, str(value[0]), kind[value[1]])
                    else:
                        sheet.write_number(r, col, value or 0, fmt[kind])
            last_row = first_row + len(rows)
            if rows and main:
                sheet.autofilter(first_row, 0, last_row, len(headers) - 1)
                sheet.freeze_panes(first_row + 1, 0)
            if rows:
                for col, color in bars:
                    sheet.conditional_format(first_row + 1, col, last_row, col, {
                        "type": "data_bar", "bar_color": BAR_TINTS.get(color, color),
                        "bar_solid": True})
            if totals and rows:
                sheet.write(last_row + 1, 0, total_label, fmt["total_label"])
                for col in range(1, len(headers)):
                    kind = kinds[col]
                    if col in totals:
                        letter = xlsxwriter.utility.xl_col_to_name(col)
                        # cached value too: viewers that don't recalculate show it
                        cached = sum((row[col] or 0) for row in rows)
                        sheet.write_formula(last_row + 1, col, "=SUBTOTAL(9,%s%d:%s%d)" % (
                            letter, first_row + 2, letter, last_row + 1),
                            fmt["total_num" if kind == "num" else "total_int"], cached)
                    else:
                        sheet.write(last_row + 1, col, "", fmt["total_label"])
            return last_row

        # --- Summary: one-page dashboard for management ------------------
        self._mm_xlsx_dashboard(book, data, levels)

        # --- By Branch -------------------------------------------------
        sheet = sheet_with_title(_("By Branch"), _("Violations by Branch"), [34, 12, 12, 12, 14, 12, 44])
        table(sheet, 3, [_("Branch"), _("Violations"), _("Total Points"), _("Employees"),
                         _("Avg Points / Employee"), _("Share %"), _("Most Common Violation")],
              [(r["name"], r["violations"], r["points"], r["employees"], r["avg"], r["share"], r["top_type"])
               for r in data["branches"]],
              ["text", "int", "num", "int", "num", "pct", "text"], totals={1, 2, 3},
              bars=((2, NAVY),))

        # --- By Violation ----------------------------------------------
        sheet = sheet_with_title(_("By Violation"), _("Violations by Type"), [60, 14, 12, 12, 12, 12])
        table(sheet, 3, [_("Violation"), _("Points per Violation"), _("Count"), _("Total Points"),
                         _("Employees"), _("Share %")],
              [(r["name"], r["default_points"], r["violations"], r["points"], r["employees"], r["share"])
               for r in data["types"]],
              ["text", "num", "int", "num", "int", "pct"], totals={2, 3},
              bars=((2, GOLD),))

        # --- By Employee -----------------------------------------------
        sheet = sheet_with_title(_("By Employee"), _("Employee Ranking"),
                                 [7, 14, 32, 16, 34, 11, 12, 13, 12])
        table(sheet, 3, [_("Rank"), _("Employee Number"), _("Employee"), _("Job Position"), _("Branch"),
                         _("Violations"), _("Total Points"), _("Deduction %"), _("Risk Level")],
              [(r["rank"], r["number"], r["name"], r["job"], r["branch"], r["violations"], r["points"],
                r["deduction"], (r["level_label"], r["level"])) for r in data["employees"]],
              ["int", "text", "text", "text", "text", "int", "num", "pct", levels], totals={5, 6},
              bars=((6, RED),))

        # --- By Source / Note type -------------------------------------
        sheet = sheet_with_title(_("By Source"), _("Violations by Source and Note Type"), [40, 12, 12, 12])
        last = table(sheet, 3, [_("Source"), _("Count"), _("Total Points"), _("Share %")],
                     [(r["name"], r["violations"], r["points"], r["share"]) for r in data["sources"]],
                     ["text", "int", "num", "pct"], totals={1, 2}, bars=((1, BLUE),))
        table(sheet, last + 4, [_("Note Type"), _("Count"), _("Total Points"), _("Share %")],
              [(r["name"], r["violations"], r["points"], r["share"]) for r in data["note_types"]],
              ["text", "int", "num", "pct"], totals={1, 2}, bars=((1, GREEN),), main=False)

        # --- Monthly trend ---------------------------------------------
        if data["show_trend"]:
            sheet = sheet_with_title(_("Monthly Trend"), _("Monthly Trend"), [14, 12, 12, 12])
            last = table(sheet, 3, [_("Month"), _("Violations"), _("Total Points"), _("Employees")],
                         [(r["month"], r["violations"], r["points"], r["employees"]) for r in data["trend"]],
                         ["text", "int", "num", "int"], totals={1, 2})
            chart = book.add_chart({"type": "column"})
            chart.add_series({
                "name": _("Violations"),
                "categories": [sheet.name, 4, 0, last, 0],
                "values": [sheet.name, 4, 1, last, 1],
                "fill": {"color": NAVY},
            })
            chart.set_title({"name": _("Violations per Month")})
            chart.set_legend({"none": True})
            chart.set_size({"width": 620, "height": 300})
            sheet.insert_chart(3, 5, chart)

        # --- Details ---------------------------------------------------
        if data["details"]:
            sheet = sheet_with_title(_("Details"), _("Violation List"),
                                     [16, 11, 30, 28, 13, 14, 48, 26, 9, 14, 50])
            table(sheet, 3, [_("Reference"), _("Date"), _("Branch"), _("Employee"), _("Employee Number"),
                             _("Job Position"), _("Violation"), _("Source"), _("Points"), _("Status"),
                             _("Description")],
                  [(r["ref"], str(r["date"]), r["branch"], r["employee"], r["number"], r["job"],
                    r["type"], r["source"], r["points"], (r["state"], r["state_key"]), r["description"])
                   for r in data["details"]],
                  ["text", "text", "text", "text", "text", "text", "text", "text", "num", states, "text"],
                  totals={8})

        book.close()
        return buf.getvalue()

    def _mm_xlsx_dashboard(self, book, data, levels):
        """Executive summary: everything a manager needs, on one screen and
        one printed page - headline figures vs the previous period, plain
        insights, top branches / mistakes / employees, and three charts."""
        meta, kpis, deltas = data["meta"], data["kpis"], data["deltas"]
        rtl = meta["rtl"]
        align = "right" if rtl else "left"
        sheet = book.add_worksheet(_("Summary")[:31])
        sheet.activate()
        sheet.set_first_sheet()
        if rtl:
            sheet.right_to_left()
        sheet.hide_gridlines(2)
        sheet.set_paper(9)
        sheet.set_landscape()
        sheet.fit_to_pages(1, 1)
        sheet.set_margins(left=0.3, right=0.3, top=0.3, bottom=0.3)
        sheet.set_column(0, 11, 13.5)
        F = book.add_format

        band = F({"bold": True, "font_size": 20, "font_color": "#FFFFFF", "bg_color": NAVY,
                  "align": align, "valign": "vcenter", "indent": 1})
        band_sub = F({"font_size": 10, "font_color": "#DCE3F0", "bg_color": NAVY,
                      "align": align, "valign": "vcenter", "indent": 1, "text_wrap": True})
        section = F({"bold": True, "font_size": 12, "font_color": NAVY, "bottom": 2,
                     "bottom_color": GOLD, "align": align, "valign": "vcenter"})
        th = F({"bold": True, "font_size": 9, "font_color": "#FFFFFF", "bg_color": NAVY,
                "align": "center", "valign": "vcenter", "border": 1, "border_color": NAVY})
        td = F({"font_size": 10, "align": align, "valign": "vcenter", "border": 1,
                "border_color": BORDER})
        td_int = F({"font_size": 10, "align": "center", "valign": "vcenter", "border": 1,
                    "border_color": BORDER, "num_format": "#,##0"})
        td_num = F({"font_size": 10, "align": "center", "valign": "vcenter", "border": 1,
                    "border_color": BORDER, "num_format": "#,##0.##"})
        td_pct = F({"font_size": 10, "align": "center", "valign": "vcenter", "border": 1,
                    "border_color": BORDER, "num_format": '0.0"%"'})
        insight = F({"font_size": 11, "font_color": "#1F2937", "align": align, "valign": "vcenter",
                     "text_wrap": True, "bg_color": "#FFFBEA", "left": 5 if not rtl else 0,
                     "right": 5 if rtl else 0, "left_color": GOLD, "right_color": GOLD})
        foot = F({"font_size": 8, "font_color": GREY, "align": align})

        def merge(r1, c1, r2, c2, value, fmt):
            if (r1, c1) == (r2, c2):
                sheet.write(r1, c1, value, fmt)
            else:
                sheet.merge_range(r1, c1, r2, c2, value, fmt)

        # 1. title band ------------------------------------------------
        sheet.set_row(0, 34)
        sheet.set_row(1, 30)
        merge(0, 0, 0, 11, meta["title"], band)
        merge(1, 0, 1, 11, "%s   |   %s   |   %s" % (
            meta["company"].name, meta["period_label"], meta["filters_label"]), band_sub)

        # 2. headline cards (value + change vs previous period) --------
        cards = [
            ("violations", _("Violations"), NAVY, "#,##0"),
            ("points", _("Total Points"), GOLD, "#,##0.##"),
            ("employees", _("Employees Affected"), BLUE, "#,##0"),
            ("branches", _("Branches Affected"), GREEN, "#,##0"),
            ("avg_points", _("Avg Points / Employee"), ORANGE, "#,##0.0"),
            ("critical", _("Employees at 30%+"), RED, "#,##0"),
        ]
        sheet.set_row(3, 22)
        sheet.set_row(4, 44)
        sheet.set_row(5, 20)
        sheet.set_row(6, 16)
        vs_label = _("vs previous period")
        for idx, (key, label, color, num) in enumerate(cards):
            c1, c2 = idx * 2, idx * 2 + 1
            value_color = RED if key == "critical" and kpis[key] else NAVY
            merge(3, c1, 3, c2, label, F({
                "font_size": 9, "font_color": GREY, "bg_color": LIGHT, "align": "center",
                "valign": "bottom", "top": 6, "top_color": color, "bold": True}))
            merge(4, c1, 4, c2, kpis[key], F({
                "bold": True, "font_size": 26, "font_color": value_color, "bg_color": LIGHT,
                "align": "center", "valign": "vcenter", "num_format": num}))
            delta = deltas[key]
            merge(5, c1, 5, c2, delta["text"], F({
                "bold": True, "font_size": 11, "font_color": delta["color"], "bg_color": LIGHT,
                "align": "center", "valign": "vcenter"}))
            merge(6, c1, 6, c2, "%s (%s)" % (vs_label, self._mm_num(data["previous"][key])), F({
                "font_size": 8, "font_color": GREY, "bg_color": LIGHT, "align": "center",
                "valign": "top"}))

        # 3. key insights ----------------------------------------------
        row = 8
        sheet.set_row(row, 22)
        merge(row, 0, row, 11, _("Key Insights"), section)
        row += 1
        for text in data["insights"] or [_("No violations match the selected filters.")]:
            sheet.set_row(row, 21)
            merge(row, 0, row, 11, "•  " + text, insight)
            row += 1
        row += 1

        # 4. top 5 branches (left) + top 5 mistakes (right) -----------
        sheet.set_row(row, 22)
        merge(row, 0, row, 5, _("Top 5 Branches"), section)
        merge(row, 6, row, 11, _("Top 5 Mistakes"), section)
        row += 1
        head_row = row
        merge(row, 0, row, 2, _("Branch"), th)
        sheet.write(row, 3, _("Violations"), th)
        sheet.write(row, 4, _("Total Points"), th)
        sheet.write(row, 5, _("Share %"), th)
        merge(row, 6, row, 9, _("Violation"), th)
        sheet.write(row, 10, _("Count"), th)
        sheet.write(row, 11, _("Share %"), th)
        top_branches = data["branches"][:5]
        top_types = data["types"][:5]
        other_types = data["types"][5:]
        type_rows = [(t["name"], t["violations"], t["share"]) for t in top_types]
        if other_types:
            type_rows.append((_("Others"), sum(t["violations"] for t in other_types),
                              round(sum(t["share"] for t in other_types), 1)))
        for i in range(max(len(top_branches), len(type_rows), 1)):
            r = head_row + 1 + i
            sheet.set_row(r, 19)
            if i < len(top_branches):
                b = top_branches[i]
                merge(r, 0, r, 2, b["name"], td)
                sheet.write_number(r, 3, b["violations"], td_int)
                sheet.write_number(r, 4, b["points"], td_num)
                sheet.write_number(r, 5, b["share"], td_pct)
            if i < len(type_rows):
                name, n, share = type_rows[i]
                merge(r, 6, r, 9, name, td)
                sheet.write_number(r, 10, n, td_int)
                sheet.write_number(r, 11, share, td_pct)
        last_table_row = head_row + max(len(top_branches), len(type_rows), 1)
        if top_branches:
            sheet.conditional_format(head_row + 1, 4, head_row + len(top_branches), 4, {
                "type": "data_bar", "bar_color": BAR_TINTS[NAVY], "bar_solid": True})
        if type_rows:
            sheet.conditional_format(head_row + 1, 10, head_row + len(type_rows), 10, {
                "type": "data_bar", "bar_color": BAR_TINTS[GOLD], "bar_solid": True})

        # charts for the two tables
        row = last_table_row + 2
        chart_rows = 13
        name = sheet.name
        if top_branches:
            chart = book.add_chart({"type": "bar"})
            chart.add_series({
                "name": _("Total Points"),
                "categories": [name, head_row + 1, 0, head_row + len(top_branches), 0],
                "values": [name, head_row + 1, 4, head_row + len(top_branches), 4],
                "fill": {"color": NAVY}, "gap": 60,
                "data_labels": {"value": True, "font": {"size": 9, "color": NAVY}},
            })
            chart.set_title({"name": _("Points by Branch"), "name_font": {"size": 11, "color": NAVY}})
            chart.set_legend({"none": True})
            chart.set_y_axis({"reverse": True, "num_font": {"size": 8}})
            chart.set_x_axis({"major_gridlines": {"visible": True, "line": {"color": BORDER}},
                              "num_font": {"size": 8}})
            chart.set_chartarea({"border": {"color": BORDER}})
            chart.set_size({"width": 6 * 90, "height": chart_rows * 20})
            sheet.insert_chart(row, 0, chart, {"x_offset": 2, "y_offset": 2})
        if type_rows:
            chart = book.add_chart({"type": "doughnut"})
            chart.add_series({
                "name": _("Violations"),
                "categories": [name, head_row + 1, 6, head_row + len(type_rows), 6],
                "values": [name, head_row + 1, 10, head_row + len(type_rows), 10],
                "points": [{"fill": {"color": TYPE_COLORS[i % len(TYPE_COLORS)]}}
                           for i in range(len(type_rows))],
                "data_labels": {"percentage": True, "font": {"size": 9, "color": "#FFFFFF", "bold": True}},
            })
            chart.set_title({"name": _("Share of Mistakes"), "name_font": {"size": 11, "color": NAVY}})
            chart.set_legend({"position": "right", "font": {"size": 8}})
            chart.set_hole_size(50)
            chart.set_chartarea({"border": {"color": BORDER}})
            chart.set_size({"width": 6 * 90, "height": chart_rows * 20})
            sheet.insert_chart(row, 6, chart, {"x_offset": 2, "y_offset": 2})
        row += chart_rows + 1

        # 5. employees at risk (left) + trend / sources chart (right) --
        sheet.set_row(row, 22)
        merge(row, 0, row, 5, _("Top 5 Employees at Risk"), section)
        trend = data["show_trend"]
        merge(row, 6, row, 11, _("Monthly Trend") if trend else _("Violations by Source"), section)
        row += 1
        emp_head = row
        merge(row, 0, row, 1, _("Employee"), th)
        merge(row, 2, row, 3, _("Branch"), th)
        sheet.write(row, 4, _("Deduction %"), th)
        sheet.write(row, 5, _("Risk Level"), th)
        for i, e in enumerate(data["employees"][:5]):
            r = emp_head + 1 + i
            sheet.set_row(r, 19)
            label = e["name"] + ("  #%s" % e["number"] if e["number"] else "")
            merge(r, 0, r, 1, label, td)
            merge(r, 2, r, 3, e["branch"], td)
            sheet.write_number(r, 4, e["deduction"], F({
                "font_size": 10, "bold": True, "font_color": e["color"], "align": "center",
                "border": 1, "border_color": BORDER, "num_format": '0.0"%"'}))
            sheet.write(r, 5, e["level_label"], levels[e["level"]])
        # data for the right-hand chart lives on a hidden sheet
        helper = ([(r["month"], r["violations"]) for r in data["trend"]] if trend
                  else [(r["name"], r["violations"]) for r in data["sources"]])
        helper_sheet = book.add_worksheet("chart_data")
        helper_sheet.hide()
        for i, (label, value) in enumerate(helper):
            helper_sheet.write(i, 0, label)
            helper_sheet.write_number(i, 1, value)
        if helper:
            chart = book.add_chart({"type": "column" if trend else "pie"})
            series = {
                "name": _("Violations"),
                "categories": ["chart_data", 0, 0, len(helper) - 1, 0],
                "values": ["chart_data", 0, 1, len(helper) - 1, 1],
            }
            if trend:
                series.update({"fill": {"color": GOLD}, "gap": 80,
                               "data_labels": {"value": True, "font": {"size": 9}}})
            else:
                series.update({"points": [{"fill": {"color": TYPE_COLORS[i % len(TYPE_COLORS)]}}
                                          for i in range(len(helper))],
                               "data_labels": {"percentage": True,
                                               "font": {"size": 9, "color": "#FFFFFF", "bold": True}}})
            chart.add_series(series)
            chart.set_title({"none": True})
            chart.set_legend({"none": True} if trend else {"position": "right", "font": {"size": 8}})
            chart.set_chartarea({"border": {"color": BORDER}})
            chart.set_size({"width": 6 * 90, "height": 7 * 20})
            sheet.insert_chart(emp_head, 6, chart, {"x_offset": 2, "y_offset": 2})
        row = emp_head + 8

        # 6. footer ------------------------------------------------------
        merge(row, 0, row, 11, "%s: %s ≥30%%   ·   %s: 15–29%%   ·   %s: <15%%   |   %s: %s   ·   %s   |   %s: %s   ·   %s: %s" % (
            _("Critical"), _("Deduction %"), _("Warning"), _("Low"),
            _("Previous period"), data["previous_label"], _("▲ = more violations (worse), ▼ = fewer (better)"),
            _("Generated By"), meta["generated_by"], _("Generated On"), meta["generated_on"]), foot)
        sheet.print_area(0, 0, row, 11)
