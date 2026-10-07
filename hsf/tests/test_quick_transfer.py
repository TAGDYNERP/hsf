from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, call, patch

import frappe

from hsf.api import material_request as api


def item(
	name,
	item_code,
	qty=10,
	stock_qty=10,
	ordered_qty=0,
	conversion_factor=1,
	from_warehouse="Main - T",
	warehouse="HSF - T",
):
	return frappe._dict(
		name=name,
		item_code=item_code,
		item_name=f"{item_code} Name",
		qty=qty,
		stock_qty=stock_qty,
		ordered_qty=ordered_qty,
		conversion_factor=conversion_factor,
		from_warehouse=from_warehouse,
		warehouse=warehouse,
		uom="Nos",
		stock_uom="Nos",
	)


def material_request(items, status="Pending", docstatus=1):
	return SimpleNamespace(
		name="MR-TEST",
		items=items,
		status=status,
		docstatus=docstatus,
		material_request_type="Material Transfer",
	)


class TestOutstandingRows(TestCase):
	def flags(self, serial=False, batch=False, stock=True):
		return frappe._dict(has_serial_no=serial, has_batch_no=batch, is_stock_item=stock)

	@patch.object(api, "_item_flags")
	@patch.object(api, "_availability")
	def test_all_items_available(self, availability, flags):
		availability.side_effect = [25, 11]
		flags.return_value = self.flags()
		rows = api._get_outstanding_rows(material_request([item("ROW-A", "ITEM-A"), item("ROW-B", "ITEM-B")]))

		self.assertEqual([row.transfer_qty for row in rows], [10, 10])
		self.assertTrue(all(row.status == "Available" for row in rows))

	@patch.object(api, "_item_flags")
	@patch.object(api, "_availability")
	def test_one_and_multiple_unavailable_items(self, availability, flags):
		availability.side_effect = [8, 0, 0]
		flags.return_value = self.flags()
		rows = api._get_outstanding_rows(
			material_request([item("ROW-A", "ITEM-A"), item("ROW-B", "ITEM-B"), item("ROW-C", "ITEM-C")])
		)

		self.assertEqual([row.transfer_qty for row in rows], [8, 0, 0])
		self.assertEqual([row.status for row in rows], ["Available", "Not Available", "Not Available"])

	@patch.object(api, "_item_flags")
	@patch.object(api, "_availability")
	def test_partial_stock_and_uom_conversion(self, availability, flags):
		availability.return_value = 12  # stock UOM; 2 stock units per Box
		flags.return_value = self.flags()
		rows = api._get_outstanding_rows(
			material_request([item("ROW-A", "ITEM-A", qty=10, stock_qty=20, conversion_factor=2)])
		)

		self.assertEqual(rows[0].available_qty, 6)
		self.assertEqual(rows[0].remaining_qty, 10)
		self.assertEqual(rows[0].transfer_qty, 6)

	@patch.object(api, "_item_flags")
	@patch.object(api, "_availability")
	def test_partially_and_completely_transferred_requests(self, availability, flags):
		availability.return_value = 50
		flags.return_value = self.flags()
		rows = api._get_outstanding_rows(
			material_request(
				[
					item("PARTIAL", "ITEM-A", ordered_qty=4),
					item("COMPLETE", "ITEM-B", ordered_qty=10),
				]
			)
		)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].material_request_item, "PARTIAL")
		self.assertEqual(rows[0].transferred_qty, 4)
		self.assertEqual(rows[0].remaining_qty, 6)

	@patch.object(api, "_item_flags")
	@patch.object(api, "_availability")
	def test_different_source_warehouses(self, availability, flags):
		availability.side_effect = [3, 7]
		flags.return_value = self.flags()
		doc = material_request(
			[
				item("ROW-A", "ITEM-A", from_warehouse="Main A - T"),
				item("ROW-B", "ITEM-B", from_warehouse="Main B - T"),
			]
		)
		rows = api._get_outstanding_rows(doc)

		self.assertEqual([row.transfer_qty for row in rows], [3, 7])
		self.assertEqual(
			availability.call_args_list,
			[call("ITEM-A", "Main A - T"), call("ITEM-B", "Main B - T")],
		)

	@patch.object(api, "_item_flags")
	@patch.object(api, "_availability")
	def test_serial_or_batch_item_requires_standard_flow(self, availability, flags):
		availability.return_value = 10
		flags.return_value = self.flags(serial=True)
		row = api._get_outstanding_rows(material_request([item("ROW-A", "SERIAL-A")]))[0]

		self.assertFalse(row.can_quick_transfer)
		self.assertEqual(row.transfer_qty, 0)
		self.assertIn("serial/batch", row.status)


