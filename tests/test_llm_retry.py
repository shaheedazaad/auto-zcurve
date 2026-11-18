from __future__ import annotations

import ast
import unittest
from pathlib import Path

from auto_zcurve import gemini, llm, openrouter


class SharedRetryHelperTests(unittest.TestCase):
    def test_retryable_statuses(self):
        for code in (408, 409, 425, 429, 500, 503, "502"):
            self.assertTrue(llm.retryable_status(code), code)
        for code in (400, 401, 403, 404, 422, "bad", None):
            self.assertFalse(llm.retryable_status(code), code)

    def test_retry_delay_backs_off_and_honours_capped_retry_after(self):
        class Error(Exception):
            def __init__(self, retry_after_sec):
                self.retry_after_sec = retry_after_sec

        self.assertEqual([llm.retry_delay(n) for n in (1, 2, 3)], [1.0, 2.0, 4.0])
        self.assertEqual(llm.retry_delay(1, Error(60)), 30.0)
        self.assertEqual(llm.retry_delay(1, Error(0.5)), 0.5)
        self.assertEqual(llm.retry_delay(2, Error(-1)), 2.0)
        self.assertEqual(llm.retry_delay(2, Error(None)), 2.0)

    def test_attempt_limit_is_shared(self):
        self.assertEqual(llm.MAX_PROVIDER_ATTEMPTS, 3)
        self.assertEqual(openrouter.MAX_COMPLETION_ATTEMPTS, llm.MAX_PROVIDER_ATTEMPTS)
        self.assertEqual(gemini.MAX_REQUEST_ATTEMPTS, llm.MAX_PROVIDER_ATTEMPTS)

    def test_gemini_does_not_import_openrouter(self):
        tree = ast.parse(Path(gemini.__file__).read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("openrouter", imported)
        self.assertIs(gemini.retry_delay, llm.retry_delay)
        self.assertIs(gemini.retryable_status, llm.retryable_status)


if __name__ == "__main__":
    unittest.main()
