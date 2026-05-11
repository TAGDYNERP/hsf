import frappe


# All item groups that HSF users should have access to,
# including their descendant groups via the tree (lft/rgt).
HSF_ITEM_GROUPS = [
	"facility items",
	"facility items 1mables",
	"FACILITY ITEMS Consumables",
	"Facility Temp",
]


def _get_hsf_item_groups():
	"""Return all HSF item groups including tree descendants."""
	all_groups = set()
	for grp in HSF_ITEM_GROUPS:
		node = frappe.db.get_value("Item Group", grp, ["lft", "rgt"], as_dict=True)
		if node:
			descendants = frappe.db.sql(
				"SELECT name FROM `tabItem Group` WHERE lft >= %s AND rgt <= %s",
				(node.lft, node.rgt),
				pluck="name",
			)
			all_groups.update(descendants)
		else:
			all_groups.add(grp)
	return all_groups


def item_query_conditions(user):
	"""HSF Users can only see items in facility-related item groups."""
	if not user:
		user = frappe.session.user

	if "Administrator" in frappe.get_roles(user):
		return ""

	if "HSF User" in frappe.get_roles(user) and "Stock User" not in frappe.get_roles(user):
		groups = _get_hsf_item_groups()
		escaped = ", ".join(f"'{g.replace(chr(39), chr(39)+chr(39))}'" for g in groups)
		return f"`tabItem`.`item_group` IN ({escaped})"

	return ""


def item_has_permission(doc, ptype=None, user=None):
	"""Check if HSF User has permission to access this item."""
	if not user:
		user = frappe.session.user

	if "Administrator" in frappe.get_roles(user):
		return True

	if "HSF User" in frappe.get_roles(user) and "Stock User" not in frappe.get_roles(user):
		return doc.item_group in _get_hsf_item_groups()

	return True


def has_app_permission(user=None):
	"""Only show HSF app to users with HSF User role."""
	if not user:
		user = frappe.session.user

	return "HSF User" in frappe.get_roles(user)
