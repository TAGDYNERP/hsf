"""Warehouse-friendly Material Request actions built on ERPNext's stock flow."""

import json
import math

import frappe
from erpnext.stock.doctype.material_request.material_request import make_stock_entry
from erpnext.stock.get_item_details import get_bin_details
from frappe import _
from frappe.utils import flt

BLOCKED_STATUSES = {"Stopped", "Cancelled"}


def _get_material_request(name: str, *, for_update: bool = False):
	doc = frappe.get_doc("Material Request", name, for_update=for_update)
	doc.check_permission("read")

	if doc.docstatus != 1 or doc.material_request_type != "Material Transfer":
		frappe.throw(_("Quick Transfer is only available for submitted Material Transfer requests."))

	if doc.status in BLOCKED_STATUSES:
		frappe.throw(_("Material Request {0} is {1}.").format(doc.name, doc.status))

	return doc


def _check_stock_entry_permissions():
	frappe.has_permission("Stock Entry", ptype="create", throw=True)
	frappe.has_permission("Stock Entry", ptype="submit", throw=True)


def _item_flags(item_code: str):
	return (
		frappe.get_cached_value(
			"Item",
			item_code,
			["has_serial_no", "has_batch_no", "is_stock_item"],
			as_dict=True,
		)
		or frappe._dict()
	)


def _availability(item_code: str, warehouse: str | None) -> float:
	if not warehouse:
		return 0

	return flt(get_bin_details(item_code, warehouse).get("actual_qty"))


def _row_status(item, flags, available_stock_qty: float) -> str:
	if not item.from_warehouse:
		return _("Source warehouse is required")
	if not item.warehouse:
		return _("Target warehouse is required")
	if not flags.is_stock_item:
		return _("Not a stock item")
	if flags.has_serial_no or flags.has_batch_no:
		return _("Requires serial/batch selection in the standard Stock Entry flow")
	if available_stock_qty <= 0:
		return _("Not Available")
	return _("Available")


def _get_outstanding_rows(material_request):
	rows = []
	for item in material_request.items:
		conversion_factor = flt(item.conversion_factor) or 1
		outstanding_stock_qty = max(flt(item.stock_qty) - flt(item.ordered_qty), 0)
		if not outstanding_stock_qty:
			continue

		flags = _item_flags(item.item_code)
		available_stock_qty = _availability(item.item_code, item.from_warehouse)
		remaining_qty = outstanding_stock_qty / conversion_factor
		available_qty = available_stock_qty / conversion_factor
		status = _row_status(item, flags, available_stock_qty)
		can_quick_transfer = status == _("Available")
		transfer_qty = min(remaining_qty, max(available_qty, 0)) if can_quick_transfer else 0

		rows.append(
			frappe._dict(
				{
					"material_request_item": item.name,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"requested_qty": flt(item.qty),
					"transferred_qty": flt(item.ordered_qty) / conversion_factor,
					"remaining_qty": remaining_qty,
					"source_warehouse": item.from_warehouse,
					"available_qty": available_qty,
					"transfer_qty": transfer_qty,
					"target_warehouse": item.warehouse,
					"uom": item.uom,
					"stock_uom": item.stock_uom,
					"conversion_factor": conversion_factor,
					"status": status,
					"can_quick_transfer": can_quick_transfer,
				}
			)
		)

	return rows


@frappe.whitelist()
def get_quick_transfer_items(material_request: str):
	"""Return outstanding rows and current source-warehouse stock."""
	_check_stock_entry_permissions()
	doc = _get_material_request(material_request)
	return {"items": _get_outstanding_rows(doc)}


def _parse_selected_items(selected_items) -> dict[str, float]:
	if isinstance(selected_items, str):
		selected_items = json.loads(selected_items)

	if not isinstance(selected_items, list):
		frappe.throw(_("Selected items must be a list."))

	selected = {}
	for row in selected_items:
		if not isinstance(row, dict):
			frappe.throw(_("Each selected item must be an object."))

		item_name = row.get("material_request_item")
		qty = flt(row.get("transfer_qty"))
		if not item_name or not math.isfinite(qty) or qty <= 0:
			frappe.throw(_("Every selected row must have a positive Transfer Qty."))
		if item_name in selected:
			frappe.throw(_("Material Request Item {0} was selected more than once.").format(item_name))
		selected[item_name] = qty

	if not selected:
		frappe.throw(_("Select at least one item to transfer."))

	return selected


def _validate_selection(material_request, selected: dict[str, float]):
	items_by_name = {item.name: item for item in material_request.items}
	unknown_items = set(selected) - set(items_by_name)
	if unknown_items:
		frappe.throw(_("One or more selected items do not belong to this Material Request."))

	for item_name, qty in selected.items():
		item = items_by_name[item_name]
		conversion_factor = flt(item.conversion_factor) or 1
		requested_stock_qty = qty * conversion_factor
		outstanding_stock_qty = max(flt(item.stock_qty) - flt(item.ordered_qty), 0)

		if requested_stock_qty > outstanding_stock_qty + 1e-9:
			frappe.throw(
				_("Transfer Qty for {0} exceeds the outstanding quantity of {1} {2}.").format(
					item.item_code, outstanding_stock_qty / conversion_factor, item.uom
				)
			)

		flags = _item_flags(item.item_code)
		available_stock_qty = _availability(item.item_code, item.from_warehouse)
		status = _row_status(item, flags, available_stock_qty)
		if status != _("Available"):
			frappe.throw(_("{0}: {1}").format(item.item_code, status))

		if requested_stock_qty > available_stock_qty + 1e-9:
			frappe.throw(
				_("Stock availability changed for {0}. Available: {1} {2}; requested: {3} {2}.").format(
					item.item_code,
					available_stock_qty / conversion_factor,
					item.uom,
					qty,
				)
			)


def _apply_selection(stock_entry, selected: dict[str, float]):
	stock_entry.set("items", [row for row in stock_entry.items if row.material_request_item in selected])
	if len(stock_entry.items) != len(selected):
		frappe.throw(_("One or more selected items are no longer outstanding. Refresh and try again."))

	for row in stock_entry.items:
		row.qty = selected[row.material_request_item]

	stock_entry.set_transfer_qty()


@frappe.whitelist()
def execute_quick_transfer(material_request: str, selected_items):
	"""Map, filter, submit, and return a standard ERPNext Stock Entry."""
	_check_stock_entry_permissions()
	selected = _parse_selected_items(selected_items)

	# Lock the MR and its child rows until this request commits. This serializes
	# concurrent quick transfers and makes the mapper see the latest completed qty.
	doc = _get_material_request(material_request, for_update=True)
	_validate_selection(doc, selected)

	stock_entry = make_stock_entry(doc.name)
	_apply_selection(stock_entry, selected)
	stock_entry.insert()
	stock_entry.submit()

	return {"stock_entry": stock_entry.name}
