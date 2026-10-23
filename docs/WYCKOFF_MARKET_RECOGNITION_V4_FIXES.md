# 威科夫 V4 评审逐项修复记录

日期：2026-10-23。针对 DOC-01～DOC-05、CODE-01～CODE-10 的核验结果完成修复，并更新 [规范正文](WYCKOFF_MARKET_RECOGNITION_V4.md) 与 `cta_lab/wyckoff_v4/`。规则版本为 `4.1`，参数版本为 `v4.1-default-unvalidated`；默认阈值数值不变。原 4.0 输出不能与新规则混用，需要重新回放。

| 条目 | 完成的处理 | 回归文件 |
|---|---|---|
| CODE-01 / DOC-05 | 吸收假设冻结完成根当前区间；背离事实保留原 onset，空/旧区间参照不再导致崩溃 | test_report_lifecycle_fixes.py |
| CODE-02 / DOC-02 | STRUCTURE 无候选确认期限，缺口和超时支持 null；仅按方向及当前有效触发组联合退出 | test_report_engine_fixes.py、test_report_lifecycle_fixes.py |
| CODE-03 | 新分支或超过 ε 的更深极值替换待确认路径，新建独立 owner；保持收盘价格失效，confirmed 不被替换，终止对象不复活 | test_report_lifecycle_fixes.py |
| CODE-04 / DOC-01 | 退役采用当前有效接纳曾获 Phase E 的历史，严格绑定区间版本和方向；普通区间外横盘可退役，旧失败接纳历史不可借用 | test_report_engine_fixes.py |
| CODE-05 | 结构入口、来源及刷新统一过滤近期、实际可用、同区间事件；LPS/LPSY 必须有效。曾合法采用后来失效的 LPS 也不能延寿 | test_report_engine_fixes.py |
| CODE-06 / DOC-04 | range_only 禁止内部来源 LPS；摆动点限定原点之后、已可用且在对称价格范围内，不越过来源收盘 | test_report_lifecycle_fixes.py |
| CODE-07 | 保留波完整描述 OI，新增按读取时间的实时视图；后来从原记录重新读取可恢复已到达值，不改过去发布 | test_report_feature_fixes.py |
| CODE-08 | AR 宽度超限只拒绝路径 A 区间，保存拒绝原因，继续 ST/停止闭环与 Phase A | test_report_lifecycle_fixes.py |
| CODE-09 | 单独 Phase D 候选随关联有效来源持续；选中的新 LPS/LPSY 续期同向有效 Spring/UT，不复活已过期路径 | test_report_engine_fixes.py、test_report_lifecycle_fixes.py |
| CODE-10 OI | 单根、多根事件、波共用 OIz 窗口计算与规范原因码；原根迟到、本根已到达的数据用于本次描述，按实际 decision_time 脱敏 | test_report_feature_fixes.py |
| CODE-10 评价 | 增加原信号参照、pre_crossed、方向净位移、收盘回撤、MFE/MAE 与 ATR 标准化；首次命中标签和全期限统计独立，重叠/缺口明确 | test_report_evaluation_fixes.py |
| DOC-03 | 四项 JOC/FTI 与 SOS/SOW 阈值不等式；允许双确认路径的总期限取最大值，Test-only 分支不延长 | test_report_feature_fixes.py、test_report_lifecycle_fixes.py |

回归文件均位于 `cta_lab/wyckoff_v4/tests/`。新增用例覆盖原始 OHLC 引擎路径、双向镜像、期限末根、ε/阈值等号、当前有效性、接纳历史隔离、迟到 OI、评价重叠、首次命中后缺口及完整期限统计。原有重放、事务回滚和不可变发布测试继续通过。

验证命令：

```powershell
.\.venv\Scripts\python.exe -m pytest cta_lab/wyckoff_v4/tests/ -q -p no:cacheprovider
# 387 passed in 24.26s（原基线 289）
git diff --check
# 无空白错误
```

评价补充接口采用 `direction`、`signal_reference`、`data_version` 可选参数。省略方向或原信号参照时，对应指标返回 null 和明确原因，现有对称障碍调用仍可使用。日历需要覆盖原信号根至 tau 及未来窗口；MFE/MAE 持续完整 horizon，数据不足明示部分样本。实时波消费者使用原始波记录的 `realtime_view(decision_time)`，原始波对象是带独立可用时间的描述记录。

修复前的反例与结果保留在 `runs/v4_report_check/`，用于记录核验基线；其中脚本断言针对旧缺陷，不能作为新版本验收。当前验收使用上述 pytest 回归。

本次完成工程正确性与明确口径修复；默认参数仍未完成样本外验证，V4 识别器尚未接入 CLI/Web/数据库。
