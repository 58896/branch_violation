# -*- coding: utf-8 -*-
{
    "name": "Branch Violations",
    "version": "18.0.9.0.0",
    "category": "Human Resources",
    "summary": "Log branch/employee violations with a four-eyes approval "
               "workflow and deduct their points from POS commission",
    "description": "Replaces the Operations 'Branch Notes and Reports' Excel "
                   "sheet: one record per violation (branch, employee, source, "
                   "violation type, deduction points %), Officer -> Manager "
                   "approval with segregation of duties, per-employee "
                   "analysis, and a one-time employee-number import wizard. "
                   "Approved points are deducted as a % from the employee's "
                   "own line of the POS commission distribution "
                   "(pos_sales_target_commission), locked when the "
                   "distribution is approved, with carry-forward for late "
                   "approvals and an Export for Jisr xlsx. Customer complaints get "
                   "a Customer Service follow-up (contacted / resolved) with "
                   "a configurable response deadline and reminders. A Performance "
                   "Scorecard ranks branches and employees by mistakes "
                   "(best/worst branch, employee of the period). A Violations "
                   "Dashboard gives managers everything at a glance: to-do "
                   "strip, KPIs vs the previous period, trend, branch x type "
                   "heatmap, Pareto of violation types, repeat offenders and "
                   "complaint handling, with cross-filtering and saved views.",
    "author": "Muhammad Rizwan",
    "website": "",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "auto_install": False,
    "depends": [
        "hr",
        "mail",
        "stock_analytic_integration",
        "pos_sales_target_commission",
    ],
    "external_dependencies": {"python": ["openpyxl", "xlsxwriter"]},
    "data": [
        "security/mm_branch_violation_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/mm_violation_type_data.xml",
        "data/mm_violation_source_data.xml",
        "data/mail_activity_type_data.xml",
        "data/ir_cron_data.xml",
        "views/mm_violation_type_views.xml",
        "views/mm_violation_source_views.xml",
        "views/mm_branch_violation_views.xml",
        "views/hr_employee_views.xml",
        "views/pos_commission_views.xml",
        "views/mm_violation_deduction_period_views.xml",
        "views/res_config_settings_views.xml",
        "wizard/mm_violation_cancel_wizard_views.xml",
        "wizard/mm_employee_number_import_views.xml",
        "wizard/mm_violation_report_wizard_views.xml",
        "wizard/mm_performance_scorecard_views.xml",
        "report/mm_violation_report.xml",
        "report/mm_performance_scorecard_report.xml",
        "views/mm_branch_violation_menus.xml",
        "views/mm_violation_dashboard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mm_branch_violation/static/src/dashboard/violation_dashboard.js",
            "mm_branch_violation/static/src/dashboard/violation_dashboard.xml",
            "mm_branch_violation/static/src/dashboard/violation_dashboard.scss",
        ],
    },
}
