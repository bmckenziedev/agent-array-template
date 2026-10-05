"""Escalation obtains a bounded account list only through the pace contract."""

import json
import unittest
from unittest.mock import MagicMock, patch

from factory_engine.scheduler import route_escalation


class PaceRouteTests(unittest.TestCase):
    def response(self, value):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(value).encode()
        return response

    def test_only_pace_accounts_are_returned(self):
        accounts = [{"id": "api-alpha", "vendor": "example", "headroom_pct": 50}]
        with patch("urllib.request.urlopen", return_value=self.response({"accounts": accounts})) as call:
            result = route_escalation("http://pace.example", "alpha", "doc_map", "internal", "fake-token")
        self.assertEqual(result, accounts)
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, "http://pace.example/v1/route")
        self.assertEqual(json.loads(request.data), {
            "team": "alpha", "task_class": "doc_map", "data_class": "internal"})

    def test_restricted_never_routes(self):
        with patch("urllib.request.urlopen") as call:
            self.assertEqual(route_escalation("http://pace.example", "alpha", "doc_map", "restricted"), [])
        call.assert_not_called()

    def test_interactive_seat_is_refused(self):
        with patch("urllib.request.urlopen", return_value=self.response({"accounts": [{"type": "seat"}]})):
            with self.assertRaises(ValueError):
                route_escalation("http://pace.example", "alpha", "doc_map", "internal")


if __name__ == "__main__":
    unittest.main()
