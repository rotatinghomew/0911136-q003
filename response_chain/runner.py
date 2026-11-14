"""运行领域摘要、过程目录、形态检索或跨代沿革。"""

import argparse
import json
from pathlib import Path
from http.server import ThreadingHTTPServer

from .analysis import summarize
from .datasource import SourceClient
from .lineage import lineage_report
from .processes import normalise_config
from .reports import package_report, save_report
from .search import search_similar
from .service import handler_for, load_repository, serve


parser = argparse.ArgumentParser(description="领域摘要与跨站过程目录")
parser.add_argument("--dataset", default="fixtures/dataset.json")
parser.add_argument("--source-url")
parser.add_argument("--output", default="/tmp/domain-summary.json")
parser.add_argument("--serve", action="store_true")
parser.add_argument("--port", type=int, default=8765)
# 分段与合并规则可由使用者配置，缺省值见 processes.DEFAULT_CONFIG。
parser.add_argument("--config", metavar="FILE",
                    help="分段配置 JSON 文件路径，可含 dry_gap_minutes、clock_offset_minutes、"
                         "missing_bridge_minutes、include_suspect")
# 形态检索，不按雨量排序。
parser.add_argument("--search", metavar="PROCESS_ID_OR_LABEL",
                    help="检索与该过程形态相似的历史过程")
parser.add_argument("--weights", metavar="FILE", help="形态维度权重 JSON 文件路径")
parser.add_argument("--scales", metavar="FILE", help="形态容差 JSON 文件路径")
parser.add_argument("--limit", type=int, default=5)
parser.add_argument("--same-geometry", action="store_true",
                    help="只比较观测站集合完全一致的过程")
# 资料修订沿革。
parser.add_argument("--lineage", metavar="OLD_REPORT",
                    help="与旧代报告比较，输出过程合并/拆分与备注沿革")


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    arguments = parser.parse_args()
    schema_path = Path(__file__).with_name("schema.json")
    if arguments.serve:
        server = ThreadingHTTPServer(("127.0.0.1", arguments.port),
                                     handler_for(load_repository(arguments.dataset, schema_path)))
        try:
            print(json.dumps({"listening": server.server_address}, ensure_ascii=False), flush=True)
            server.serve_forever()
        finally:
            server.server_close()
        return
    config = normalise_config(_read_json(arguments.config) if arguments.config else None)
    if arguments.source_url:
        snapshot = SourceClient(arguments.source_url).read()
    else:
        with serve(load_repository(arguments.dataset, schema_path)) as base_url:
            snapshot = SourceClient(base_url).read()
    summary = summarize(snapshot.tables, metadata=snapshot.metadata, config=config)
    catalog = summary["catalog"]
    extras = {}
    if arguments.search:
        extras["search"] = search_similar(
            catalog, arguments.search,
            weights=_read_json(arguments.weights) if arguments.weights else None,
            scales=_read_json(arguments.scales) if arguments.scales else None,
            limit=arguments.limit, same_geometry_only=arguments.same_geometry)
    if arguments.lineage:
        previous = _read_json(arguments.lineage)
        previous_catalog = previous.get("summary", {}).get("catalog")
        if previous_catalog is None:
            raise ValueError("旧报告缺少过程目录，无法比较沿革")
        extras["lineage"] = lineage_report(previous_catalog, catalog)
    report = package_report(snapshot, summary, extras)
    save_report(arguments.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
