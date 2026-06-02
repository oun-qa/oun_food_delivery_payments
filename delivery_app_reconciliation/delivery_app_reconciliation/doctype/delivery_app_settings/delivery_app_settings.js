// Copyright (c) 2024, Developer and contributors
// License: MIT

frappe.ui.form.on('Delivery App Settings', {
    refresh: function(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button('Import Orders from CSV', function() {
                _show_import_dialog(frm);
            }, 'Actions');

            frm.add_custom_button('View Outstanding Balance Report', function() {
                frappe.set_route('query-report', 'Delivery App Outstanding Balance', {
                    delivery_app: frm.doc.name
                });
            }, 'Actions');
        }
    }
});

function _show_import_dialog(frm) {
    let d = new frappe.ui.Dialog({
        title: `Import Orders from CSV — ${frm.doc.app_name}`,
        fields: [
            {
                label: 'CSV File',
                fieldname: 'csv_file',
                fieldtype: 'Attach',
                reqd: 1,
                description: 'Upload a CSV with columns: order_id, order_date, gross_amount, notes (optional)'
            },
            {
                label: 'Income / Revenue Account',
                fieldname: 'income_account',
                fieldtype: 'Link',
                options: 'Account',
                reqd: 1,
                description: 'The revenue account to credit for these orders (e.g. "Sales - CompanyAbbr")'
            },
            {
                label: 'Cost Center',
                fieldname: 'cost_center',
                fieldtype: 'Link',
                options: 'Cost Center',
                description: 'Optional'
            }
        ],
        primary_action_label: 'Import',
        primary_action: function(values) {
            frappe.show_progress('Importing Orders...', 0, 100, 'Please wait');
            frappe.call({
                method: 'delivery_app_reconciliation.delivery_app_reconciliation.doctype.delivery_app_settings.delivery_app_settings.import_orders_from_csv',
                args: {
                    file_url: values.csv_file,
                    delivery_app: frm.doc.name,
                    income_account: values.income_account,
                    cost_center: values.cost_center || null
                },
                callback: function(r) {
                    frappe.hide_progress();
                    if (r.message) {
                        let res = r.message;
                        let indicator = res.failed > 0 ? 'orange' : 'green';
                        let msg = `
                            <b>Import Complete</b><br><br>
                            ✅ Created: <b>${res.created}</b> Sales Invoices<br>
                            ⏭️ Skipped (already exist): <b>${res.skipped_duplicates}</b><br>
                            ❌ Failed: <b>${res.failed}</b>
                        `;
                        if (res.errors && res.errors.length) {
                            msg += `<br><br><b>Errors:</b><br><small>${res.errors.slice(0, 10).join('<br>')}</small>`;
                            if (res.errors.length > 10) {
                                msg += `<br><small>...and ${res.errors.length - 10} more. Check Error Log for full details.</small>`;
                            }
                        }
                        frappe.msgprint({
                            message: msg,
                            title: 'Import Result',
                            indicator: indicator
                        });
                    }
                }
            });
            d.hide();
        }
    });
    d.show();
}
