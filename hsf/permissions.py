import frappe


# Facility item groups HSF users are limited to (their tree descendants are
# included automatically via lft/rgt). Lookups are case-insensitive.
HSF_ITEM_GROUPS = [
	"FACILITY ITEMS",
	"FACILITY ITEMS Consumables",
	"Facility Temp",
]

# Roles that are NEVER facility-restricted (always see every item).
BYPASS_ROLES = {"System Manager"}


def _get_hsf_item_groups():
	"""Return the facility item groups including their tree descendants."""
	all_groups = set()
	for grp in HSF_ITEM_GROUPS:
		node = frappe.db.get_value("Item Group", grp, ["name", "lft", "rgt"], as_dict=True)
		if node:
			all_groups.update(
				frappe.db.sql(
					"SELECT name FROM `tabItem Group` WHERE lft >= %s AND rgt <= %s",
					(node.lft, node.rgt),
					pluck="name",
				)
			)
		else:
			all_groups.add(grp)
	return all_groups


def _is_facility_restricted(user):
	"""HSF users are restricted to facility items, except true admins.

	The restriction is strict: it applies even to HSF users who also hold the
	Stock User role, so the item filter cannot be bypassed or removed.
	"""
	if user == "Administrator":
		return False
	roles = set(frappe.get_roles(user))
	if roles & BYPASS_ROLES:
		return False
	return "HSF User" in roles


def item_query_conditions(user=None):
	"""HSF Users can only see items in facility-related item groups."""
	user = user or frappe.session.user
	if not _is_facility_restricted(user):
		return ""

	groups = _get_hsf_item_groups()
	if not groups:
		return ""
	escaped = ", ".join("'" + g.replace("'", "''") + "'" for g in groups)
	return f"`tabItem`.`item_group` IN ({escaped})"


def item_has_permission(doc, ptype=None, user=None):
	"""Block opening / using an item outside the facility item groups."""
	user = user or frappe.session.user
	if not _is_facility_restricted(user):
		return True
	return doc.get("item_group") in _get_hsf_item_groups()


def has_app_permission(user=None):
	"""Only show HSF app to users with HSF User role."""
	if not user:
		user = frappe.session.user

	return "HSF User" in frappe.get_roles(user)
