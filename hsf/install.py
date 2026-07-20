import json

import frappe

# Item groups the HSF workspace's "Item" shortcut is filtered to, so the shortcut
# (and New Item from it) only surfaces facility items.
FACILITY_ITEM_GROUPS = ["FACILITY ITEMS", "FACILITY ITEMS Consumables", "Facility Temp"]


def after_migrate():
	"""Filter the HSF workspace's Item shortcut to the facility item groups.

	Existing workspaces are not re-synced from the app JSON on migrate, so we set
	the shortcut's stats_filter here idempotently.
	"""
	if not frappe.db.exists("Workspace", "HSF"):
		return

	wanted = json.dumps([["Item", "item_group", "in", FACILITY_ITEM_GROUPS]])
	ws = frappe.get_doc("Workspace", "HSF")
	changed = False
	for shortcut in ws.shortcuts:
		if shortcut.link_to == "Item" and shortcut.label == "Item":
			try:
				same = json.loads(shortcut.stats_filter or "null") == json.loads(wanted)
			except Exception:
				same = False
			if not same:
				shortcut.stats_filter = wanted
				changed = True

	if changed:
		ws.save(ignore_permissions=True)
		frappe.clear_cache()
