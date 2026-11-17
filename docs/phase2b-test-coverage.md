# Phase 2B 测试覆盖与证据边界

核查日期：2026-11-02（Asia/Shanghai）。本文件将 Phase 2B 要求的 18 类检查映射到实际测试函数；它不是行覆盖率报告，也不以函数数量替代 pytest 实际执行结果。

## 已执行结果与原测试保留

Phase 2A 原有 **91 项测试保留**，未删除或降低原有校验条件。Phase 2B 首次完整集成检查实际执行结果为 **165 passed in 16.36s**，由根任务在项目虚拟环境中运行并核实。此处记录该次完整检查；后续若增加测试，最终数量和耗时以 `docs/phase2b-record.md` 的最后一次完整执行记录为准，不将尚未运行的新测试算作通过。

原测试继续验证日档解析、官方校验、UTC/ms/us、缓存/目录边界、因果特征、标签成熟、时间切分、训练期 scaler、Zero/Mean/Ridge 和单日 smoke runner。月档、研究数据加载及正式 runner 采用新增文件，保留原 API 与已有测试。

复查完整测试的命令如下，所有临时文件位于项目内：

```powershell
Set-Location D:\zgs_project\QuantForge
.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/pytest-phase2b
```

## 18 项要求的实际映射

| 编号 | 要求 | 实际测试文件与函数 | 断言内容 |
|---:|---|---|---|
| 1 | 28/29/30/31 天月档 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_calendar_months_and_exact_parquet_roundtrip` | 参数覆盖 2026-02、2024-02、2025-09、2025-12；行数为自然月天数 × 1,440，起止时刻与日历一致。2024-02 是合成闰年 fixture，不是新增数据下载。 |
| 2 | 跨年边界 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_calendar_year_boundary_has_exact_one_minute_gap`；[test_research_dataset.py](../tests/test_research_dataset.py)：`test_build_and_load_ten_synthetic_months_preserves_cross_year_and_month_history` | 2025-12 最后一根到 2026-01 第一根恰好一分钟；十个月合并仍保持完整分钟网格。 |
| 3 | 微秒时间戳 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_calendar_months_and_exact_parquet_roundtrip`、`test_invalid_monthly_contract_is_rejected` | 2025+ Spot 使用 us；2024 fixture 使用 ms。混合单位和 close_time 精度错误被拒绝。 |
| 4 | 错误 SHA-256 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_bad_checksum_writes_failure_and_no_success`；[test_research_dataset.py](../tests/test_research_dataset.py)：`test_wrong_manifest_hash_is_rejected_before_market_access` | 官方 checksum 不匹配时没有成功 manifest、Parquet 或原始归档发布；manifest 自身 hash 不匹配在市场文件读取前失败。 |
| 5 | ZIP CRC 与成员安全 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_zip_structure_path_crc_and_decompression_guards` | 篡改 ZIP 中存储的字节触发 CRC 拒绝；`../outside.csv` 等非预期成员路径拒绝，不向文件系统提取成员。 |
| 6 | 解压体积超限 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_zip_structure_path_crc_and_decompression_guards` | 用缩小测试限额构造超限成员，确认拒绝而非解压继续；生产 CSV 上限保持 32 MiB。 |
| 7 | 累计下载预算 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_cumulative_budget_prevents_request_body_and_persists`、`test_reserved_budget_survives_reopening`、`test_incomplete_read_partial_bytes_are_counted_exactly` | 剩余额度不足时不读取 ZIP 正文；预留额度跨重启保留；中断时收到的 partial bytes 正确计入账本。 |
| 8 | 月档缺失和重复分钟 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_invalid_monthly_contract_is_rejected` | 删除一分钟、重复、乱序、月外时间均失败；同时检验 schema、OHLC、有限值、成交量和整数成交笔数。 |
| 9 | 跨月连续性 | [test_research_dataset.py](../tests/test_research_dataset.py)：`test_build_and_load_ten_synthetic_months_preserves_cross_year_and_month_history`、`test_build_failure_never_publishes_success_manifest` | 十个合成完整月合并为 436,320 行，九个月边界连续，首尾精确匹配开发区间；验证失败不发布整体成功 manifest。 |
| 10 | Parquet 精确往返 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_calendar_months_and_exact_parquet_roundtrip`、`test_complete_download_cache_revalidation_and_persistent_ledger` | DataFrame 写入、读回使用 `check_exact=True`；缓存重验仍执行原始完整性和 Parquet 内容检查。 |
| 11 | 训练与验证边界 | [test_research_splits.py](../tests/test_research_splits.py)：`test_four_fixed_folds_maturity_purge_roles_and_month_end_cutoffs`、`test_missing_month_start_does_not_delay_fit_or_expand_training_information` | 四个固定月度 fold、角色及边界由 `decision_time` 定义；即使月初观测缺失，拟合时刻仍为预先确定的月初，不移动到首观测后吸收未来信息。 |
| 12 | 标签延迟与成熟 | [test_labels.py](../tests/test_labels.py)：`test_strict_maturity_boundary_and_explicit_filter`、`test_maturity_guard_catches_deliberate_future_label_selection`；[test_research_splits.py](../tests/test_research_splits.py)：`test_four_fixed_folds_maturity_purge_roles_and_month_end_cutoffs` | 成熟前一纳秒拒绝，恰好成熟允许；训练目标不越过拟合时间和首评估目标；评估目标成熟时间不越过固定月末截止。 |
| 13 | 最终留出访问拒绝 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_holdout_rejected_before_parsing_or_network`；[test_research_dataset.py](../tests/test_research_dataset.py)：`test_forbidden_month_fails_before_any_file_open`、`test_holdout_manifest_path_rejected_before_source_read`、`test_late_manifest_redirect_is_rejected_before_first_market_read`；[test_research_experiment.py](../tests/test_research_experiment.py)：`test_config_overrides_rejected_before_any_data_read` | 对下载、解析、文件路径和配置绕过放置 spy，确认在网络、CSV 解析或市场文件打开前拒绝。未使用真实 July/August 行情作 fixture。 |
| 14 | 月初历史特征连续性 | [test_research_dataset.py](../tests/test_research_dataset.py)：`test_build_and_load_ten_synthetic_months_preserves_cross_year_and_month_history`；[test_features.py](../tests/test_features.py)：`test_missing_minute_resets_windows_without_imputation` | 连续跨月历史不会在月初重新 warm-up；真正缺口会切断窗口，不 forward-fill。 |
| 15 | scaler 不使用验证数据 | [test_research_experiment.py](../tests/test_research_experiment.py)：`test_runner_scaler_uses_only_mature_training_rows_and_no_eval_refit`；[test_baselines.py](../tests/test_baselines.py)：`test_intentionally_leaky_full_data_scaler_is_caught_by_oracle` | 独立比较训练行均值/方差与样本数；扰动拟合之后的行情不改变首折模型；故意用训练+测试拟合 scaler 的负对照被 oracle 检出。 |
| 16 | 固定配置重复运行一致 | [test_research_experiment.py](../tests/test_research_experiment.py)：`test_research_runner_fixed_folds_reproducibility_and_complete_artifacts` | 两次合成运行的非耗时指标、模型状态、配置哈希和预测 DataFrame 完全一致；耗时允许自然波动，未要求耗时数值一致。 |
| 17 | 网络中断安全恢复 | [test_data_monthly.py](../tests/test_data_monthly.py)：`test_retry_counts_partial_transfer_and_resume_does_not_redownload`、`test_failure_before_manifest_commit_cannot_leave_success`、`test_old_success_does_not_mask_a_failed_revalidation` | 用假 HTTP 响应制造一次中断；有限重试、中断计费、已验证缓存复用和 manifest 提交失败恢复均受检查；较早成功不会掩盖最新验证失败。 |
| 18 | 不完整结果不可成功 | [test_research_experiment.py](../tests/test_research_experiment.py)：`test_incomplete_outputs_never_have_success_summary`；[test_research_dataset.py](../tests/test_research_dataset.py)：`test_build_failure_never_publishes_success_manifest`；[test_data_monthly.py](../tests/test_data_monthly.py)：`test_failure_before_manifest_commit_cannot_leave_success` | 分别注入 Parquet、报告、整体数据验证和月 manifest 持久化失败；失败记录可见，成功 summary/manifest 不生成。 |

