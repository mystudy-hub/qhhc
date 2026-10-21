# 维科夫加权指数实时行情识别规范 V3.0

版本日期：2026-10-21  
规则版本：`3.0`；参数版本：`v3-default-unvalidated`  
前置文档：[V2.0 规范](WYCKOFF_MARKET_RECOGNITION_V2.md)、[V2.0 评审](WYCKOFF_MARKET_RECOGNITION_V2_REVIEW.md)  
适用范围：单一期货品种的加权指数序列。不覆盖实际合约、换月与可成交价格。  
版本性质：完整替代 V2.0 的工程规范；阈值尚未完成样本外验证

> 本规范要求识别器"确定、可复现"：相同数据、规则版本和参数版本产生相同输出。这不代表对吸筹、派发、反转或未来收益有确定判断。文中默认数字全部是研究起点。

## 0. V3 相对 V2 的变化

V3 保留 V2 的总体架构，并把数据范围收窄为加权指数。修改来自 V2 评审（下称"评审"）中经核对成立的意见，以及本次复核新发现的问题。逐条处置见附录 A。

| 类别 | 主要变化 | 章节 |
|---|---|---|
| 数据范围 | 只适用于加权指数；删除实际合约、换月、合约涨跌停和流动性预热规则；新增指数输入契约（来源、权重时点、同步 OHLC、量能口径、价格精度）；禁止缺少指数时静默换成其他序列 | 2.2、4.1—4.3 |
| 价格精度 | 不再按合约最小跳动取整；ε 和"超过前高"的比较距离按指数价格分辨率定义 | 4.3、5.2 |
| 突破与 LPS 状态机 | 外侧维持改为滚动连续计数；LPS 候选随来源对象一起失效或过期；确认先于"新高结束回调"；最低点位置与回调长度分开；两条确认路径的状态转换统一；LPS 确认后仍有失效出口；同一根不能产生两个 LPS 来源 | 5.5、7.4、7.5 |
| 方向证据 | 活动率组不再单独否决；改为四条明确路径；活动率与累计努力分开保存 | 4.5、8.3 |
| 阶段语义 | SOS 加 LPS 只给 Phase D 候选，规则支持另需后续推进或方向证据；无 Spring 的 Phase C 明确为未实现；深穿不再被描述为"大量供应" | 3.2、9.1 |
| 补全定义 | UT 测试序列；回看型多根模式的快照环；日线与日内的样本门槛；ATR 初始化 | 4.4、6.0、7.3 |
| 基准 | 截尾基准改为模式开关，标准基准同时计算；偏差表述改为待验证 | 4.4 |
| 验证 | 用例分 A/B/C 三组独立验收；评价样本只能按决策时点已知的信息选取；迟到信号的锚点与 `pre_crossed` 标记 | 11 |

V3 与 V2 的输出不能混用。

## 1. 目标、理论边界与输出分层

### 1.1 每根已收盘 K 线需要回答的问题

- 当前更接近上涨趋势、下跌趋势、交易区间还是过渡状态？
- 最近发生了哪些可直接观测的量价事实？
- 哪些结构解释获得规则支持？哪些竞争解释仍然成立？
- 下一步什么动作会增强判断？什么动作会使它失效？
- 本次判断使用的数据最晚到什么时候？

| 层次 | 输出示例 | 含义 |
|---|---|---|
| 市场状态 | `uptrend`、`range`、`transition` | 规则对当前状态的分类 |
| 结构假设 | 止跌候选、Spring 供应测试候选、吸筹候选 | 对事件链条的解释，允许被后续价格否定 |
| 事实事件 | 宽幅高量、下穿收回、上破边界 | 截至当时确实满足了规定条件 |

识别器只输出观察与研究信号。加权指数不可直接成交；仓位、买卖、止损和成交模拟由独立策略层决定，并须另行定义从指数到可成交合约的映射。

### 1.2 三大定律的量化替代

| 理论概念 | V3 可观测替代量 | 解释边界 |
|---|---|---|
| 供求法则 | 波幅、收盘位置、净位移、波段推进与回吐 | 指数 OHLCV 只反映成分合约整体的表现，不能观测挂单与参与者意图 |
| 因果法则 | 区间年龄、宽度、触碰、内部量价结构 | 区间根数不决定后续涨跌幅；V3 不实现点数图目标价 |
| 努力与结果 | 标准化活动率、累计努力与波幅、净位移、波段结果的组合 | 大努力小结果是背离证据，吸收方向需要上下文和后续反应 |

指数成交量是成分合约活动的聚合代理，其含义取决于 `volume_method`（第 4.5 节）。持仓量（OI）增加说明多空双方同时新开仓，减少说明双方同时平仓，不能说明哪一方是主动方；指数 OI 还会随交割月份合约到期而系统性下降。V3 把 OI 作为描述特征保存，默认不参与任何判定。

Composite Man 是理解大资金行为的分析模型。V3 不输出"已证明某机构吸筹"之类的身份或订单行为判断。

### 1.3 操作流程

1. 登记研究对象：指数 ID、来源、编制方法版本、权重方法与取值时点、OHLC 方法、量能口径、价格分辨率、周期与交易时段；保存规则版本和参数版本。
2. 执行第 4.2 节的输入合格性检查；不合格时整条序列输出 `not_evaluable` 及原因。
3. 按第 9.4 节顺序逐根处理已收盘 K 线，保存快照、事件和假设状态转换。
4. 阅读结果：先看质量与市场状态，再看仍有效的结构假设，最后看下一确认、失效条件和期限。
5. 收盘后复查新增事件和状态转换，检查数据修订、成分变动和边界版本变化。
6. 按第 11 节验证，按第 10、12 节分批校准；通过以前一律标记"未验证基线"，不输出概率。

## 2. 约束分类

### 2.1 三类约束

| 类别 | 含义 | 能否更改 |
|---|---|---|
| I：不变原则 | 因果性、真实可用时间、事实与解释分离、可复现性 | 仍称为实时识别器就不能违背 |
| F：V3 固定约定 | 公式、枚举、时间计数、边界与波浪方法 | 同一版本内不能更改；修改需升级规则版本 |
| P：研究参数 | 周期、倍数、窗口、容忍度、次数与模式开关 | 训练与验证阶段可校准；运行前冻结 |

### 2.2 I 类不变原则

| 编号 | 内容 |
|---|---|
| I-01 | 时间 t 的输出只使用在 t 已到达并可用的数据 |
| I-02 | `event_time`、`detected_at`、`confirmed_at`、`available_at` 分别保存；后续确认不能倒填 |
| I-03 | 事实事件与结构解释分开记录；后续失败不抹掉过去确实发生的事实 |
| I-04 | 当前待检测 K 线不能定义自己的检测边界；边界必须在检测前已知 |
| I-05 | 不移动原有边界挽救失败事件；新边界必须产生新的区间版本 |
| I-06 | 缺失数据、指标不可用和未实现的情形不能用"没有信号"代替 |
| I-07 | 相同数据版本、规则版本和参数版本产生相同输出 |
| I-08 | 规则分数、证据条数和 `confirmed` 标签不能称为概率 |
| I-09 | 样本外数据不能用于选参数；未来标签重叠时必须隔离 |
| I-10 | 成交量和持仓量变化都不能直接称为主动买量、主动卖量或某方开仓 |
| I-11 | 多根模式中声明为"起始前已知"的上下文，必须读取模式第一根之前的快照（第 6.0 节） |
| I-12 | 输入必须是已声明来源的加权指数，且指数在每根 K 线上使用的权重在该 K 线计算时已经可知。来源不符、缺失或使用事后权重时返回缺项，不得静默换成主连、复权序列或合成序列 |

### 2.3 F 类 V3 固定约定

| 编号 | 内容 |
|---|---|
| F-01 | 正式输出只使用已收盘 K 线；盘中输出单列为 `provisional` |
| F-02 | `Spread=H-L`；真实波幅单独计算；成交量基准和 ATR 比较基准排除当前 K 线 |
| F-03 | ATR 使用 Wilder 平滑；`baseline_mode=robust` 时 ATR 输入与成交量基准按第 4.4 节截尾；均线用 SMA；中位数偶数样本取中间两数均值 |
| F-04 | 严格按公式使用 `>`、`>=`、`<`、`<=` |
| F-05 | 时间窗口按有效交易 K 线计数，上下界都包含；"N 个价格变化"需要 N+1 个收盘 |
| F-06 | 前台区间的 S/R 是固定水平区域；内部 Creek/Ice 使用第 5.6 节的因果摆动点，不使用曲线拟合 |
| F-07 | 所有候选采用相同生命周期，状态转换只追加不覆盖；依附来源对象的候选随来源对象一起终止（第 7.1 节） |
| F-08 | 波浪使用第 8 节的因果划分，成交量不按事后极值重新分配 |
| F-09 | Spring/UT 的量能分组、深穿分界和收回窗口是工程分类，不宣称为 Wyckoff 或 Pruden 的固定数字 |
| F-10 | 自动识别的 LPS/LPSY 只覆盖第 7.5、7.6 节列出的子类型；其他形态标记为未覆盖 |
| F-11 | 跨根或跨时段比较量能时使用标准化活动率（第 4.5 节），不直接比较原始成交量 |
| F-12 | 每条失效计数必须登记计数起点（第 7.1 节） |
| F-13 | 价格以 `price_resolution` 为单位的定点整数比较；不使用合约最小跳动 |
| F-14 | 突破外侧维持使用滚动连续计数（第 5.5、7.4 节） |
| F-15 | 快照环长度 = `max(3, spring_max_pattern_bars, shakeout_max_pattern_bars) + 1`，随参数推导 |

