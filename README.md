# 跨站降雨过程的可修订分段与形态检索

本项目组织领域资料并提供当前摘要，通过本地 HTTP 供数服务连接应用。业务代码在 `response_chain`，字段与单位约定见 `docs/data-contract.md`。示例均为本地合成记录，不代表真实县域记录或业务结论。

## 环境

Python 3.11 及以上，业务运行仅使用标准库。构建工具及其依赖完整固定在 `requirements-build.txt`；先用 `python3 -m pip install -r requirements-build.txt` 安装。源码和业务测试无需访问外部服务，首次安装构建依赖需要访问 Python 包仓库。

## 当前摘要

在项目根目录运行，会临时启动真实本地 HTTP 服务、核验资料代际、计算现有摘要并完整打印 JSON：

```sh
python3 -m response_chain.runner --output /tmp/response_chain-summary.json
```

当前能力是源码 `analysis.py` 定义的摘要，不会自动给出其他统计推断。输出中的 scope 说明边界，sources 记录资料依据。摘要中的 `catalog` 是跨站过程目录；过程按绝对时间分段，缺测不作为过程结束证据，不按雨量排序。

## 跨站过程目录与形态检索

分段与合并阈值由使用者配置；未知配置项会被拒绝。

```sh
python3 -m response_chain.runner \
  --config config.json \
  --search manual-1 \
  --output /tmp/response_chain-search.json
```

`config.json` 可含 `dry_gap_minutes`、`clock_offset_minutes`、`missing_bridge_minutes`、`include_suspect`，缺省值见 `docs/data-contract.md`。检索按持续时间、移动次序、空间覆盖与起止形态排序；每题都说明依据，并列出因覆盖不同而无法比较的维度。`--weights` 和 `--scales` 调整维度权重与容差，`--same-geometry` 只比较观测站集合完全一致的过程，`--limit` 控制返回条数。

## 资料修订沿革

先保存旧代报告，再用新代资料与它比较：

```sh
python3 -m response_chain.runner --dataset old.json --output /tmp/old-report.json
python3 -m response_chain.runner --dataset new.json --lineage /tmp/old-report.json \
  --output /tmp/lineage.json
```

沿革报告给出过程级的 `unchanged`/`revised`/`split`/`disappeared`、合并到单一过程的旧过程，以及人工备注标签的对应变化。两代分段配置不同时，报告会显式提示差异可能来自配置。

## 分开运行供数与应用

```sh
python3 -m response_chain.runner --serve --port 8765
```

另一个终端可运行：

```sh
python3 -m response_chain.runner --source-url http://127.0.0.1:8765 --output /tmp/response_chain-summary.json
```

## 测试与检查

```sh
python3 -m unittest discover -s tests -t . -v
python3 -m compileall -q response_chain tests
python3 -m build --wheel --no-isolation
```

测试覆盖现有领域摘要、非法引用、真实 HTTP 换代拒绝、表清单内容核验及结果保存；另覆盖跨站分段的跨午夜、干旱与缺测边界、配置校验、形态检索依据及跨代沿革。展示现有产品可直接运行上述摘要命令，不需要回放历史日志。扩展分析时应同步说明新增能力的实际运行入口，保留现行摘要调用。

## 容器运行

Dockerfile 固定官方 Python 3.11.14 slim-bookworm 镜像摘要，不依赖本机私有镜像；首次构建需下载基础镜像和固定构建工具。构建与运行实际摘要：

```sh
docker build -t local/response-chain .
docker run --rm local/response-chain
```

容器内测试：

```sh
docker run --rm --entrypoint python3 local/response-chain -m unittest discover -s tests -t . -v
```
