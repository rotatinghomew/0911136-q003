# 领域资料与供数约定

## 数据含义

pulses 为供数端记录的湿润区间，彼此允许重叠；一个 pulse_id 的内容在同一 generation 内唯一。time_error_minutes 是时间戳误差上界，不是需要自动加到持续时间的值。gaps 为无观测区间，并非干旱。notes.process_ref 引用既有人工过程标签 manual-1 等；这些标签的初始 pulse 成员由 notes 同代 metadata 字段提供。坐标为平面米；只处理分析与检索，不实现降水物理模拟。

## 跨站过程目录

`response_chain/processes.py` 把各站记录整理为跨站过程目录：

- 时间一律按带时区的绝对时间归一到 UTC，跨午夜不按自然日拆分或拼接。
- 同站先合并重叠/相接的湿润区间，再按 `dry_gap_minutes` 决定是否分段。
- 跨站区间重叠、相接，或间隔不超过 `clock_offset_minutes` 时并入同一过程；后者是时钟偏移容忍，优先于干旱判断，不算已观测干旱。间隔超过容忍后再按已观测干旱与缺测规则判断。
- `gaps` 是无观测，不是干旱。间隔若被缺测覆盖，缺测本身不能证明过程结束：不超过 `missing_bridge_minutes` 的间隔桥接；超过则拆开并把边界记为 `proves_boundary=false`。只有未被缺测覆盖的已观测干旱超过 `dry_gap_minutes` 才是已证实的结束（`proves_boundary=true`）。
- 每个过程的 `end_boundary` / `start_boundary` 记录决定、理由、实际间隔、参与者、缺测分钟数与所用阈值，可逐条复核。
- 过程按开始时间升序排列，同刻按 `process_id`；雨量不参与排序，也不作为检索依据。
- 质量标记：`valid` 参与；`suspect` 默认排除，可由 `include_suspect` 纳入；`withdrawn`/`invalid`/`retracted` 一律排除并记入 `diagnostics.excluded_pulses`，不会因被撤回记录的异常时间戳阻断整批资料。

`DEFAULT_CONFIG` 的四个键都可由调用者配置；未知键、负数或非数值会被拒绝，不会被静默忽略：

| 键 | 含义 | 默认 |
| --- | --- | --- |
| `dry_gap_minutes` | 已观测干旱超过该值才分段并认定过程结束 | 120 |
| `clock_offset_minutes` | 跨站小间隔的时钟偏移容忍 | 15 |
| `missing_bridge_minutes` | 纯缺测间隔可桥接的上限 | 360 |
| `include_suspect` | 是否纳入可疑观测 | false |

一次修订可能让两个旧过程合并、让一个过程拆开，或让备注指向的过程改变。

## 形态检索

`response_chain/search.py` 按持续时间、移动次序与速度、空间覆盖、起止形态四个维度比较，不使用雨量。每项得分都带 `basis`（分项依据）和 `metrics`（实际数值）；`explanation.used_dimensions` 与 `not_comparable` 说明本次使用了哪些维度、哪些维度因覆盖或移动信息不足而无法比较。站集合不同的过程仍可比较持续时间与空间覆盖，但移动维度和按共同站的起止形态可能不可比，报告会明确写出 `only_target` / `only_candidate` 站。权重与容差可配置；权重为 0 的维度标注为“未参与”，不计入 `not_comparable`。

人工标签（如 `manual-1`）可作为检索目标，但只有其唯一对应一个过程时才可解析；对应 0 个或多于 1 个过程时拒绝并说明原因。

## 资料修订沿革

`response_chain/lineage.py` 按 pulse 成员重叠比较两代目录：`unchanged`、`revised`、`split`、`disappeared`，以及 `merged_into_single`。不按雨量或开始时间近似匹配。人工备注沿革区分 `unchanged`、`extended`、`retracted`、`split`、`merged`、`remapped`、`status_changed`、`new_note`、`removed_note`。若两代使用了不同的分段配置，报告会显式标明差异可能来自配置而非资料修订。

## 现行摘要

`response_chain/analysis.py` 生成现行资料摘要，包含原有独立区间统计与跨站过程目录。源码和运行结果中的 scope 说明其统计边界，不能把未提供的推断当成已有能力。


## 供数接口

`GET /manifest` 返回 generation、tables（每表 count 与 sha256）及 metadata。`GET /tables/<name>?generation=<generation>` 返回同代 rows。供数换代时旧 generation 返回 HTTP 409，不允许将新代记录接入旧分析；不存在的表返回 404。表内容为稳定键唯一的完整同代记录，不是增量事件流。客户端对清单摘要进行核验。摘要使用 UTF-8、排序对象键、紧凑 JSON 的 SHA256，数组顺序保留供数端顺序。

字段与主键声明位于 `response_chain/schema.json`，合成资料位于 `fixtures/dataset.json`。`metadata.cutoff` 是供数展示截点，不自动为分析选择训练截止点。服务可将整个 dataset 原子替换为新一代，但不在同一 generation 下更改内容。

## 结果保存

`reports.py` 保存现有摘要及来源表摘要，report_id 是内容摘要，不是随机编号。相同内容生成相同编号，临时文件写入完成后原子替换目标文件；历史内容是否保留由调用者选择不同目标路径。服务只监听本机，不接入生产系统。