### 2.4 P 类参数的使用要求

第 10 节登记全部默认数字和模式开关。调整时保存：参数名、旧值、新值、适用指数、周期、时段、训练区间、样本外结果和修改原因。运行中不能因信号失败而临时放宽；在线校准必须作为独立规则版本验证。

## 3. 术语与阶段

### 3.1 计算与数据术语

| 名称 | 含义 |
|---|---|
| TR（区间语境） | Trading Range，交易区间；代码中真实波幅写作 `true_range`，避免混淆 |
| ATR | 平均真实波幅 |
| `A_t` | 事件比较用 ATR，等于 `ATR_(t-1)`，不含当前根 |
| `A_ref` | 区间建立时冻结的 ATR，用于该区间的容忍度和宽度 |
| Bv | 成交量基准，排除当前根 |
| RVOL | 单根相对成交量 `V_t/Bv_t` |
| Effort | 标准化活动率：一段 K 线的 `sum(V)/sum(Bv)`，相当于平均每根的相对活动强度；单根时等于 RVOL |
| CumEffort | 累计标准化努力：一段 K 线的 `sum(RVOL)`，随持续时间增加 |
| NS | 标准化波幅 `(H-L)/A_t` |
| CP | 收盘位置 `(C-L)/(H-L)`，0 接近低点，1 接近高点 |
| LW / UW | 下影 / 上影占比 |
| NetMove | `(C_t-C_(t-1))/A_t` |
| ER | 方向效率：净位移绝对值 / 逐根变化绝对值之和；不含方向 |
| SignedDelta | 有方向的净位移，`(C_end-C_start)/A` |
| `price_resolution` | 指数价格分辨率，来自数据契约，不是可成交的最小跳动 |
| `confirm_step` | "超过前高 / 跌破前低"的最小距离，`confirm_step_units*price_resolution` |
| outside_streak | 收盘连续位于前台区间外侧的根数，见第 5.5 节 |
| 起始上下文 | 多根模式第一根之前的快照，见第 6.0 节 |
| 因果摆动点 | 需要后续 k 根确认的局部高低点，确认前不可用，见第 5.6 节 |
| `not_evaluable` | 必要数据或指标不可用，不是"条件未触发" |
| `not_implemented` | 本版本未覆盖，不是"市场没有该事件" |

### 3.2 威科夫事件术语

| 缩写 | 中文 | V3 用法 |
|---|---|---|
| PS / PSY | 初步支撑 / 初步供应 | 仅解释性术语，不单独输出 |
| SC / BC | 抛售高潮 / 买入高潮 | 单根版本和多根高潮簇（第 6.1、6.2 节） |
| AR | 吸筹语境为自然反弹 `AR_RALLY`，派发语境为自然回落 `AR_REACTION` | 两个方向使用不同枚举 |
| ST | 二次测试 | 测试形态与测试后反应分两步（第 7.2 节） |
| Spring | 弹簧，已知支撑被短暂下穿后收回 | 按收回位置、深度、量能分型（第 6.4 节） |
| Shakeout | 震仓，深度下穿后收回 | `SHAKEOUT_RECLAIM`，必须通过后续测试才增强证据 |
| Test | 测试 | 测试形态与测试后反应分两步（第 7.3 节） |
| UT / UTAD | 上冲回落 / 派发后上冲 | UT 有对应测试序列；UTAD 需要成熟区间和起始上下文中的供应占优 |
| SOS / SOW | 力量信号 / 弱势信号 | 可以发生在区间内部 |
| JOC (JAC) | 越过小溪 | 越过事前已知阻力（前台 R 或内部 Creek）的强突破 |
| FTI | 跌穿冰面 | JOC 的向下对应 |
| BU / BUEC | 回撤 / 回到小溪边 | BUEC 是 LPS 的一个子类型 |
| LPS / LPSY | 最后支撑点 / 最后供应点 | 子类型见第 7.5、7.6 节 |

Spring #1/#2/#3 等编号在不同教学体系中定义不一致。V3 事件代码只描述可观测特征：`SPRING_LOW_VOLUME`、`SPRING_HIGH_VOLUME`、`SPRING_OTHER`、`SHAKEOUT_RECLAIM`。编号别名只能用于显示，并须在配置中声明所用的教学体系。

深穿是按价格深度划分的，本身没有量能门槛。深穿增加结构失败的风险；如果同时高量（`shakeout_high_volume` 标记），需要进一步检查供应与价格结果。V3 保守地要求所有深穿收回都通过后续测试才增强吸筹解释，这是研究选择，不代表深穿一定伴随大量供应。

### 3.3 SOS、JOC、BU、BUEC、LPS 的关系

- SOS 描述力量表现，可在区间内部；JOC 额外要求越过事前已知的阻力。普通低量上破是 `BREAKOUT_UP`，不满足 JOC 的量价条件。
- BU 是突破后的回撤过程；BUEC 要求回到旧阻力附近。
- LPS 描述 SOS 或突破后的回调中供应减弱、随后需求重新推进的支撑点。V3 自动识别三种子类型：回到小溪边（`LPS_AT_CREEK`，即 BUEC）、在边界上方止跌（`LPS_ABOVE_CREEK`）、区间内部 SOS 之后（`LPS_INTERNAL`）。
- 突破有效性由第 7.4 节的外侧维持或失效规则单独判断，不依赖是否出现 BUEC。
- 一次 SOS 加一个合格 LPS 只构成 Phase D 候选；规则支持还需要后续推进或独立方向证据（第 9.1 节）。

### 3.4 Phase A—E

| 阶段 | 吸筹方向 | 派发方向 | V3 输出原则 |
|---|---|---|---|
| A | 下跌停止：SC、AR、ST | 上涨停止：BC、AR、ST | 止跌或止涨证据，不等于永久反转 |
| B | 区间内部运行 | 区间内部运行 | 区间成熟不等于方向已知 |
| C | 剩余供应的测试，可能出现 Spring | 剩余需求的测试，可能出现 UT/UTAD | Spring 和 UTAD 都不是必经路径；无 Spring/UT 的 Phase C 本版本未实现（`phase_c_without_spring=not_implemented`），阶段保持未知 |
| D | SOS 与较弱回调 / LPS | SOW 与较弱反弹 / LPSY | 先给候选，满足第 9.1 节额外条件才获规则支持 |
| E | 离开区间后的同向拉升 | 离开区间后的同向下跌 | 必须满足方向条件（第 9.1 节） |

V3 保存 `phase_candidate` 与 `phase_supported_by_rules`，允许多次 ST、多次 LPS、无 Spring 的结构和方向暂不明的区间。没有检测到 Spring 不等于没有 Phase C。

## 4. 数据契约、计算定义与时间口径

### 4.1 指数输入契约

V3 只支持 `data_profile=weighted_index`。所有状态按 `index_id + timeframe` 维护。

```text
序列级（研究开始前登记，变更即为新 data_version）：
  data_profile = weighted_index
  index_id, index_source, index_method_version, data_version
  constituent_universe_definition      # 哪些月份合约进入指数
  weight_method                        # 例如按持仓量、按成交量
  weight_asof_rule                     # 权重取值时点，例如"上一交易日收盘持仓"
  weight_update_schedule
  ohlc_method                          # vendor_published / synchronous_path
  price_resolution
  volume_method, volume_unit           # 见第 4.5 节
  oi_method                            # 可选
  revision_policy                      # 供应商是否会修订历史值

K 线级：
  bar_start, bar_end, trading_day, session_id
  open, high, low, close, volume
  is_closed, received_at, quality_flags
  open_interest（可选）, constituent_coverage（可选）
```

未知的权重、成分范围或量能含义不能默认为已知。供应商指数可以不在本地重建，但必须登记供应商的定义和修订口径。

### 4.2 输入合格性检查

| 情形 | 处理 |
|---|---|
| `index_source` 未声明，或实际读取的序列与声明来源不符 | 整条序列 `not_evaluable`，原因 `index_source_mismatch` |
| 输入是主力连续序列、复权序列或去跳空序列（即使命名为 `*_index`） | 同上。这类序列没有成分与权重，不是加权指数 |
| 声明的指数缺失 | 返回缺项；不得自动回退到其他序列 |
| `ohlc_method` 为对各成分的 H、L 分别加权 | 拒绝。成分极值未必同时发生，会制造不存在的宽幅和影线 |
| 只有成分 K 线、无法恢复同步极值 | 属于另一个 `data_profile`，需注明近似方法并单独验证，不在 V3 范围内 |
| 某根使用的权重取值时点晚于该根的计算时刻（例如日内指数事后用当日收盘持仓重算） | 拒绝作实时输入（I-12）；只能标记 `ex_post_weights` 用于事后描述 |

同步路径指数的反例：两个等权成分，一个时点分别为 110、90，另一时点为 90、110。同步指数始终为 100；分别加权各自的 H/L 会得到 H=110、L=90。

基本合法性：`H>=max(O,C)`、`L<=min(O,C)`、`H>=L`、`V>=0`、`OI>=0`、时间有序、主键不重复。不要求价格永远大于零。

### 4.3 价格精度

- 价格保留数据源原始精度，按 `price_resolution` 的整数倍做定点表示与比较。
- 若原始价格不是 `price_resolution` 的整数倍，判为契约不符并拒绝，不自行取整。
- 成分合约的最小跳动和显示精度都不影响规则。例：成分最小跳动为 1，指数为 100.37 时按 100.37 比较。
- 阈值在比较前不先取整。

### 4.4 波幅与成交量基准

