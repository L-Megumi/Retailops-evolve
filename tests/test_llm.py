from __future__ import annotations

import unittest

from retailops_agent.llm import _parse_json_object


class JsonParsingTests(unittest.TestCase):
    def test_plain_json_object(self):
        self.assertEqual(_parse_json_object('{"ok": true}'), {"ok": True})

    def test_markdown_wrapped_json_object(self):
        self.assertEqual(
            _parse_json_object('```json\n{"ok": true}\n```'),
            {"ok": True},
        )

    def test_json_object_after_short_preamble(self):
        self.assertEqual(
            _parse_json_object('结果如下：\n{"ok": true}'),
            {"ok": True},
        )


if __name__ == "__main__":
    unittest.main()
