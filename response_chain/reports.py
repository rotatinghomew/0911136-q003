"""原子保存分析摘要和来源证据。"""

import json
import os
import tempfile
from pathlib import Path

from .datasource import canonical, digest


def package_report(snapshot, summary):
    content = {"generation": snapshot.generation, "sources": snapshot.evidence,
               "summary": summary, "metadata": snapshot.metadata}
    return {"report_id": digest(content), **content}


def save_report(path, report):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".report-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(report) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_report(path):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    identifier = report.pop("report_id")
    if digest(report) != identifier:
        raise ValueError("摘要内容已变化")
    return {"report_id": identifier, **report}