令 t 为当前已收盘 K 线，`A_t=ATR_(t-1)`。

```text
true_range_t = max(H_t-L_t, abs(H_t-C_(t-1)), abs(L_t-C_(t-1)))

# ATR（Wilder）
ATR 初始值 = 前 n_atr 个可计算 true_range 的算术平均（不截尾）
ATR_t = ((n_atr-1)*ATR_(t-1) + tr_in_t) / n_atr
robust：  tr_in_t = min(true_range_t, tr_cap_mult * median(true_range_(t-n_atr), ..., true_range_(t-1)))
standard：tr_in_t = true_range_t

# 成交量基准
W_t = 取样集合（第 4.5 节）中的合格 K 线成交量，不含当前根
robust：  Bv_t = mean( min(V_i, volume_cap_mult * median(W_t)) for V_i in W_t )
standard：Bv_t = mean(W_t)

RVOL_t = V_t / Bv_t
Spread_t = H_t-L_t
NS_t = Spread_t / A_t
CP_t = (C_t-L_t) / Spread_t
LW_t = (min(O_t,C_t)-L_t) / Spread_t
UW_t = (H_t-max(O_t,C_t)) / Spread_t
NetMove_t = (C_t-C_(t-1)) / A_t
Gap_t = abs(O_t-C_(t-1)) / A_t
```

ATR 的截尾从初始值之后的第一根开始，此时已有 n_atr 个历史真实波幅可算中位数。Bv 在 W_t 合格样本达到第 4.5 节门槛前不可用。合格 K 线：非 `data_gap`、成交量为真实值。

`baseline_mode` 默认 `robust`。两种模式的 ATR 和 Bv 都计算并保存，另存截尾比例；只有当前模式的值参与判定。

选择 robust 为默认的理由：高潮参照努力 `Effort_SC` 的基准不含高潮根自身，而之后各根的基准包含它，于是 ST/Test/LPS 回调的 RVOL 被压低，相对门槛和绝对门槛都变得更容易满足。这是方向确定的偏差；截尾只能减弱、不能消除它。偏差对识别结果的实际影响尚未验证。代价是波动率或活跃度真实抬升时，robust 基准适应更慢，可能增加异常标签。两种模式必须在同样本上对比。

`H=L` 时 CP、LW、UW 不可用，不填 0.5。Bv 为 0 或 ATR 为 0 时相应指标不可用，相关规则返回 `not_evaluable`。

### 4.5 标准化活动率、累计努力与量能口径

```text
Effort(a..b)    = sum(V_i, i=a..b) / sum(Bv_i, i=a..b)   # 活动率，与持续时间无关
CumEffort(a..b) = sum(RVOL_i, i=a..b)                    # 累计努力，随根数增加
```

要求区间内每根 Bv 都可用。所有跨根比较（ST 对 SC、Test 对 Spring、回调对突破）使用 Effort。两者含义不同：上波 10 根、每根 RVOL 0.8，下波 2 根、每根 1.2，则下波活动率更高，但上波累计努力更大（8.0 对 2.4）。文中说"更活跃"时指 Effort，说"累计努力"时指 CumEffort。

| 运行配置 | Bv 取样集合 | 最低合格样本 |
|---|---|---|
| 日线 / 非日内 | 前 `volume_period` 根 | `volume_min_samples` |
| 日内 `session_matched` | 过去 `session_baseline_days` 个交易日中同一 session、同一 bar slot 的 K 线 | `session_min_samples` |

两个门槛各自只用于对应配置。

**量能口径：** `volume_method` 必须显式声明，例如 `sum_raw`（成分原始量直接加总）、`weight_scaled`、`turnover_sum`。不同口径含义不同，RVOL 及其阈值只能在同一口径内校准和比较。价格按持仓加权、量按全成分无权重加总是可以研究的配置，但要检查一种代理偏差：低权重、高成交的成分可能造成"指数巨量而位移小"，被误读为吸收。

### 4.6 持仓量特征（描述用）

```text
dOI_t = OI_t - OI_(t-1)
OIz_t = dOI_t / median(|dOI_(t-n_oi)|, ..., |dOI_(t-1)|)
OIz(a..b) = sum(dOI_i, i=a..b) / median(|dOI_(a-n_oi)|, ..., |dOI_(a-1)|)
```

| 价格方向 | `OIz >= oi_flat_band` | `OIz <= -oi_flat_band` | 其他 |
|---|---|---|---|
| `C_t>C_(t-1)` | `up_oi_up` | `up_oi_down` | `up_oi_flat` |
| `C_t<C_(t-1)` | `down_oi_up` | `down_oi_down` | `down_oi_flat` |
| 平收 | `flat_oi_up` | `flat_oi_down` | `flat_oi_flat` |

只在 `oi_method` 已声明时计算，否则 `not_evaluable`。日线 OI 在收盘后才发布时，按实际发布时间可用。交割月前后的系统性减仓标记 `delivery_season`。常见解读只能作为显示说明。`oi_mode` 默认 `annotate`；`filter` 模式属于新的研究配置。

### 4.7 零量、缺口、成分变动与修订

- 真实零量 K 线保留，但不触发低量 Spring、Test、ST 或吸收事件；缺失 K 线不能填零。
- 交易时段内缺失应有的 K 线时，受影响窗口暂停并输出 `data_gap`，修复前不用不完整窗口确认事件。
- 成分范围变化（新合约进入、旧合约到期退出）当根标记 `constituent_change`；成交量和 OI 可能因此发生台阶变化。标记 K 线照常参与计算，但以它为确认根的强事件附带该标记。
- 指数没有单一涨跌停价，`limit_state=not_applicable`，不推导指数封板价。成分受限比例可以作为将来的质量字段，本版本 `not_implemented`。主要成分封板时可能出现 `H=L` 的指数 K 线，按上文 `H=L` 规则处理。
- 数据修订时保存原实时版本与修订版本；实时回放使用当时的版本。

### 4.8 时间口径

`t0+a～t0+b` 指第 a 至第 b 根后续有效 K 线，两端都包含。单根首次可用时间不早于 `max(bar_end, received_at)+计算延迟`；多根条件取所有相关数据的最晚到达时间。迟到的 K 线只能在到达后影响实时判断。预热从序列第一根合格 K 线计数。

## 5. 趋势背景、交易区间与内部参照位

### 5.1 趋势背景

- `trend_context_t`：事件 t 的前置背景，只使用 t-1 及以前的数据。
- `trend_now_t`：第 9.1 节当前状态，使用截至 t 的数据。

以 `trend_context_t` 为例（n=`trend_window`）：

```text
Delta_n = (C_(t-1)-C_(t-1-n)) / A_t                         # 有方向
ER_n = abs(C_(t-1)-C_(t-1-n)) / sum(abs(C_i-C_(i-1)), i=t-n..t-1)
Slope_n = OLS_slope(C_(t-n), ..., C_(t-1)) / A_t             # n 个收盘

# SMA_fast / SMA_slow 的周期为 trend_sma_fast / trend_sma_slow
downtrend_context: C_(t-1) < SMA_fast_(t-1), C_(t-1) < SMA_slow_(t-1),
                   Delta_n <= -trend_min_displacement_atr,
                   Slope_n <= -trend_min_abs_slope_atr,
                   ER_n >= trend_min_er
uptrend_context:   对称
```

ER 分母为 0 时定义为 0。`trend_now_t` 把窗口整体后移一根，以 t 结尾，比较基准使用 `ATR_t`。

### 5.2 支撑、阻力与容忍度

```text
S, R = 固定候选下沿、上沿；M = (S+R)/2；W = R-S
A_ref = 路径A沿用高潮的 A_SC 或 A_BC；路径B用建立区间前一根 ATR
epsilon = max(boundary_resolution_units * price_resolution, boundary_atr_fraction * A_ref)
range_min_width_atr*A_ref <= W <= range_max_width_atr*A_ref
```

支撑区域 `[S-ε,S+ε]`，阻力区域 `[R-ε,R+ε]`。S、R、ε、A_ref 随区间版本冻结。区间年龄 = 从原点到当前的有效 K 线数（含两端）；`bars_since_established` 单独保存。新建区间从下一根开始用于检测。

### 5.3 路径 A：高潮—反应—测试

SC（或高潮簇）→ AR_RALLY → ST 建立区间，BC 方向对称；细则见第 7.2 节。原点为高潮第一根，建立时间为 ST 反应确认时间。AR 的最小幅度等于 `range_min_width_atr*A_SC`；`W > range_max_width_atr*A_SC` 时 AR 事实保留，区间候选以 `width_too_large` 拒绝。

### 5.4 路径 B：一般震荡区间

使用截至建立时刻的最近 `generic_range_window` 根有效 K 线：

```text
S = min(L)，R = max(H)
ER_window <= generic_range_max_er          # 需要 window+1 个收盘
至少 range_interior_close_fraction 的收盘位于 [S+ε, R-ε]
上下边各至少 range_min_touches_per_side 次有效触碰
窗口内无 data_gap
```

触碰：K 线范围与边界区域相交。每侧按时间贪心选取，相邻计数触碰间隔至少 `touch_min_separation_bars` 根，且中间至少一根离开该侧区域。定义极值的那一根算作一次触碰，因此孤立长影线最多贡献一次触碰。路径 B 只确认工程上的震荡区间，不确认吸筹、派发或 Phase A。

### 5.5 区间生命周期与外侧计数

每个"指数＋周期"最多一个前台区间。事件参照边界（事件创建时引用的 S/R/ε）永久保存；当前分析边界可以因区间退出、超时或被替代而更换版本。

