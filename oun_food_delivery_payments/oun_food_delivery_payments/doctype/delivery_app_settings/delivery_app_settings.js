// Copyright (c) 2024, Developer and contributors
// License: MIT

frappe.ui.form.on('Delivery App Settings', {
    setup: function(frm) {
        frm.set_query('receivable_account', function() {
            return {
                filters: {
                    company: frm.doc.company,
                    root_type: 'Asset',
                    account_type: 'Receivable',
                    is_group: 0
                }
            };
        });

        frm.set_query('delivery_service_item', function() {
            return {
                filters: {
                    is_stock_item: 0,
                    disabled: 0
                }
            };
        });
    },

    company: function(frm) {
        frm.set_value('receivable_account', null);
    },

    refresh: function(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button('Import Orders from CSV', function() {
                if (!frm.doc.company) {
                    frappe.msgprint({
                        message: 'Please select a Company before importing orders.',
                        title: 'Missing Company',
                        indicator: 'orange'
                    });
                    return;
                }
                if (!frm.doc.delivery_service_item) {
                    frappe.msgprint({
                        message: 'Please select a Delivery Service Item before importing orders.',
                        title: 'Missing Delivery Service Item',
                        indicator: 'orange'
                    });
                    return;
                }
                _show_import_dialog(frm);
            }, 'Actions');

            frm.add_custom_button('Check Last CSV Import', function() {
                _check_import_status(frm);
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
                label: 'Cost Center',
                fieldname: 'cost_center',
                fieldtype: 'Link',
                options: 'Cost Center',
                get_query: function() {
                    return {
                        filters: {
                            company: frm.doc.company,
                            is_group: 0
                        }
                    };
                },
                description: 'Optional'
            }
        ],
        primary_action_label: 'Start Background Import',
        primary_action: function(values) {
            d.hide();
            frappe.show_alert({
                message: 'Starting CSV import in the background...',
                indicator: 'blue'
            });
            frappe.call({
                method: 'oun_food_delivery_payments.oun_food_delivery_payments.doctype.delivery_app_settings.delivery_app_settings.import_orders_from_csv',
                args: {
                    file_url: values.csv_file,
                    delivery_app: frm.doc.name,
                    cost_center: values.cost_center || null
                },
                callback: function(r) {
                    if (r.message) {
                        frappe.msgprint({
                            message: `
                                <b>CSV import started in the background.</b><br><br>
                                Job ID: <code>${frappe.utils.escape_html(r.message.job_id)}</code><br>
                                You can keep working while invoices are created.
                            `,
                            title: 'Background Import Started',
                            indicator: 'blue'
                        });
                        _poll_import_status(frm, r.message.job_id);
                    }
                }
            });
        }
    });
    d.show();
}

function _check_import_status(frm) {
    frappe.call({
        method: 'oun_food_delivery_payments.oun_food_delivery_payments.doctype.delivery_app_settings.delivery_app_settings.get_csv_import_status',
        args: {
            delivery_app: frm.doc.name
        },
        callback: function(r) {
            if (r.message) {
                _show_import_status(r.message);
            }
        }
    });
}

function _poll_import_status(frm, job_id) {
    let attempts = 0;
    let max_attempts = 720;

    let poll = function() {
        attempts += 1;
        frappe.call({
            method: 'oun_food_delivery_payments.oun_food_delivery_payments.doctype.delivery_app_settings.delivery_app_settings.get_csv_import_status',
            args: {
                delivery_app: frm.doc.name,
                job_id: job_id
            },
            callback: function(r) {
                if (!r.message || !r.message.is_current_job) {
                    return;
                }

                let status = r.message.last_import_status;
                if (['Completed', 'Completed With Errors', 'Failed'].includes(status)) {
                    frm.reload_doc();
                    _show_import_status(r.message);
                    return;
                }

                if (attempts < max_attempts) {
                    setTimeout(poll, 5000);
                }
            }
        });
    };

    setTimeout(poll, 5000);
}

function _show_import_status(status) {
    let indicator = 'blue';
    if (status.last_import_status === 'Completed') {
        indicator = 'green';
    } else if (status.last_import_status === 'Completed With Errors') {
        indicator = 'orange';
    } else if (status.last_import_status === 'Failed') {
        indicator = 'red';
    }

    let error_log = status.last_import_error_log || '';
    let errors = error_log.split('\n').filter(Boolean);
    let msg = `
        <b>Status:</b> ${frappe.utils.escape_html(status.last_import_status || 'Not Started')}<br><br>
        Created Sales Invoices: <b>${status.last_import_created || 0}</b><br>
        Skipped Duplicates: <b>${status.last_import_skipped || 0}</b><br>
        Failed Rows: <b>${status.last_import_failed || 0}</b>
    `;

    if (status.last_import_job_id) {
        msg += `<br><br>Job ID: <code>${frappe.utils.escape_html(status.last_import_job_id)}</code>`;
    }

    if (errors.length) {
        msg += `<br><br><b>Errors:</b><br><small>${errors.slice(0, 10).map(frappe.utils.escape_html).join('<br>')}</small>`;
        if (errors.length > 10) {
            msg += `<br><small>...and ${errors.length - 10} more. Open Delivery App Settings to see the stored log.</small>`;
        }
    }

    frappe.msgprint({
        message: msg,
        title: 'CSV Import Status',
        indicator: indicator
    });
}
