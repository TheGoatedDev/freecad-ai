"""Run: python3 -m unittest discover tests"""

import json
import unittest
import urllib.error
import urllib.request
from typing import Any

from freecad.acp_chat.mcp_server import handle, serve


def respond(msg: dict[str, Any]) -> dict[str, Any]:
    resp = handle(msg, fake_run)
    assert resp is not None
    return resp


def fake_run(code: str) -> tuple[str, bool]:
    return (f"ran {code}", code != "boom")


class McpServerTest(unittest.TestCase):
    def test_handle(self) -> None:
        init = respond({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertIsNone(handle({"method": "notifications/initialized"}, fake_run))
        tools = respond({"id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["execute_python"])

        def call(code: str) -> Any:
            params = {"name": "execute_python", "arguments": {"code": code}}
            return respond({"id": 3, "method": "tools/call", "params": params})["result"]

        self.assertEqual(call("x=1"), {"content": [{"type": "text", "text": "ran x=1"}], "isError": False})
        self.assertTrue(call("boom")["isError"])
        self.assertEqual(respond({"id": 4, "method": "nope"})["error"]["code"], -32601)

    def test_http_requires_token(self) -> None:
        url, token = serve(fake_run)
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()

        def post(auth: str) -> Any:
            req = urllib.request.Request(
                url, data=body, headers={"Authorization": auth, "Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())

        with self.assertRaises(urllib.error.HTTPError) as e:
            post("Bearer wrong")
        self.assertEqual(e.exception.code, 401)
        self.assertEqual(post(f"Bearer {token}")["result"]["tools"][0]["name"], "execute_python")


if __name__ == "__main__":
    unittest.main()