```text
building -> active -> exit_up_candidate / exit_down_candidate
                   -> exit_up_accepted / exit_down_accepted -> retired
active -> expired
active -> superseded
```

状态转换由第 7.4 节的转换表唯一决定。其他状态：

- `retired`：Phase E 获支持后，在区间外形成满足路径 B 的新整理时，旧区间退役，新区间下一根生效。
- `expired`：前台年龄达到 `range_max_age` 仍无明确外部趋势；超时不代表方向。
- `superseded`：区间年龄至少 `range_stale_min_age`，最近 `range_stale_bars` 根没有触碰任一边界区域，且区间内部完成一个新的路径 B 区间。新区间作为前台，`parent_range_id` 指向旧区间。

前台区间逐根维护两个属性，不依赖任何假设的状态：

```text
outside_up_streak_t   = C_t > R+epsilon ? outside_up_streak_(t-1)+1 : 0
outside_down_streak_t = C_t < S-epsilon ? outside_down_streak_(t-1)+1 : 0
```

收盘落在容忍带内归零。旧区间的边界继续服务于引用它的事件和假设，直到这些对象自身结束。

### 5.6 因果摆动点与内部 Creek / Ice

```text
摆动高点 j：H_j > max(H_(j-k), ..., H_(j-1))
           H_j >= max(H_(j+1), ..., H_(j+k))
           H_j - min(L_(j+1), ..., L_(j+k)) >= pivot_min_reaction_atr * A_j
确认时间：j+k 收盘；最早用于检测：j+k+1
摆动低点：对称
```

k=`pivot_confirm_bars`。摆动点在确认前不存在，确认后不改变位置。

- 内部 Creek：前台区间原点之后、最近确认的、价格位于 `[M, R+ε]` 的摆动高点。
- 内部 Ice：前台区间原点之后、最近确认的、价格位于 `[S-ε, M]` 的摆动低点。
- 被事件引用时，Creek/Ice 的价格、确认时间与区间版本一起冻结在事件记录中。

`creek_mode=range_and_internal`（默认）时，内部 Creek/Ice 用于内部 JOC/FTI 与内部 LPS/LPSY；`range_only` 时这些事件输出 `not_implemented`。曲线或斜线 Creek 不在 V3 范围内。

## 6. 事实事件规则

本节判断"形态是否发生"；结构假设在第 7 节更新。所有默认数字属于 P 类，见第 10 节。

### 6.0 多根模式与快照环

多根模式从第一根 p 开始，到检测完成根 r 结束（p≤r）。

```text
event_time   = bar_end(p)
detected_at  = bar_end(r)
available_at = r 及所有相关数据的最晚可用时间 + 计算延迟
onset_snapshot = p-1 收盘后的快照：
    range_id, range_version, S, R, epsilon, range_age
    trend_context, direction_evidence_ids, dominance_labels
```

识别器在每根收盘后把该根的快照写入快照环，环长度见 F-15。两类多根模式都从环中读取 p-1 快照：

- 前向观察型（Spring、UT）：在 p 创建观察对象时复制 p-1 快照，之后只读取该副本。
- 回看型（高潮簇）：在完成根 t 确定 p 后，从环中读取 p-1 快照。这不使用任何未来数据。

规则中写明"起始上下文"的条件只读取 `onset_snapshot`。检测完成时新增的证据可以用于当根的新解释，但必须记录为"确认时新增证据"，且不能充当起始上下文。单根事件视为 p=r。

### 6.1 SC / BC：单根版本

```text
trend_context_t = downtrend
L_t < min(L_(t-n_ext), ..., L_(t-1))          # n_ext = climax_new_extreme_window
RVOL_t >= climax_min_rvol
NS_t >= climax_min_ns
CP_t >= sc_min_cp
LW_t >= climax_min_rejection_wick
V_t > 0
```

输出 `SC_FEATURE`，创建 `SC_BOTTOM_CANDIDATE`。不要求 SC 是阴线。BC 对称：上涨背景、新高、`CP_t<=bc_max_cp`、`UW_t>=climax_min_rejection_wick`。

### 6.2 SC / BC：多根高潮簇

以 t 为完成根，依次检查 m=2、m=3，取第一个满足的 m，令 p=t-m+1：

```text
起始上下文（快照环中的 p-1）：trend_context = downtrend
min(L_p, ..., L_t) < min(L_(p-n_ext), ..., L_(p-1))
Effort(p..t) >= cluster_min_effort
(max(H_p..H_t) - min(L_p..L_t)) / A_p >= cluster_min_span_atr
完成根 t：CP_t >= sc_min_cp 或 LW_t >= climax_min_rejection_wick
完成根 t：RVOL_t >= cluster_stop_min_rvol
p..t 内无 data_gap，各根 V>0
```

输出 `SC_CLUSTER_FEATURE`。若 t 同时满足单根规则，只输出单根 SC，高潮簇作为别名关联。BC 高潮簇对称。

高潮（单根或簇）统一保存：

```text
t0 = t（检测完成根，后续窗口从这里计数）
L_SC = min(L_p..L_t)；A_SC = A_p
epsilon_SC = max(boundary_resolution_units*price_resolution, boundary_atr_fraction*A_SC)
Effort_SC = Effort(p..t)；NS_SC_ref = max(NS_p..NS_t)
```

### 6.3 努力与结果背离

```text
单根窄幅高量：RVOL_t >= absorption_min_rvol，NS_t <= absorption_max_ns，V_t>0
三根低推进：  Effort(t-2..t) >= absorption_min_rvol
             abs(C_t-C_(t-3)) / A_(t-2) <= low_progress_max_move_atr
             三根 V>0，数据连续
```

分别输出 `EFFORT_RESULT_NARROW_BAR`、`EFFORT_RESULT_LOW_PROGRESS`，并保存路径距离 `sum(abs(C_i-C_(i-1)))/A`。若存在前台区间且本根触及支撑或阻力区域，另建 `SUPPORT_ABSORPTION_CANDIDATE` 或 `RESISTANCE_ABSORPTION_CANDIDATE`。无区间时只报背离事实。

后续确认：支撑侧在 1～`absorption_response_max_bars` 根内出现 SOS，或出现 `C>O, NetMove>=strong_bar_min_move_atr, CP>=sos_min_cp` 的向上反应，确认 `SUPPORT_RESPONSE_SEQUENCE`；连续两根收盘 `<S-ε` 使候选失效（`INCL`）。阻力侧对称，确认 `RESISTANCE_RESPONSE_SEQUENCE`。

### 6.4 支撑下穿：Spring、深穿与复合事件

**步骤 1：下穿事实（无深度限制）。**

```text
起始上下文：前台区间已建立，range_age >= event_min_range_age
L_p < S-epsilon
C_(p-1) >= S-epsilon
```

输出 `SUPPORT_UNDERCUT`，记录 `depth_p=(S-L_p)/A_p`。

**步骤 2：按深度分流。**

```text
depth_p <= spring_max_depth_atr：SPRING_RECLAIM_WATCH，收回根 r ∈ {p, ..., p+spring_max_pattern_bars-1}
depth_p >  spring_max_depth_atr：输出 DEEP_UNDERCUT，SHAKEOUT_RECLAIM_WATCH，r ∈ {p, ..., p+shakeout_max_pattern_bars-1}
```

Spring 观察期间若后续根使 `(S-min(L_p..L_t))/A_p > spring_max_depth_atr`，在该根输出 `DEEP_UNDERCUT`，观察对象转为深穿观察，起点和窗口仍从 p 计数。

**步骤 3：收回。** `C_r > S+ε`，`CP_r >= spring_min_cp`，p..r 内各根 V>0。

**步骤 4：分型。**

| 条件 | 事件代码 |
|---|---|
| 深穿分支，`C_r <= R+ε` | `SHAKEOUT_RECLAIM`；`max(RVOL_p..RVOL_r) > spring_high_volume_min_rvol` 时加 `shakeout_high_volume` |
| Spring 分支，`C_r <= R+ε`，p..r 每根 `RVOL < spring_low_volume_max_rvol` | `SPRING_LOW_VOLUME` |
| Spring 分支，`C_r <= R+ε`，`max(RVOL_p..RVOL_r) > spring_high_volume_min_rvol` | `SPRING_HIGH_VOLUME` |
| Spring 分支，`C_r <= R+ε`，其他 | `SPRING_OTHER` |
| 任一分支，`C_r > R+ε` | `UNDERCUT_RECLAIM_BREAKOUT`（复合事件，与同根 `BREAKOUT_UP` 关联） |

保存 `L_SP=min(L_p..L_r)`、`Effort_SP=Effort(p..r)`、`max_RVOL`、`OIz(p..r)`、分支与 `onset_snapshot`；`tS=r`。观察窗口结束仍未收回，输出 `UNDERCUT_NOT_RECLAIMED`；向下突破仍按第 6.5 节单独判断。

### 6.5 普通突破、JOC 与 FTI

```text
BREAKOUT_UP：  前台区间已建立且 range_age >= event_min_range_age
               C_(t-1) <= R+epsilon，C_t > R+epsilon
BREAKOUT_DOWN：C_(t-1) >= S-epsilon，C_t < S-epsilon
```

每个普通突破都创建方向有效性假设 `UP_BREAKOUT_VALIDITY` / `DOWN_BREAKOUT_VALIDITY`（第 7.4 节）。同方向已有活动的有效性假设时，只记录新的突破事实并关联到已有假设，不新建假设，不改变已有假设的 tB、边界和期限。

