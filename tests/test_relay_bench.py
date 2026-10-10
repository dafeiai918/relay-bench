# -*- coding: utf-8 -*-
"""离线冒烟测试：用本地 mock 服务模拟一个 OpenAI 兼容站点"""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import relay_bench as rb  # noqa: E402

ANSWERS = {
    "9.11": "9.9",
    "strawberry": "3",
    "星期三": "星期六",
    "苹果": "6",
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # 只在 /v1 前缀下提供服务，模拟真实部署
        if self.path == "/v1/models":
            self._send(200, {"data": [{"id": "mock-model"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length).decode("utf-8"))
        prompt = req["messages"][-1]["content"]
        if req.get("response_format"):
            content = '{"ok": true}'
        elif prompt.startswith("从1数到100"):
            content = " ".join(str(i) for i in range(1, 101))
        elif "BBB" in (req.get("stop") or []):
            content = "AAA "
        else:
            content = next((a for k, a in ANSWERS.items() if k in prompt), "47")

        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in (content[:1], content[1:]):
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}]})


class RelayBenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_mask_key(self):
        self.assertEqual(rb.mask_key("sk-1234567890abcd"), "sk-123****abcd")
        self.assertEqual(rb.mask_key(""), "(未提供)")

    def test_normalize_accepts_bare_host(self):
        p = rb.Probe(f"http://127.0.0.1:{self.port}", "sk-test")
        self.assertTrue(p.normalize())
        self.assertTrue(p.base.endswith("/v1"))
        self.assertEqual(p.models, ["mock-model"])

    def test_full_run_scores_high(self):
        rc = rb.main([
            "-u", f"http://127.0.0.1:{self.port}", "-k", "sk-test",
            "-c", "3", "--skip", "",
        ])
        self.assertEqual(rc, 0)

    def test_block_scores_are_bounded(self):
        p = rb.Probe(f"http://127.0.0.1:{self.port}/v1", "sk-test")
        p.normalize()
        blocks = [
            rb.probe_connectivity(p),
            rb.probe_stream(p, "mock-model"),
            rb.probe_concurrency(p, "mock-model", 2),
            rb.probe_params(p, "mock-model"),
            rb.probe_ability(p, "mock-model"),
        ]
        for b in blocks:
            self.assertGreaterEqual(b["score"], 0)
            self.assertLessEqual(b["score"], b["max"])

    def test_grade_thresholds(self):
        self.assertEqual(rb.grade(95)[0], "A")
        self.assertEqual(rb.grade(80)[0], "B")
        self.assertEqual(rb.grade(65)[0], "C")
        self.assertEqual(rb.grade(45)[0], "D")
        self.assertEqual(rb.grade(10)[0], "E")


if __name__ == "__main__":
    unittest.main(verbosity=2)
