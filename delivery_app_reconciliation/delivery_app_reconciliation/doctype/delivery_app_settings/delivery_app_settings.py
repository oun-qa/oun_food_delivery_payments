# Copyright (c) 2024, Developer and contributors
# License: MIT

import frappe
from frappe.model.document import Document


class DeliveryAppSettings(Document):
    def validate(self):
        if self.commission_rate < 0 or self.commission_rate > 100:
            frappe.throw("Commission Rate must be between 0 and 100.")
        if self.contract_end_date and self.contract_start_date:
            if self.contract_end_date < self.contract_start_date:
                frappe.throw("Contract End Date cannot be before Contract Start Date.")


# Helpers
# ---------------------------------------------------------------------------

def _get_app_for_customer(customer):
    """Return the Delivery App Settings name for a given ERPNext Customer, or None."""
    return frappe.db.get_value(
        "Delivery App Settings",
        {"linked_customer": customer, "is_active": 1},
        "name"
    )


# ---------------------------------------------------------------------------
# Doc Event Hooks (called from hooks.py)
# ---------------------------------------------------------------------------

def auto_tag_sales_invoice(doc, method=None):
    """
    Before saving a Sales Invoice, if the customer is a known delivery app customer
    and the dar_delivery_app field is not already set, populate it automatically.
    """
    if doc.customer and not doc.get("dar_delivery_app"):
        app = _get_app_for_customer(doc.customer)
        if app:
            doc.dar_delivery_app = app


def auto_tag_payment_entry(doc, method=None):
    """
    Before saving a Payment Entry (Receive type), if the party is a known delivery
    app customer and the dar_delivery_app field is not already set, populate it.
    """
    if doc.payment_type == "Receive" and doc.party_type == "Customer" and doc.party:
        if not doc.get("dar_delivery_app"):
            app = _get_app_for_customer(doc.party)
            if app:
                doc.dar_delivery_app = app


# ---------------------------------------------------------------------------
# Scheduled Task
# ---------------------------------------------------------------------------

def flag_overdue_invoices():
    """
    Daily scheduled task: log any Sales Invoices tagged to a delivery app
    that are overdue (past due date with outstanding amount > 0).
    Useful for surfacing unpaid periods without manual checking.
    """
    from frappe.utils import today
    overdue = frappe.db.sql("""
        SELECT name, customer, due_date, outstanding_amount, dar_delivery_app
        FROM `tabSales Invoice`
        WHERE dar_delivery_app IS NOT NULL
          AND dar_delivery_app != ''
          AND outstanding_amount > 0
          AND due_date < %s
          AND docstatus = 1
    """, today(), as_dict=True)

    for inv in overdue:
        # Only log if not already logged recently
        existing = frappe.db.exists("Error Log", {
            "method": "Delivery App Overdue Invoice",
            "error": ["like", f"%{inv.name}%"]
        })
        if not existing:
            frappe.log_error(
                title="Delivery App Overdue Invoice",
                message=(
                    f"Invoice {inv.name} for {inv.dar_delivery_app} "
                    f"(Customer: {inv.customer}) is overdue since {inv.due_date}. "
                    f"Outstanding: {inv.outstanding_amount}"
                )
            )


# ---------------------------------------------------------------------------
# CSV Importer
# ---------------------------------------------------------------------------

@frappe.whitelist()
def import_orders_from_csv(file_url, delivery_app, income_account, cost_center=None):
    """
    Import historical delivery app orders from a CSV file as Sales Invoices in ERPNext.

    Each row in the CSV becomes one submitted Sales Invoice against the delivery app's
    linked Customer. The invoice amount is the gross order value. The commission is
    recorded as a separate line item (negative amount) so the net invoice value
    represents what the delivery app owes the restaurant.

    Expected CSV columns (case-insensitive, order does not matter):
        order_id        — Unique order reference from the delivery app
        order_date      — Date of the order (YYYY-MM-DD)
        gross_amount    — Full order value before commission
        notes           — Optional description

    Args:
        file_url (str): Frappe File URL of the uploaded CSV.
        delivery_app (str): Name of the Delivery App Settings record (e.g. "Snoonu").
        income_account (str): ERPNext income/revenue account to credit (e.g. "Sales - Company").
        cost_center (str): Optional cost center.

    Returns:
        dict: Summary with created, skipped, failed counts and any error messages.
    """
    import csv
    from frappe.utils.file_manager import get_file_path
    from frappe.utils import getdate, today

    app_settings = frappe.get_doc("Delivery App Settings", delivery_app)
    customer = app_settings.linked_customer
    commission_rate = app_settings.commission_rate
    currency = app_settings.currency or frappe.get_cached_value("Company",
        frappe.defaults.get_user_default("Company"), "default_currency") or "QAR"

    file_doc = frappe.get_doc("File", {"file_url": file_url})
    file_path = get_file_path(file_doc.name)

    created = 0
    skipped = 0
    failed = 0
    errors = []

    with open(file_path, newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        # Normalize headers
        reader.fieldnames = [h.strip().lower().replace(" ", "_") for h in reader.fieldnames]

        for i, row in enumerate(reader, start=2):
            order_id = row.get("order_id", "").strip()
            order_date_raw = row.get("order_date", "").strip()
            gross_amount_raw = row.get("gross_amount", "").strip()

            if not order_id or not order_date_raw or not gross_amount_raw:
                errors.append(f"Row {i}: Missing required field (order_id, order_date, or gross_amount). Skipped.")
                failed += 1
                continue

            # Skip if an invoice with this order_id already exists
            if frappe.db.exists("Sales Invoice", {"dar_order_id": order_id}):
                skipped += 1
                continue

            try:
                gross_amount = float(gross_amount_raw.replace(",", ""))
                commission_amount = round(gross_amount * (commission_rate / 100), 3)
                net_amount = round(gross_amount - commission_amount, 3)
                order_date = getdate(order_date_raw)
                notes = row.get("notes", "")

                # Build the Sales Invoice
                inv = frappe.get_doc({
                    "doctype": "Sales Invoice",
                    "customer": customer,
                    "posting_date": order_date,
                    "currency": currency,
                    "debit_to": app_settings.receivable_account,
                    "dar_delivery_app": delivery_app,
                    "dar_order_id": order_id,
                    "remarks": notes or f"{delivery_app} Order {order_id}",
                    "items": [
                        {
                            # Revenue line: gross order amount
                            "item_name": f"{delivery_app} Order",
                            "description": f"Order {order_id} via {delivery_app}",
                            "qty": 1,
                            "rate": gross_amount,
                            "income_account": income_account,
                            "cost_center": cost_center,
                        },
                        {
                            # Commission deduction line: negative amount
                            "item_name": f"{delivery_app} Commission ({commission_rate}%)",
                            "description": f"Commission deducted by {delivery_app} for order {order_id}",
                            "qty": 1,
                            "rate": -commission_amount,
                            "income_account": income_account,
                            "cost_center": cost_center,
                        },
                    ],
                })
                inv.insert(ignore_permissions=True)
                inv.submit()
                created += 1

            except Exception as e:
                errors.append(f"Row {i} (Order ID: {order_id}): {str(e)}")
                failed += 1

    frappe.db.commit()

    return {
        "created": created,
        "skipped_duplicates": skipped,
        "failed": failed,
        "errors": errors,
    }