```text
JOC：C_t>O_t，RVOL_t>=strong_break_min_rvol，NS_t>=strong_break_min_ns，
     CP_t>=joc_min_cp，NetMove_t>=strong_bar_min_move_atr
FTI：C_t<O_t，RVOL_t>=strong_break_min_rvol，NS_t>=strong_break_min_ns，
     CP_t<=fti_max_cp，NetMove_t<=-strong_bar_min_move_atr
Gap_t >= gap_flag_min_atr 时追加 gap_breakout
```

事件字段 `strength ∈ {ordinary, joc, fti}`，只用于分组评价，不改变有效性规则。`JOC implies SOS_features` 作为关联保存。

### 6.6 SOS / SOW 与内部 JOC / FTI

```text
SOS：前台区间 range_age >= event_min_range_age，
     C>O，NS>=sos_sow_min_ns，RVOL>=sos_sow_min_rvol，CP>=sos_min_cp，NetMove>=strong_bar_min_move_atr
SOW：对称，C<O，CP<=sow_max_cp，NetMove<=-strong_bar_min_move_atr
```

SOS 不限制收盘位置，突破根也可以同时是 SOS；LPS 来源的去重见第 7.5 节。无前台区间时，同样条件输出 `STRONG_UP_BAR` / `STRONG_DOWN_BAR`，不建立威科夫阶段。

内部 JOC（`creek_mode=range_and_internal`）：

```text
在 t-1 已可用的内部 Creek K，且 K < R-epsilon
C_(t-1) <= K+epsilon，C_t > K+epsilon，C_t <= R+epsilon
t 满足 SOS 条件
```

输出 `INTERNAL_JOC`，冻结 K。若 `C_t > R+ε`，按第 6.5 节记为前台突破。内部 FTI 对称，使用内部 Ice。

### 6.7 阻力上穿：UT、UTAD 与复合事件

第 6.4 节的价格镜像：

- `RESISTANCE_OVERSHOOT`：起始上下文中前台区间已建立且年龄达标，`H_p > R+ε`，`C_(p-1) <= R+ε`。
- 深度 ≤ `spring_max_depth_atr`：UT 观察，r ∈ {p, …, p+spring_max_pattern_bars-1}；更深：`DEEP_OVERSHOOT` 与深穿观察，r ∈ {p, …, p+shakeout_max_pattern_bars-1}。
- 收回：`C_r < R-ε`，`CP_r <= ut_max_cp`，模式内各根 V>0。
- 分型：`UT_FEATURE`、`UPTHRUST_DEEP_REJECTION`、`C_r < S-ε` 时的复合事件 `UPTHRUST_REVERSAL_BREAKDOWN`。UT 不设成交量必要门槛，量能作为属性保存。
- 保存 `H_UT=max(H_p..H_r)`、`Effort_UT=Effort(p..r)`；`tU=r`。

**UTAD 候选** 只在起始上下文同时满足 `range_age >= mature_range_age` 且已有 `supply_dominance_by_rules` 时创建。刺破当根或收回当根才形成的供应占优不满足条件。JOC 后的失败先记为 `FAILED_UP_BREAKOUT`；只有失败模式本身满足 UT 条件才追加 UT，只有满足 UTAD 起始上下文才追加 UTAD。

### 6.8 事件代码汇总

| 类别 | 代码 |
|---|---|
| 高潮 | `SC_FEATURE`、`SC_CLUSTER_FEATURE`、`BC_FEATURE`、`BC_CLUSTER_FEATURE` |
| 背离 | `EFFORT_RESULT_NARROW_BAR`、`EFFORT_RESULT_LOW_PROGRESS` |
| 下穿 | `SUPPORT_UNDERCUT`、`DEEP_UNDERCUT`、`SPRING_LOW_VOLUME`、`SPRING_HIGH_VOLUME`、`SPRING_OTHER`、`SHAKEOUT_RECLAIM`、`UNDERCUT_RECLAIM_BREAKOUT`、`UNDERCUT_NOT_RECLAIMED` |
| 上穿 | `RESISTANCE_OVERSHOOT`、`DEEP_OVERSHOOT`、`UT_FEATURE`、`UPTHRUST_DEEP_REJECTION`、`UPTHRUST_REVERSAL_BREAKDOWN`、`OVERSHOOT_NOT_REJECTED` |
| 突破 | `BREAKOUT_UP`、`BREAKOUT_DOWN`（strength：ordinary / joc / fti）、`FAILED_UP_BREAKOUT`、`FAILED_DOWN_BREAKOUT`、`BREAKOUT_HOLD_CONFIRMED` |
| 强弱 | `SOS`、`SOW`、`INTERNAL_JOC`、`INTERNAL_FTI`、`STRONG_UP_BAR`、`STRONG_DOWN_BAR` |
| 测试 | `ST_OBSERVED`、`TEST_OBSERVED`、`UT_TEST_OBSERVED`、`LPS_PULLBACK_OBSERVED`、`LPSY_RALLY_OBSERVED` |
| 序列 | `SC_STOPPING_SEQUENCE`、`BC_STOPPING_SEQUENCE`、`SUPPORT_RESPONSE_SEQUENCE`、`RESISTANCE_RESPONSE_SEQUENCE`、`SPRING_TEST_CONFIRMED`、`SPRING_SOS_SEQUENCE`、`UT_TEST_CONFIRMED`、`UT_SOW_SEQUENCE`、`UTAD_WEAKNESS_SEQUENCE` |
| 数据 | `data_gap`、`index_source_mismatch`、`ex_post_weights`、`constituent_change`、`delivery_season`、`warmup_incomplete` |

## 7. 后续确认、失效与超时

### 7.1 通用生命周期、计数起点与依附关系

```text
candidate -> confirmed | invalidated | expired
confirmed -> invalidated | expired
```

`confirmed` 表示"截至确认时，规定的后续规则已经满足"，不代表吸筹等解释被永久证明。失效或超时的假设不复活；后续证据创建新假设并关联旧事件。

同一根 K 线的处理顺序：

1. 对已有假设执行失效检查。
2. 检测新事实，创建新假设。
3. 对本根新建的假设，执行计数起点为 `INCL` 的失效检查。
4. 对所有未失效的假设执行确认检查。
5. 对到期且未确认的假设执行超时。
6. 对依附对象执行级联（见下）。

同根失效与确认冲突时失效优先。窗口末根可以确认，末根处理完仍未满足才超时。所有转换追加记录。

**依附关系：** 创建时登记 `parent_hypothesis_id`。来源对象 `invalidated` 时，依附候选同根 `invalidated`；来源对象未确认而 `expired` 时，依附候选同根 `expired`；来源对象已确认时，依附候选按自身期限运行。V3 中的依附关系：突破来源的 LPS/LPSY 依附于对应的突破有效性假设；Phase D 规则支持依附于它引用的 LPS/LPSY。

| 起点 | 含义 |
|---|---|
| `PRE` | 创建前的必要条件，不满足就不创建 |
| `INCL` | 从创建根开始计数，创建根本身可以计入 |
| `POST` | 从创建根的下一根开始计数 |

| 失效条件 | 起点 |
|---|---|
| SC 闭环形成前收盘 `<L_SC-ε_SC`；BC 对称 | `INCL` |
| 吸收候选：连续收盘穿越边界外侧 | `INCL` |
| Spring/Shakeout 测试：收盘 `<L_SP-ε`；连续收盘 `<S-ε` | `POST`（从 tS+1） |
| UT 测试：收盘 `>H_UT+ε`；连续收盘 `>R+ε` | `POST`（从 tU+1） |
| 突破有效性：连续回区间；深度回区间 | `POST`（从 tB+1） |
| LPS/LPSY 候选：低点（高点）越过参照位 | `POST`（从 tX+1） |
| LPS/LPSY 确认后的位置失效 | `POST`（从确认根+1） |
| UTAD：收盘 `>H_UT+ε`；连续收盘 `>R+ε` | `POST`（从 tU+1） |
| 结构假设：相反方向占优连续成立 | `POST` |

"连续"指相邻有效 K 线都满足；收盘落在边界容忍带内是中性状态，使连续计数归零。

每种来源类型在每个区间同时最多一个活动候选；已有活动候选时，新的同类来源只记录事实。

### 7.2 SC→AR→ST：止跌闭环与路径 A 区间

使用第 6.2 节保存的 `t0, L_SC, A_SC, ε_SC, Effort_SC, NS_SC_ref`。

**AR_RALLY，确认根 t ∈ t0+ar_window_min ～ t0+ar_window_max：**

```text
j = t0+1..t 中最高 H 所在根（同值取最早）
H_j - L_SC >= range_min_width_atr * A_SC
最近 ar_reversal_close_steps 个收盘变化全部为负（需 steps+1 个收盘）
H_j - C_t >= ar_reversal_atr * A_SC
```

首个满足的 t 为 `ar_confirmed_at`，冻结 `S=L_SC`、`R=H_j`、`A_ref=A_SC`、`ε=ε_SC`；宽度超过上限时 AR 事实保留，区间候选以 `width_too_large` 拒绝。

**ST 测试形态，q ∈ ar_confirmed_at+st_window_min ～ ar_confirmed_at+st_window_max：**

```text
S-epsilon <= L_q <= S + st_near_support_atr * A_SC
C_q >= S-epsilon
RVOL_q <= st_max_effort_vs_climax * Effort_SC
NS_q <= st_max_ns_vs_climax * NS_SC_ref
V_q > 0
```

输出 `ST_OBSERVED`。ST 不要求是阳线；允许低点比 SC 略低，但不超过 ε。窗口内可以有多个 ST_OBSERVED，最新的一个成为待反应的 q。

