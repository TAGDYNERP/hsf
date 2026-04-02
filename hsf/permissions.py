import frappe


def item_query_conditions(user):
	"""HSF Users can only see items in the 'Facility Items' item group."""
	if not user:
		user = frappe.session.user

	if "Administrator" in frappe.get_roles(user):
		return ""

	if "HSF User" in frappe.get_roles(user) and "Stock User" not in frappe.get_roles(user):
		return "`tabItem`.`item_group` = 'Facility Items'"

	return ""


def item_has_permission(doc, ptype=None, user=None):
	"""Check if HSF User has permission to access this item."""
	if not user:
		user = frappe.session.user

	if "Administrator" in frappe.get_roles(user):
		return True

	if "HSF User" in frappe.get_roles(user) and "Stock User" not in frappe.get_roles(user):
		return doc.item_group == "Facilities"

	return True
