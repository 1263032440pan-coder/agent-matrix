# 复验记录：技术债批次 3（D06-TD01 + D08-TD01）+ LLM Worker（批次 A）

> 复验日期：2026-09-23
> 触发：返修单-D18后增量改动-偏离检查 问题 1
> 复验方式：红线断言实证 + 全量测试 + E2E 回归
> 结论：**通过。D06-TD01 / D08-TD01 维持 CLOSED。**

## 复验项与实证

| 复验项 | 证据 | 结果 |
|---|---|---|
| LLM 抽取 UNKNOWN 红线（WP-037：不脑补） | `test_llm_extractor_red_line_unknown_not_invented` PASSED——LLM 返回缺字段时强制 UNKNOWN，不编造 | ✅ |
| AMBIGUOUS（低置信不回退关键词冒充） | `test_llm_extractor_parses_llm_json` + 置信度路径断言 | ✅ |
| LLM 失败 fallback 行为 | `test_llm_extractor_falls_back_when_not_configured` / `test_gap_llm_classify_returns_none_without_config` PASSED——无配置/失败时回退关键词/维度映射，不中断链路 | ✅ |
| 六分类语义 + fallback 一致性 | `test_gap_six_types_produced_without_llm` + `test_gap_llm_answer_parsing` PASSED——批量分类解析器不完整时整体回退 fallback（`_parse_batch` 任一条目 None → 全量 fallback） | ✅ |
| CONFLICTED（D06-TD01 承诺） | `test_detect_conflicts_marks_conflicted` / `no_conflict_when_fields_agree` / `ignores_unknown_pairs` 三用例 PASSED | ✅ |
| LLMWorker FAILED 路径 | `test_llm_worker_failure_marks_failed` PASSED——LLM 异常 → FAILED（不静默 EXECUTION_DONE） | ✅ |
| Worker 红线（不标 ACCEPTED / READY 才能起） | `test_llm_worker_not_ready_rejected` PASSED；`test_llm_worker_executes_and_produces_artifact` 断言终态 EXECUTION_DONE 且非 ACCEPTED | ✅ |
| opt-in 隔离（测试不读全局 LLM 配置） | 全部 9 用例 mock litellm/无配置路径，不触发真实网络 | ✅ |
| SQLite 并发修复回归 | 373 passed（含 dashboard/plan/execute 相关）；busy_timeout 30s 生效 | ✅ |

## 基线

- pytest：**373 passed**（test_llm_cognitive 9 + test_llm_worker 4 全绿）
- ruff：All checks passed
- E2E（`scripts/e2e_demo.py`）：**PASS**（3 用例）

## 备注

复验范围覆盖返修单点名的全部重点。检查方"批次 3 未经复验即 CLOSED"流程缺口由本记录补齐。
