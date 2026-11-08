"""Run: python3 -m unittest discover tests"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "freecad", "acp_chat"))
from mcp_server import handle, serve  # noqa: E402


def fake_run(code):
    return (f"ran {code}", code != "boom")


class McpServerTest(unittest.TestCase):
    def test_handle(self):
        init = handle({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}, fake_run)
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertIsNone(handle({"method": "notifications/initialized"}, fake_run))
        tools = handle({"id": 2, "method": "tools/list"}, fake_run)["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["execute_python"])
        call = lambda code: handle(
            {"id": 3, "method": "tools/call", "params": {"name": "execute_python", "arguments": {"code": code}}},
            fake_run,
        )["result"]
        self.assertEqual(call("x=1"), {"content": [{"type": "text", "text": "ran x=1"}], "isError": False})
        self.assertTrue(call("boom")["isError"])
        self.assertEqual(handle({"id": 4, "method": "nope"}, fake_run)["error"]["code"], -32601)

    def test_http_requires_token(self):
        url, token = serve(fake_run)
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()

        def post(auth):
            req = urllib.request.Request(url, data=body, headers={"Authorization": auth, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())

        with self.assertRaises(urllib.error.HTTPError) as e:
            post("Bearer wrong")
        self.assertEqual(e.exception.code, 401)
        self.assertEqual(post(f"Bearer {token}")["result"]["tools"][0]["name"], "execute_python")


if __name__ == "__main__":
    unittest.main()
