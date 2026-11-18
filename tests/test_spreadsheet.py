import csv
import io
import unittest

from auto_zcurve.spreadsheet import escape_cell, spreadsheet_safe_csv

BOM = b"\xef\xbb\xbf"
MAX_TEST_READ_LIMIT = 1024 * 1024


def read_rows(payload: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline="")))


class EscapeCellTests(unittest.TestCase):
    def test_formula_like_cells_get_a_leading_apostrophe(self):
        for value in ["=SUM(A1:A2)", "+cmd", "-cmd|' /C calc'!A0", "@SUM(1)", "\tvalue", "\rvalue", "\nvalue", "-", "+", "=", "@"]:
            with self.subTest(value=value):
                self.assertEqual(escape_cell(value), "'" + value)

    def test_formula_like_cells_after_leading_whitespace_are_escaped(self):
        for value in ["  =1+1", " +1+", "   @x", " \t=1", "\t ", " \r", " \n=1", "\n "]:
            with self.subTest(value=value):
                self.assertEqual(escape_cell(value), "'" + value)

    def test_line_feed_prefixed_hyperlink_is_escaped(self):
        # Regression for #15: a line feed before a formula must not bypass escaping.
        for value in ['\n=HYPERLINK("http://x","click")', '  \n=HYPERLINK("http://x","click")']:
            with self.subTest(value=value):
                self.assertEqual(escape_cell(value), "'" + value)

    def test_plain_signed_numbers_stay_numeric(self):
        for value in ["-1.25", "+1.25", "-1.25e-3", "-1.25E+03", "-.5", "-5.", "-0", "+7", " -1.25 ", "-1e5"]:
            with self.subTest(value=value):
                self.assertEqual(escape_cell(value), value)

    def test_ordinary_cells_are_unchanged(self):
        for value in ["", "1.25", "t(24) = 2.31", "study.pdf", "p < .05", " text", "1-2", "a=b"]:
            with self.subTest(value=value):
                self.assertEqual(escape_cell(value), value)

    def test_number_like_cells_with_formula_suffixes_are_escaped(self):
        for value in ["-1.25+A1", "-1e", "--1", "-1.2.3", "-inf", "+1,000"]:
            with self.subTest(value=value):
                self.assertEqual(escape_cell(value), "'" + value)


class SpreadsheetSafeCsvTests(unittest.TestCase):
    def test_output_has_bom_crlf_and_escaped_cells_including_headers(self):
        source = '=header,value\nstudy.pdf,-1.25\n"  @quote","=HYPERLINK(""x"")"\n'
        payload = spreadsheet_safe_csv(source)
        self.assertTrue(payload.startswith(BOM))
        self.assertFalse(payload[len(BOM):].startswith(BOM))
        body = payload[len(BOM):]
        self.assertEqual(body.count(b"\r\n"), 3)
        self.assertNotIn(b"\n", body.replace(b"\r\n", b""))
        self.assertTrue(body.endswith(b"\r\n"))
        self.assertEqual(
            read_rows(payload),
            [
                ["'=header", "value"],
                ["study.pdf", "-1.25"],
                ["'  @quote", "'=HYPERLINK(\"x\")"],
            ],
        )

    def test_quoted_cell_starting_with_line_feed_is_escaped(self):
        payload = spreadsheet_safe_csv('quote\n"\n=HYPERLINK(""http://x"")"\n')
        self.assertEqual(read_rows(payload), [["quote"], ["'\n=HYPERLINK(\"http://x\")"]])

    def test_unicode_text_and_embedded_newlines_survive(self):
        source = 'quote,beta\n"Line one\nline two — résumé",β\n'
        payload = spreadsheet_safe_csv(source)
        self.assertEqual(read_rows(payload), [["quote", "beta"], ["Line one\nline two — résumé", "β"]])
        self.assertIn("résumé".encode("utf-8"), payload)

    def test_existing_bom_and_crlf_input_are_normalised(self):
        payload = spreadsheet_safe_csv("﻿a,b\r\n1,-2\r\n")
        self.assertEqual(payload, BOM + b"a,b\r\n1,-2\r\n")

    def test_empty_input_yields_only_a_bom(self):
        self.assertEqual(spreadsheet_safe_csv(""), BOM)

    def test_very_large_fields_are_supported(self):
        large = "x" * (200 * 1024)
        previous_limit = csv.field_size_limit()
        payload = spreadsheet_safe_csv(f"quote\n{large}\n")
        self.assertEqual(csv.field_size_limit(), previous_limit)
        previous_reader_limit = csv.field_size_limit(MAX_TEST_READ_LIMIT)
        try:
            self.assertEqual(read_rows(payload), [["quote"], [large]])
        finally:
            csv.field_size_limit(previous_reader_limit)


if __name__ == "__main__":
    unittest.main()
