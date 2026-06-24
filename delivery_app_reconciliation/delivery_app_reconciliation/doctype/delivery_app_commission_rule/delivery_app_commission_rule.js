frappe.ui.form.on('Delivery App Commission Rule', {
    fee_type: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];

        if (row.fee_type === 'Percentage') {
            frappe.model.set_value(cdt, cdn, 'fixed_amount', 0);
        } else if (row.fee_type === 'Fixed') {
            frappe.model.set_value(cdt, cdn, 'rate_percent', 0);
        }
    }
});
