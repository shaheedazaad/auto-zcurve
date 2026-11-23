from __future__ import annotations

import io
import tempfile
import unicodedata
import unittest
from pathlib import Path

from auto_zcurve.projects import (
    AnalysisResetRequired,
    ProjectError,
    UploadTooLarge,
    create_project,
    file_size_display,
    get_project,
    list_projects,
    read_project_instructions,
    read_project_schema,
    safe_upload_name,
    DuplicateUpload,
    save_upload,
    save_uploads,
    update_project_instructions,
    update_project_schema,
)


class ManagedProjectTests(unittest.TestCase):
    def test_legacy_project_reopens_without_rewriting_inputs_or_results(self):
        from auto_zcurve.schema import read_extraction_schema, validate_extracted_json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = create_project("Legacy extraction", root=root)
            schema_text = "effects:\n  reported_statistic:\n    type: string\n"
            (project.path / "extraction_schema.yml").write_text(schema_text)
            instructions = "Extract primary outcomes only.\n"
            (project.path / "extraction_instructions.md").write_text(instructions)
            result_path = project.path / "output" / "raw" / "saved.json"
            result_path.write_text('{"effects": [{"reported_statistic": "z=2.41"}]}')
            reopened = get_project(project.project_id, root=root)
            self.assertEqual(read_project_schema(reopened), schema_text)
            self.assertEqual(read_project_instructions(reopened), instructions)
            self.assertTrue(result_path.is_file())
            schema = read_extraction_schema(reopened.path / "extraction_schema.yml")
            validate_extracted_json({"effects": [{"reported_statistic": "z=2.41"}]}, schema)

    def test_legacy_effect_definition_is_migrated_into_project_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project("Legacy instructions", root=Path(tmp))
            instruction_path = project.path / "extraction_instructions.md"
            instruction_path.write_text("## Effects of interest\n\n{{effect_definition}}\n", encoding="utf-8")
            settings_path = project.path / ".auto_zcurve" / "run_settings.json"
            settings_path.parent.mkdir(exist_ok=True)
            settings_path.write_text(
                '{"primary_model":"legacy-model","effect_definition":"Extract primary outcomes only."}\n',
                encoding="utf-8",
            )

            self.assertEqual(
                read_project_instructions(project),
                "## Effects of interest\n\nExtract primary outcomes only.\n",
            )
            self.assertNotIn("effect_definition", settings_path.read_text(encoding="utf-8"))

    def test_legacy_instruction_without_saved_value_uses_historical_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project("Legacy default", root=Path(tmp))
            instruction_path = project.path / "extraction_instructions.md"
            instruction_path.write_text("{{effect_definition}}\n", encoding="utf-8")

            self.assertEqual(
                read_project_instructions(project),
                "Include only tests that support the claims in the article's title and/or abstract.\n",
            )

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

    def test_uploads_with_content_already_in_the_project_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project("Duplicates", root=Path(tmp))
            save_upload(project, "paper.pdf", io.BytesIO(b"%PDF-one"))

            result = save_uploads(
                project,
                [
                    ("renamed copy.pdf", io.BytesIO(b"%PDF-one")),
                    ("new.pdf", io.BytesIO(b"%PDF-new")),
                    ("new again.pdf", io.BytesIO(b"%PDF-new")),
                ],
            )

            self.assertEqual(result.saved, ["new.pdf"])
            self.assertEqual(
                result.skipped,
                [
                    {"name": "renamed copy.pdf", "existing": "paper.pdf"},
                    {"name": "new again.pdf", "existing": "new.pdf"},
                ],
            )
            self.assertEqual(sorted(p.name for p in (project.path / "sources").iterdir()), ["new.pdf", "paper.pdf"])
            with self.assertRaises(DuplicateUpload):
                save_upload(project, "paper.pdf", io.BytesIO(b"%PDF-one"))
            self.assertEqual(sorted(p.name for p in (project.path / "sources").iterdir()), ["new.pdf", "paper.pdf"])

    def test_file_size_display_picks_a_readable_unit(self):
        self.assertEqual(file_size_display(512), "512 B")
        self.assertEqual(file_size_display(250 * 1024), "250 KB")
        self.assertEqual(file_size_display(3 * 1024 * 1024 + 300_000), "3.3 MB")

    def test_upload_rejects_unsafe_non_pdf_and_oversized_files(self):
        self.assertEqual(safe_upload_name("../../study.pdf"), "study.pdf")
        with self.assertRaises(ProjectError):
            safe_upload_name("../../secret.txt")
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project("Limits", root=Path(tmp))
            with self.assertRaises(UploadTooLarge):
                save_upload(project, "large.pdf", io.BytesIO(b"%PDF" + b"x" * 20), max_bytes=10)
            self.assertEqual(list((project.path / "sources").iterdir()), [])

    def test_upload_names_keep_unicode_letters_in_nfc(self):
        decomposed = "Cafe\u0301 e\u0301tude.pdf"
        self.assertEqual(safe_upload_name("Müller 2020.pdf"), "Müller 2020.pdf")
        self.assertEqual(safe_upload_name("Перевод 研究 دراسة.pdf"), "Перевод 研究 دراسة.pdf")
        self.assertEqual(safe_upload_name(decomposed), "Café étude.pdf")
        self.assertEqual(safe_upload_name("Smith, J. (2020) & O'Neil [draft] #1.pdf"),
                         "Smith, J. (2020) & O'Neil [draft] #1.pdf")

    def test_upload_names_replace_windows_forbidden_and_control_characters(self):
        self.assertEqual(safe_upload_name('a<b>c:d"e|f?g*h.pdf'), "a_b_c_d_e_f_g_h.pdf")
        self.assertEqual(safe_upload_name("tab\there\x00\x7f\x85.pdf"), "tab_here_.pdf")
        self.assertEqual(safe_upload_name("lone\udc80surrogate.pdf"), "lone_surrogate.pdf")

    def test_upload_names_make_windows_reserved_device_names_safe(self):
        for name in ["CON.pdf", "nul.pdf", "Com1.pdf", "LPT9.pdf", "PRN.draft.pdf",
                     "aux .pdf", "COM¹.pdf"]:
            with self.subTest(name=name):
                self.assertEqual(safe_upload_name(name), "_" + name)
        for name in ["CONSOLE.pdf", "COM0.pdf", "LPT10.pdf", "nullable.pdf"]:
            with self.subTest(name=name):
                self.assertEqual(safe_upload_name(name), name)

    def test_long_multibyte_upload_name_is_shortened_by_bytes_and_saved(self):
        name = safe_upload_name("研究" * 100 + ".pdf")
        self.assertTrue(name.endswith(".pdf"))
        self.assertLessEqual(len(name.encode("utf-8")), 164)
        self.assertEqual(name, "研究" * 26 + "研.pdf")
        cluster = "e\u0301\u0302"
        combining = safe_upload_name("a" + cluster * 100 + ".pdf")
        self.assertEqual(combining, "a" + unicodedata.normalize("NFC", cluster) * 39 + ".pdf")
        marks = safe_upload_name("e" + "\u0301" * 100 + ".pdf")
        self.assertTrue(marks.endswith(".pdf"))
        self.assertLessEqual(len(marks.encode("utf-8")), 164)
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project("Long names", root=Path(tmp))
            first = save_upload(project, "研究" * 100 + ".pdf", io.BytesIO(b"%PDF-one"))
            second = save_upload(project, "研究" * 100 + ".pdf", io.BytesIO(b"%PDF-two"))
            self.assertEqual(first.name, name)
            self.assertEqual(second.name, "研究" * 26 + "研 (2).pdf")
            self.assertEqual(second.read_bytes(), b"%PDF-two")
            self.assertEqual(
                sorted(path.name for path in (project.path / "sources").iterdir()),
                sorted([first.name, second.name]),
            )

    def test_long_upload_name_keeps_marks_with_zero_combining_class_on_their_base(self):
        # Devanagari vowel sign I (Mc) and Thai sara i (Mn) both have combining class 0.
        cases = {
            "devanagari": ("कि", "कि" * 26),
            "thai": ("กิ", "กิ" * 26),
            "thai stacked marks": ("กิ่", "กิ่" * 17),
        }
        for label, (cluster, expected_stem) in cases.items():
            with self.subTest(label):
                name = safe_upload_name(cluster * 100 + ".pdf")
                self.assertEqual(name, expected_stem + ".pdf")
                self.assertLessEqual(len(Path(name).stem.encode("utf-8")), 160)

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
