# Copyright (c) 2024, Developer and contributors
# License: MIT

from collections import defaultdict

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
    if not frappe.has_permission("Sales Invoice", "read"):
        return []

    invoice_filters = [
        ["docstatus", "=", 1],
        ["dar_delivery_app", "!=", ""],
    ]

    if filters.get("delivery_app"):
        invoice_filters.append(["dar_delivery_app", "=", filters["delivery_app"]])

    if filters.get("from_date"):
        invoice_filters.append(["posting_date", ">=", filters["from_date"]])

    if filters.get("to_date"):
        invoice_filters.append(["posting_date", "<=", filters["to_date"]])

    invoices = frappe.get_list(
        "Sales Invoice",
        filters=invoice_filters,
        fields=[
            "name",
            "dar_delivery_app",
            "posting_date",
            "grand_total",
            "outstanding_amount",
            "due_date",
        ],
        order_by="dar_delivery_app asc, posting_date desc",
        limit_page_length=0,
    )

    raw = _aggregate_invoices(invoices)

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


def _aggregate_invoices(invoices):
    grouped = defaultdict(lambda: {
        "invoice_count": 0,
        "gross_invoiced": 0,
        "total_paid": 0,
        "outstanding_amount": 0,
        "oldest_due_date": None,
    })

    for invoice in invoices:
        posting_date = getdate(invoice.posting_date)
        period_key = posting_date.strftime("%Y-%m")
        key = (invoice.dar_delivery_app, period_key)
        row = grouped[key]

        row["delivery_app"] = invoice.dar_delivery_app
        row["period_key"] = period_key
        row["period"] = posting_date.strftime("%B %Y")
        row["invoice_count"] += 1
        row["gross_invoiced"] += invoice.grand_total or 0
        row["total_paid"] += (invoice.grand_total or 0) - (invoice.outstanding_amount or 0)
        row["outstanding_amount"] += invoice.outstanding_amount or 0

        if invoice.due_date:
            due_date = getdate(invoice.due_date)
            if not row["oldest_due_date"] or due_date < getdate(row["oldest_due_date"]):
                row["oldest_due_date"] = invoice.due_date

    return [
        frappe._dict(row)
        for row in sorted(
            grouped.values(),
            key=lambda item: (item["delivery_app"], -int(item["period_key"].replace("-", ""))),
        )
    ]


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