**ST 后反应，r ∈ q ～ q+st_response_max_bars：**

```text
r = q： C_q > O_q，C_q >= S+epsilon，C_q > H_(q-1) + confirm_step      # 同根严格子类型
r > q： C_r > max(H_q, ..., H_(r-1)) + confirm_step，C_r > S+epsilon
```

首个满足的 r 确认 `SC_STOPPING_SEQUENCE`；区间宽度有效时建立路径 A 区间（原点为高潮第一根，建立时间为 r）。Phase A 获规则支持，方向仍可演变为再派发。

**失效：** 闭环形成前任意收盘 `<L_SC-ε_SC`（`INCL`）。**超时：** t0+ar_window_max 仍未确认 AR；或 `ar_confirmed_at+st_window_max+st_response_max_bars` 仍未确认 ST 反应。超时不转成派发结论。

BC 闭环对称，确认 `BC_STOPPING_SEQUENCE`：AR_REACTION 取最低 L 与连续上升收盘；ST 接近上沿 `R-st_near_support_atr*A_BC <= H_q <= R+ε`、`C_q <= R+ε`；反应为收盘低于测试以来最低价减 `confirm_step` 且 `<R-ε`。

### 7.3 Spring / Shakeout → Test / SOS

使用第 6.4 节保存的 `tS, L_SP, Effort_SP, 分支`。复合事件 `UNDERCUT_RECLAIM_BREAKOUT` 不进入本节，按突破路径处理。

**测试形态，q ∈ tS+test_window_min ～ tS+test_window_max：**

```text
L_q >= L_SP + epsilon                                # 较高低点
S-epsilon <= L_q <= S + test_near_support_atr * A_q   # 确实回到支撑附近
RVOL_q <= test_max_rvol
NS_q <= test_max_ns
分支为 HIGH / OTHER / SHAKEOUT 时：RVOL_q <= test_max_effort_vs_spring * Effort_SP
V_q > 0
```

输出 `TEST_OBSERVED`。测试形态可以是阴线、十字线或阳线。低量 Spring 本身努力已经很低，只使用绝对上限。

**测试后反应，r ∈ q ～ q+test_response_max_bars：** 规则与第 7.2 节 ST 反应相同。确认后输出 `TEST_RESPONSE_CONFIRMED` 与 `SPRING_TEST_CONFIRMED`；r=q 时加 `same_bar` 子类型。

| Spring 分支 | 增强证据的方式 |
|---|---|
| `SPRING_LOW_VOLUME`、`SPRING_OTHER` | Test 反应确认；或 tS+spring_sos_window_min～tS+spring_sos_window_max 出现 SOS，输出 `SPRING_SOS_SEQUENCE` |
| `SPRING_HIGH_VOLUME`、`SHAKEOUT_RECLAIM` | 只能通过 Test 反应确认；SOS 不能替代测试 |

所有分支在收回时即为 Phase C 候选。**失效（`POST`）：** 收盘 `<L_SP-ε`；或 `spring_fail_consecutive_closes` 根连续收盘 `<S-ε`。**超时：** `tS+test_window_max+test_response_max_bars` 仍未确认。Spring 事实始终保留。

### 7.4 UT → 测试 / SOW

第 7.3 节的镜像，使用第 6.7 节保存的 `tU, H_UT, Effort_UT`，参数与 Spring 测试共用。

**测试形态，q ∈ tU+test_window_min ～ tU+test_window_max：**

```text
H_q <= H_UT - epsilon                                # 较低高点
R - test_near_support_atr * A_q <= H_q <= R+epsilon   # 确实回到阻力附近
RVOL_q <= test_max_rvol
NS_q <= test_max_ns
UT 模式内任一根 RVOL >= spring_low_volume_max_rvol 时：RVOL_q <= test_max_effort_vs_spring * Effort_UT
V_q > 0
```

输出 `UT_TEST_OBSERVED`。**测试后反应，r ∈ q ～ q+test_response_max_bars：**

```text
r = q： C_q < O_q，C_q <= R-epsilon，C_q < L_(q-1) - confirm_step
r > q： C_r < min(L_q, ..., L_(r-1)) - confirm_step，C_r < R-epsilon
```

确认后输出 `UT_TEST_CONFIRMED`。tU+spring_sos_window_min～tU+spring_sos_window_max 出现 SOW 时输出 `UT_SOW_SEQUENCE`。**失效（`POST`）：** 收盘 `>H_UT+ε`；或 `spring_fail_consecutive_closes` 根连续收盘 `>R+ε`。**超时：** `tU+test_window_max+test_response_max_bars` 仍未确认。

### 7.5 突破有效性：维持、失败、超时与状态转换

适用于所有 `UP_BREAKOUT_VALIDITY` / `DOWN_BREAKOUT_VALIDITY`，与突破强度无关。以向上为例，tB 为创建该假设的突破根，期限为 `tB+breakout_validity_window`，`f=breakout_fail_deep_fraction`：

```text
维持确认：期限内任一根 outside_up_streak_t >= breakout_hold_closes
          （tB 处 streak=1；非外侧根归零；再次越界不移动 tB、边界或期限）
回测确认：以本假设为来源的 LPS 在期限内确认（第 7.6 节）
失败（POST）：连续 breakout_fail_inside_closes 根 C < R-epsilon
             或任意一根 C < S + f*(R-S)
```

例：R=100，ε=0.2，收盘依次 100.3（tB，streak=1）、100.1（容忍带内，归零）、100.4、100.5、100.6。第三根起 streak 为 1、2、3，在第五根维持确认；100.4 那根同时是一个新的 `BREAKOUT_UP` 事实，关联到原假设，不新建假设。

**唯一状态转换表（向上；向下对称）：**

| 触发 | 有效性假设 | 前台区间 |
|---|---|---|
| `BREAKOUT_UP` 创建假设 | `candidate` | `active → exit_up_candidate` |
| 维持确认或 LPS 确认（先到者） | `confirmed`；维持确认时输出 `BREAKOUT_HOLD_CONFIRMED` | `exit_up_accepted` |
| 失败（候选或已确认状态下） | `invalidated`；输出 `FAILED_UP_BREAKOUT` | `active` |
| 期限处理完仍未确认 | `expired` | 当根收盘在 `[S-ε,R+ε]` 内：`active`；否则保持 `exit_up_candidate` |
| 假设已过期、区间仍为 `exit_up_candidate` 时 `outside_up_streak >= breakout_hold_closes` | 不复活旧假设 | `exit_up_accepted`，输出 `BREAKOUT_HOLD_CONFIRMED`（`hypothesis_id=null`，标记 `late_acceptance`） |
| `exit_up_candidate` 且无活动假设时收盘回到 `[S-ε,R+ε]` | — | `active` |
| 反向 `BREAKOUT_DOWN` | 原假设 `invalidated` | 按向下转换 |

已确认的假设继续受失败条件监测，直到区间 `retired`/`superseded` 或发生反向突破。超时不等于失败；未出现 BUEC 也不等于假突破。

JOC 可记录附加证据 `shallow_body_retrace`：令 `pullback_low` 为 tB 之后至今最低价，`max(0,C_tB-pullback_low)/(C_tB-O_tB) <= body_retrace_fraction`。它不替代任何边界条件。

### 7.6 LPS 族：回调中的支撑点

LPS 描述 SOS 或向上突破之后、供应减弱的回调及其后的重新推进。

| 来源（来源根 tX），优先级从上到下 | 参照位 L* | 子类型 | 依附 |
|---|---|---|---|
| 新建 `UP_BREAKOUT_VALIDITY` 的突破根 | 前台 R | 回调低点 `<=R+ε`：`LPS_AT_CREEK`（即 BUEC）；`>R+ε`：`LPS_ABOVE_CREEK` | 依附该有效性假设 |
| `INTERNAL_JOC` | 冻结的内部 Creek K | `LPS_INTERNAL` | 无 |
| `SOS` 且 `C_tX <= R+ε` | tX 时已可用的最近摆动低点，且不低于 S；没有则用 S | `LPS_INTERNAL` | 无 |

同一根 K 线满足多个来源时，只按最高优先级创建一个候选。关联到已有有效性假设的再次越界不是新来源。容忍度统一使用前台区间的 ε。

**回合处理：**

```text
1. 回调起点 b：tX 之后第一个 C_b < C_(b-1) 的 K 线。
   本回合截止 deadline = min(b + lps_max_pullback_bars, tX + lps_window_max)
2. 对每根 t（b < t <= deadline）按顺序：
   a. 价格失效：L_t < L* - epsilon -> 候选 invalidated
   b. 反应检查：若 t-b >= lps_min_pullback_bars，以 b..t-1 为回调段，
      回调证据与反应条件全部满足 -> 确认，r = t
   c. 未确认且 C_t > max(C_tX, ..., C_(t-1))（来源以来收盘新高）-> 本回合结束，下一根起寻找新的 b
   d. 否则回调段延续到 t
3. deadline 处理完仍未确认：deadline = tX+lps_window_max 时候选 expired；否则本回合结束，下一根起寻找新的 b
```

创高可能正是合格的反应根，所以先做 b 步确认，只有不满足完整条件的创高才结束回合。回调段长度、努力和波幅按完整的 b..t-1 统计；最低点位置只用于价格判定和子类型：

```text
q = b..t-1 中最低 L 所在根（同值取最早）

回调证据：
  Effort(b..t-1) <= lps_max_pullback_effort                # 不含来源根
  mean(NS_b..NS_(t-1)) <= lps_max_pullback_ns
  (max(H_tX..H_(b-1)) - L_q) / A_b >= lps_min_retrace_atr

反应条件（t 根）：
  C_t > H_(t-1) + confirm_step
  C_t > L* + epsilon
  NetMove_t >= lps_response_min_move_atr
```

