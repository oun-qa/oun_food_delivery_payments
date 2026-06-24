# Copyright (c) 2024, Developer and contributors
# License: MIT

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class DeliveryAppSettings(Document):
    def validate(self):
        if not self.company:
            self.company = frappe.defaults.get_user_default("Company")
        if not self.company:
            frappe.throw("Company is required.")
        if not self.get("commission_rules") and flt(self.get("commission_rate")):
            self.append("commission_rules", {
                "enabled": 1,
                "rule_name": "Legacy Commission",
                "fee_type": "Percentage",
                "rate_percent": self.commission_rate,
                "description": "Migrated from the legacy flat commission rate.",
            })
        _validate_commission_rules(self)
        if self.contract_end_date and self.contract_start_date:
            if self.contract_end_date < self.contract_start_date:
                frappe.throw("Contract End Date cannot be before Contract Start Date.")
        if self.receivable_account:
            _validate_account_company(self.receivable_account, self.company, "Receivable Account")
        if self.delivery_service_item:
            _validate_delivery_service_item(self.delivery_service_item)


# Helpers
# ---------------------------------------------------------------------------

def _get_app_for_customer(customer, company=None):
    """Return the Delivery App Settings name for a given ERPNext Customer, or None."""
    filters = {"linked_customer": customer, "is_active": 1}
    if company:
        filters["company"] = company

    return frappe.db.get_value(
        "Delivery App Settings",
        filters,
        "name"
    )


def _validate_delivery_service_item(item_code):
    item = frappe.db.get_value(
        "Item",
        item_code,
        ["is_stock_item", "disabled"],
        as_dict=True,
    )

    if not item:
        frappe.throw(f"Delivery Service Item {item_code} does not exist.")
    if item.disabled:
        frappe.throw(f"Delivery Service Item {item_code} is disabled.")
    if item.is_stock_item:
        frappe.throw("Delivery Service Item must be a non-stock item.")


def _validate_account_company(account, company, label="Account"):
    account_company = frappe.db.get_value("Account", account, "company")
    if not account_company:
        frappe.throw(f"{label} {account} does not exist.")
    if account_company != company:
        frappe.throw(f"{label} {account} does not belong to company {company}.")


def _validate_cost_center_company(cost_center, company):
    cost_center_company = frappe.db.get_value("Cost Center", cost_center, "company")
    if not cost_center_company:
        frappe.throw(f"Cost Center {cost_center} does not exist.")
    if cost_center_company != company:
        frappe.throw(f"Cost Center {cost_center} does not belong to company {company}.")


def _sales_invoice_exists_for_order(order_id):
    return frappe.db.exists(
        "Sales Invoice",
        {
            "dar_order_id": order_id,
            "docstatus": ["!=", 2],
        },
    )


def _validate_commission_rules(app_settings):
    for rule in app_settings.get("commission_rules") or []:
        if not rule.enabled:
            continue

        if rule.fee_type == "Percentage":
            if flt(rule.rate_percent) < 0 or flt(rule.rate_percent) > 100:
                frappe.throw(f"Commission rule {rule.rule_name}: Rate (%) must be between 0 and 100.")
        elif rule.fee_type == "Fixed":
            if flt(rule.fixed_amount) < 0:
                frappe.throw(f"Commission rule {rule.rule_name}: Fixed Amount cannot be negative.")
        else:
            frappe.throw(f"Commission rule {rule.rule_name}: Invalid Fee Type.")

        if flt(rule.apply_if_order_amount_gt) < 0:
            frappe.throw(f"Commission rule {rule.rule_name}: threshold cannot be negative.")


def _get_commission_rules(app_settings):
    rules = [rule for rule in app_settings.get("commission_rules") or [] if rule.enabled]
    if rules:
        return rules

    # Backwards compatibility for existing records until users migrate old flat rates.
    if flt(app_settings.get("commission_rate")):
        return [
            frappe._dict({
                "rule_name": "Legacy Commission",
                "fee_type": "Percentage",
                "rate_percent": app_settings.commission_rate,
                "fixed_amount": 0,
                "apply_if_order_amount_gt": 0,
                "description": "Legacy flat commission rate",
            })
        ]

    return []


