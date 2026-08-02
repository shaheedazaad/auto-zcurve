from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from auto_zcurve.projects import (
    AnalysisResetRequired,
    ProjectError,
    UploadTooLarge,
    create_project,
    get_project,
    list_projects,
    safe_upload_name,
    save_upload,
    update_project_instructions,
    update_project_schema,
)


class ManagedProjectTests(unittest.TestCase):
    def test_create_reopen_and_list_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_project("  Unicode 研究 project  ", root=root)

            self.assertTrue((created.path / "sources").is_dir())
            self.assertTrue((created.path / "extraction_schema.yml").is_file())
            self.assertTrue((created.path / "extraction_instructions.md").is_file())
            self.assertEqual(get_project(created.project_id, root=root).name, "Unicode 研究 project")
            self.assertEqual([item.project_id for item in list_projects(root=root)], [created.project_id])

    def test_duplicate_names_are_preserved_with_unique_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project("Duplicates", root=Path(tmp))
            first = save_upload(project, "paper.pdf", io.BytesIO(b"%PDF-one"))
            second = save_upload(project, "paper.pdf", io.BytesIO(b"%PDF-two"))

            self.assertEqual(first.name, "paper.pdf")
            self.assertEqual(second.name, "paper (2).pdf")
            self.assertEqual(second.read_bytes(), b"%PDF-two")

    def test_upload_rejects_unsafe_non_pdf_and_oversized_files(self):
        self.assertEqual(safe_upload_name("../../study.pdf"), "study.pdf")
        with self.assertRaises(ProjectError):
            safe_upload_name("../../secret.txt")
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project("Limits", root=Path(tmp))
            with self.assertRaises(UploadTooLarge):
                save_upload(project, "large.pdf", io.BytesIO(b"%PDF" + b"x" * 20), max_bytes=10)
            self.assertEqual(list((project.path / "sources").iterdir()), [])

    def test_invalid_pdf_signature_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project("Validation", root=Path(tmp))
            with self.assertRaises(ProjectError):
                save_upload(project, "fake.pdf", io.BytesIO(b"not really a PDF"))
            self.assertEqual(list((project.path / "sources").iterdir()), [])

    def test_instruction_change_requires_confirmation_and_resets_only_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project("Instructions", root=Path(tmp))
            source = project.path / "sources" / "study.pdf"
            source.write_bytes(b"%PDF-fixture")
            output = project.path / "output"
            (output / "extractions.json").write_text("[]\n", encoding="utf-8")
            (output / "report.html").write_text("<h1>Old</h1>", encoding="utf-8")

            with self.assertRaises(AnalysisResetRequired):
                update_project_instructions(project, "# New instructions")

            self.assertTrue((output / "report.html").exists())
            result = update_project_instructions(
                project,
                "# New instructions",
                confirm_reset=True,
            )
            self.assertEqual(result, {"changed": True, "reset": True})
            self.assertEqual(
                (project.path / "extraction_instructions.md").read_text(encoding="utf-8"),
                "# New instructions\n",
            )
            self.assertTrue(source.exists())
            self.assertTrue((output / "raw").is_dir())
            self.assertFalse((output / "report.html").exists())
            self.assertFalse((output / "extractions.json").exists())

    def test_schema_change_validates_before_confirmation_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project("Schema", root=Path(tmp))
            source = project.path / "sources" / "study.pdf"
            source.write_bytes(b"%PDF-fixture")
            schema_path = project.path / "extraction_schema.yml"
            original_schema = schema_path.read_text(encoding="utf-8")
            output = project.path / "output"
            (output / "extractions.json").write_text("[]\n", encoding="utf-8")
            (output / "report.html").write_text("<h1>Old</h1>", encoding="utf-8")

            with self.assertRaisesRegex(ProjectError, "Invalid YAML"):
                update_project_schema(project, "effects:\n  result: [\n", confirm_reset=True)
            self.assertEqual(schema_path.read_text(encoding="utf-8"), original_schema)
            self.assertTrue((output / "report.html").exists())

            revised_schema = (
                "name: revised\n"
                "effects:\n"
                "  result:\n"
                "    type: string\n"
                "    role: reported_statistic\n"
                "    description: The focal result.\n"
            )
            with self.assertRaises(AnalysisResetRequired):
                update_project_schema(project, revised_schema)
            self.assertTrue((output / "report.html").exists())

            result = update_project_schema(project, revised_schema, confirm_reset=True)
            self.assertEqual(result, {"changed": True, "reset": True})
            self.assertEqual(schema_path.read_text(encoding="utf-8"), revised_schema)
            self.assertTrue(source.exists())
            self.assertTrue((output / "raw").is_dir())
            self.assertFalse((output / "report.html").exists())
            self.assertFalse((output / "extractions.json").exists())


if __name__ == "__main__":
    unittest.main()
