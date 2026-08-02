from pathlib import Path
import tempfile
import unittest

from auto_zcurve.schema import (
    build_response_schema,
    parse_extraction_schema,
    read_extraction_schema,
    validate_extracted_json,
)


class SchemaTests(unittest.TestCase):
    def test_schema_builds_response_schema_and_validates_json(self):
        schema = read_extraction_schema(Path("config/extraction_schema.yml"))
        response_schema = build_response_schema(schema)

        self.assertEqual(response_schema["type"], "object")
        self.assertIn("effects", response_schema["properties"])

        validate_extracted_json(
            {
                "meta_data": {
                    "doi": "10.123/example",
                    "study_description": "A test study",
                },
                "effects": [
                    {
                        "claim": "Claim",
                        "claim_quote": "Quote",
                        "test_description": "Test",
                        "test_description_quote": "Quote",
                        "reported_statistic": "t(38)=2.14",
                        "reported_statistic_quote": "Quote",
                        "significant": True,
                        "one_sided": False,
                        "pre_registration": False,
                        "power_analysis": False,
                        "sanity_check": "ok",
                        "sample_id": "1",
                        "notes": "",
                    }
                ],
            },
            schema,
        )

    def test_invalid_schema_rejects_unknown_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.yml"
            path.write_text("effects:\n  bad:\n    type: object\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_extraction_schema(path)

    def test_schema_text_reports_yaml_location_and_invalid_required_value(self):
        with self.assertRaisesRegex(ValueError, r"Invalid YAML at line 3, column"):
            parse_extraction_schema("effects:\n  result: [\n")

        with self.assertRaisesRegex(ValueError, "required must be true or false"):
            parse_extraction_schema(
                "effects:\n  result:\n    type: string\n    required: sometimes\n"
            )

    def test_schema_rejects_duplicate_roles(self):
        with self.assertRaisesRegex(ValueError, "repeats role `reported_statistic`"):
            parse_extraction_schema(
                "effects:\n"
                "  first:\n    type: string\n    role: reported_statistic\n"
                "  second:\n    type: string\n    role: reported_statistic\n"
            )


if __name__ == "__main__":
    unittest.main()
