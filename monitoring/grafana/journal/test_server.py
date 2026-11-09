import importlib.util
import json
import subprocess
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

spec = importlib.util.spec_from_file_location("journal", Path(__file__).with_name("server.py"))
journal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal)


class JournalTests(unittest.TestCase):
    def test_bounds_order_and_command_without_shell(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout='{"__REALTIME_TIMESTAMP":"20","MESSAGE":"second"}\n{"__REALTIME_TIMESTAMP":"10","MESSAGE":"first"}\n')
        rows = journal.read_rows("webhook.service", 0, 30000, 2, run)
        self.assertEqual(rows, [["20000", "second"], ["10000", "first"]])
        self.assertIn("--unit=webhook.service", calls[0][0])
        self.assertEqual(calls[0][1]["timeout"], 4)
        self.assertNotIn("shell", calls[0][1])

    def test_http_auth_query_and_failure(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), journal.Handler)
        server.token, server.unit = "x" * 64, "webhook.service"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        def get(path, token=True):
            req = Request(base + path, headers={"Authorization": "Bearer " + server.token} if token else {})
            with urlopen(req) as r:
                return r.status, json.load(r)
        from urllib.parse import urlencode
        path = "/loki/api/v1/query_range?" + urlencode(dict(query=journal.SELECTOR, start=0, end=1000))
        original = journal.read_rows
        try:
            with self.assertRaises(HTTPError) as ctx:
                get(path, False)
            self.assertEqual(ctx.exception.code, 401)
            with self.assertRaises(HTTPError) as ctx:
                get(path.replace("end=1000", "end=999999999999999"))
            self.assertEqual(ctx.exception.code, 400)
            journal.read_rows = lambda *args: []
            self.assertEqual(get(path)[1]["data"]["result"], [])
            def fail(*args):
                raise subprocess.TimeoutExpired("journalctl", 4)
            journal.read_rows = fail
            with self.assertRaises(HTTPError) as ctx:
                get(path)
            self.assertEqual(ctx.exception.code, 503)
        finally:
            journal.read_rows = original
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
