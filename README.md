# Delivery App Reconciliation — Frappe/ERPNext Custom App

A thin ERPNext customization layer for tracking and reconciling payments from food delivery platforms (Snoonu, Careem, Talabat, etc.).

## Design Philosophy

This app does **not** duplicate ERPNext. It extends it. Every order is a native **Sales Invoice**. Every payment is a native **Payment Entry**. The app adds a configuration layer (Delivery App Settings), two custom fields on existing DocTypes, a CSV importer for the backlog, and a reconciliation report. ERPNext's own Accounts Receivable engine, Payment Reconciliation tool, and Journal Entry system do all the heavy lifting.

## Installation

```bash
bench get-app /path/to/delivery_app_reconciliation
bench --site your-site.local install-app delivery_app_reconciliation
bench --site your-site.local migrate
```

The `migrate` step applies the custom fields to Sales Invoice and Payment Entry automatically.

## Components

### 1. Delivery App Settings (New DocType)
One record per delivery platform. Stores:
- The commission rate (e.g. 25%)
- The linked ERPNext Customer record for this platform
- The receivable account
- Contract start/end dates

To add a new delivery app (e.g. Careem), simply create a new Delivery App Settings record. No code changes needed.

### 2. Custom Fields on Sales Invoice
- **Delivery App** (Link → Delivery App Settings) — which platform the order came from
- **Delivery App Order ID** (Data) — the platform's own order reference

These fields are added non-destructively via the fixtures system. They appear in a collapsible "Delivery App" section on the Sales Invoice form.

### 3. Custom Fields on Payment Entry
- **Delivery App** (Link → Delivery App Settings) — which platform made this payment
- **Settlement Reference** (Data) — the bank transfer or batch reference from the platform

### 4. CSV Importer (Backlog)
Accessible from the Delivery App Settings form via **Actions → Import Orders from CSV**.

**CSV format:**
```csv
order_id,order_date,gross_amount,notes
SNO-2024-07-001,2024-07-01,150.000,
SNO-2024-07-002,2024-07-02,220.500,Disputed order
```

Each row creates one submitted Sales Invoice with two line items:
1. Gross order amount (revenue)
2. Commission deduction (negative line) — so the invoice net = what the platform owes

Duplicate order IDs are automatically skipped.

### 5. Delivery App Outstanding Balance (Script Report)
Found under **Accounts > Reports > Delivery App Outstanding Balance**.

Shows, grouped by delivery app and month:
- Number of orders
- Gross invoiced amount
- Total paid
- Outstanding balance
- Status (Unpaid / Partly Paid / Paid / Overdue)

Filterable by delivery app, date range, and status. Summary bar shows totals across all rows.

## Workflow

### Setup (one-time)
1. Create a **Customer** in ERPNext for Snoonu (e.g. "Snoonu Qatar").
2. Create a **Delivery App Settings** record: link the customer, set commission rate (e.g. 25%), set the receivable account.

### Backlog Import
1. Open the Delivery App Settings record for Snoonu.
2. Click **Actions → Import Orders from CSV**.
3. Upload the CSV, select the income account, click Import.
4. The system creates submitted Sales Invoices for all historical orders.

### Ongoing Operations
- New orders: create a Sales Invoice against the Snoonu customer. The "Delivery App" field auto-populates.
- When Snoonu pays: create a Payment Entry (Receive, Customer = Snoonu). Link it to the outstanding invoices using ERPNext's standard Payment Entry reference table. The "Delivery App" field auto-populates.
- Use **Payment Reconciliation** (Accounts > Tools > Payment Reconciliation) to match payments to invoices if needed.

### Reconciliation
Open the **Delivery App Outstanding Balance** report. Filter by Snoonu. Any row with a positive outstanding balance and status "Overdue" is money Snoonu owes and has not paid.

## Accounting Flow

```
Order placed via Snoonu
        ↓
Sales Invoice (Customer: Snoonu Qatar)
  Line 1: Gross order amount     → Debit Receivable, Credit Revenue
  Line 2: Commission deduction   → Debit Revenue, Credit Receivable
  Net invoice = what Snoonu owes
        ↓
Snoonu pays (bank transfer)
        ↓
Payment Entry (Receive, Customer: Snoonu Qatar)
  → Debit Bank Account, Credit Receivable
  → Links to the Sales Invoice(s) it covers
        ↓
Outstanding Amount on invoice drops to 0 → Reconciled
```
