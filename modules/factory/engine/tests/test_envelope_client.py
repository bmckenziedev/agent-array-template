"""Envelope acceptance is independent of any estate or model fixture."""
import unittest

from factory_engine.envelope import parse_chat, parse_completion


class EnvelopeTest(unittest.TestCase):
    def test_completion_success_normalizes_newline(self):
        for stop in ("marker", "eos"):
            with self.subTest(stop=stop):
                parsed = parse_completion('{"summary":"synthetic range"}', stop)
                self.assertTrue(parsed.ok)
                self.assertEqual(parsed.code, '{"summary":"synthetic range"}\n')

    def test_completion_rejects_ambiguous_or_truncated_output(self):
        cases = [("partial", "length", "truncated"), ("payload", "unknown", "unknown"),
                 ("", "marker", "empty"), ("```json\n{}\n```", "marker", "fence"),
                 ("prefix <<<CODE suffix", "marker", "second"),
                 ("{} CODE>>>", "marker", "own line")]
        for text, stop, reason in cases:
            with self.subTest(stop=stop, reason=reason):
                parsed = parse_completion(text, stop)
                self.assertFalse(parsed.ok)
                self.assertIn(reason.split()[0], parsed.reason.lower())
        self.assertEqual(parse_completion("", "error").stage, "infra")

    def test_chat_accepts_valid_opening_and_optional_stop_marker(self):
        for text in ("<<<CODE\n{}\nCODE>>>", "  <<<CODE\r\n{}\r\nCODE>>>\n", "<<<CODE\n{}"):
            with self.subTest(text=text):
                self.assertTrue(parse_chat(text, "stop").ok)

    def test_chat_rejects_wrong_envelope_or_finish_reason(self):
        for text, reason in [("{}", "stop"), ("<<<CODE {}\nCODE>>>", "stop"),
                             ("<<<CODE\n{}", None), ("<<<CODE\n{}\nCODE>>>\nextra", "stop"),
                             ("<<<CODE\n{}\nCODE>>>", "length")]:
            with self.subTest(text=text, reason=reason):
                self.assertFalse(parse_chat(text, reason).ok)


if __name__ == "__main__":
    unittest.main()
