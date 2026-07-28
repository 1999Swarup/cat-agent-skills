#!/usr/bin/env python3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from audit_core import DEFAULT_RULES, Rules, audit_text, read_csv_units


class AuditCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = Rules(DEFAULT_RULES)

    def test_region_specific_findings(self):
        result = audit_text(
            "Our eco-friendly product is carbon neutral through carbon offsets.",
            "conversation",
            "test",
            self.rules,
            ["CA", "EU", "UK"],
            date(2026, 9, 27),
            True,
        )
        codes = {finding["finding_code"] for finding in result["findings"]}
        self.assertIn("ANY-VAGUE", codes)
        self.assertIn("CA-MISLEADING", codes)
        self.assertIn("EU-GENERIC", codes)
        self.assertIn("EU-OFFSET-PRODUCT", codes)
        self.assertIn("UK-CLARITY", codes)

    def test_eu_rule_is_date_sensitive(self):
        result = audit_text(
            "An eco-friendly product.",
            "conversation",
            "test",
            self.rules,
            ["EU"],
            date(2026, 9, 26),
            True,
        )
        codes = {finding["finding_code"] for finding in result["findings"]}
        self.assertNotIn("EU-GENERIC", codes)

    def test_csv_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claims.csv"
            path.write_text("Title,Body\nOne,Eco-friendly packaging\n", encoding="utf-8")
            units = list(read_csv_units(path, ["Body"], None))
        self.assertEqual(units, [("row 2, column Body", "Eco-friendly packaging")])


if __name__ == "__main__":
    unittest.main()
