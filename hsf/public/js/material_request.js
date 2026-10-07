frappe.ui.form.on("Material Request", {
	refresh(frm) {
		if (hsf_quick_transfer.is_eligible(frm)) {
			frm.add_custom_button(
				__("Quick Transfer"),
				() => hsf_quick_transfer.open_dialog(frm),
				__("Warehouse")
			);
		}

		if (hsf_quick_transfer.can_close(frm)) {
			frm.add_custom_button(
				__("Close MR"),
				() => hsf_quick_transfer.close_material_request(frm),
				__("Warehouse")
			);
		}
	},
});

const hsf_quick_transfer = {
	can_close(frm) {
		return (
			frm.doc.docstatus === 1 &&
			frm.doc.material_request_type === "Material Transfer" &&
			!["Stopped", "Cancelled"].includes(frm.doc.status) &&
			frm.has_perm("write")
		);
	},

	is_eligible(frm) {
		return (
			frm.doc.docstatus === 1 &&
			frm.doc.material_request_type === "Material Transfer" &&
			!["Stopped", "Cancelled"].includes(frm.doc.status) &&
			flt(frm.doc.per_ordered) < 100 &&
			frappe.model.can_create("Stock Entry") &&
			frappe.perm.has_perm("Stock Entry", 0, "submit")
		);
	},

	async open_dialog(frm) {
		const response = await frappe.call({
			method: "hsf.api.material_request.get_quick_transfer_items",
			args: { material_request: frm.doc.name },
			freeze: true,
			freeze_message: __("Checking stock availability..."),
		});
		const items = response.message?.items || [];
		if (!items.length) {
			frappe.msgprint(__("There are no outstanding items to transfer."));
			return;
		}

		const dialog = new frappe.ui.Dialog({
			title: __("Quick Transfer - {0}", [frm.doc.name]),
			size: "extra-large",
			fields: [
				{
					fieldname: "items",
					fieldtype: "Table",
					label: __("Outstanding Items"),
					cannot_add_rows: true,
					in_place_edit: true,
					data: items.map((row) => ({
						...row,
						include: row.can_quick_transfer ? 1 : 0,
					})),
					fields: [
						{
							fieldname: "include",
							fieldtype: "Check",
							label: __("Include"),
							in_list_view: 1,
							columns: 1,
						},
						{
							fieldname: "item_code",
							fieldtype: "Link",
							options: "Item",
							label: __("Item Code"),
							read_only: 1,
							in_list_view: 1,
							columns: 2,
						},
						{
							fieldname: "item_name",
							fieldtype: "Data",
							label: __("Item Name"),
							read_only: 1,
						},
						{
							fieldname: "requested_qty",
							fieldtype: "Float",
							label: __("Requested Qty"),
							read_only: 1,
						},
						{
							fieldname: "transferred_qty",
							fieldtype: "Float",
							label: __("Already Transferred"),
							read_only: 1,
						},
						{
							fieldname: "remaining_qty",
							fieldtype: "Float",
							label: __("Remaining Qty"),
							read_only: 1,
							in_list_view: 1,
							columns: 1,
						},
						{
							fieldname: "source_warehouse",
							fieldtype: "Link",
							options: "Warehouse",
							label: __("Source Warehouse"),
							read_only: 1,
						},
						{
							fieldname: "available_qty",
							fieldtype: "Float",
							label: __("Available Qty"),
							read_only: 1,
							in_list_view: 1,
							columns: 1,
						},
						{
							fieldname: "transfer_qty",
							fieldtype: "Float",
							label: __("Transfer Qty"),
							non_negative: 1,
							in_list_view: 1,
							columns: 1,
						},
						{
							fieldname: "target_warehouse",
							fieldtype: "Link",
							options: "Warehouse",
							label: __("Target Warehouse"),
							read_only: 1,
						},
						{
							fieldname: "uom",
							fieldtype: "Link",
							options: "UOM",
							label: __("UOM"),
							read_only: 1,
							in_list_view: 1,
							columns: 1,
						},
						{
							fieldname: "status",
							fieldtype: "Data",
							label: __("Status"),
							read_only: 1,
							in_list_view: 1,
							columns: 2,
						},
						{ fieldname: "material_request_item", fieldtype: "Data", hidden: 1 },
						{ fieldname: "can_quick_transfer", fieldtype: "Check", hidden: 1 },
					],
				},
			],
			primary_action_label: __("Transfer"),
			primary_action: async (values) => {
				const selected_items = (values.items || [])
					.filter((row) => row.include && flt(row.transfer_qty) > 0)
					.map((row) => ({
						material_request_item: row.material_request_item,
						transfer_qty: flt(row.transfer_qty),
					}));

				if (!selected_items.length) {
					frappe.msgprint(
						__("Select at least one available item with a positive Transfer Qty.")
					);
					return;
				}

				dialog.disable_primary_action();
				try {
					const transfer = await frappe.call({
						method: "hsf.api.material_request.execute_quick_transfer",
						args: { material_request: frm.doc.name, selected_items },
						freeze: true,
						freeze_message: __("Submitting Stock Entry..."),
					});
					const stock_entry = transfer.message.stock_entry;
					dialog.hide();
					await frm.reload_doc();
					frappe.msgprint({
						title: __("Transfer completed successfully."),
						indicator: "green",
						message: __("Stock Entry: {0}", [
							`<a href="/app/stock-entry/${encodeURIComponent(
								stock_entry
							)}">${frappe.utils.escape_html(stock_entry)}</a>`,
						]),
					});
				} finally {
					dialog.enable_primary_action();
				}
			},
			secondary_action_label: __("Transfer All Available"),
			secondary_action: () => {
				const grid = dialog.fields_dict.items.grid;
				grid.data.forEach((row) => {
					row.include = row.can_quick_transfer ? 1 : 0;
					row.transfer_qty = row.can_quick_transfer
						? Math.min(flt(row.remaining_qty), Math.max(flt(row.available_qty), 0))
						: 0;
				});
				grid.refresh();
			},
		});
		dialog.show();
	},

	close_material_request(frm) {
		frappe.confirm(__("Close Material Request {0}?", [frm.doc.name]), () => {
			frappe.call({
				method: "erpnext.stock.doctype.material_request.material_request.update_status",
				args: { name: frm.doc.name, status: "Stopped" },
				freeze: true,
				callback: (response) => {
					if (!response.exc) {
						frm.reload_doc();
					}
				},
			});
		});
	},
};