def _calculate_commission_deductions(app_settings, gross_amount, precision):
    deductions = []

    for rule in _get_commission_rules(app_settings):
        threshold = flt(rule.get("apply_if_order_amount_gt"), precision)
        if threshold and gross_amount <= threshold:
            continue

        if rule.fee_type == "Percentage":
            amount = flt(gross_amount * (flt(rule.rate_percent) / 100), precision)
            detail = f"{flt(rule.rate_percent)}% of order amount"
        else:
            amount = flt(rule.fixed_amount, precision)
            detail = "fixed fee"

        if amount <= 0:
            continue

        deductions.append({
            "rule_name": rule.rule_name,
            "amount": amount,
            "description": rule.description or detail,
        })

    return deductions


def _format_commission_deduction_notes(deductions):
    if not deductions:
        return ""

    lines = ["Commission deductions applied:"]
    for deduction in deductions:
        lines.append(
            f"- {deduction['rule_name']}: {deduction['amount']} ({deduction['description']})"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Doc Event Hooks (called from hooks.py)
# ---------------------------------------------------------------------------

def auto_tag_sales_invoice(doc, method=None):
    """
    Before saving a Sales Invoice, if the customer is a known delivery app customer
    and the dar_delivery_app field is not already set, populate it automatically.
    """
    if doc.customer and not doc.get("dar_delivery_app"):
        app = _get_app_for_customer(doc.customer, doc.get("company"))
        if app:
            doc.dar_delivery_app = app


def auto_tag_payment_entry(doc, method=None):
    """
    Before saving a Payment Entry (Receive type), if the party is a known delivery
    app customer and the dar_delivery_app field is not already set, populate it.
    """
    if doc.payment_type == "Receive" and doc.party_type == "Customer" and doc.party:
        if not doc.get("dar_delivery_app"):
            app = _get_app_for_customer(doc.party, doc.get("company"))
            if app:
                doc.dar_delivery_app = app


# ---------------------------------------------------------------------------
# Scheduled Task
# ---------------------------------------------------------------------------

def flag_overdue_invoices():
    """
    Daily scheduled task: email Accounts Managers a single summary of overdue
    delivery app invoices grouped by platform, company, and currency.
    """
    from frappe.utils import escape_html, fmt_money, formatdate, today

    overdue = frappe.db.sql("""
        SELECT
            dar_delivery_app,
            company,
            currency,
            COUNT(*) AS invoice_count,
            SUM(outstanding_amount) AS outstanding_amount,
            MIN(due_date) AS oldest_due_date
        FROM `tabSales Invoice`
        WHERE dar_delivery_app IS NOT NULL
          AND dar_delivery_app != ''
          AND outstanding_amount > 0
          AND due_date < %s
          AND docstatus = 1
        GROUP BY dar_delivery_app, company, currency
        ORDER BY company, dar_delivery_app
    """, today(), as_dict=True)

    if not overdue:
        return

    accounts_managers = _get_accounts_manager_users()
    if not accounts_managers:
        return

    rows = []
    for item in overdue:
        rows.append(
            "<tr>"
            f"<td>{escape_html(item.dar_delivery_app)}</td>"
            f"<td>{escape_html(item.company)}</td>"
            f"<td style='text-align: right;'>{item.invoice_count}</td>"
            f"<td style='text-align: right;'>{fmt_money(item.outstanding_amount, currency=item.currency)}</td>"
            f"<td>{formatdate(item.oldest_due_date)}</td>"
            "</tr>"
        )

    message = f"""
        <p>The following delivery app balances are overdue as of {formatdate(today())}.</p>
        <table class="table table-bordered" style="border-collapse: collapse; width: 100%;">
            <thead>
                <tr>
                    <th style="text-align: left;">Delivery App</th>
                    <th style="text-align: left;">Company</th>
                    <th style="text-align: right;">Overdue Invoices</th>
                    <th style="text-align: right;">Outstanding Amount</th>
                    <th style="text-align: left;">Oldest Due Date</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
    """

    subject = "Delivery App Overdue Invoice Summary"
    recipients = [user.email for user in accounts_managers if user.email]

    if recipients:
        try:
            frappe.sendmail(
                recipients=recipients,
                subject=subject,
                message=message,
                delayed=True,
            )
            return
        except frappe.OutgoingEmailError:
            frappe.clear_messages()

    _create_overdue_summary_notifications(accounts_managers, subject, message)


def _get_accounts_manager_users():
    users = frappe.db.sql("""
        SELECT DISTINCT u.name, u.email
        FROM `tabUser` u
        INNER JOIN `tabHas Role` r
            ON r.parent = u.name
           AND r.parenttype = 'User'
        WHERE r.role = 'Accounts Manager'
          AND u.enabled = 1
    """, as_dict=True)

    return users


def _create_overdue_summary_notifications(users, subject, message):
    for user in users:
        notification = frappe.new_doc("Notification Log")
        notification.update({
            "subject": subject,
            "type": "Alert",
            "for_user": user.name,
            "from_user": "Administrator",
            "email_content": message,
        })
        notification.insert(ignore_permissions=True)


def migrate_legacy_commission_rates():
    """Create commission rule rows for old settings that still use commission_rate."""
    settings = frappe.get_all(
        "Delivery App Settings",
        fields=["name", "commission_rate"],
        filters={"commission_rate": [">", 0]},
    )

    for setting in settings:
        if frappe.db.exists("Delivery App Commission Rule", {"parent": setting.name}):
            continue

        doc = frappe.get_doc("Delivery App Settings", setting.name)
        doc.append("commission_rules", {
            "enabled": 1,
            "rule_name": "Legacy Commission",
            "fee_type": "Percentage",
            "rate_percent": setting.commission_rate,
            "description": "Migrated from the legacy flat commission rate.",
        })
        doc.save(ignore_permissions=True)


# ---------------------------------------------------------------------------
# CSV Importer
# ---------------------------------------------------------------------------

IMPORT_STATUS_QUEUED = "Queued"
IMPORT_STATUS_RUNNING = "Running"
IMPORT_STATUS_COMPLETED = "Completed"
IMPORT_STATUS_COMPLETED_WITH_ERRORS = "Completed With Errors"
IMPORT_STATUS_FAILED = "Failed"


@frappe.whitelist()
def import_orders_from_csv(file_url, delivery_app, income_account=None, cost_center=None):
    """
    Queue historical delivery app orders CSV import as a background job.

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
        income_account (str): Deprecated. Kept for backwards compatibility.
        cost_center (str): Optional cost center.

    Returns immediately with the background job id. The job updates the latest import
    status fields on the Delivery App Settings document as it runs.
    """
    from frappe.utils import get_url_to_form

    app_settings = frappe.get_doc("Delivery App Settings", delivery_app)
    app_settings.check_permission("write")
    if not app_settings.company:
        frappe.throw("Please select a Company before importing orders.")
    if app_settings.receivable_account:
        _validate_account_company(app_settings.receivable_account, app_settings.company, "Receivable Account")
    if cost_center:
        _validate_cost_center_company(cost_center, app_settings.company)
    if not app_settings.delivery_service_item:
        frappe.throw("Please select a Delivery Service Item before importing orders.")
    _validate_delivery_service_item(app_settings.delivery_service_item)

    frappe.get_doc("File", {"file_url": file_url}).check_permission("read")

    job_id = frappe.generate_hash(length=12)
    started_at = now_datetime()

    _update_import_status(
        delivery_app,
        status=IMPORT_STATUS_QUEUED,
        job_id=job_id,
        started_at=started_at,
        completed_at=None,
        created=0,
        skipped=0,
        failed=0,
        error_log="",
        commit=True,
    )

    frappe.enqueue(
        "delivery_app_reconciliation.delivery_app_reconciliation.doctype.delivery_app_settings.delivery_app_settings.process_orders_csv_import",
        queue="long",
        timeout=3600,
        enqueue_after_commit=True,
        job_id=job_id,
        file_url=file_url,
        delivery_app=delivery_app,
        cost_center=cost_center,
        import_job_id=job_id,
        user=frappe.session.user,
    )

    return {
        "queued": True,
        "job_id": job_id,
        "status": IMPORT_STATUS_QUEUED,
        "message": "CSV import has started in the background.",
        "settings_url": get_url_to_form("Delivery App Settings", delivery_app),
    }


@frappe.whitelist()
def get_csv_import_status(delivery_app, job_id=None):
    """Return the latest CSV import status stored on Delivery App Settings."""
    frappe.get_doc("Delivery App Settings", delivery_app).check_permission("read")

    fields = [
        "last_import_status",
        "last_import_job_id",
        "last_import_started_at",
        "last_import_completed_at",
        "last_import_created",
        "last_import_skipped",
        "last_import_failed",
        "last_import_error_log",
    ]
    status = frappe.db.get_value("Delivery App Settings", delivery_app, fields, as_dict=True)

    if job_id and status and status.last_import_job_id != job_id:
        status["is_current_job"] = False
        return status

    status["is_current_job"] = True
    return status


def process_orders_csv_import(file_url, delivery_app, cost_center=None, import_job_id=None, user=None):
    """
    Background worker for CSV imports. Creates and submits Sales Invoices, then
    records the import summary on Delivery App Settings for UI polling.
    """
    import csv
    from frappe.utils.file_manager import get_file_path
    from frappe.utils import getdate

    frappe.set_user(user or "Administrator")

    _update_import_status(
        delivery_app,
        status=IMPORT_STATUS_RUNNING,
        job_id=import_job_id,
        started_at=now_datetime(),
        completed_at=None,
        created=0,
        skipped=0,
        failed=0,
        error_log="",
        commit=True,
    )

    created = 0
    skipped = 0
    failed = 0
    errors = []

    try:
        app_settings = frappe.get_doc("Delivery App Settings", delivery_app)
        customer = app_settings.linked_customer
        company = app_settings.company
        if not company:
            frappe.throw("Please select a Company before importing orders.")
        if app_settings.receivable_account:
            _validate_account_company(app_settings.receivable_account, company, "Receivable Account")
        if cost_center:
            _validate_cost_center_company(cost_center, company)

        delivery_service_item = app_settings.delivery_service_item
        if not delivery_service_item:
            frappe.throw("Please select a Delivery Service Item before importing orders.")
        _validate_delivery_service_item(delivery_service_item)

        currency = app_settings.currency or frappe.get_cached_value(
            "Company",
            company,
            "default_currency",
        ) or "QAR"

        file_doc = frappe.get_doc("File", {"file_url": file_url})
        file_path = get_file_path(file_doc.name)

        with open(file_path, newline="", encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            if not reader.fieldnames:
                frappe.throw("CSV file is empty or missing headers.")

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

                # Skip if a non-cancelled invoice with this order_id already exists.
                if _sales_invoice_exists_for_order(order_id):
                    skipped += 1
                    continue

                try:
                    savepoint = f"csv_import_row_{i}"
                    frappe.db.savepoint(savepoint)

                    order_date = getdate(order_date_raw)
                    notes = row.get("notes", "")

                    # Build the Sales Invoice
                    inv = frappe.get_doc({
                        "doctype": "Sales Invoice",
                        "company": company,
                        "customer": customer,
                        "posting_date": order_date,
                        "set_posting_time": 1,
                        "due_date": order_date,
                        "ignore_default_payment_terms_template": 1,
                        "currency": currency,
                        "debit_to": app_settings.receivable_account,
                        "dar_delivery_app": delivery_app,
                        "dar_order_id": order_id,
                        "remarks": notes or f"{delivery_app} Order {order_id}",
                        "items": [
                            {
                                # Revenue line: gross order amount
                                "item_code": delivery_service_item,
                                "description": f"Order {order_id} via {delivery_app}",
                                "qty": 1,
                                "rate": 0,
                                "cost_center": cost_center,
                            },
                        ],
                    })
                    rate_precision = inv.precision("rate", "items")
                    gross_amount = flt(gross_amount_raw.replace(",", ""), rate_precision)
                    inv.items[0].rate = gross_amount

                    deductions = _calculate_commission_deductions(
                        app_settings,
                        gross_amount,
                        rate_precision,
                    )
                    total_deductions = flt(
                        sum(deduction["amount"] for deduction in deductions),
                        inv.precision("discount_amount"),
                    )

                    if total_deductions >= gross_amount:
                        frappe.throw(
                            "Total commission deductions cannot be greater than or equal "
                            f"to the gross order amount for order {order_id}."
                        )

                    if total_deductions:
                        inv.apply_discount_on = "Net Total"
                        inv.discount_amount = total_deductions
                        deduction_notes = _format_commission_deduction_notes(deductions)
                        inv.remarks = "\n\n".join(filter(None, [inv.remarks, deduction_notes]))

                    inv.set_missing_values()
                    inv.set("payment_schedule", [])
                    inv.due_date = order_date
                    inv.calculate_taxes_and_totals()
                    inv.set("payment_schedule", [])
                    inv.due_date = order_date
                    inv.insert(ignore_permissions=True)
                    inv.submit()
                    frappe.db.release_savepoint(savepoint)
                    created += 1

                except Exception as e:
                    frappe.db.rollback(save_point=savepoint)
                    errors.append(f"Row {i} (Order ID: {order_id}): {str(e)}")
                    failed += 1

        status = IMPORT_STATUS_COMPLETED_WITH_ERRORS if failed else IMPORT_STATUS_COMPLETED
        _update_import_status(
            delivery_app,
            status=status,
            job_id=import_job_id,
            completed_at=now_datetime(),
            created=created,
            skipped=skipped,
            failed=failed,
            error_log="\n".join(errors),
            commit=True,
        )

    except Exception:
        frappe.db.rollback()
        failed += 1
        errors.append(frappe.get_traceback())
        _update_import_status(
            delivery_app,
            status=IMPORT_STATUS_FAILED,
            job_id=import_job_id,
            completed_at=now_datetime(),
            created=created,
            skipped=skipped,
            failed=failed,
            error_log="\n".join(errors),
            commit=True,
        )
        frappe.log_error(
            title="Delivery App CSV Import Failed",
            message="\n".join(errors),
        )
        raise

    return {
        "created": created,
        "skipped_duplicates": skipped,
        "failed": failed,
        "errors": errors,
    }


def _update_import_status(
    delivery_app,
    status,
    job_id=None,
    started_at=None,
    completed_at=None,
    created=None,
    skipped=None,
    failed=None,
    error_log=None,
    commit=False,
):
    values = {"last_import_status": status}

    if job_id is not None:
        values["last_import_job_id"] = job_id
    if started_at is not None:
        values["last_import_started_at"] = started_at
    if completed_at is not None:
        values["last_import_completed_at"] = completed_at
    if created is not None:
        values["last_import_created"] = created
    if skipped is not None:
        values["last_import_skipped"] = skipped
    if failed is not None:
        values["last_import_failed"] = failed
    if error_log is not None:
        values["last_import_error_log"] = error_log[:10000]

    frappe.db.set_value("Delivery App Settings", delivery_app, values, update_modified=False)
    if commit:
        frappe.db.commit()