## 合成测试与真实数据验证的区别

上述 pytest 使用确定性合成市场数据、临时目录和 mock HTTP，不下载数据。网络中断测试验证的是代码对模拟中断的响应，不能说真实 Binance 服务发生了该中断。留出拒绝测试中的日期和越界 timestamp 也是人工构造，不代表访问过真实留出行情。

十个月加载器的合成集成测试使用完整自然月 Parquet 检验路径、hash、footer、合并与窗口契约；其中月档 ZIP 验证器通过隔离 seam 注入，ZIP/CRC/解析正确性由月档测试独立验证。不能把这一测试当成十个月真实数据已经取得的证据。

真实数据需要另行核验实际下载记录、官方 CHECKSUM、ZIP/CSV/Parquet hashes、UTC 行数、跨月连续性与实际 runner 产物。这些结论写入 `docs/multimonth-data-validation.md`、`docs/research-baseline-report.md` 和 `docs/phase2b-record.md`，只有实际成功后才报告。pytest 全绿本身不证明数据可访问、市场预测有效、统计显著或成本后盈利。

本阶段没有经过验证的交易执行账本，因此测试和研究报告均不输出虚构的 Sharpe、最大回撤、净收益。重叠 5m/15m 标签具有自相关，本轮描述性 MSE/MAE/IC 也不等于 IID 显著性检验。