class TestSelectionAndExecution(TestCase):
	def test_manual_exclusion_filters_mapped_stock_entry(self):
		stock_entry = SimpleNamespace(
			items=[
				frappe._dict(material_request_item="ROW-A", qty=10),
				frappe._dict(material_request_item="ROW-B", qty=10),
			]
		)
		stock_entry.set = lambda field, value: setattr(stock_entry, field, value)
		stock_entry.set_transfer_qty = Mock()

		api._apply_selection(stock_entry, {"ROW-A": 4})

		self.assertEqual(len(stock_entry.items), 1)
		self.assertEqual(stock_entry.items[0].material_request_item, "ROW-A")
		self.assertEqual(stock_entry.items[0].qty, 4)
		stock_entry.set_transfer_qty.assert_called_once()

	@patch.object(api, "_item_flags")
	@patch.object(api, "_availability")
	def test_stock_change_is_rejected_server_side(self, availability, flags):
		availability.return_value = 3
		flags.return_value = frappe._dict(has_serial_no=0, has_batch_no=0, is_stock_item=1)
		doc = material_request([item("ROW-A", "ITEM-A")])

		with self.assertRaises(frappe.ValidationError):
			api._validate_selection(doc, {"ROW-A": 4})

	@patch.object(api.frappe, "has_permission")
	def test_stock_entry_create_and_submit_permissions_are_required(self, has_permission):
		api._check_stock_entry_permissions()
		self.assertEqual(
			has_permission.call_args_list,
			[
				call("Stock Entry", ptype="create", throw=True),
				call("Stock Entry", ptype="submit", throw=True),
			],
		)

	@patch.object(api.frappe, "has_permission", side_effect=frappe.PermissionError)
	def test_user_without_stock_entry_permission_is_rejected(self, has_permission):
		with self.assertRaises(frappe.PermissionError):
			api._check_stock_entry_permissions()

		has_permission.assert_called_once_with("Stock Entry", ptype="create", throw=True)

	@patch.object(api, "make_stock_entry")
	@patch.object(api, "_validate_selection")
	@patch.object(api, "_get_material_request")
	@patch.object(api, "_check_stock_entry_permissions")
	def test_execute_uses_lock_standard_mapper_insert_and_submit(
		self, check_permissions, get_mr, validate, mapper
	):
		doc = material_request([item("ROW-A", "ITEM-A")])
		get_mr.return_value = doc
		stock_entry = SimpleNamespace(
			name="MAT-STE-0001",
			items=[frappe._dict(material_request_item="ROW-A", qty=10)],
			insert=Mock(),
			submit=Mock(),
			set_transfer_qty=Mock(),
		)
		stock_entry.set = lambda field, value: setattr(stock_entry, field, value)
		mapper.return_value = stock_entry

		result = api.execute_quick_transfer(
			"MR-TEST", [{"material_request_item": "ROW-A", "transfer_qty": 6, "expected_ordered_qty": 0}]
		)

		get_mr.assert_called_once_with("MR-TEST", for_update=True)
		mapper.assert_called_once_with("MR-TEST")
		self.assertEqual(stock_entry.items[0].qty, 6)
		stock_entry.insert.assert_called_once()
		stock_entry.submit.assert_called_once()
		self.assertEqual(result, {"stock_entry": "MAT-STE-0001"})

	@patch.object(api.frappe, "get_doc")
	def test_stopped_and_cancelled_requests_are_rejected(self, get_doc):
		for status in ("Stopped", "Cancelled"):
			doc = material_request([], status=status)
			doc.check_permission = Mock()
			get_doc.return_value = doc
			with self.assertRaises(frappe.ValidationError):
				api._get_material_request("MR-TEST")

	def test_duplicate_selection_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			api._parse_selected_items(
				[
					{"material_request_item": "ROW-A", "transfer_qty": 1},
					{"material_request_item": "ROW-A", "transfer_qty": 1},
				]
			)

	def test_non_finite_quantity_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			api._parse_selected_items([{"material_request_item": "ROW-A", "transfer_qty": float("nan")}])

	def test_repeated_partial_transfer_is_rejected(self):
		doc = material_request([item("ROW-A", "ITEM-A", ordered_qty=4)])
		with self.assertRaises(frappe.ValidationError):
			api._validate_snapshot(doc, [{"material_request_item": "ROW-A", "expected_ordered_qty": 0}])

	@patch.object(api, "_item_flags")
	@patch.object(api, "_availability", return_value=6)
	def test_shared_source_stock_is_not_allocated_twice(self, availability, flags):
		flags.return_value = frappe._dict(has_serial_no=0, has_batch_no=0, is_stock_item=1)
		doc = material_request([item("ROW-A", "ITEM-A", qty=4, stock_qty=4), item("ROW-B", "ITEM-A")])
		rows = api._get_outstanding_rows(doc)
		self.assertEqual([row.transfer_qty for row in rows], [4, 2])
		with self.assertRaises(frappe.ValidationError):
			api._validate_selection(doc, {"ROW-A": 4, "ROW-B": 4})


