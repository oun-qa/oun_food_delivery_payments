# Copyright (c) 2024, Developer and contributors
# License: MIT

import frappe
from frappe import _
from frappe.utils import getdate, today


def execute(filters=None):
    """
    Delivery App Outstanding Balance Report.

    Queries native ERPNext Sales Invoices tagged with a Delivery App and aggregates
    them by month, showing gross invoiced amount, total paid, and outstanding balance.
    This report is the primary reconciliation tool — it shows exactly how much each
    delivery app owes the restaurant and for which periods.
    """
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    summary = get_summary(data)
    return columns, data, None, None, summary


def get_columns():
    return [
        {
            "fieldname": "delivery_app",
            "label": _("Delivery App"),
            "fieldtype": "Link",
            "options": "Delivery App Settings",
            "width": 150,
        },
        {
            "fieldname": "period",
            "label": _("Period (Month)"),
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "fieldname": "invoice_count",
            "label": _("Orders"),
            "fieldtype": "Int",
            "width": 80,
        },
        {
            "fieldname": "gross_invoiced",
            "label": _("Gross Invoiced"),
            "fieldtype": "Currency",
            "width": 150,
        },
        {
            "fieldname": "total_paid",
            "label": _("Total Paid"),
            "fieldtype": "Currency",
            "width": 150,
        },
        {
            "fieldname": "outstanding_amount",
            "label": _("Outstanding Balance"),
            "fieldtype": "Currency",
            "width": 170,
        },
        {
            "fieldname": "oldest_due_date",
            "label": _("Oldest Due Date"),
            "fieldtype": "Date",
            "width": 130,
        },
        {
            "fieldname": "status",
            "label": _("Status"),
            "fieldtype": "Data",
            "width": 110,
        },
    ]


def get_data(filters):
    conditions = ["si.docstatus = 1", "si.dar_delivery_app IS NOT NULL", "si.dar_delivery_app != ''"]
    values = {}

    if filters.get("delivery_app"):
        conditions.append("si.dar_delivery_app = %(delivery_app)s")
        values["delivery_app"] = filters["delivery_app"]

    if filters.get("from_date"):
        conditions.append("si.posting_date >= %(from_date)s")
        values["from_date"] = filters["from_date"]

    if filters.get("to_date"):
        conditions.append("si.posting_date <= %(to_date)s")
        values["to_date"] = filters["to_date"]

    where_clause = "WHERE " + " AND ".join(conditions)

    raw = frappe.db.sql(f"""
        SELECT
            si.dar_delivery_app                         AS delivery_app,
            DATE_FORMAT(si.posting_date, '%%Y-%%m')     AS period_key,
            DATE_FORMAT(si.posting_date, '%%M %%Y')     AS period,
            COUNT(si.name)                              AS invoice_count,
            SUM(si.grand_total)                         AS gross_invoiced,
            SUM(si.grand_total - si.outstanding_amount) AS total_paid,
            SUM(si.outstanding_amount)                  AS outstanding_amount,
            MIN(si.due_date)                            AS oldest_due_date
        FROM `tabSales Invoice` si
        {where_clause}
        GROUP BY si.dar_delivery_app, period_key
        ORDER BY si.dar_delivery_app, period_key DESC
    """, values, as_dict=True)

    today_date = getdate(today())
    result = []
    for row in raw:
        # Determine status
        outstanding = row.outstanding_amount or 0
        gross = row.gross_invoiced or 0
        paid = row.total_paid or 0

        if outstanding <= 0:
            status = "Paid"
        elif paid > 0 and outstanding > 0:
            status = "Partly Paid"
        elif row.oldest_due_date and getdate(row.oldest_due_date) < today_date and outstanding > 0:
            status = "Overdue"
        else:
            status = "Unpaid"

        # Apply status filter if set
        if filters.get("status") and status != filters["status"]:
            continue

        result.append({
            "delivery_app": row.delivery_app,
            "period": row.period,
            "invoice_count": row.invoice_count,
            "gross_invoiced": gross,
            "total_paid": paid,
            "outstanding_amount": outstanding,
            "oldest_due_date": row.oldest_due_date,
            "status": status,
        })

    return result


def get_summary(data):
    """Return totals for the report summary bar."""
    total_gross = sum(r.get("gross_invoiced") or 0 for r in data)
    total_paid = sum(r.get("total_paid") or 0 for r in data)
    total_outstanding = sum(r.get("outstanding_amount") or 0 for r in data)

    return [
        {
            "value": total_gross,
            "label": _("Total Gross Invoiced"),
            "datatype": "Currency",
            "currency": frappe.get_cached_value(
                "Company", frappe.defaults.get_user_default("Company"), "default_currency"
            ) or "QAR",
        },
        {
            "value": total_paid,
            "label": _("Total Received"),
            "datatype": "Currency",
            "currency": frappe.get_cached_value(
                "Company", frappe.defaults.get_user_default("Company"), "default_currency"
            ) or "QAR",
            "indicator": "green",
        },
        {
            "value": total_outstanding,
            "label": _("Total Outstanding"),
            "datatype": "Currency",
            "currency": frappe.get_cached_value(
                "Company", frappe.defaults.get_user_default("Company"), "default_currency"
            ) or "QAR",
            "indicator": "red" if total_outstanding > 0 else "green",
        },
    ]
