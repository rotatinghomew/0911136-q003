"""提供本地领域资料服务，保留换代拒绝和内容摘要契约。"""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .datasource import canonical, digest


class DataRepository:
    def __init__(self, dataset, schema):
        self.lock = threading.RLock()
        self.schema = schema
        self.replace(dataset)

    def replace(self, dataset):
        if not isinstance(dataset.get("generation"), str) or not dataset["generation"]:
            raise ValueError("资料代际不能为空")
        if set(dataset["tables"]) != set(self.schema):
            raise ValueError("供数表集合与领域契约不一致")
        for name, declaration in self.schema.items():
            keys = set()
            for row in dataset["tables"][name]:
                if not set(declaration["columns"]).issubset(row):
                    raise ValueError("供数记录缺少约定字段：" + name)
                key = tuple(row[column] for column in declaration["keys"])
                if key in keys:
                    raise ValueError("同代记录键冲突：" + name)
                keys.add(key)
        with self.lock:
            if hasattr(self, "dataset") and self.dataset["generation"] == dataset["generation"] and digest(self.dataset) != digest(dataset):
                raise ValueError("同代内容不能被改写，请使用新的资料代际")
            self.dataset = json.loads(canonical(dataset))

    def manifest(self):
        with self.lock:
            return {
                "generation": self.dataset["generation"],
                "tables": {name: {"count": len(rows), "sha256": digest(rows)}
                           for name, rows in self.dataset["tables"].items()},
                "metadata": self.dataset.get("metadata", {}),
            }

    def table(self, name, generation):
        with self.lock:
            if generation != self.dataset["generation"]:
                return 409, {"error": "资料代际已变化"}
            if name not in self.dataset["tables"]:
                return 404, {"error": "没有该领域表"}
            return 200, {"generation": generation, "rows": self.dataset["tables"][name]}


def handler_for(repository):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_string, *arguments):
            return

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/manifest":
                status, value = 200, repository.manifest()
            elif parsed.path.startswith("/tables/"):
                name = unquote(parsed.path.removeprefix("/tables/"))
                generation = parse_qs(parsed.query).get("generation", [""])[0]
                status, value = repository.table(name, generation)
            else:
                status, value = 404, {"error": "没有该接口"}
            body = canonical(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return Handler


@contextmanager
def serve(repository):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(repository))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def load_repository(path, schema_path):
    return DataRepository(json.loads(Path(path).read_text(encoding="utf-8")),
                          json.loads(Path(schema_path).read_text(encoding="utf-8")))
