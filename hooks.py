app_name = "delivery_app_reconciliation"
app_title = "Delivery App Reconciliation"
app_publisher = "Developer"
app_description = "Delivery App Payment Reconciliation — ERPNext customization layer"
app_email = "dev@company.qa"
app_license = "MIT"

# Fixtures: custom fields applied to existing ERPNext DocTypes on migrate
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [
            ["module", "=", "Delivery App Reconciliation"]
        ]
    }
]

# Doc Events: auto-tag Payment Entry when party is a known delivery app customer
doc_events = {
    "Payment Entry": {
        "before_save": "delivery_app_reconciliation.delivery_app_reconciliation.doctype.delivery_app_settings.delivery_app_settings.auto_tag_payment_entry"
    },
    "Sales Invoice": {
        "before_save": "delivery_app_reconciliation.delivery_app_reconciliation.doctype.delivery_app_settings.delivery_app_settings.auto_tag_sales_invoice"
    }
}

# Scheduled Tasks
scheduler_events = {
    "daily": [
        "delivery_app_reconciliation.delivery_app_reconciliation.doctype.delivery_app_settings.delivery_app_settings.flag_overdue_invoices"
    ]
}