反应不要求放量：供应减少后，较小的需求也可能推动价格。`RVOL_t` 作为属性保存。

**确认：** 输出对应子类型的 LPS。来源为突破且来源假设仍为 `candidate` 时，按第 7.5 节转换表同时确认来源假设。来源假设已终止时，依附规则已使本候选终止，不会出现确认已过期对象的情况。`lps_window_max > breakout_validity_window` 时，超出部分只在来源已因外侧维持而确认时才可能用到。

**严格子类型 `strict_v1`：** `LPS_AT_CREEK` 另外满足以下条件时加标，作为策略研究子集，不是 LPS 的定义：

```text
t-b >= strict_backup_min_pullback_bars
mean(RVOL_b..RVOL_(t-1)) < strict_backup_max_mean_rvol
mean(NS_b..NS_(t-1)) <= strict_backup_max_mean_ns
OLS_slope(RVOL_b..RVOL_(t-1)) < 0
C_(t-1) < O_(t-1)，C_t > O_t，O_t <= C_(t-1)，C_t >= O_(t-1)     # 阳线实体吞没前一根阴线
RVOL_t >= backup_turn_min_rvol
```

**确认后的当前有效性（`POST`，从 r+1）：**

| 条件 | 转换 |
|---|---|
| 收盘 `< L_q - ε` | `invalidated`（支撑点失守） |
| 来源突破假设失效 | `invalidated`（依附） |
| 同方向新的 LPS 确认 | 旧 LPS 当前标签 `expired`，由新 LPS 承接 |
| 确认后超过 `lps_current_max_age` 根且无新的同方向 LPS | `expired` |

LPS 事实始终保留；以上只改变"当前仍有效"的状态。内部来源和外部来源都适用。

### 7.7 LPSY 族

第 7.6 节的镜像：

| 来源，优先级从上到下 | 参照位 L* | 子类型 |
|---|---|---|
| 新建 `DOWN_BREAKOUT_VALIDITY` 的突破根 | 前台 S | 反弹高点 `>=S-ε`：`LPSY_AT_ICE`；`<S-ε`：`LPSY_BELOW_ICE` |
| `INTERNAL_FTI` | 冻结的内部 Ice | `LPSY_INTERNAL` |
| `SOW` 且 `C_tX >= S-ε` | 最近摆动高点，且不高于 R；没有则用 R | `LPSY_INTERNAL` |

反弹起点为 tX 后第一个 `C_b > C_(b-1)`；价格失效为 `H_t > L*+ε`；回合结束条件为来源以来收盘新低；q 为反弹段最高 H 所在根；回撤幅度为 `(H_q - min(L_tX..L_(b-1)))/A_b`；反应根要求 `C_t < L_(t-1)-confirm_step`、`C_t < L*-ε`、`NetMove_t <= -lps_response_min_move_atr`；确认后 `C > H_q+ε` 使当前有效性失效。多空使用相同初始参数只为建立基线，可以分别校准。

### 7.8 UTAD 后续证据

UTAD 候选在 tU 创建。tU+utad_response_window_min～tU+utad_response_window_max 内出现 SOW 或已确认的 LPSY，确认 `UTAD_WEAKNESS_SEQUENCE`。失效（`POST`）：收盘 `>H_UT+ε`，或连续 `breakout_fail_inside_closes` 根收盘 `>R+ε`。窗口结束未确认则超时。

### 7.9 事件重叠

- 同一量价事实的多个别名只计一条证据，保存关联关系：JOC 与 SOS；复合事件与突破；单根 SC 与高潮簇；同一 SOW 产生的 `UT_SOW_SEQUENCE` 与 `UTAD_WEAKNESS_SEQUENCE`。
- LPS/LPSY 来源按第 7.6、7.7 节的优先级去重。
- 已有活动 SC 候选时，新的 SC 特征只记事实，不更换原候选的低点。
- 已有前台区间时，新完成的高潮闭环作为附加证据，不覆盖 S/R。

### 7.10 结构解释的退出规则

| 假设 | 退出或降级 |
|---|---|
| SC/BC 止跌 / 止涨 | 闭环后连续两根收盘穿越高潮极值的失效侧，或相反方向区间状态变为 `exit_*_accepted` 时 `invalidated`；进入后续阶段时当前阶段标签 `expired` |
| 支撑 / 阻力吸收 | 第 6.3 节失效规则继续适用；最近反应证据超过 `absorption_current_evidence_max_age` 根则 `expired` |
| Spring / Shakeout / UT 测试 | 第 7.3、7.4 节价格失效继续适用；超过 `bias_sequence_recent_bars` 根没有新的测试、SOS/SOW 或 LPS/LPSY 则 `expired` |
| UTAD | 第 7.8 节 |
| LPS / LPSY | 第 7.6 节确认后的当前有效性表 |
| 吸筹 / 派发 / 再吸筹 / 再派发 | 相反方向占优连续 `structural_opposite_persistence_bars` 根成立，或相反方向区间状态变为 `exit_*_accepted` 时 `invalidated`；原方向证据不再满足且近期信号过期时 `expired` |
| Phase C / D / E | 引用的底层对象终止时同步撤销；Phase D 规则支持依附于其引用的 LPS/LPSY；没有独立证据时返回未知 |

区间方向标签（第 8.3 节）是逐根重算的证据；结构假设是有历史的对象，二者不共用字段。

## 8. 因果波浪与区间方向证据

### 8.1 波浪划分

本节受 Weis 波浪量启发，不宣称与任何商业指标一致。只使用收盘价。

- 初始化：以第一个有效收盘为锚点，冻结 `rho = wave_reversal_atr * ATR_previous`；收盘离锚点达到 rho 才确定第一波方向，此前输出 `wave_initializing`。锚点根的成交量不归第一波。
- 上行波维护已知最高收盘 E；`C_t <= E-rho` 时在 t 确认反转，旧波结束于 t-1，`confirmed_at=t`；反转根 t 的成交量归新波，新波基准价为 `C_(t-1)`。下行波对称。
- 每个新波的 rho 在建立时冻结。相等即满足反转；成交量不按事后极值重新分配。
- 形成中的波可以展示，但不进入统计。含 `data_gap` 的波标记 `quality_limited`，不进入方向统计；含 `constituent_change` 的波保留并附带标记。

不用成交量来确认波浪反转：用成交量划分波浪、再用这些波浪比较成交量是循环论证。碎波风险通过 `wave_reversal_atr` 的敏感性分析（1.0 / 1.5 / 2.0）报告。

### 8.2 波浪字段

令 `d=+1`（上行）或 `-1`（下行），`A0=atr_at_start`，波内 K 线为 a..e：

```text
wave_id, direction, start_time, end_time, confirmed_at, available_at, status
start_reference_close, extreme_close, end_close=C_e, reversal_threshold
bar_count = e-a+1
raw_volume_sum, baseline_volume_sum
wave_activity_rate = Effort(a..e)                                  # 活动率，与长度无关
wave_cum_effort = CumEffort(a..e)                                  # 累计努力
peak_excursion = d*(extreme_close-start_reference_close)/A0        # 对已完成波恒 > 0
signed_end_displacement = d*(end_close-start_reference_close)/A0   # 可以为负
giveback = peak_excursion - signed_end_displacement
path_distance = sum(abs(C_i-C_(i-1)), i=a..e)/A0
directional_efficiency = signed_end_displacement / path_distance
wave_speed = peak_excursion / bar_count
mean_bar_ns = mean(NS_a..NS_e)
wave_oiz = OIz(a..e)                                               # 仅描述
```

已完成波的 `giveback*A0 < reversal_threshold`。活动率与累计努力的预测价值需要分别验证。

### 8.3 区间方向证据

评估前提：区间年龄 ≥ `mature_range_age`；取区间原点之后、所有收盘在 `[S-ε,R+ε]` 内的最近至多 `wave_stats_max_completed` 个已完成波，每个方向至少 `wave_min_completed_each_direction` 个。不满足时 A、P、D 三组为 `not_evaluable`。统计量取各方向中位数，`k=wave_dominance_ratio`。

| 组 | 取值 | 规则 |
|---|---|---|
| A 活动 | `up_more_active` / `down_more_active` / `balanced` | `med_up(wave_activity_rate) >= k*med_down(...)` 为前者，反之为后者，其余 balanced。**只描述活动方向，不单独代表需求或供应** |
| P 价格结果 | `demand` / `supply` / `neutral` | demand：`med_up(peak_excursion) >= k*med_down(peak_excursion)` 且 `med_up(signed_end_displacement) > med_down(signed_end_displacement)`；supply 上下互换 |
| B 边界响应 | `demand` / `supply` / `neutral` | 最近 `bias_sequence_recent_bars` 根内，一侧已确认序列至少 1 个且多于另一侧 |
| D 反向结果递减 | `down_waning` / `up_waning` / 无 | down_waning：窗口内至少 3 个下行波，最新下行波的 peak_excursion < 此前各下行波的中位数；up_waning 对称 |

B 组计入的序列：需求侧 `SUPPORT_RESPONSE_SEQUENCE`、`SPRING_TEST_CONFIRMED`、`SPRING_SOS_SEQUENCE`；供应侧 `RESISTANCE_RESPONSE_SEQUENCE`、`UT_TEST_CONFIRMED`、`UT_SOW_SEQUENCE`（与 `UTAD_WEAKNESS_SEQUENCE` 同源时只计一次）。

