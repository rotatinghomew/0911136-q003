"""读取同代供数表并核验传输内容。"""

import hashlib
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen


class SourceError(RuntimeError):
    pass


class GenerationChanged(SourceError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    generation: str
    tables: dict
    evidence: dict
    metadata: dict


class SourceClient:
    def __init__(self, base_url, timeout=3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, path):
        try:
            with urlopen(self.base_url + path, timeout=self.timeout) as response:
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise SourceError("供数响应超过本地大小上限")
                return json.loads(raw)
        except HTTPError as error:
            if error.code == 409:
                raise GenerationChanged("供数资料已换代，请重新建立完整分析") from error
            raise SourceError(f"供数请求失败：HTTP {error.code}") from error
        except (URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
            raise SourceError("无法读取有效供数响应") from error

    def read(self):
        manifest = self.request("/manifest")
        generation = manifest["generation"]
        tables = {}
        evidence = {}
        for name, declaration in sorted(manifest["tables"].items()):
            path = "/tables/" + quote(name, safe="") + "?" + urlencode({"generation": generation})
            response = self.request(path)
            rows = response["rows"]
            if response["generation"] != generation:
                raise GenerationChanged("同一次分析中不能混用资料代际")
            if len(rows) != declaration["count"] or digest(rows) != declaration["sha256"]:
                raise SourceError("表内容与清单摘要不一致：" + name)
            tables[name] = rows
            evidence[name] = declaration
        return Snapshot(generation, tables, evidence, manifest.get("metadata", {}))
