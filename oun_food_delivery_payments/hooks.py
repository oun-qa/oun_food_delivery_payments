app_name = "oun_food_delivery_payments"
app_title = "Oun Food Delivery Payments"
app_publisher = "Developer"
app_description = "Delivery App Payment Reconciliation — ERPNext customization layer"
app_email = "dev@company.qa"
app_license = "MIT"

# Fixtures: custom fields applied to existing ERPNext DocTypes on migrate
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [
            ["module", "=", "Oun Food Delivery Payments"]
        ]
    }
]

# Doc Events: auto-tag Payment Entry when party is a known delivery app customer
doc_events = {
    "Payment Entry": {
        "before_save": "oun_food_delivery_payments.oun_food_delivery_payments.doctype.delivery_app_settings.delivery_app_settings.auto_tag_payment_entry"
    },
    "Sales Invoice": {
        "before_save": "oun_food_delivery_payments.oun_food_delivery_payments.doctype.delivery_app_settings.delivery_app_settings.auto_tag_sales_invoice"
    }
}

# Scheduled Tasks
scheduler_events = {
    "daily": [
        "oun_food_delivery_payments.oun_food_delivery_payments.doctype.delivery_app_settings.delivery_app_settings.flag_overdue_invoices"
    ]
}

after_migrate = [
    "oun_food_delivery_payments.oun_food_delivery_payments.doctype.delivery_app_settings.delivery_app_settings.migrate_legacy_commission_rates"
]