**需求侧路径（可多选）：**

| 路径 | 条件 | 对应的经典表现 |
|---|---|---|
| `demand_push` | P=demand 且 A=up_more_active | 上涨活跃、推进有效 |
| `supply_exhaustion` | P=demand，A 不是 up_more_active，D=down_waning | 上涨不靠更高的活动率也能推进，向下推进逐步减弱 |
| `boundary_supported` | P=demand 且 B=demand | 价格结果与支撑测试同向 |
| `absorption_response` | A=down_more_active，P ∈ {demand, neutral}，B=demand 且其序列含 `SUPPORT_RESPONSE_SEQUENCE` 或 `SPRING_TEST_CONFIRMED` | 下跌活跃但结果不大，支撑处出现有效响应 |

```text
demand_dominance_by_rules = 至少一条需求侧路径成立 AND P != supply AND B != supply
supply_dominance_by_rules = 对称（路径 supply_push / demand_exhaustion / boundary_supported / distribution_response）
```

两者不会同时成立。A=down_more_active 本身不否决需求侧：大的下行活动配合小的下行结果，可以是吸收的证据。没有任何路径成立时输出 `neutral`，即使某一组偏向一侧。输出字段为 `dominance` 与 `dominance_paths`，不输出合并分数。各路径仍是待验证规则，不能换算为上涨概率。

OI 在 `oi_mode=annotate` 时只随波浪保存，不参与占优。

### 8.4 结构假设

| 假设 | 入口条件 |
|---|---|
| `phase_b_range` | 前台区间年龄 ≥ `mature_range_age`，方向可以未知 |
| `spring_supply_test_candidate` | 存在未失效的 Spring/Shakeout 假设；不要求方向占优 |
| `ut_demand_test_candidate` | 存在未失效的 UT 测试假设 |
| `accumulation_candidate` | 成熟区间 + `demand_dominance_by_rules` + 最近 `bias_signal_recent_bars` 根内至少一个触发事件：`SOS`、`INTERNAL_JOC` 或当前有效的 LPS |
| `distribution_candidate` | 对称：`supply_dominance_by_rules` + `SOW`、`INTERNAL_FTI` 或当前有效的 LPSY |

重复计票控制：触发事件使用的事件 ID 不再计入 B 组；去掉这些 ID 后占优仍须成立。

更大尺度背景在区间建立时计算并冻结：同周期前 `larger_context_window` 个收盘变化，`SignedDelta >= larger_context_min_displacement_atr` 且 `ER >= larger_context_min_er` 为上涨背景，对称为下跌背景。上涨背景中的吸筹候选显示为 `reaccumulation_candidate`，下跌背景中的派发候选显示为 `redistribution_candidate`。

## 9. 实时状态、记录格式与处理顺序

### 9.1 市场状态与阶段

主状态 `uptrend / downtrend / range / transition / unknown`，按优先级：

1. 核心数据缺失、输入不合格或预热不足：`unknown`，说明缺项。
2. 前台区间内，收盘位于 `[S-ε,R+ε]`：`range`，另报 `trend_now`。
3. 区间外，区间状态不是 `exit_*_accepted`，或 `trend_now` 与突破方向不一致：`transition`。
4. 区间状态为 `exit_*_accepted` 且 `trend_now` 同向：`uptrend` 或 `downtrend`。
5. 无前台区间：`trend_now` 成立则报方向，否则 `transition`。

非数据质量导致的状态切换，需要目标状态连续出现 `regime_persistence_bars` 次；切换前保存 `pending_regime`。数据失效立即切到 `unknown`。事实事件不等待状态防抖。

阶段证据映射（吸筹方向；派发方向对称）：

- **Phase A 获规则支持：** SC/BC 闭环。
- **Phase B 候选：** `phase_b_range`。
- **Phase C 候选：** Spring / Shakeout / 成熟 UT；Test 反应、UT 测试或 UTAD 弱势序列增强证据。无 Spring/UT 的 Phase C：`not_implemented`。
- **Phase D 候选：** 存在当前有效的 LPS，且它对应一次 SOS：来源是 `SOS` 或 `INTERNAL_JOC`；或来源是突破且突破强度为 `joc`；或来源是普通突破且来源根之前 `bias_signal_recent_bars` 根内有 SOS。单独的 JOC/FTI 也提供 Phase D 候选。
- **Phase D 获规则支持：** Phase D 候选，且在 LPS 确认后 `phase_d_followthrough_bars` 根内至少满足一项：
  - 推进：收盘超过该 LPS 回调前的最高价 `max(H_tX..H_(b-1))`；
  - 方向：`demand_dominance_by_rules` 成立。

  满足后，只要所引用的 LPS 当前有效，支持就持续；LPS 被新 LPS 承接时，引用转移到新 LPS；LPS 失效时支持同步撤销。这些条件是研究选择，不预设增加条件必然更优。
- **Phase E（向上）获规则支持**，全部满足：

```text
区间状态 exit_up_accepted
outside_up_streak_t >= phase_e_min_outside_closes
(C_t - R) / A_ref >= phase_e_min_distance_atr
trend_now: SignedDelta_n > 0 且 Slope_n > 0 且 ER_n >= phase_e_min_er
```

向下 Phase E 使用 `outside_down_streak`、`(S-C_t)/A_ref`、负向 SignedDelta 和负向 Slope。ER 不带方向，必须与有方向的位移和斜率同时使用。

多个阶段解释可以并存或未知。`phase_supported_by_rules` 必须附证据 ID、确认时间和失效条件。

### 9.2 事件与假设记录

```text
事件 occurrence：
  event_id, event_type, strength, aliases, linked_event_ids
  index_id, data_profile, index_source, index_method_version, timeframe
  event_time, detected_at, available_at, data_asof
  pattern_start, pattern_end, onset_snapshot_id
  reference_range_id, reference_range_version, frozen_S, frozen_R, frozen_epsilon
  frozen_internal_level（如有）
  observed_metrics: RVOL, Effort, CumEffort, NS, CP, LW, UW, NetMove, Gap, depth, OIz, oi_quadrant
  baseline_mode, standard_baseline_metrics, quality_flags, threshold_margins, rejection_reasons
  rule_version, parameter_version, data_version

结构 hypothesis：
  hypothesis_id, hypothesis_type, parent_hypothesis_id, source_event_ids, competing_hypotheses
  status, created_at, confirmed_at, invalidated_at, expires_at, status_available_at
  state_transition_history
  confirmation_conditions, invalidation_conditions（含计数起点 PRE/INCL/POST）
  supporting_evidence_ids, opposing_evidence_ids, evidence_groups, dominance_paths
```

`threshold_margins` 保存每个数值条件与阈值的差距，`rejection_reasons` 保存未成立的条件，供覆盖率和参数敏感性研究使用。

例：t100 疑似 SC，t112 才完成 ST 反应：SC 事实时间保留 t100，结构确认在 t112；t100 的实时状态不能改写为"当时已确认止跌"。

### 9.3 实时输出示例

```json
{
  "asof": "2026-10-21T10:30:00+08:00",
  "index_id": "EXAMPLE_WEIGHTED",
  "data_profile": "weighted_index",
  "index_source": "vendor_placeholder",
  "timeframe": "15m",
  "rule_version": "3.0",
  "parameter_version": "v3-default-unvalidated",
  "market_regime": "range",
  "trend_now": "downtrend",
  "structure": {
    "primary": "spring_supply_test_candidate",
    "alternatives": ["phase_b_range", "redistribution_candidate"],
    "dominance": "neutral",
    "dominance_paths": [],
    "evidence_groups": {"A": "balanced", "P": "demand", "B": "neutral", "D": null},
    "phase_candidate": ["B", "C"]
  },
  "range": {"id": "R001", "version": 1, "support": 3612.40, "resistance": 3698.85, "epsilon": 2.15},
  "evidence": [{"event_id": "E0042", "type": "SPRING_LOW_VOLUME", "oi_quadrant": "up_oi_flat"}],
  "next_confirmation": ["TEST_OBSERVED 后的测试反应", "SOS（低量 Spring 允许）"],
  "invalidation": ["收盘低于 Spring 最低价减 epsilon", "连续两根收盘低于 S-epsilon"],
  "probability": null,
  "quality": "ok"
}
```

示例只说明格式：P 组偏向需求但没有路径成立时，方向为 `neutral`，主结构是 `spring_supply_test_candidate`。生产输出同时保存机器可检查的条件 ID。

### 9.4 每根 K 线的处理顺序

```text
1. 校验输入契约与数据质量（第 4.2、4.7 节）与预热。
2. 取得 t-1 快照：指标、trend_context、前台区间、已可用的摆动点、方向证据。
3. 对已有假设执行失效检查。
4. 检测本根完成的单根与多根事实（起始上下文按第 6.0 节从快照环取得）；创建新候选和观察对象。
5. 对本根新建候选执行 INCL 失效检查。
6. 对未失效候选执行确认检查，包括窗口末根。
7. 对到期未确认的候选追加超时；执行依附级联。
8. 用本根更新指标状态（两种基准模式的 ATR 与 Bv、摆动点确认、outside_streak）、波浪和方向证据。
9. 按第 7.5 节转换表更新区间状态；更新市场状态与阶段；区间建立、退役、替代登记新版本，下一根生效。
10. 将本根快照写入快照环。
11. 写出事件、假设转换、实时快照及实际可用时间。
```

第 8 步新形成的证据可以用于本根的新结构解释（第 9 步），但不能作为本根任何事件的起始上下文。

<!-- V3-CONTINUE -->
