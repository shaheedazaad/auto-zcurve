from types import SimpleNamespace
import unittest

from auto_zcurve.gemini import _response_text, _response_usage


class Part:
    def __init__(self, text=None, thought_signature=None):
        self.text = text
        self.thought_signature = thought_signature


class Content:
    def __init__(self, parts):
        self.parts = parts


class Candidate:
    def __init__(self, parts):
        self.content = Content(parts)


class Response:
    def __init__(self, candidates):
        self.candidates = candidates

    @property
    def text(self):
        raise AssertionError("response.text accessor should not be used")


class GeminiResponseTests(unittest.TestCase):
    def test_response_text_ignores_non_text_parts(self):
        response = Response(
            [
                Candidate(
                    [
                        Part(thought_signature=b"opaque"),
                        Part(text='{"effects": []}'),
                    ]
                )
            ]
        )

        self.assertEqual(_response_text(response), '{"effects": []}')

    def test_response_usage_reads_token_metadata(self):
        response = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=100,
                candidates_token_count=20,
                total_token_count=120,
            )
        )

        self.assertEqual(
            _response_usage(response),
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
        )


if __name__ == "__main__":
    unittest.main()