class TestQuickTransferTransactions(TestCase):
	"""Exercise real ERPNext documents, rolling back all test records afterward."""

	def setUp(self):
		self.original_user = frappe.session.user
		frappe.set_user("Administrator")
		frappe.db.savepoint("hsf_quick_transfer_test")
		self.addCleanup(self.cleanup_records)
		self.company = frappe.db.get_value("Company", {}, "name")
		if not self.company:
			self.skipTest("A configured company is required for stock transaction tests")
		self.prefix = "HSF-QT-" + frappe.generate_hash(length=10)
		self.source = (
			frappe.get_doc(
				{"doctype": "Warehouse", "warehouse_name": self.prefix + " Source", "company": self.company}
			)
			.insert()
			.name
		)
		self.target = (
			frappe.get_doc(
				{"doctype": "Warehouse", "warehouse_name": self.prefix + " Target", "company": self.company}
			)
			.insert()
			.name
		)
		self.item_code = (
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": self.prefix,
					"item_name": self.prefix,
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 1,
				}
			)
			.insert()
			.name
		)
		receipt = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"company": self.company,
				"stock_entry_type": "Material Receipt",
				"items": [
					{"item_code": self.item_code, "qty": 20, "t_warehouse": self.source, "basic_rate": 10}
				],
			}
		).insert()
		receipt.submit()

	def cleanup_records(self):
		frappe.db.rollback(save_point="hsf_quick_transfer_test")
		frappe.set_user(self.original_user)

	def make_request(self):
		return (
			frappe.get_doc(
				{
					"doctype": "Material Request",
					"company": self.company,
					"material_request_type": "Material Transfer",
					"schedule_date": frappe.utils.today(),
					"items": [
						{
							"item_code": self.item_code,
							"qty": 10,
							"from_warehouse": self.source,
							"warehouse": self.target,
						}
					],
				}
			)
			.insert()
			.submit()
		)

	def test_partial_transfer_retry_and_standard_completion(self):
		mr = self.make_request()
		row = api.get_quick_transfer_items(mr.name)["items"][0]
		selection = [
			{"material_request_item": row.material_request_item, "transfer_qty": 4, "expected_ordered_qty": 0}
		]
		result = api.execute_quick_transfer(mr.name, selection)
		entry = frappe.get_doc("Stock Entry", result["stock_entry"])
		self.assertEqual(entry.docstatus, 1)
		self.assertEqual(entry.items[0].material_request, mr.name)
		self.assertEqual(entry.items[0].material_request_item, mr.items[0].name)
		self.assertEqual(entry.items[0].s_warehouse, self.source)
		self.assertEqual(entry.items[0].t_warehouse, self.target)
		mr.reload()
		self.assertEqual(mr.items[0].ordered_qty, 4)
		self.assertEqual(len(mr.items), 1)
		with self.assertRaises(frappe.ValidationError):
			api.execute_quick_transfer(mr.name, selection)
		standard_entry = api.make_stock_entry(mr.name)
		self.assertEqual(standard_entry.items[0].qty, 6)
		standard_entry.insert().submit()
		mr.reload()
		self.assertEqual(mr.items[0].ordered_qty, 10)
		self.assertEqual(mr.per_ordered, 100)
		self.assertEqual(api.get_quick_transfer_items(mr.name)["items"], [])

	def test_standard_stop_is_respected(self):
		from erpnext.stock.doctype.material_request.material_request import update_status

		mr = self.make_request()
		update_status(mr.name, "Stopped")
		mr.reload()
		self.assertEqual(mr.status, "Stopped")
		with self.assertRaises(frappe.ValidationError):
			api.get_quick_transfer_items(mr.name)
