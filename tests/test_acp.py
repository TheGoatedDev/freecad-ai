"""Run: python3 -m unittest discover tests"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "freecad", "acp_chat"))
from acp import AcpClient  # noqa: E402


def line(msg):
    return (json.dumps(msg) + "\n").encode()


class AcpClientTest(unittest.TestCase):
    def setUp(self):
        self.sent, self.updates, self.perms, self.errors = [], [], [], []
        self.c = AcpClient(
            lambda b: self.sent.append(json.loads(b)),
            self.updates.append,
            lambda p, reply: self.perms.append((p, reply)),
            self.errors.append,
        )

    def test_handshake_prompt_and_updates(self):
        ready, done = [], []
        self.c.start("/tmp", [{"type": "http", "name": "freecad", "url": "u", "headers": []}], ready.append)
        self.assertEqual(self.sent[0]["method"], "initialize")
        self.c.feed(line({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1, "agentCapabilities": {"promptCapabilities": {"image": True}}}}))
        self.assertEqual(self.sent[1]["method"], "session/new")
        self.assertEqual(self.sent[1]["params"]["mcpServers"][0]["name"], "freecad")
        # message split across chunks must still parse
        raw = line({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}})
        self.c.feed(raw[:10])
        self.c.feed(raw[10:])
        self.assertEqual(ready, ["s1"])
        self.assertTrue(self.c.supports_images())

        image = {"type": "image", "mimeType": "image/png", "data": "AAAA"}
        self.c.prompt([{"type": "text", "text": "make a cube"}, image], done.append)
        self.assertEqual(self.sent[2]["params"]["prompt"][0]["text"], "make a cube")
        self.assertEqual(self.sent[2]["params"]["prompt"][1], image)
        upd = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi"}}
        self.c.feed(line({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s1", "update": upd}}))
        self.c.feed(line({"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}))
        self.assertEqual(self.updates, [upd])
        self.assertEqual(done, ["end_turn"])

    def test_permission_reply_and_unknown_method(self):
        self.c.feed(line({"jsonrpc": "2.0", "id": 7, "method": "session/request_permission",
                          "params": {"options": [{"optionId": "a", "name": "Allow", "kind": "allow_once"}]}}))
        self.perms[0][1]("a")
        self.assertEqual(self.sent[-1], {"jsonrpc": "2.0", "id": 7,
                                         "result": {"outcome": {"outcome": "selected", "optionId": "a"}}})
        self.perms[0][1](None)
        self.assertEqual(self.sent[-1]["result"]["outcome"], {"outcome": "cancelled"})
        self.c.feed(line({"jsonrpc": "2.0", "id": 8, "method": "fs/read_text_file", "params": {}}))
        self.assertEqual(self.sent[-1]["error"]["code"], -32601)

    def test_config_options(self):
        mode = {"id": "mode", "category": "mode", "type": "select", "currentValue": "auto", "options": []}
        self.c.session_id = "s1"
        self.c.request("session/new", {}, lambda r: None)  # id 1 placeholder
        self.c.feed(line({"jsonrpc": "2.0", "id": 1, "result": {}}))
        self.c.set_config("mode", "plan")
        self.assertEqual(self.sent[-1]["method"], "session/set_config_option")
        self.assertEqual(self.sent[-1]["params"], {"sessionId": "s1", "configId": "mode", "value": "plan"})
        self.c.feed(line({"jsonrpc": "2.0", "id": 2, "result": {"configOptions": [dict(mode, currentValue="plan")]}}))
        self.assertEqual(self.c.config_options[0]["currentValue"], "plan")
        self.assertEqual(self.updates[-1]["sessionUpdate"], "config_option_update")
        # legacy mode notification is mirrored into the mode option
        self.c.feed(line({"jsonrpc": "2.0", "method": "session/update",
                          "params": {"sessionId": "s1", "update": {"sessionUpdate": "current_mode_update", "currentModeId": "default"}}}))
        self.assertEqual(self.c.config_options[0]["currentValue"], "default")

    def test_auth_required_then_retry(self):
        ready, asked = [], []
        self.c.start("/tmp", [], ready.append, lambda methods, auth: asked.append((methods, auth)))
        self.c.feed(line({"jsonrpc": "2.0", "id": 1, "result": {"authMethods": [{"id": "chat-gpt", "name": "ChatGPT"}]}}))
        self.c.feed(line({"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "Authentication required"}}))
        self.assertEqual(asked[0][0][0]["id"], "chat-gpt")
        self.assertEqual(self.errors, [])
        asked[0][1]("chat-gpt")
        self.assertEqual((self.sent[-1]["method"], self.sent[-1]["params"]), ("authenticate", {"methodId": "chat-gpt"}))
        self.c.feed(line({"jsonrpc": "2.0", "id": 3, "result": {}}))
        self.assertEqual(self.sent[-1]["method"], "session/new")
        self.c.feed(line({"jsonrpc": "2.0", "id": 4, "result": {"sessionId": "s9"}}))
        self.assertEqual(ready, ["s9"])

    def test_error_response(self):
        self.c.request("initialize", {}, lambda r: self.fail("should not succeed"))
        self.c.feed(line({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "auth required"}}))
        self.assertIn("auth required", self.errors[0])


if __name__ == "__main__":
    unittest.main()
