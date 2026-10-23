# 维科夫加权指数实时行情识别规范 V4.1

版本日期：2026-10-23<br>
规则版本：`4.1`；参数版本：`v4.1-default-unvalidated`<br>
前置文档：[V3.0 规范](WYCKOFF_MARKET_RECOGNITION_V3.md)、[V3.0 评审](WYCKOFF_MARKET_RECOGNITION_V3_REVIEW.md)  
适用范围：单一期货品种的加权指数序列。不覆盖实际合约、换月与可成交价格。  
版本性质：完整替代 V3.0 的工程基础规范；阈值尚未完成样本外验证

> 本规范要求识别器"确定、可复现"：相同数据、规则版本和参数版本产生相同输出。这不代表对吸筹、派发、反转或未来收益有确定判断。文中默认数字全部是研究起点。

## 0. V4 相对 V3 的变化

V4 以 V3 当前工作区版本为基础，落实 V3 评审中的 R1—R8 及三项口径澄清。修订集中在执行顺序、对象生命周期、证据可用性和评价时间，不扩大市场事件类型。正文是完整规范，实施时不需要拼接前代正文。逐条处置见附录 A。

| 类别 | 主要变化 | 章节 |
|---|---|---|
| 当根计算 | 确认前计算当根特征与 `outside_streak_t`；供下一根使用的基准另行更新 | 4.4、9.4 |
| 突破接纳 | 引入独立 `acceptance_id`；普通、回测及晚接纳共用失败出口；不复活过期假设 | 5.5、7.5 |
| 生命周期 | 价格失效立即级联；末根确认后才处理候选超时；已确认对象到期、联动确认和引用承接有明确顺序 | 7.1、7.6、9.4 |
| 测试优先级 | 先检查旧 ST/Test/UT Test 的当根反应，再激活新观察；同根事实仍完整保存 | 7.2—7.4 |
| 子类型与精度 | 内部 LPS 固定为 INTERNAL；最小确认距离统一采用含等号比较 | 4.3、7.6、7.7 |
| 证据有效性 | B 组仅使用当前有效、当时可用且未过时的证据；路径用真/假/不可评估三值逻辑 | 8.3 |
| 快照与 OI | 明确 `context_for_next_bar`；OI 缺失、晚到、零分母独立处理 | 4.6、5.1、6.0 |
| 评价与验收 | 从真实可用后的首根完整 K 线评价；增加处理顺序、生命周期、三值和边界用例 | 11 |

V4.1 补齐 V4.0 评审发现的区间绑定、候选来源、期限与描述特征交界，默认阈值数值不变。V4.0、V4.1 与 V3 的输出不能混用；规则与约束变化必须按新版本重新回放。前代样本结果不代表 V4.1 已完成样本外验证。逐项处置见附录 A.4。

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

| 理论概念 | V4 可观测替代量 | 解释边界 |
|---|---|---|
| 供求法则 | 波幅、收盘位置、净位移、波段推进与回吐 | 指数 OHLCV 只反映成分合约整体的表现，不能观测挂单与参与者意图 |
| 因果法则 | 区间年龄、宽度、触碰、内部量价结构 | 区间根数不决定后续涨跌幅；V4 不实现点数图目标价 |
| 努力与结果 | 标准化活动率、累计努力与波幅、净位移、波段结果的组合 | 大努力小结果是背离证据，吸收方向需要上下文和后续反应 |

指数成交量是成分活动的聚合代理，其含义取决于 `volume_method`（第 4.5 节）。在固定统计口径下，OI 变化反映未平仓合约总量的净变化，不能据此逐笔判断交易双方开平仓或主动方向；指数 OI 还可能受成分范围和统计权重变化影响。V4 把 OI 作为描述特征保存，默认不参与任何判定。

Composite Man 是理解大资金行为的分析模型。V4 不输出"已证明某机构吸筹"之类的身份或订单行为判断。

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
| F：V4 固定约定 | 公式、枚举、时间计数、边界与波浪方法 | 同一版本内不能更改；修改需升级规则版本 |
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

### 2.3 F 类 V4 固定约定

| 编号 | 内容 |
|---|---|
| F-01 | 正式输出只使用已收盘 K 线；盘中输出单列为 `provisional` |
| F-02 | `Spread=H-L`；真实波幅单独计算；成交量基准和 ATR 比较基准排除当前 K 线 |
| F-03 | ATR 使用 Wilder 平滑；`baseline_mode=robust` 时 ATR 输入与成交量基准按第 4.4 节截尾；均线用 SMA；中位数偶数样本取中间两数均值 |
| F-04 | 严格按公式使用 `>`、`>=`、`<`、`<=` |
| F-05 | 时间窗口按有效交易 K 线计数，上下界都包含；"N 个价格变化"需要 N+1 个收盘 |
| F-06 | 前台区间的 S/R 是固定水平区域；内部 Creek/Ice 使用第 5.6 节的因果摆动点，不使用曲线拟合 |
| F-07 | 状态转换只追加；依附边按类型处理失效、候选超时和当前有效期终止；联动确认与引用承接原子提交（第 7.1 节） |
| F-08 | 波浪使用第 8 节的因果划分，成交量不按事后极值重新分配 |
| F-09 | Spring/UT 的量能分组、深穿分界和收回窗口是工程分类，不宣称为 Wyckoff 或 Pruden 的固定数字 |
| F-10 | 自动识别的 LPS/LPSY 只覆盖第 7.6、7.7 节列出的子类型；其他形态标记为未覆盖 |
| F-11 | 跨根或跨时段比较量能时使用标准化活动率（第 4.5 节），不直接比较原始成交量 |
| F-12 | 每条失效计数必须登记计数起点（第 7.1 节） |
| F-13 | 价格以 `price_resolution` 为单位的定点整数比较；不使用合约最小跳动 |
| F-14 | 突破外侧维持使用滚动连续计数（第 5.5、7.5 节） |
| F-15 | 快照环长度 = `max(3, spring_max_pattern_bars, shakeout_max_pattern_bars) + 1`，随参数推导 |
| F-16 | 当前根特征与区间计数先计算后判定；ATR/Bv 对下一根的更新不反过来改变当根事件指标 |
| F-17 | 最小确认距离按 `>= reference+confirm_step` / `<= reference-confirm_step` 比较，`confirm_step_units` 为正整数 |
| F-18 | 内部 LPS/LPSY 不按前台外边界重新分成 AT/ABOVE/BELOW；子类型由来源类型和对应参照位决定 |
| F-19 | 不可评估按三值逻辑传播；缺少必需证据不默认为 neutral |
| F-20 | 一个收盘事务结束后才发布对外快照；确认检查前已确定终止的对象不得再确认，不发布临时状态作为当前支持 |

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
| acceptance | 已接纳突破的当前有效性对象；有独立 ID、冻结边界和失败出口，不是新的市场形态 |
| `confirmation_deadline_bar` | 候选最后允许确认的根；该根机会处理完仍未确认才过期 |
| valid_through_bar | 已确认对象当前有效期的最后一根；在下一根开始时过期，除非已合法承接 |
| context_for_next_bar | 截至快照所属根计算的趋势背景，供下一根事件使用；与该根 `trend_now_asof_bar` 值相同 |
| 起始上下文 | 多根模式第一根之前的快照，见第 6.0 节 |
| 因果摆动点 | 需要后续 k 根确认的局部高低点，确认前不可用，见第 5.6 节 |
| `not_evaluable` | 必要数据或指标不可用，不是"条件未触发" |
| `not_implemented` | 本版本未覆盖，不是"市场没有该事件" |

### 3.2 威科夫事件术语

| 缩写 | 中文 | V4 用法 |
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
| LPS / LPSY | 最后支撑点 / 最后供应点 | 子类型见第 7.6、7.7 节 |

Spring #1/#2/#3 等编号在不同教学体系中定义不一致。V4 事件代码只描述可观测特征：`SPRING_LOW_VOLUME`、`SPRING_HIGH_VOLUME`、`SPRING_OTHER`、`SHAKEOUT_RECLAIM`。编号别名只能用于显示，并须在配置中声明所用的教学体系。

深穿是按价格深度划分的，本身没有量能门槛。深穿增加结构失败的风险；如果同时高量（`shakeout_high_volume` 标记），需要进一步检查供应与价格结果。V4 保守地要求所有深穿收回都通过后续测试才增强吸筹解释，这是研究选择，不代表深穿一定伴随大量供应。

### 3.3 SOS、JOC、BU、BUEC、LPS 的关系

- SOS 描述力量表现，可在区间内部；JOC 额外要求越过事前已知的阻力。普通低量上破是 `BREAKOUT_UP`，不满足 JOC 的量价条件。
- BU 是突破后的回撤过程；BUEC 要求回到旧阻力附近。
- LPS 描述 SOS 或突破后的回调中供应减弱、随后需求重新推进的支撑点。V4 自动识别三种子类型：回到小溪边（`LPS_AT_CREEK`，即 BUEC）、在边界上方止跌（`LPS_ABOVE_CREEK`）、区间内部 SOS 之后（`LPS_INTERNAL`）。
- 突破有效性由第 7.5 节的外侧维持或失效规则单独判断，不依赖是否出现 BUEC。
- 一次 SOS 加一个合格 LPS 只构成 Phase D 候选；规则支持还需要后续推进或独立方向证据（第 9.1 节）。

### 3.4 Phase A—E

| 阶段 | 吸筹方向 | 派发方向 | V4 输出原则 |
|---|---|---|---|
| A | 下跌停止：SC、AR、ST | 上涨停止：BC、AR、ST | 止跌或止涨证据，不等于永久反转 |
| B | 区间内部运行 | 区间内部运行 | 区间成熟不等于方向已知 |
| C | 剩余供应的测试，可能出现 Spring | 剩余需求的测试，可能出现 UT/UTAD | Spring 和 UTAD 都不是必经路径；无 Spring/UT 的 Phase C 本版本未实现（`phase_c_without_spring=not_implemented`），阶段保持未知 |
| D | SOS 与较弱回调 / LPS | SOW 与较弱反弹 / LPSY | 先给候选，满足第 9.1 节额外条件才获规则支持 |
| E | 离开区间后的同向拉升 | 离开区间后的同向下跌 | 必须满足方向条件（第 9.1 节） |

V4 保存 `phase_candidate` 与 `phase_supported_by_rules`，允许多次 ST、多次 LPS、无 Spring 的结构和方向暂不明的区间。没有检测到 Spring 不等于没有 Phase C。

## 4. 数据契约、计算定义与时间口径

### 4.1 指数输入契约

V4 只支持 `data_profile=weighted_index`。所有状态按 `index_id + timeframe` 维护。

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
  open_interest（可选）, oi_available_at（有 OI 时必需）
  constituent_coverage（可选）
```

未知的权重、成分范围或量能含义不能默认为已知。供应商指数可以不在本地重建，但必须登记供应商的定义和修订口径。

### 4.2 输入合格性检查

| 情形 | 处理 |
|---|---|
| `index_source` 未声明，或实际读取的序列与声明来源不符 | 整条序列 `not_evaluable`，原因 `index_source_mismatch` |
| 输入是主力连续序列、复权序列或去跳空序列（即使命名为 `*_index`） | 同上。这类序列没有成分与权重，不是加权指数 |
| 声明的指数缺失 | 返回缺项；不得自动回退到其他序列 |
| `ohlc_method` 为对各成分的 H、L 分别加权 | 拒绝。成分极值未必同时发生，会制造不存在的宽幅和影线 |
| 只有成分 K 线、无法恢复同步极值 | 属于另一个 `data_profile`，需注明近似方法并单独验证，不在 V4 范围内 |
| 某根使用的权重取值时点晚于该根的计算时刻（例如日内指数事后用当日收盘持仓重算） | 拒绝作实时输入（I-12）；只能标记 `ex_post_weights` 用于事后描述 |

同步路径指数的反例：两个等权成分，一个时点分别为 110、90，另一时点为 90、110。同步指数始终为 100；分别加权各自的 H/L 会得到 H=110、L=90。

基本合法性：`H>=max(O,C)`、`L<=min(O,C)`、`H>=L`、`V>=0`、`OI>=0`、时间有序、主键不重复。不要求价格永远大于零。

### 4.3 价格精度

- 价格保留数据源原始精度，按 `price_resolution` 的整数倍做定点表示与比较。
- 若原始价格不是 `price_resolution` 的整数倍，判为契约不符并拒绝，不自行取整。
- 成分合约的最小跳动和显示精度都不影响规则。例：成分最小跳动为 1，指数为 100.37 时按 100.37 比较。
- 阈值在比较前不先取整。

`confirm_step = confirm_step_units * price_resolution`，`confirm_step_units>=1` 且为整数。"至少超过前高一个确认距离"用 `C>=H_ref+confirm_step`；向下用 `C<=L_ref-confirm_step`。例如前高100.00、分辨率0.01、单位数1，100.01即达标。S/R区域的越界仍按正文的严格 `>` / `<`，不得把所有比较批量改为含等号。

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

每根 t 先从已提交的 t−1 状态取得 A_t、Bv_t，用原始 t 根计算 RVOL_t、NS_t、NetMove_t 等临时特征，供当根全部规则读取。随后计算 ATR_t 以及 Bv_(t+1) 的输入状态，只服务于趋势现状和下一根。即使 ATR_t 已在内存临时算出，也不能覆盖当根事件的 A_t。

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

只在 `oi_method` 已声明、所需原值与历史样本齐全、分母大于0时计算；否则 `not_evaluable`，记录 `oi_method_unknown / oi_missing / oi_history_insufficient / oi_zero_scale / oi_not_yet_available`。OIz 不可评估时，`oi_quadrant` 也不可评估，不能落入“其他”列而变成 flat。不能以0填补缺失，也不输出无穷大。

每个 OI 特征保存独立 `feature_available_at`，不早于参与计算的所有 OI 的 `oi_available_at`。晚到 OI 只追加描述记录，不回写较早快照，也不改变已发布价格事件的 available_at。`oi_mode=annotate` 默认不等待 OI 才发布量价事件；若未来启用 `filter`，受 OI 条件影响的结果必须等待 OI 真正可用。季节性标记只在有明确数据定义时使用，不能作为方向判定。

单根、多根事件与波浪的 OIz 使用同一窗口计算和原因传播。方法未声明优先为 `oi_method_unknown`；窗口历史不足为 `oi_history_insufficient`；参与数据明确缺失为 `oi_missing`。完整数值的可用时间晚于当前读取时间时，先输出 `oi_not_yet_available`，不能提前暴露未来数据造成的零分母结果；数据已到达且分母为0才输出 `oi_zero_scale`。质量缺口和非法窗口使用对应工程原因。完整描述存储保留原值和独立时间，实时消费者按当前 `decision_time` 生成视图；过去单根不可用的 OI 在本次多根/波浪计算时已到达，可以用于本次新描述，但不能回写过去快照。波浪的量价可用时间与 OIz 可用时间分别保存，读取实时波记录必须使用 `realtime_view(decision_time)`。

### 4.7 零量、缺口、成分变动与修订

- 真实零量 K 线保留，但不触发低量 Spring、Test、ST 或吸收事件；缺失 K 线不能填零。
- 交易时段内缺失应有的 K 线时，受影响窗口暂停并输出 `data_gap`，修复前不用不完整窗口确认事件。
- 成分范围变化（新合约进入、旧合约到期退出）当根标记 `constituent_change`；成交量和 OI 可能因此发生台阶变化。标记 K 线照常参与计算，但以它为确认根的强事件附带该标记。
- 指数没有单一涨跌停价，`limit_state=not_applicable`，不推导指数封板价。成分受限比例可以作为将来的质量字段，本版本 `not_implemented`。主要成分封板时可能出现 `H=L` 的指数 K 线，按上文 `H=L` 规则处理。
- 数据修订时保存原实时版本与修订版本；实时回放使用当时的版本。

### 4.8 时间口径

`t0+a～t0+b` 指第 a 至第 b 根后续有效 K 线，两端都包含。单根首次可用时间不早于 `max(bar_end, received_at)+计算延迟`；多根条件取所有相关数据的最晚到达时间。迟到的 K 线只能在到达后影响实时判断。预热从序列第一根合格 K 线计数。

`bar_end` 是行情时间，`available_at` 是实际可用时间，二者不能互换。依赖链的可用时间取全部输入、父对象状态和本次计算/提交完成时间的最大值。同一收盘事务中的新事实可供后续步骤使用，但只能随完整事务提交后对外发布。预测评价按第 11.4 节独立登记起点，不能默认使用“信号根之后的整根 K 线”。

## 5. 趋势背景、交易区间与内部参照位

### 5.1 趋势背景与快照下标

先定义唯一函数 `trend_at(u)`，只使用截至 u 已到达且可用的数据，n=`trend_window`：

```text
Delta_n(u) = (C_u-C_(u-n)) / ATR_u
ER_n(u) = abs(C_u-C_(u-n)) / sum(abs(C_i-C_(i-1)), i=u-n+1..u)
Slope_n(u) = OLS_slope(C_(u-n+1), ..., C_u) / ATR_u

downtrend: C_u < SMA_fast_u, C_u < SMA_slow_u,
           Delta_n(u) <= -trend_min_displacement_atr,
           Slope_n(u) <= -trend_min_abs_slope_atr,
           ER_n(u) >= trend_min_er
uptrend:   对称

trend_now_t = trend_at(t)
trend_context_t = snapshot_(t-1).context_for_next_bar = trend_at(t-1)
```

ER 分母为0且价格序列完整时定义为0；ATR无效或历史不足时趋势不可评估。Delta/ER使用n个价格变化、n+1个收盘，OLS使用n个收盘。事件t的趋势分母仍是ATR_(t-1)，与A_t一致。

提交快照u时同时写入 `asof_bar=u`、`trend_now_asof_bar=trend_at(u)`、`context_for_next_bar=trend_at(u)` 及其实际可用时间。两个字段是同一个已知值的不同用途，不能把 `trend_context_u`（只到u−1）存成下一根的背景。模式p的起始趋势永远读取 `snapshot_(p-1).context_for_next_bar`。

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

状态转换由第 7.5 节的转换表唯一决定。`exit_*_accepted` 必须指向同方向、同区间版本且当前为 confirmed 的 `acceptance_id`；没有该对象时不得维持 accepted。其他状态：

- `retired`：当前仍为 confirmed 的同区间版本、同方向 `acceptance_id` 曾获得 Phase E 规则支持，且在该接纳方向的区间外形成满足路径 B 的新整理时，旧区间退役，新区间下一根生效。不要求整理完成当根仍满足 Phase E 的 ER 条件；失败/过期接纳或其他区间版本的历史支持不能复用。
- `expired`：前台年龄达到 `range_max_age` 仍无明确外部趋势；超时不代表方向。
- `superseded`：区间年龄至少 `range_stale_min_age`，最近 `range_stale_bars` 根没有触碰任一边界区域，且区间内部完成一个新的路径 B 区间。新区间作为前台，`parent_range_id` 指向旧区间。

`retired / superseded / expired` 的区间停止产生新前台事件，其活动接纳对象、突破假设与当前有效性标签以 `range_closed` 过期并级联；事实和冻结边界保留。仅仍有独立观察期限且明确允许使用旧参照位的事实观察可完成，但不能修改新前台区间状态。每次转换必须以自身 `reference_range_id/version` 为目标，不使用一个可变的全局前台指针更新其他区间。

前台区间逐根维护两个属性，不依赖任何假设的状态；新版本生效前初始化为0，不追补其建立之前的连续根数：

```text
outside_up_streak_t   = C_t > R+epsilon ? outside_up_streak_(t-1)+1 : 0
outside_down_streak_t = C_t < S-epsilon ? outside_down_streak_(t-1)+1 : 0
```

收盘落在容忍带内归零。t根计数在第9.4节的临时计算阶段完成，确认和超时都读取这个t根值。新建立的区间从下一根开始计数。区间的晚接纳也必须创建接纳对象，详见第7.5节。

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

`creek_mode=range_and_internal`（默认）时，内部 Creek/Ice 用于内部 JOC/FTI 与内部 LPS/LPSY；`range_only` 时这些事件输出 `not_implemented`。曲线或斜线 Creek 不在 V4 范围内。

## 6. 事实事件规则

本节判断"形态是否发生"；结构假设在第 7 节更新。所有默认数字属于 P 类，见第 10 节。

### 6.0 多根模式与快照环

多根模式从第一根 p 开始，到检测完成根 r 结束（p≤r）。

```text
event_time   = bar_end(p)
detected_at  = bar_end(r)
available_at = r 及所有相关数据的最晚可用时间 + 计算延迟
onset_snapshot = p-1 收盘后的快照：
    asof_bar=p-1, context_for_bar=p
    range_id, range_version, S, R, epsilon, range_age
    context_for_next_bar, direction_evidence_ids, dominance_labels
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
起始上下文：snapshot_(p-1).context_for_next_bar = downtrend
min(L_p, ..., L_t) < min(L_(p-n_ext), ..., L_(p-1))
Effort(p..t) >= cluster_min_effort
(max(H_p..H_t) - min(L_p..L_t)) / A_p >= cluster_min_span_atr
完成根 t：CP_t >= sc_min_cp 或 LW_t >= climax_min_rejection_wick
完成根 t：RVOL_t >= cluster_stop_min_rvol
p..t 内无 data_gap，各根 V>0
```

输出 `SC_CLUSTER_FEATURE`，在该方向没有已有候选时创建与单根版本相同的止跌假设。若 t 同时满足单根规则，只输出单根 SC，高潮簇作为别名关联，不创建第二个假设。BC 高潮簇对称。

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

吸收假设绑定完成根 t 当前生效的前台区间，冻结该区间的 `reference_range_id/version`、S/R/ε。背离事实仍保留模式起始 p−1 的 `onset_snapshot` 和事实参照，不将当前区间伪装为模式开始前已知。起始快照没有区间或是旧区间时，也只允许假设使用完成根当前区间。

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

普通突破在没有同方向活动候选或接纳对象时创建 `UP_BREAKOUT_VALIDITY` / `DOWN_BREAKOUT_VALIDITY`（第7.5节）。已有候选则关联候选，已有confirmed acceptance则关联接纳；不新建假设，不改变既有tB、边界或期限。

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

SOS 不限制收盘位置，突破根也可以同时是 SOS；LPS 来源的去重见第 7.6 节。无前台区间时，同样条件输出 `STRONG_UP_BAR` / `STRONG_DOWN_BAR`，不建立威科夫阶段。

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
| 接纳有效性 | `BREAKOUT_ACCEPTANCE_INVALIDATED`；接纳对象通过acceptance_id引用，mode为hold/pullback/late_hold |
| 强弱 | `SOS`、`SOW`、`INTERNAL_JOC`、`INTERNAL_FTI`、`STRONG_UP_BAR`、`STRONG_DOWN_BAR` |
| 测试 | `ST_OBSERVED`、`TEST_OBSERVED`、`UT_TEST_OBSERVED`、`LPS_PULLBACK_OBSERVED`、`LPSY_RALLY_OBSERVED` |
| 序列 | `SC_STOPPING_SEQUENCE`、`BC_STOPPING_SEQUENCE`、`SUPPORT_RESPONSE_SEQUENCE`、`RESISTANCE_RESPONSE_SEQUENCE`、`TEST_RESPONSE_CONFIRMED`、`SPRING_TEST_CONFIRMED`、`SPRING_SOS_SEQUENCE`、`UT_TEST_CONFIRMED`、`UT_SOW_SEQUENCE`、`UTAD_WEAKNESS_SEQUENCE` |
| 回调确认 | `LPS`、`LPSY`，子类型见第 7.6、7.7 节；`strict_v1` 仅为附加属性 |
| 数据 | `data_gap`、`index_source_mismatch`、`ex_post_weights`、`constituent_change`、`delivery_season`、`warmup_incomplete` |

## 7. 后续确认、失效与超时

### 7.1 通用生命周期、计数起点与依附关系

事实 occurrence 永久保留。结构 hypothesis 使用以下状态：

```text
candidate -> confirmed | invalidated | expired
confirmed -> invalidated | expired
```

confirmed 表示规定证据已经满足，不是未来保证。终止对象不复活。接纳对象 acceptance 在满足接纳条件的事务中直接以 confirmed 建立，此后只能 invalidated 或 expired；它是当前有效性对象，不是新增市场形态。

**两种期限：**

- `confirmation_deadline_bar`：有确认过程的候选最后可确认的根。先完成该根合法确认，仍未确认才 expired。第8.4节吸筹/派发结构假设保存 null，不参与第6步确认超时，也不因缺口延长一个不存在的期限。
- `valid_through_bar`：已确认对象当前有效期的最后一根。在 `t>valid_through_bar` 的第一根开始时 expired，并先级联、后检查依附确认。LPS 可用至 r+lps_current_max_age。

价格失效立即级联；不能先确认子对象、事务末尾再撤销。候选超时保留末根机会。每次依附确认都先检查父对象当前状态及实际可用时间。没有固定当前有效期的对象保存 `valid_through_bar=null`，仍受自身价格失效和区间关闭约束；不能拿原候选确认期限替代确认后的有效期。

#### 7.1.1 依附类型与联动事务

登记 `lifecycle_parent_ref={object_type,id}`；`source_event_ids`、`source_hypothesis_id` 只是溯源，不自动构成生命周期边。

| 对象 | 生命周期依附 | 父对象终止时 |
|---|---|---|
| 外部来源 LPS/LPSY 候选 | 对应突破假设，candidate/confirmed 均可 | 父失效立即 invalidated；父过期立即 expired。父已确认后不再受原确认期限限制，子候选继续受自身期限限制 |
| 已确认的普通突破假设 | 对应 acceptance | 同步失效/过期 |
| 外部来源已确认 LPS/LPSY | 确认事务中改为 acceptance | 同步终止；自身位置失守/有效期结束也会终止 |
| 内部 LPS/LPSY | 无突破父对象 | 自身位置、期限及区间关闭决定终止 |
| Phase D 规则支持 | 当前引用的 LPS/LPSY | 引用终止立即撤销，合法承接除外 |
| Phase E 规则支持 | acceptance | 接纳终止时撤销；当下价格/趋势条件不成立也结束当前阶段支持 |

**LPS反馈确认事务：** 合格LPS与仍为candidate的来源突破一起确认：创建acceptance，确认突破和LPS，并重新绑定依附边，全部原子提交。维持与LPS同根满足时，先登记维持接纳，再将LPS加入同一接纳的依据，不创建第二个acceptance。记录优先级不表示推断了盘中先后。

**LPS承接事务：** 仅同一区间版本、同方向、旧LPS当根仍有效且新LPS确认时，旧LPS才以replaced过期。旧Phase D引用仅在新LPS也符合第9.1节来源条件时转移；否则结束旧支持，新对象重新走候选。承接与新LPS确认在第9.4节第5步一起完成，先准备新引用再使旧对象过期，不发布中间空引用，也不留到方向计票后再替换。旧LPS当根已价格失效或超过有效期时，不能通过新对象复活旧Phase D ID。

所有转换按对象ID和区间版本执行，不依赖集合遍历顺序。生命周期图不得成环；acceptance记录来源突破仅作溯源，不能同时把它设为生命周期父对象。

#### 7.1.2 计数起点

| 起点 | 含义 |
|---|---|
| PRE | 创建前必要条件 |
| INCL | 从创建根开始计数 |
| POST | 从登记起始根之后一根计数；必须保存具体根索引 |

| 条件 | 起点 |
|---|---|
| SC/BC闭环前高潮极值收盘失守 | INCL，从高潮完成根t0 |
| 吸收候选连续越界 | INCL，从候选创建根 |
| Spring/Shakeout测试失效 | POST，从tS+1 |
| UT测试与UTAD失效 | POST，从tU+1 |
| 突破候选失败 | POST，从tB+1 |
| acceptance失败 | POST，从accepted_bar+1；创建时失败连续计数为0 |
| LPS/LPSY候选参照位失守 | POST，从来源tX+1；包括尚无b、起点b和反应根r |
| LPS/LPSY确认后位置失守 | POST，从r+1 |
| 结构假设相反占优持续 | POST，从结构创建根下一根 |

连续计数中任何不满足根都归零。先得到t根临时值，再判定。

每个区间、方向、来源类型最多一个candidate；confirmed对象不阻止后续新来源。突破有效性同区间同方向最多一个candidate/confirmed对象，同一区间最多一个confirmed acceptance。SC/BC尚无区间时按index_id+timeframe+方向限制候选，不使用不存在的range_id。不同来源的LPS同根满足确认时，按外部突破、内部突破、内部SOS/SOW的优先级选择当前标签；全部合格形态事实保留，未选中的候选以 `not_selected_same_bar` 过期，不反复承接，也不重复触发阶段支持。

Spring/UT 测试每同区间版本、同方向最多一个待确认路径。新收回模式属于不同分支，或新极值向失效侧超过旧极值 ε 时，在第4步将旧 candidate 以 `new_pattern_replaced` 过期，建立独立新路径，冻结新分支、极值、努力、起点和期限。它是候选替换，不改变第7.3/7.4节收盘价格失效条件。同分支且未超过 ε 的新事实只关联旧路径，不改原冻结参照或总期限；confirmed 路径不因新模式替换，已终止对象不复活。

#### 7.1.3 多次测试观察的优先级

ST、Spring Test、UT Test各路径保存一个pending_observation，含q、response_deadline_bar和冻结参照：

1. 先处理路径价格失效；旧观察窗口已在t之前结束则不再检查。
2. 用t根检查仍有效旧q的反应。t也符合新测试形态时，旧q不先替换。
3. 新测试事实照常记录；旧q已确认闭环时，新事实不再重复确认该闭环。
4. 旧q未确认且新测试仍在观察窗口内，才激活新q，再检查r=q的严格反应。
5. 观察期限末根无反应则清除该观察；父路径仍有观察窗口可以等新测试，不延长父路径总期限。

闭环首次确认后清空pending_observation；后续合格依据只追加到该闭环，不再次发布同一个确认转换。历史观察、未采用原因与已确认来源ID保留。相同事实别名按稳定ID去重。

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

宽度拒绝只针对路径 A 区间，止跌/止涨假设仍继续观察 ST 和反应，保存 `range_rejected=width_too_large`；合法闭环与 Phase A 可以确认而不建立区间。

**ST 测试形态，q ∈ ar_confirmed_at+st_window_min ～ ar_confirmed_at+st_window_max：**

```text
S-epsilon <= L_q <= S + st_near_support_atr * A_SC
C_q >= S-epsilon
RVOL_q <= st_max_effort_vs_climax * Effort_SC
NS_q <= st_max_ns_vs_climax * NS_SC_ref
V_q > 0
```

输出 `ST_OBSERVED`。ST不要求阳线，允许低点略低但不超过ε。窗口内可以有多个观察，按第7.1.3节先检查旧q的当根反应；旧q未确认时才激活最新观察，不能先覆盖旧q。

**ST 后反应，r ∈ q ～ q+st_response_max_bars：**

```text
r = q： C_q > O_q，C_q >= S+epsilon，C_q >= H_(q-1) + confirm_step      # 同根严格子类型
r > q： C_r >= max(H_q, ..., H_(r-1)) + confirm_step，C_r > S+epsilon
```

首个满足的 r 确认 `SC_STOPPING_SEQUENCE`；区间宽度有效时建立路径 A 区间（原点为高潮第一根，建立时间为 r）。Phase A 获规则支持，方向仍可演变为再派发。

**失效：** 闭环形成前任意收盘 `<L_SC-ε_SC`（`INCL`）。**超时：** t0+ar_window_max 仍未确认 AR；或 `ar_confirmed_at+st_window_max+st_response_max_bars` 仍未确认 ST 反应。超时不转成派发结论。

BC闭环对称，确认BC_STOPPING_SEQUENCE：AR_REACTION取最低L与连续上升收盘；ST接近上沿，`R-st_near_support_atr*A_BC<=H_q<=R+ε`、`C_q<=R+ε`；后续反应为 `C_r<=min(L_q..L_(r-1))−confirm_step` 且 `C_r<R−ε`。同根反应要求阴线、`C_q<=L_(q-1)−confirm_step` 和 `C_q<=R−ε`，仍优先检查旧观察。

### 7.3 Spring / Shakeout → Test / SOS

使用第6.4节的tS、L_SP、Effort_SP和分支，在收回根创建SPRING_SUPPLY_TEST_HYPOTHESIS，初始candidate。复合事件UNDERCUT_RECLAIM_BREAKOUT不进入本节，按突破路径处理。观察与响应按第7.1.3节选择，旧q优先。

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

**测试后反应，r∈q～q+test_response_max_bars：** 使用第7.2节含等号的ST反应公式。确认后假设转为confirmed，输出TEST_RESPONSE_CONFIRMED与SPRING_TEST_CONFIRMED，共享一个confirmation_trigger_id；r=q加same_bar。低量/其他分支通过允许的SOS确认时，同一假设转为confirmed并输出SPRING_SOS_SEQUENCE。首次确认后保留全部合法依据，但不重复创建相同闭环。

| Spring 分支 | 增强证据的方式 |
|---|---|
| `SPRING_LOW_VOLUME`、`SPRING_OTHER` | Test 反应确认；或 tS+spring_sos_window_min～tS+spring_sos_window_max 出现 SOS，输出 `SPRING_SOS_SEQUENCE` |
| `SPRING_HIGH_VOLUME`、`SHAKEOUT_RECLAIM` | 只能通过 Test 反应确认；SOS 不能替代测试 |

所有分支在收回时即为 Phase C 候选。**失效（`POST`）：** 收盘 `<L_SP-ε`；或 `spring_fail_consecutive_closes` 根连续收盘 `<S-ε`。**超时：** LOW/OTHER 在 `tS+max(test_window_max+test_response_max_bars, spring_sos_window_max)` 末根仍未确认；HIGH/SHAKEOUT 只用 `tS+test_window_max+test_response_max_bars`。测试观察窗口不因另一确认路径延长。Spring 事实始终保留。

### 7.4 UT → 测试 / SOW

第7.3节的镜像，使用tU、H_UT、Effort_UT，在收回根创建UT_DEMAND_TEST_HYPOTHESIS，初始candidate，参数与Spring测试共用。复合事件UPTHRUST_REVERSAL_BREAKDOWN按突破路径处理，不进入本测试路径。多次观察采用旧q优先。

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
r = q： C_q < O_q，C_q <= R-epsilon，C_q <= L_(q-1) - confirm_step
r > q： C_r <= min(L_q, ..., L_(r-1)) - confirm_step，C_r < R-epsilon
```

Test反应确认后，所属假设confirmed并输出UT_TEST_CONFIRMED；允许窗口内的SOW也可使UT假设confirmed，输出UT_SOW_SEQUENCE。若同一个SOW还确认UTAD序列，共享confirmation_trigger_id。**失效（POST）：** C>H_UT+ε，或spring_fail_consecutive_closes根连续C>R+ε。**超时：** `tU+max(test_window_max+test_response_max_bars, spring_sos_window_max)` 处理完仍未确认。深上穿是否必须Test不作对称高量假设，本版本仍允许UT的SOW路径，按分型分组验证。

### 7.5 突破假设、接纳对象与区间状态

#### 7.5.1 有界突破假设

没有同方向活动候选或接纳时，普通突破创建UP_BREAKOUT_VALIDITY / DOWN_BREAKOUT_VALIDITY，与强度无关。tB为创建根，确认期限为tB+breakout_validity_window。已有同方向candidate时重新越界只关联事实，不改tB、边界或期限；已有同方向confirmed acceptance时关联接纳对象，不另建假设。

向上候选满足任一即可确认：

```text
期限内 outside_up_streak_t >= breakout_hold_closes
或：依附本候选的LPS在期限末根机会结束前确认
```

当根计数先计算。tB初次越界计1，中性根归零，之后重新累计。两种依据同根成立时，primary_basis=hold，但保存两项依据。

候选失败从tB+1检查：向上连续breakout_fail_inside_closes根C<R−ε，或任意根C<S+f*(R−S)，f=breakout_fail_deep_fraction；向下连续C>S+ε，或任意根C>R−f*(R−S)。失败立即终止依附候选。期限末根检查后未确认才expired并级联。

#### 7.5.2 独立接纳当前有效性

每次接纳都建立对象：

```text
acceptance_id, direction, reference_range_id, reference_range_version
status = confirmed
accepted_bar, accepted_at, available_at
mode = hold / pullback / late_hold
source_hypothesis_id = 正常接纳的来源ID；late_hold固定为空，旧尝试列入prior_attempt_ids
source_event_ids, prior_attempt_ids
frozen_S, frozen_R, frozen_epsilon, failure_inside_streak
primary_basis, confirmation_bases
```

接纳时收盘必在对应边界外侧，失败计数初始化为0。从accepted_bar+1独立检查与候选相同的失败条件，不依赖旧假设是否存在。

建立接纳时，同一事务内执行第7.10节由相反方向接纳触发的结构失效及其级联，再处理后续依附确认与当前证据计票；不能等到下一根才撤销相反结构。

- 接纳失败：acceptance invalidated，输出BREAKOUT_ACCEPTANCE_INVALIDATED；正常接纳的confirmed突破假设同步invalidated，输出FAILED_UP_BREAKOUT或FAILED_DOWN_BREAKOUT，依附对象立即处理。
- 晚接纳失败：旧expired假设保持expired，失败事实引用acceptance_id，不修改或复活旧假设。
- 区间retired/superseded/expired：acceptance expired，原因range_closed，按依附规则传播。
- 同根反向突破：先终止旧方向，再建新方向；旧对象回调不能覆盖新方向状态。

#### 7.5.3 晚接纳与唯一转换表

旧候选已在更早的根过期、区间仍为exit_up_candidate、没有同方向活动候选/接纳，且当前滚动计数达标时，创建mode=late_hold的acceptance。输出BREAKOUT_HOLD_CONFIRMED，带late_acceptance=true和acceptance_id。旧假设不复活。向下对称。

| 触发（向上；向下对称） | 对象变化 | 区间状态 |
|---|---|---|
| 新BREAKOUT_UP且无同向活动对象 | 创建candidate | exit_up_candidate |
| 同向再次越界，已有候选 | 关联原假设，期限不变 | 保持 |
| 同向再次越界，已有接纳 | 关联acceptance，不新建假设 | exit_up_accepted |
| 候选维持或LPS确认 | 原子创建acceptance，假设confirmed | exit_up_accepted，绑定acceptance_id |
| 未接纳候选失败 | invalidated并级联，输出FAILED_UP_BREAKOUT | active；同根反向事件可随后改变状态 |
| 期限结束未确认 | expired并级联 | 收盘在[S−ε,R+ε]内则active，否则保留exit_up_candidate |
| 满足晚接纳 | 新建late_hold acceptance | exit_up_accepted，绑定新ID |
| acceptance价格失败 | acceptance及依附对象终止 | active，清除当前acceptance_id |
| 无活动对象且exit_up_candidate收回区间 | 不复活对象 | active |
| 反向突破/区间关闭 | 按生命周期结束旧对象，再处理新方向/版本 | 新方向状态或closed状态 |

exit_up_accepted与当前confirmed向上acceptance一一对应；向下相同。价格暂回区间但尚未满足失败条件，接纳可暂时有效；市场状态仍按第9.1节位置和趋势判断。

例：R=100、ε=0.2，收盘100.3、100.1、100.4、100.5、100.6，第五根及时确认。晚接纳之后连续收99.5、99.4，独立接纳对象失败，区间回active，无需仍活动的旧假设。

JOC附加证据shallow_body_retrace：tB之后最低价为pullback_low，`max(0,C_tB-pullback_low)/(C_tB-O_tB)<=body_retrace_fraction`。没有正实体或尚无后续根时不可评估；它不替代边界条件。

### 7.6 LPS 族：回调中的支撑点

来源tX收盘就创建候选并冻结L*、ε和区间版本。b是候选内部回调阶段起点，不是候选创建时刻。

| 来源，优先级从上到下 | 参照位L* | source_kind | 依附 |
|---|---|---|---|
| 新建UP_BREAKOUT_VALIDITY的突破根 | 外部R | external_breakout | 对应突破假设 |
| INTERNAL_JOC | 内部Creek K | internal_break | 无突破父对象 |
| SOS且C_tX<=R+ε | 已可用最近摆动低点，index>origin_bar 且 S<=price<=min(R+ε,C_tX)，没有则S | internal_sos | 无突破父对象 |

同根只用最高优先级来源。关联旧候选/接纳的再次越界不是新来源；晚接纳不补建LPS候选。确认前sub_type为空。

**分型：** 仅external_breakout来源按L_q分为LPS_AT_CREEK（L_q<=R+ε，即BUEC）和LPS_ABOVE_CREEK（L_q>R+ε）；内部两类来源始终为LPS_INTERNAL，保存K或局部支撑，不用外部R替换。

**全程失效：** tX+1起每根L_t<L*−ε即invalidated，包括尚无b、起点b和反应根r；父对象终止也立即传给外部候选。

```text
search_from = tX+1
b = 从search_from开始首个C_b<C_(b-1)的根
deadline = min(b+lps_max_pullback_bars, tX+lps_window_max)

对每根t，b<t<=deadline：
  1. 父状态与价格失效已检查，不合格即停止。
  2. 若t-b>=lps_min_pullback_bars，取b..t-1为完整回调段，
     检查下列回调证据和反应；全部满足则确认。
  3. 未确认且C_t>max(C_tX..C_(t-1))，结束回合，search_from=t+1。
  4. 否则回调延续。

deadline机会结束无确认：
  若deadline=tX+lps_window_max，候选expired；
  否则仅结束回合，search_from=deadline+1。
始终未找到b，也在tX+lps_window_max处理完后过期。
```

来源突破未确认而先到期限时，候选在父期限末根机会结束后同步过期。来源已确认且acceptance有效时，候选继续自身期限；不能确认已终止父对象。

```text
q = b..t-1中最低L的根，同值取最早
回调证据：
  Effort(b..t-1) <= lps_max_pullback_effort
  mean(NS_b..NS_(t-1)) <= lps_max_pullback_ns
  (max(H_tX..H_(b-1))-L_q)/A_b >= lps_min_retrace_atr
反应条件：
  C_t >= H_(t-1)+confirm_step
  C_t > L*+epsilon
  NetMove_t >= lps_response_min_move_atr
```

q只定位最低点；持续长度和量价统计使用完整回调段。首次已收盘回调前缀满足最短长度和回调证据时记录LPS_PULLBACK_OBSERVED；段扩展追加明细，不覆盖首个观察时间。反应根不计入回调量能，也不要求放量。

**确认事务：** 输出LPS事实和confirmed假设，保存q、L_q、回调前高点、来源SOS关系和有效期。外部父突破尚candidate时联动创建接纳并确认；已接纳则复用。外部confirmed LPS改为依附acceptance，内部仍独立。任一父在事务前终止则不能确认。

**严格形态标记strict_v1：** 仅外部LPS_AT_CREEK可加标：

```text
t-b >= strict_backup_min_pullback_bars
mean(RVOL_b..RVOL_(t-1)) < strict_backup_max_mean_rvol
mean(NS_b..NS_(t-1)) <= strict_backup_max_mean_ns
OLS_slope(RVOL_b..RVOL_(t-1)) < 0
C_(t-1)<O_(t-1)，C_t>O_t，O_t<=C_(t-1)，C_t>=O_(t-1)
RVOL_t >= backup_turn_min_rvol
```

名称保留作形态标签，但使用V4的精度、期限和基准，不能宣称与V1历史输出逐根相同。

| 确认后条件 | 转换 |
|---|---|
| r+1起C<L_q−ε | invalidated，support_lost |
| 依附acceptance终止或区间关闭 | 同步invalidated/expired |
| 同区间同方向新LPS确认且旧LPS当根仍有效 | 按第7.1节事务承接，旧标签expired，Phase D有条件转移 |
| t>r+lps_current_max_age | 当根开始时expired，先于依附确认 |

发生事实永久保留。全部来源都有位置失效和有效期出口。

### 7.7 LPSY 族

第7.6节的价格镜像，使用相同处理顺序、依附、回合长度、确认距离和有效期。

| 来源，优先级从上到下 | 参照位L* | 分型 |
|---|---|---|
| 新建DOWN_BREAKOUT_VALIDITY的突破根 | 外部S | H_q>=S−ε为LPSY_AT_ICE，否则LPSY_BELOW_ICE |
| INTERNAL_FTI | 内部Ice | 始终LPSY_INTERNAL |
| SOW且C_tX>=S−ε | 已可用最近摆动高点，index>origin_bar 且 max(S−ε,C_tX)<=price<=R，没有则R | 始终LPSY_INTERNAL |

tX收盘创建候选，tX+1起每根H_t>L*+ε使候选失效；b为首个C_b>C_(b-1)，q为完整反弹段最高H位置，回撤幅度为`(H_q-min(L_tX..L_(b-1)))/A_b`。先确认，再处理来源以来收盘新低的回合重置。

反应为`C_t<=L_(t-1)−confirm_step`、`C_t<L*−ε`、`NetMove_t<=−lps_response_min_move_atr`。确认后r+1起C>H_q+ε使当前有效性失效。外部来源确认后依附向下acceptance，内部不按外部S重新分型。其余期限、量能和三值规则相同；多空默认对称仅用于研究基线。

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
| 突破接纳 acceptance | 第7.5节独立失败和区间关闭规则；普通、回测及晚接纳相同 |
| 吸筹 / 派发 / 再吸筹 / 再派发 | 相反方向占优连续 `structural_opposite_persistence_bars` 根成立，或相反方向区间状态变为 `exit_*_accepted` 时 `invalidated`；原方向证据不再满足且近期信号过期时 `expired` |
| Phase C / D / E | 引用的底层对象终止时同步撤销；Phase D 规则支持依附于其引用的 LPS/LPSY；没有独立证据时返回未知 |

区间方向标签逐根重算，结构假设保留历史。每个确认序列必须记录evidence_owner_id：吸收响应对应吸收假设，Spring序列对应SPRING_SUPPLY_TEST_HYPOTHESIS，UT序列对应UT_DEMAND_TEST_HYPOTHESIS，UTAD序列对应UTAD假设。确认序列使其owner成为confirmed，并按本节价格失效及证据期限管理当前有效性。当前证据不可评估不等于出现相反证据；只暂停依赖该证据的新确认/支持，不把not_evaluable按false处理。已知的价格失效、父终止和明确期限仍照常执行。

第5步选中的 LPS/LPSY 确认时，同区间版本、同方向且当根仍为 confirmed 的 Spring/UT 更新 `last_evidence=t`、`valid_through_bar=t+bias_sequence_recent_bars`。未选中形态不续期；第3步已价格失效或到期的路径不能复活。

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

### 8.3 当前有效证据与三值方向判定

#### 8.3.1 波浪组

区间年龄至少mature_range_age，取原点之后、所有收盘在[S−ε,R+ε]内的最近至多wave_stats_max_completed个已完成且可用的波，每方向至少wave_min_completed_each_direction个。统计取各方向中位数，k=wave_dominance_ratio。历史不足、指标无效或模块关闭时相关组not_evaluable，附原因。

| 组 | 值 | 规则 |
|---|---|---|
| A活动 | up_more_active / down_more_active / balanced / not_evaluable | med_up(activity)>=k*med_down(activity)为up；反向为down；其余balanced。仅描述活动方向 |
| P价格结果 | demand / supply / neutral / not_evaluable | demand：med_up(peak)>=k*med_down(peak)，且med_up(signed_end)>med_down(signed_end)；supply对称 |
| D反向结果递减 | down_waning、up_waning各为true/false/not_evaluable | 对应方向至少3个波时，最新peak小于此前各波peak中位数为true，否则false；不足3个则该方向not_evaluable |

D的两个方向可以同时true，不能用互斥枚举丢掉其中一个。CumEffort与活动率同时保存，但本版本路径仍使用活动率；累计努力的增量价值独立验证，不额外计票。

#### 8.3.2 B组只统计当前有效证据

需求侧序列：SUPPORT_RESPONSE_SEQUENCE、SPRING_TEST_CONFIRMED、SPRING_SOS_SEQUENCE；供应侧：RESISTANCE_RESPONSE_SEQUENCE、UT_TEST_CONFIRMED、UT_SOW_SEQUENCE及同源UTAD_WEAKNESS_SEQUENCE。

序列参与当前B组必须全部满足：

```text
reference_range_id/version 与当前评价区间一致
0 <= t - sequence.confirmed_bar < bias_sequence_recent_bars
sequence.available_at <= 当前决策实际时间
owner_hypothesis.status == confirmed
owner本根价格/期限/父状态已检查，当前仍有效
必需底层事实及数据质量可评估
```

每个序列保存evidence_owner_id、confirmation_trigger_id、primitive_event_ids。共享确认触发（例如同一SOW同时形成UT_SOW与UTAD弱势序列）只计一次；历史原事件全部保留。多个别名不能通过不同sequence_id重复计票。同一owner的一个确认闭环最多贡献一票；先按当前有效性过滤，再按owner与共享触发关系去重，不能由已经失效的别名压掉另一条仍有效证据。附加证据不能重写原序列的confirmed_bar或available_at来延长该序列的计票期限。

在完整且已评估的回看窗口内，一侧有效序列数至少1且大于另一侧则B为该侧；相等（含0比0）为neutral。回看窗口的数据或必要检测模块未覆盖时B=not_evaluable，不把“尚未检查”当0个。另输出history_confirmed_sequences和current_valid_sequences，失败序列仅留在历史或独立反向研究特征，不继续计入当前正向B。

#### 8.3.3 路径与三值逻辑

所有路径布尔结果为true / false / not_evaluable。不得将not_evaluable作为普通字符串参与相等、不等或非运算。

| 运算 | 固定规则 |
|---|---|
| AND | 任一false则false；否则任一not_evaluable则not_evaluable；否则true |
| OR | 任一true则true；否则任一not_evaluable则not_evaluable；否则false |
| NOT | true/false互换；not_evaluable仍not_evaluable |
| 组值比较 | 组不可评估时，等于/不等于任一方向均为not_evaluable |

需求侧路径：

| 路径 | 条件 |
|---|---|
| demand_push | P=demand AND A=up_more_active |
| supply_exhaustion | P=demand AND A!=up_more_active AND D.down_waning=true |
| boundary_supported | P=demand AND B=demand |
| absorption_response | A=down_more_active AND P属于{demand,neutral} AND B=demand，且有效B序列包含支撑响应或Spring测试 |

```text
demand_result = OR(需求侧路径) AND NOT(P=supply) AND NOT(B=supply)
supply_result = 对称路径及约束
```

供应侧路径为supply_push、demand_exhaustion、boundary_supported、distribution_response；上下行、需求/供应及支撑/阻力对换。大下行活动本身不否决需求，必须结合价格结果与当前响应。

最终dominance：一侧true则该方向；两侧均false为neutral；没有true且至少一侧not_evaluable为not_evaluable。两侧同时true违反互斥约束，返回配置/实现错误，不任选一侧。某一条路径已经确定成立时，不要求无关路径也全部可评估。

输出dominance、dominance_paths（仅true路径）、path_states、evidence_groups、evaluation_reasons。wave_module_enabled=false时A/P/D不可评估，方向也不可评估；若数据足够但各路径明确不成立，才输出neutral。不输出合并概率或胜率。

### 8.4 结构假设

| 假设 | 入口条件 |
|---|---|
| `phase_b_range` | 前台区间年龄 ≥ `mature_range_age`，方向可以未知 |
| `spring_supply_test_candidate` | 存在未失效的 Spring/Shakeout 假设；不要求方向占优 |
| `ut_demand_test_candidate` | 存在未失效的 UT 测试假设 |
| `accumulation_candidate` | 成熟区间 + `demand_dominance_by_rules` + 最近 `bias_signal_recent_bars` 根内至少一个触发事件：`SOS`、`INTERNAL_JOC` 或当前有效的 LPS |
| `distribution_candidate` | 对称：`supply_dominance_by_rules` + `SOW`、`INTERNAL_FTI` 或当前有效的 LPSY |

重复计票控制：触发事件及其 `primitive_event_ids` 与B组序列共享事实时，剔除对应序列后重新计算B组和该结构入口所需的占优结果，占优仍须为true；不可评估不能作为通过。原始区间方向另行保留，不能只删显示ID却沿用未去重的结论。

更大尺度背景在区间建立时计算并冻结：同周期前 `larger_context_window` 个收盘变化，`SignedDelta >= larger_context_min_displacement_atr` 且 `ER >= larger_context_min_er` 为上涨背景，对称为下跌背景。上涨背景中的吸筹候选显示为 `reaccumulation_candidate`，下跌背景中的派发候选显示为 `redistribution_candidate`。

这些方向结构始终以 candidate 表达当前解释，没有独立 confirmed 入口，`confirmation_deadline_bar=null`；退出只由第7.10节驱动。入口、来源选择及 `last_trigger` 刷新使用同一组当前区间版本、已实际可用、最近窗口内的触发器；LPS/LPSY 的 owner 必须当前 confirmed。先处理本根有效触发，再按“原方向不满足且信号过期”联合条件退出；不可评估不等于原方向为 false。

`last_trigger` 保留最后曾合法采用的触发时间供审计，当前信号是否过期以当前有效触发组为空判断。后来失效的 LPS/LPSY 即使曾被合法采用，也不能继续延长结构寿命；原方向仍成立或不可评估时，联合退出条件仍不成立。

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

  单独外部 JOC/FTI 候选随其明确关联的突破假设或接纳当前有效性持续；同区间新接纳不能借用已失败的旧强突破。内部 JOC/FTI 候选随其内部突破来源 LPS/LPSY candidate 持续，确认后按正常 LPS 阶段路径处理。源对象终止、不可用或区间关闭时结束单独候选；无 LPS 时不能产生 D 规则支持。
- **Phase D 获规则支持：** Phase D 候选，且在 LPS 确认后 `phase_d_followthrough_bars` 根内至少满足一项：
  - 推进：收盘超过该 LPS 回调前的最高价 `max(H_tX..H_(b-1))`；
  - 方向：`demand_dominance_by_rules` 成立。

  新支持的检查窗口明确为 `r+1～r+phase_d_followthrough_bars`，不包含LPS确认根r；没有LPS的单独JOC/FTI候选不能跳过这项依附条件。满足后，只要所引用的LPS当前有效，支持就持续。LPS承接仅按第7.1.1节在新来源合格时转移引用；旧对象已失效/到期或新来源不合格时结束旧支持。这些条件是研究选择，不预设增加条件必然更优。
- **Phase E（向上）获规则支持**，全部满足：

```text
区间状态 exit_up_accepted
outside_up_streak_t >= phase_e_min_outside_closes
(C_t - R) / A_ref >= phase_e_min_distance_atr
trend_now: SignedDelta_n > 0 且 Slope_n > 0 且 ER_n >= phase_e_min_er
```

向下 Phase E 使用 `outside_down_streak`、`(S-C_t)/A_ref`、负向 SignedDelta 和负向 Slope。ER 不带方向，必须与有方向的位移和斜率同时使用。

Phase E 必须引用当前 `acceptance_id`，并在每根重算上述全部条件。接纳失效/过期时立即撤销；接纳仍有效但位置或趋势条件不成立时结束当前支持，记录 `conditions_no_longer_met`；条件不可评估则输出未知及缺项。以后再次满足只能创建新的阶段支持记录，不能改写旧记录。市场状态的防抖不能延迟对象失效或保留已经撤销的阶段支持。

Phase E 记录同时保存 `reference_range_id/version`；结束记录保留原接纳来源。第5.5节退役可以读取当前接纳的历史支持，不把历史支持重新显示为当前 Phase E。

多个阶段解释可以并存或未知。`phase_supported_by_rules` 必须附证据 ID、确认时间和失效条件。

### 9.2 事件与假设记录

```text
事件 occurrence：
  event_id, event_type, sub_type, strength, aliases, linked_event_ids
  index_id, data_profile, index_source, index_method_version, timeframe
  event_time, detected_at, available_at, data_asof
  pattern_start, pattern_end, onset_snapshot_id
  reference_range_id, reference_range_version, frozen_S, frozen_R, frozen_epsilon
  frozen_internal_level（如有）
  observed_metrics: RVOL, Effort, CumEffort, NS, CP, LW, UW, NetMove, Gap, depth, OIz, oi_quadrant
  baseline_mode, standard_baseline_metrics, quality_flags, threshold_margins, rejection_reasons
  acceptance_id（如适用）, evidence_owner_id（确认序列）, confirmation_trigger_id, primitive_event_ids
  rule_version, parameter_version, data_version

结构 hypothesis：
  hypothesis_id, hypothesis_type, source_kind, source_event_ids, competing_hypotheses
  reference_range_id, reference_range_version, lifecycle_parent_ref（object_type + id，或null）
  status, created_at, confirmed_at, invalidated_at, expires_at, status_available_at
  created_bar, confirmed_bar, confirmation_deadline_bar, valid_through_bar
  state_transition_history
  confirmation_conditions, invalidation_conditions（含计数起点 PRE/INCL/POST）
  pending_observation（q、response_deadline_bar、冻结参照，或null）
  supporting_evidence_ids, opposing_evidence_ids, evidence_groups, dominance_paths, path_states

接纳 acceptance：
  第7.5.2节的全部字段；status、invalidated_at、expires_at、status_available_at
  state_transition_history（每项含原因、有效根、实际可用时间、transaction_id）

快照 snapshot：
  snapshot_id, asof_bar, bar_end, available_at, transaction_id
  trend_now_asof_bar, context_for_next_bar, context_available_at
  range_id, range_version, range_state, acceptance_id, outside_up_streak, outside_down_streak
  history_confirmed_sequences, current_valid_sequences, evidence_groups, path_states
  dominance, dominance_paths, evaluation_reasons, phase_candidate, phase_supported_by_rules
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
  "rule_version": "4.1",
  "parameter_version": "v4.1-default-unvalidated",
  "market_regime": "range",
  "trend_now": "downtrend",
  "structure": {
    "primary": "spring_supply_test_candidate",
    "alternatives": ["phase_b_range", "redistribution_candidate"],
    "dominance": "not_evaluable",
    "dominance_paths": [],
    "evidence_groups": {
      "A": "balanced", "P": "demand", "B": "neutral",
      "D": {"down_waning": "not_evaluable", "up_waning": "not_evaluable"}
    },
    "path_states": {
      "demand": {"demand_push": false, "supply_exhaustion": "not_evaluable", "boundary_supported": false, "absorption_response": false},
      "supply": {"supply_push": false, "demand_exhaustion": false, "boundary_supported": false, "distribution_response": false}
    },
    "evaluation_reasons": ["D_each_direction_has_only_two_completed_waves"],
    "phase_candidate": ["B", "C"]
  },
  "range": {"id": "R001", "version": 1, "state": "active", "acceptance_id": null, "support": 3612.40, "resistance": 3698.85, "epsilon": 2.15},
  "evidence": [{"event_id": "E0042", "type": "SPRING_LOW_VOLUME", "oi_quadrant": "up_oi_flat"}],
  "next_confirmation": ["TEST_OBSERVED 后的测试反应", "SOS（低量 Spring 允许）"],
  "invalidation": ["收盘低于 Spring 最低价减 epsilon", "连续两根收盘低于 S-epsilon"],
  "probability": null,
  "quality": "ok"
}
```

示例只说明格式：已有波数足以计算A/P，但不足以判断D；`supply_exhaustion`尚不可评估，所以最终方向为 `not_evaluable`，主结构仍可为不依赖占优的 `spring_supply_test_candidate`。若D.down_waning明确为false且其他值相同，才输出neutral。alternatives仅为待评估解释名称，不代表这些解释已满足入口；生产输出分别保存其条件ID、状态和缺项。

### 9.4 每根 K 线的唯一处理顺序

以下以有效且已收盘t根为一次事务。全部正式输出只在事务完成后发布，内部临时状态不作为历史快照。

```text
1. 校验输入、质量和预热；取得已提交的snapshot_(t-1)。
   前置趋势读context_for_next_bar；多根模式读各自p-1副本/快照环。
2. 计算当根临时值：用A_t/Bv_t算RVOL_t、NS_t、NetMove_t等；
   用C_t与冻结边界算outside_streak_t及适用的失败连续计数。
   不先把V_t/TR_t混入当根事件比较基准。
3. 处理已有对象的价格失效、已确认对象t>valid_through_bar的到期；
   立即向依附对象传播。此前已确定的区间关闭也在这里执行，不推迟到第9步。
   此时不提前执行候选末根超时。
4. 检测当根事实并记录；按来源优先级创建新观察/候选，执行INCL检查。
   新ST/Test/UT Test观察先暂存，不替换已有pending q。
5. 按依赖执行确认：
   a. 用outside_streak_t确认仍有效候选或创建符合规则的late_hold，
      同时处理接纳触发的相反结构失效及其级联。
   b. 处理AR、吸收响应等已就绪的独立确认，再检查旧测试q和已有LPS回合；
      合法反馈确认、相反结构失效与LPS承接在相应事务中一起完成。
   c. 旧测试未确认时才激活新q，检查同根严格反应。
   d. 处理合法SOS/SOW及UTAD序列，引用当根已可用的事实/确认；
      一个闭环只首次确认一次，同源依据去重。
   每次确认前再验证生命周期父对象，不确认已终止对象。
6. 处理本根达到confirmation_deadline_bar且未确认的对象，expired并立即级联；
   处理观察窗口/回合截止。此阶段不会撤销已经合法完成的确认。
7. 更新ATR_t与供下一根使用的Bv状态、摆动点和因果波浪；
   trend_now_t=trend_at(t)，准备context_for_next_bar相同值。
   摆动点当根确认但下一根才能用作事件前置参照。
8. 在完成失效、超时和LPS承接后的对象集合上计算B组、三值方向及结构/阶段。
   基于新方向产生的结构终止立即向其依附对象传播。
9. 区间新建/关闭/替代按自身版本提交；新版本下一根生效、计数初值0。
   关闭旧版本使其当前有效性对象过期并传播；对受影响的当前证据/阶段重新归约，
   不重新检测本根市场事件，不让旧对象改变新前台区间。
10. 写入snapshot_t：asof_bar、trend_now_asof_bar、context_for_next_bar、
    最终对象状态、acceptance_id、计数、方向证据、实际可用时间。
11. 一次性发布事件事实、状态转换日志和最终实时快照。
```

第5步依赖的当根事实已经在第4步产生；例如UTAD可引用同根先确认的LPSY。LPS对突破的反馈确认属于显式事务，不允许通用遍历中出现生命周期循环。实现应采用固定优先级和稳定ID排序，或等价的依赖调度，结果必须一致。

第7/8步产生的新方向证据可用于当前新解释，不能作为当前事实模式“开始前已知”的上下文。结束时才将它们写入快照，供未来事件读取。新LPS替代旧LPS时，引用转移与旧对象过期作为同一事务准备和提交；最终B组和阶段不得读取被替代前的暂态。

第9步只处理本根新证据才决定的区间建立或更替。若此前合法确认的对象因这次更替结束当前有效性，保留其确认事实与 `range_closed` 转换，最终快照只显示终止后的状态；不能把这种本根结束时的参照版本更替与第3步早已确定的父对象失效混为一谈。两个转换共享transaction_id，均不以中间当前支持对外发布。

## 10. V4 参数登记表

全部数值标为 P：待验证。级别：**C** = 首轮校准（共 8 个）；**B** = 冻结基线，首轮不调；**M** = 模式开关，更换即为新研究配置。

### 10.1 数据、指标与背景

| 参数 | 默认 | 级别 |
|---|---:|:---:|
| `warmup_bars` | 60，且不低于所有启用窗口所需历史 | B |
| `atr_period`（n_atr） / `tr_cap_mult` | 14 / 3.0 | B |
| `baseline_mode` | `robust`（`standard` 同时计算） | M |
| `volume_period` / `volume_cap_mult` / `volume_min_samples`（仅日线） | 20 / 3.0 / 15 | B |
| `intraday_volume_mode` | `session_matched` | M |
| `session_baseline_days` / `session_min_samples`（仅日内） | 20 / 10 | B |
| `oi_mode` | `annotate` | M |
| `oi_window`（n_oi） / `oi_flat_band` | 20 / 1.0 | B |
| `trend_window` / `trend_sma_fast` / `trend_sma_slow` | 20 / 20 / 50 | B |
| `trend_min_displacement_atr` / `trend_min_abs_slope_atr` / `trend_min_er` | 2.0 / 0.05 / 0.30 | B |
| `larger_context_window` / `larger_context_min_displacement_atr` / `larger_context_min_er` | 50 / 3.0 / 0.30 | B |
| `regime_persistence_bars` | 2 | B |
| `confirm_step_units` | 1 | B |

### 10.2 区间与参照位

| 参数 | 默认 | 级别 |
|---|---:|:---:|
| `boundary_resolution_units` | 2 | B |
| `boundary_atr_fraction` | 0.10 | **C** |
| `range_min_width_atr` / `range_max_width_atr` | 3.0 / 12.0 | B |
| `generic_range_window` / `generic_range_max_er` | 30 / 0.25 | B |
| `range_interior_close_fraction` / `range_min_touches_per_side` / `touch_min_separation_bars` | 0.80 / 2 / 3 | B |
| `event_min_range_age` | 15 | **C** |
| `mature_range_age` | 25；校准时保持 `mature - event_min = 10` | 随 C 联动 |
| `range_max_age` / `range_stale_min_age` / `range_stale_bars` | 200 / 60 / 40 | B |
| `pivot_confirm_bars` / `pivot_min_reaction_atr` | 3 / 1.0 | B |
| `creek_mode` | `range_and_internal` | M |

### 10.3 事实事件

| 参数 | 默认 | 级别 |
|---|---:|:---:|
| `climax_new_extreme_window` | 20 | B |
| `climax_min_rvol` | 2.5 | **C** |
| `climax_min_ns` / `sc_min_cp` / `bc_max_cp` / `climax_min_rejection_wick` | 1.8 / 0.35 / 0.65 / 0.25 | B |
| `cluster_min_effort` / `cluster_min_span_atr` / `cluster_stop_min_rvol` | 2.0 / 2.5 / 1.5 | B |
| `absorption_min_rvol` / `absorption_max_ns` / `low_progress_max_move_atr` | 2.0 / 0.7 / 0.5 | B |
| `absorption_response_max_bars` / `absorption_current_evidence_max_age` | 5 / 10 | B |
| `spring_max_depth_atr` | 2.0 | **C** |
| `spring_max_pattern_bars` / `shakeout_max_pattern_bars` | 2 / 3 | B |
| `spring_min_cp` / `ut_max_cp` | 0.70 / 0.30 | B |
| `spring_low_volume_max_rvol`（严格小于） / `spring_high_volume_min_rvol`（严格大于） | 0.8 / 1.3 | B |
| `strong_break_min_rvol` | 1.8 | **C** |
| `strong_break_min_ns` / `joc_min_cp` / `fti_max_cp` | 1.5 / 0.75 / 0.25 | B |
| `strong_bar_min_move_atr` | 0.5 | **C** |
| `sos_sow_min_rvol` / `sos_sow_min_ns` / `sos_min_cp` / `sow_max_cp` | 1.4 / 1.3 / 0.70 / 0.30 | B |
| `gap_flag_min_atr` | 1.0 | B |

### 10.4 序列、确认与失效

| 参数 | 默认 | 级别 |
|---|---:|:---:|
| `ar_window_min` / `ar_window_max` | 3 / 8 | B |
| `ar_reversal_close_steps` / `ar_reversal_atr` | 2 / 0.5 | B |
| `st_window_min` / `st_window_max` / `st_response_max_bars` | 5 / 15 / 3 | B |
| `st_near_support_atr` / `st_max_effort_vs_climax` / `st_max_ns_vs_climax` | 0.5 / 0.60 / 0.70 | B |
| `test_window_min` | 3 | B |
| `test_window_max` | 10 | **C** |
| `test_near_support_atr` / `test_max_rvol` / `test_max_ns` | 0.5 / 0.85 / 1.0 | B |
| `test_max_effort_vs_spring` / `test_response_max_bars` | 0.65 / 3 | B |
| `spring_sos_window_min` / `spring_sos_window_max` / `spring_fail_consecutive_closes` | 1 / 10 / 2 | B |
| `breakout_hold_closes` / `breakout_fail_inside_closes` / `breakout_fail_deep_fraction` | 3 / 2 / 0.50 | B |
| `breakout_validity_window` / `body_retrace_fraction` | 12 / 0.50 | B |
| `lps_min_pullback_bars` / `lps_max_pullback_bars` | 2 / 8 | B |
| `lps_max_pullback_effort` / `lps_max_pullback_ns` / `lps_min_retrace_atr` / `lps_response_min_move_atr` | 0.90 / 1.0 / 0.5 / 0.3 | B |
| `lps_window_max` | 15 | **C** |
| `lps_current_max_age` / `phase_d_followthrough_bars` | 20 / 10 | B |
| `strict_backup_min_pullback_bars` / `strict_backup_max_mean_rvol` / `strict_backup_max_mean_ns` / `backup_turn_min_rvol` | 3 / 0.85 / 1.0 / 1.2 | B |
| `utad_response_window_min` / `utad_response_window_max` | 1 / 12 | B |
| `phase_e_min_outside_closes` / `phase_e_min_distance_atr` / `phase_e_min_er` | 5 / 2.0 / 0.40 | B |

### 10.5 波浪与结构

| 参数 | 默认 | 级别 |
|---|---:|:---:|
| `wave_module_enabled` | true（关闭时 A、P、D 组输出 `not_evaluable`） | M |
| `wave_reversal_atr` | 1.0（必须报告 1.0/1.5/2.0 敏感性） | B |
| `wave_stats_max_completed` / `wave_min_completed_each_direction` / `wave_dominance_ratio` | 6 / 2 / 1.2 | B |
| `bias_signal_recent_bars` / `bias_sequence_recent_bars` / `structural_opposite_persistence_bars` | 10 / 20 / 2 | B |

### 10.6 参数约束与版本对照

参数取值必须满足：成对的最小/最大窗口参数满足 `min <= max`；`spring_low_volume_max_rvol < spring_high_volume_min_rvol`；`range_min_width_atr < range_max_width_atr`；`mature_range_age >= event_min_range_age`；`spring_max_pattern_bars ∈ {1,2}` 且 `shakeout_max_pattern_bars >= spring_max_pattern_bars`；`lps_min_pullback_bars < lps_max_pullback_bars`；`strict_backup_min_pullback_bars <= lps_max_pullback_bars`；`warmup_bars` 覆盖所有启用窗口。价格分辨率、ATR与量能倍数为正；根数为正整数，`confirm_step_units` 为正整数；`wave_dominance_ratio>1`，`0<breakout_fail_deep_fraction<1`。不满足时拒绝运行，不静默修正。

为了保证 JOC/FTI 蕴含 SOS/SOW 特征，必须有 `strong_break_min_rvol >= sos_sow_min_rvol`、`strong_break_min_ns >= sos_sow_min_ns`、`joc_min_cp >= sos_min_cp`、`fti_max_cp <= sow_max_cp`。校准邻域也不得越过这些跨参数约束；总候选期限按第7.3/7.4节允许路径的最大截止计算，不能截断合法 SOS/SOW 窗口。

| V2 参数 | V4 处理 |
|---|---|
| `roll_confirm_days`、`liquid_min_ratio` | 删除（不覆盖实际合约） |
| `boundary_ticks` | 改为 `boundary_resolution_units`，单位为指数价格分辨率 |
| 固定 1 tick 的确认距离 | 改为 `confirm_step_units * price_resolution` |
| `lps_turn_max_bars` | 删除；改用每回合固定截止 `lps_max_pullback_bars`（第 7.6 节） |
| 新增 | `baseline_mode`、`confirm_step_units`、`lps_max_pullback_bars`、`lps_current_max_age`、`phase_d_followthrough_bars` |

同一参数被多条规则复用时，调整会同时影响这些规则；拆分成独立参数属于参数模式变化，必须登记。

上表保留的是V2到V3已经完成的参数迁移。V4沿用V3的8个首轮校准参数及默认值，不增加市场形态阈值。V4更改的是比较符、计数时序、接纳生命周期、观察优先级与评价口径；即使数值相同，也必须使用新的规则/参数版本重新回放。评价路径模式、20根期限与2 ATR障碍另随研究配置登记（第11.4节），不能在看过结果后逐事件切换。

## 11. 验证方案与验收要求

### 11.1 工程正确性：必须通过

| 验收项 | 要求 |
|---|---|
| 前缀回放一致性 | 只输入到 t 的回放结果，与全量回放中保存的 t 时快照完全一致 |
| 未来扰动 | 修改 t 之后的数据，不改变截至 t 的任何输出 |
| 后续追认 | t+N 的确认不出现在 t 的历史可用状态中 |
| 边界冻结 | 当前 K 线不抬高自身阻力；失败后原 S/R/ε 不移动 |
| 窗口与冲突 | 窗口末根可确认，末根之后不确认；同根失效优先；依附级联正确 |
| 数据质量 | 零量、缺失、H=L、ATR=0、来源不符、事后权重、成分变动返回明确原因 |
| 版本 | 输出保存数据、规则和参数版本，可重放 |
| 依附与事务 | 改变对象集合的枚举顺序不改变结果；父对象已终止则不确认子对象；LPS反馈确认不产生生命周期环 |
| 接纳唯一性 | accepted区间有且仅有一个同版本同方向confirmed acceptance；清除接纳时同步更新阶段与区间状态 |
| 三值与证据 | 缺项不当作0或false；已失效序列不进入当前B组；同源事实与序列别名不重复计票 |
| 可用时间评价 | 锚点数据在信号可用时已到达；OHLC评价不含跨越该时刻的部分K线 |

### 11.2 构造路径用例：三组模块验收与一组集成验收

A/B/C三组可以分别实现和验收。C组用构造的已完成波浪记录作输入，不依赖A、B组先通过。D组专门覆盖V3评审与本版新增的交界条件，模块验收后执行。除用例明确指定外沿用第10节默认参数；各用例需提供足够的预热、合法区间和前置状态。这些用例是实现要求，不表示当前程序已通过，也不说明市场预测价值。

**A 组：数据与指标**

| 编号 | 构造 | 期望 |
|---|---|---|
| A01 | 声明的加权指数缺失，数据库中存在同名合成 `*_index` | `index_source_mismatch`，不替换输入 |
| A02 | 两成分极值不同步，`ohlc_method` 为成分 H/L 分别加权 | 拒绝 |
| A03 | 指数价格 100.37，成分最小跳动为 1 | 按 100.37 比较，ε 按 price_resolution 计算 |
| A04 | Bv 稳定在 100；t0 根成交量 500，其余各根 100 | t0+10：standard Bv=120，robust Bv=110（截尾到 300）；t0+21：两者都回到 100 |
| A05 | 序列前 n_atr 根 | ATR 初始值为未截尾均值，截尾从下一根开始 |
| A06 | 日内同槽有 12 个合格样本 | 可用（使用 session_min_samples=10，不使用 15） |
| A07 | 日内指数使用当日收盘持仓作权重 | 拒绝作实时输入，标记 `ex_post_weights` |

**B 组：区间、事件与生命周期**

| 编号 | 构造 | 期望 |
|---|---|---|
| B01 | R=100，突破后 20 个收盘从 130 单调降到 110 | 不输出向上 Phase E |
| B02 | 低量普通上破，随后 3 根收于 R+ε 之上 | 有效性假设维持确认，区间 `exit_up_accepted` |
| B03 | R=100，ε=0.2，收盘 100.3、100.1、100.4、100.5、100.6 | 第五根维持确认；tB 不变；100.4 关联为新突破事实，不新建假设 |
| B04 | 首根下穿 3 ATR | `SUPPORT_UNDERCUT` 与 `DEEP_UNDERCUT`，进入深穿观察 |
| B05 | 供应占优在 p 收盘才成立，p+1 收回形成 UT | `UT_FEATURE`，不创建 UTAD 候选 |
| B06 | 三根高潮簇 p=t-2；trend_at(t-3)为下跌、trend_at(t-1)已不满足，其余条件成立 | 读取snapshot_(t-3).context_for_next_bar，簇成立 |
| B07 | 吸收候选创建根已收于 S-ε 下方，下一根再收于下方 | 第二根失效（`INCL`） |
| B08 | 阴线测试 + 下一根越过测试高点 | `TEST_OBSERVED` 后 `TEST_RESPONSE_CONFIRMED` |
| B09 | 突破在第 12 根过期，第 13 根满足 LPS 反应条件 | LPS 候选已随来源在第 12 根过期；不确认、不复活 |
| B10 | 来源收盘 105；前一根收 104.8、高 105；反应根收 105.2，净推进 0.4 ATR | LPS 确认，不被新高重置 |
| B11 | 回调首根即最低点，之后 3 根同低低量横盘，再出现反应 | 回调段长度为 4，确认 |
| B12 | LPS 确认早于外侧三根维持 | 来源假设 confirmed，区间 `exit_up_accepted` |
| B13 | 内部 LPS 确认后收盘跌破 L_q-ε | LPS 事实保留，当前有效性 invalidated，Phase D 支持撤销 |
| B14 | 同一根既是新建突破又是 SOS | 只创建突破来源的一个 LPS 候选 |
| B15 | UT 后低量较低高点测试，下一根跌破测试低点 | `UT_TEST_CONFIRMED` |
| B16 | SOS + LPS 确认，之后 10 根既无推进也无需求占优 | 只有 Phase D 候选 |
| B17 | 突破假设过期时仍在区间外，之后outside_up_streak达到3，无新活动假设 | 建立mode=late_hold的独立acceptance，区间exit_up_accepted，旧假设不复活 |
| B18 | AR 幅度 2.5 ATR | AR 不确认 |

**C 组：波浪与方向证据**

| 编号 | 构造 | 期望 |
|---|---|---|
| C01 | 上波活动率 0.6、下波 1.2；上行峰值 3、净 2.5，下行峰值 1、净 0.5；已有支撑响应序列 | A=down_more_active，P=demand，B=demand；路径 `boundary_supported` 与 `absorption_response`；需求占优成立 |
| C02 | 同C01但完整窗口内无支撑响应序列；至少3个下波可判定down_waning=false | 无路径，`neutral`；不能用仅2个下波替代本构造 |
| C03 | P=demand，A=balanced，B=neutral，至少3个下波且最新peak低于此前中位数 | 路径 `supply_exhaustion` |
| C04 | 上行波回吐后收盘低于起点基准价 | `signed_end_displacement < 0` 被保存 |
| C05 | 上波 10 根每根 RVOL 0.8，下波 2 根每根 1.2 | 活动率：下波更高；累计努力：上波 8.0，下波 2.4 |

**D 组：V4处理顺序与边界集成验收**

| 编号 | 构造 | 期望 |
|---|---|---|
| D01 | 向上突破tB=100，确认期限112；进入112时streak=2，C_112仍>R+ε，价格未失效 | 当根临时streak=3，原假设及时confirmed并建立acceptance；不expired，不走late_hold |
| D02 | S=90、R=100、ε=0.2；旧假设已expired后晚接纳，随后连续收99.5、99.4 | 第一根接纳仍有效，但Phase E因外侧条件不满足已结束；第二根acceptance invalidated、区间active；旧假设保持expired |
| D03 | 父对象本根价格失效或已越过valid_through_bar，依附子对象本根其他确认条件满足 | 先终止并级联；不发布子对象确认，不依赖对象遍历顺序 |
| D04 | 外部R=110、内部Creek K=105、ε=0.2，内部突破来源回调低点105.1且响应合格 | LPS_INTERNAL，参照K；不能按R误标成BUEC。内部Ice镜像亦然 |
| D05 | S=100、ε=0.1、A_SC=1、Effort_SC=3、NS_SC_ref=4；旧q高点101。下一根O=101.5、H=101.6、L=100.3、C=101.3、RVOL=0.5、NS=1.3，confirm_step=0.01，仍在两个窗口内 | 下一根是新ST观察且收阴，但先对旧q确认反应；新观察不覆盖旧q、不重复确认闭环 |
| D06 | 波浪模块关闭或两方向样本不足；另构造数据齐全且各路径均false的对照 | 前者dominance=not_evaluable并报告缺项；后者neutral |
| D07 | 5分钟信号根10:05结束、10:05:02可用；10:05—10:10高点在10:05:01已触障 | 整根排除于OHLC预测路径，记录availability_overlap；首个评价区间10:10—10:15，不能把10:05:01计为成功 |
| D08 | 前高100.00、前低99.00、resolution=0.01、confirm_step_units=1 | C=100.01满足向上最小距离；C=98.99满足向下；等于原前高/前低不满足 |
| D09 | trend_at(p-2)非下跌，trend_at(p-1)刚变为下跌，p起高潮簇其余条件成立 | 读取准确的p-1背景，不因误存trend_context_(p-1)而多延迟一根 |
| D10 | 支撑测试序列曾confirmed，本根其owner价格失效；完整窗口内没有其他有效序列 | 历史序列保留，当前需求票剔除；B=neutral，依赖该票的当前支持重算 |
| D11 | 相同边界下分别以hold、pullback、late_hold建立接纳，再输入相同失败路径 | 三种模式的接纳失败根及区间出口相同；late_hold不改动旧假设 |
| D12 | D01、D02、D05、D08按价格镜像构造向下路径 | 下侧计数、UT/BC旧测试优先、LPSY分型及<=比较完全对应；规则未声明的语义差异不自动镜像 |
| D13 | 来源突破与依附LPS共同期限末根，streak不足3但LPS合法确认 | 同一事务创建一个acceptance、确认突破与LPS、重绑父引用；之后的超时扫描不撤销它们 |
| D14 | LPS于r确认，valid_through_bar=r+20；分别在r+20和r+21检查仍引用它的当前支持 | r+20仍可有效；r+21开始时先expired并撤销依附支持，不能被当根新证据复活 |
| D15 | 旧LPS及Phase D有效，新LPS来源合格且同根确认；再用旧LPS已失效的对照 | 前者原子转移引用，旧标签replaced；后者不得复活旧Phase D ID，新对象重新按自己的窗口判定 |
| D16 | 外侧维持与同源LPS同根满足确认 | 只有一个acceptance，primary_basis=hold，保存两项依据，LPS依附该接纳 |
| D17 | OI缺失、分母0、历史不足或晚到；价格与成交量均合格 | OIz及象限不可评估且原因明确；量价事件正常发布，晚到OI不回写旧快照 |
| D18 | 信号可用时某较晚K线已结束但尚未received；更早K线及ATR已到达 | 锚点选真正已到达的更早根及其ATR；不得事后改用尚未到达的收盘 |
| D19 | P=demand、B=demand、A/D不可评估，B有效序列与数据完整 | boundary_supported=true且否决条件均false，需求占优成立；无关路径缺项不阻断已成立路径 |
| D20 | 同一SOW产生UT与UTAD两个别名序列；其中一owner后来失效，另一仍有效 | 始终至多一票；过滤失效owner后仍保留有效证据，不能计两票或一起丢弃 |

D03/D13/D15/D16同时核对状态转换日志与最终快照：不可只检查最终状态，而漏掉曾对外发出的错误中间确认。确认根、实际可用时间、引用ID与终止原因都属于断言内容。

### 11.3 事件单位、样本选取与相关性

每项统计同时报告：原始事件数、独立候选数、交易区间数、确认链条数、去重后的评价样本数。

- 实时评价的样本选取只能是以下两种之一：每个区间每种路径的第一个候选；或在预先固定的决策时点上已知的最新候选。"区间内最后一个候选"只有区间结束后才知道，只能用于事后描述。
- 置信区间使用按区间聚类或时间块的重采样，不假设事件独立。
- Purge/embargo 只解决跨集合的标签重叠，不解决同一集合内部的相关性。
- 保留全部候选的分母（含失效、超时、`not_evaluable`）。

### 11.4 识别质量与预测价值分开

识别质量指标：候选数量、覆盖率、确认率、失效率、超时率、确认延迟、状态切换次数、`not_evaluable` 比例；按指数、年份、波动环境、日夜盘、多空方向分别报告。

预测价值使用独立的客观标签。**V4默认 `evaluation_path_mode=ohlc_full_bars`**，只评价信号真实可用之后的完整K线，不把信号根或可用时刻跨越的K线用于障碍命中。所有事件使用预先登记的同一口径。

```text
tau = signal.available_at
a = 满足bar_end<=tau、received_at<=tau、收盘及ATR状态均已可用的最后一根
descriptive_reference_price = C_a
evaluation_atr = ATR_a（在tau已知，随评价样本冻结）
upper_barrier = C_a + 2*evaluation_atr
lower_barrier = C_a - 2*evaluation_atr
j = 交易日历中bar_start>=tau的首根，不能跳过缺失根另找更晚起点
evaluation_start_at = bar_start(j)
评价路径 = j起连续20根应有的有效交易K线
```

锚点不是成交价；ATR_a已包含a根且在tau前算完，所以可以用作评价基准，不改变事件识别用A_t=ATR_(t-1)的规定。锚点价格或ATR缺失/无效则评价 `not_evaluable`，不能事后用后来到达的数据补一个更有利的锚点。保存 `reference_bar_id`、数据版本、`reference_available_at`、`evaluation_atr_available_at`、障碍值及 `evaluation_wait=bar_start(j)-tau`。

当 `bar_start<tau<bar_end` 时，该根标记 `availability_overlap` 并整根排除于默认评价路径；仅有OHLC不能判断高低点在tau之前还是之后。例：tau=10:05:02，5分钟K线10:05—10:10被排除，从10:10—10:15开始计第1根。等待期间的波动不算评价命中；等待时长和排除比例必须报告，以说明该口径牺牲了多少即时反应信息。

逐根先检查开盘是否已在障碍上/下（命中含等号），命中则记录该侧及 `gap_at_evaluation_start` 或 `gap_at_bar_open`，不假定更早触及时间；开盘在两障碍之间且同根高低价都触障则为 `ambiguous_intrabar`，不能按信号方向选择。20根内未触及记 `neither`；数据终点或缺口导致无法完整判定时记 `censored`，不能跳过缺失行情延长评价。补充方向净位移、回撤、MFE/MAE也只用同一评价路径。

`pre_crossed` 单独描述“以原信号根为参照的障碍，在tau之前已被价格穿越”。完整发生在tau之前的K线可证明该事实；跨越tau的K线只能给出 `pre_crossed_unknown` / `availability_overlap`，不能猜测。该字段不把过去的命中计为预测成功，也不能依据事后结果选择性删除默认评价样本。

评价接口 `evaluate_ohlc` 的补充输入为 `direction∈{-1,+1}`、`signal_reference`（原信号根及其已知 ATR）和 `data_version`。原信号根统一登记为：事实事件的模式完成根 `pattern_end`，确认序列的反应确认根；不使用多根模式第一根冒充可发布信号根。原根障碍取其收盘 ± `barrier_atr*原根ATR`，两侧任一被原根之后、tau之前的完整K线触及（含等号）即 `pre_crossed=true`；已有完整根证明 true 时，不被后续未知窗口抵消。没有证明且期间存在缺失/迟到行情或跨越 tau 的K线则为 null，原因 `pre_crossed_unknown`。原根/ATR未提供或尚未可用分别为 `signal_reference_missing` / `signal_reference_unavailable`。日历须覆盖原根收盘至tau和未来评价窗口的全部应有交易时段；原信号根自身不计穿越，禁止用评价锚点替代原信号参照。

方向净位移、MFE/MAE 和回撤统一相对于评价锚点 `C_a`、用冻结 `ATR_a` 标准化，统计 j 起完整 horizon 根（默认20），首次触障后仍继续补充统计：

```text
d = direction
方向净位移 = d*(最后评价收盘-C_a)
MFE = max(0, max(H_i-C_a))（d=+1）；max(0, max(C_a-L_i))（d=-1）
MAE = max(0, max(C_a-L_i))（d=+1）；max(0, max(H_i-C_a))（d=-1）
回撤 = 对 z_0=0、z_i=d*(C_i-C_a) 的收盘路径计算 max_{u<=v}(z_u-z_v)
```

每项同时输出原始价格单位和 `_atr` 字段。回撤只使用收盘路径，不推测同根高低点先后。`path_bar_count`、`metrics_complete`、`metrics_reasons` 明示完整性；缺口即停，不跳过补足。若首次障碍已经判定，后续缺口不撤销命中标签，但补充统计标为部分样本；未命中且缺口/数据终点不足期限仍为 censored。没有 direction 时，方向统计为 null、原因 `direction_unknown`，对称障碍标签仍可计算。

有可靠逐笔数据时，可另设 `evaluation_path_mode=timestamp_path`，从tau截取真实路径；另行登记首个有效价格、事前ATR、评价期限和数据版本。它是独立评价配置，不能逐事件挑选与OHLC模式中更有利的结果。两种口径均只衡量指数信号的预测表现，不模拟可成交收益。

### 11.5 逐模块增量检验

依次比较，每一步只加入一个模块：

1. 基线：趋势状态 + 普通区间突破。
2. 加事实事件（SC/BC、Spring/UT、JOC/FTI、SOS/SOW）。
3. 加测试与反应（ST、Test、UT 测试、LPS/LPSY）。
4. 加波浪方向证据。
5. 加内部 Creek/Ice（`creek_mode` 对比）。
6. `baseline_mode` 对比（robust 对 standard）。
7. OI（`oi_mode=filter` 对 `annotate`）。
8. 量能口径对比（如 `sum_raw` 对 `weight_scaled`），各自独立校准。

比较对象是趋势、波动和区间位置相近的样本。若某模块只重复均线和普通突破的信息，可以保留作解释，但不能声称有独立预测优势。

### 11.6 时间滚动、参数搜索与多重检验

- 按时间划分训练、验证与最终样本外，研究起点为 24/6/6 个月滚动；窗口长度待验证。
- 只在训练/验证区间选参；最终样本外前冻结规则与配置。
- 登记每一次试验的配置与结果（包括被放弃的配置），报告试验总数。
- 首轮只在 8 个 C 级参数上做邻域扰动，报告结果在邻域内是否稳定。
- 多个模块、多个配置的比较需要多重检验校正（如 White Reality Check、Hansen SPA，或 Romano-Wolf / Holm）。
- 只有建立客观预测对象、用独立样本校准后才输出概率，并报告校准曲线、Brier 分数和样本量；默认 `probability=null`。

### 11.7 接入交易层前的额外验证

加权指数不可成交。交易层必须另行定义：从指数信号到可成交合约的映射规则、指数与该合约之间的基差处理、合约换月、下一可成交价、手续费、滑点、涨跌停成交限制与风险预算。指数上的边界不能直接当作可成交合约的止损价。收盘后才能知道的信号不能按同一根收盘价成交。这些不属于 V4 范围，需要独立验证。

## 12. 实施顺序与范围

### 12.1 实施顺序

1. 数据层：指数输入契约与合格性检查、价格定点表示、质量标记、版本记录。
2. 指标层：两种基准模式的 ATR 与 Bv、Effort/CumEffort、OI 特征、因果摆动点。**A 组验收。**
3. 趋势与区间：路径 A/B、区间生命周期、outside_streak、内部 Creek/Ice。
4. 事实事件：第 6 节全部，含快照环。
5. 生命周期：计数起点、立即级联、ST/Test/UT观察优先级、独立接纳、LPS反馈确认与承接事务。**B组验收。**
6. 波浪与方向证据：可与第 3–5 步并行开发。**C 组验收。**
7. 集成第9.4节处理顺序、真实可用时间评价与版本快照，执行**D组验收**及前缀一致性检查。
8. 全部工程验收通过后记录实现版本，按第11.5节逐模块开展样本研究；报告覆盖率、不可评估比例、延迟、失败/超时分布、独立区间数及相对基准的增量。

未实现的模块必须输出 `not_implemented`，不能当成没有发生。

### 12.2 V4 不覆盖的范围

- 实际合约输入、换月处理、合约涨跌停与封板事件。
- 从成分合约重建加权指数的方法；只有成分 K 线时的近似指数。
- 成分受限比例等指数层面的涨跌停质量字段。
- 无 Spring/UT 的 Phase C、曲线或斜线 Creek/Ice、嵌套区间、跨周期结构。
- 点数图横向计数与目标价。
- 由 OI 推断参与者身份或主动方向。
- 概率输出。
- 从指数信号到可成交合约的执行映射。

## 附录 A：评审意见处置

本附录先保留V3已经完成的历史修订，再给出本轮V3评审到V4的逐项映射。A.1/A.2是前代处置记录，不能据此声称本轮重新运行了代码审计或市场回测。V1及更早评审见V2附录A；所有实施规则以本版正文为准。

### A.1 V2 评审报告各项

沿用V3的核对结论：当时逐项对照V2原文并复算反例，第2.6项另对照项目代码。

| 评审条目 | 核对结论 | V4 处置 |
|---|---|---|
| 2.1 指数输入契约 | 成立 | 采纳，第 4.1 节；并把范围收窄为只做加权指数 |
| 2.2 指数精度不能继承合约 tick | 成立 | 采纳，F-13、第 4.3、5.2 节、`confirm_step`；用例 A03 |
| 2.3 指数无单一涨跌停价 | 成立 | 采纳，`limit_state=not_applicable`；删除 V2 封板事件 |
| 2.4 同步 OHLC | 成立，反例正确 | 采纳，第 4.2 节；用例 A02 |
| 2.5 量能口径 | 成立 | 采纳，`volume_method` 必须声明，不设默认；代理偏差列入第 11.5 节 |
| 2.6 项目 `_index` 不是加权指数 | 成立。已核对 `build_synthetic_index` 对单条主连做累计差值调整、V/OI 原样复制；`load_index_or_synthetic` 静默回退；`_index` 规格继承合约 tick 与涨跌停幅度 | 采纳，I-12、第 4.2 节；用例 A01。代码修改不在本规范交付范围内 |
| 3.1 重新越界后维持无法确认 | 成立，反例正确 | 采纳，滚动计数；用例 B03 |
| 3.2 突破过期与 LPS 期限冲突 | 成立 | 采纳选项 1 的变体：LPS 依附来源，来源未确认而过期时同步过期；用例 B09 |
| 3.3 创高与确认同时为真 | 成立，数值例正确 | 采纳，确认先于重置；用例 B10 |
| 3.4 最低点早导致长度恒为 1 | 成立 | 采纳并修改：回调段为 b..t-1，最低点只用于价格；用每回合固定截止 `lps_max_pullback_bars` 防止无限续期，不另设反应观察启动时间；用例 B11 |
| 3.5 两条确认路径状态不一致 | 成立 | 采纳，第 7.5 节唯一转换表 + 区间属性 outside_streak；用例 B12 |
| 3.6 内部 LPS 确认后无出口 | 成立 | 采纳，第 7.6 节确认后有效性表；用例 B13 |
| 4.1 E 组单独否决 | 成立。复算反例：活动率 1.2/0.6=2 ≥ 1.2，峰值 3/1 ≥ 1.2 且净位移 2.5>0.5 | 采纳并修改：活动组只描述方向，改为四条路径；用例 C01、C02、C03。V2 附录中该项的"采纳"应视为部分处理 |
| 4.2 活动率不是累计努力 | 成立，数值例正确 | 采纳，`wave_activity_rate` 与 `wave_cum_effort` 分开；用例 C05 |
| 4.3 SOS + 局部 LPS 不足以支持 Phase D | 成立 | 采纳，分为候选与规则支持；用例 B16 |
| 4.4 无 Spring 的 Phase C | 成立 | 采纳，声明 `not_implemented` |
| 4.5 深穿不等于大量供应 | 成立 | 采纳，改写第 3.2 节；新增 `shakeout_high_volume` 属性。V2 的措辞是我在 V2 中引入的错误 |
| 5.1 B 组引用未定义的 UT 测试 | 成立 | 采纳"完整定义"选项，第 7.4 节；用例 B15 |
| 5.2 高潮簇快照来源 | 成立 | 采纳快照环方案，第 6.0 节、F-15；用例 B06 |
| 5.3 两个样本门槛 | 成立 | 采纳，第 4.5 节表；用例 A06 |
| 5.4 截尾基准不能先宣称解决假阳性 | 部分成立 | 采纳措辞、双模式并存、ATR 初始化和用例时点修正（A04、A05）。保留 robust 为默认：高潮参照努力的基准不含高潮根、之后各根的基准含它，这是方向确定的偏差，不只是普通离群影响。另，评审称 20 根后两种基准重新相等，准确地说是高潮根离开窗口的第 21 根 |
| 5.5 "最后一个候选"与迟到锚点 | 成立 | 采纳，第 11.3、11.4 节 |
| 5.6 验收分层 | 成立 | 采纳，A/B/C 三组与第 12.1 节 |

### A.2 V3 已补充的历史问题

| 问题 | V4 处置 |
|---|---|
| SOS 不限制位置，突破根同时满足 SOS，V2 会从同一根创建两个 LPS 候选 | 来源优先级去重，第 7.6 节；用例 B14 |
| 有效性假设过期时价格仍在区间外，区间会停留在 `exit_up_candidate`，且永远不能再产生新突破、无法进入 Phase E | 转换表增加 `late_acceptance`；用例 B17 |
| V2 的 `supply_exhaustion` 路径标签允许 E=supply，但同一情形会被占优规则否决，标签与结论可能矛盾 | 占优直接由路径定义，不再存在矛盾 |
| 日内指数可能事后用当日收盘持仓重算权重，构成未来信息 | I-12、第 4.2 节；用例 A07 |
| 成分合约进出会造成指数成交量和 OI 的台阶变化；交割月前后 OI 系统性下降 | `constituent_change`、`delivery_season` 标记 |
| 项目的 `build_synthetic_index` 以"跳空 > 2 ATR"判定换月，会把非换月的真实跳空一并去掉，破坏 `gap_breakout` 与高潮宽幅信息 | 作为第 2.6 项的补充理由；由 I-12 排除 |
| 指数不可成交，指数边界不能直接当作合约止损价 | 第 1.1、11.7 节 |
| V2 附录称截尾解决"系统性假阳性"，表述过强 | 第 4.4 节改为"方向确定、影响待验证的偏差" |
| 外侧维持与区间状态不应依赖候选是否过期 | outside_streak作为区间属性逐根维护；V4中Phase E还必须引用当前有效acceptance |

### A.3 V3 评审逐项闭环

依据：[V3专业评审](WYCKOFF_MARKET_RECOGNITION_V3_REVIEW.md)。该报告所审V3文件SHA256为 `FC742F77113D3AA45259B6F21DB3DFDC2A5418E6F6E482ED410BA1D011E2D976`；本版继承该工作区版本。以下“采纳”指规范已作处置，工程验收和市场有效性仍待分别验证。

| 评审项 | V4决定 | 正文 | 验收 |
|---|---|---|---|
| R1：当根外侧计数晚于确认 | 先计算t根临时值，再确认，最后处理候选超时；下一根基准单独写入 | 4.4、5.5、9.4 | D01 |
| R2：晚接纳缺少失败出口 | hold/pullback/late_hold共用独立acceptance及失败规则；旧expired假设不复活 | 7.5、9.1 | D02、D11 |
| R3：依附级联太晚 | 已知失效立即传播；区分确认截止与当前有效期；LPS反馈及承接作为事务 | 7.1、7.6、9.4 | D03、D13—D16 |
| R4：内部LPS误标外部BUEC | 来源类型先分支；内部固定INTERNAL并保存内部参照位；LPSY明确镜像 | 7.6、7.7 | D04、D12 |
| R5：新ST先覆盖旧q | 旧观察先响应，再记录/激活新观察；Spring Test及UT Test相同优先级 | 7.1.3、7.2—7.4 | D05、D12 |
| R6：不可评估混同neutral | 三值运算、组值比较和最终归约统一；例子与模块开关同步修改 | 8.3、9.3 | D06、D19 |
| R7：可用前部分K线计入预测 | 锚点同时满足结束、到达及可用条件；默认只使用tau之后首根完整K线 | 4.8、11.4 | D07、D18 |
| R8：最小距离与严格比较冲突 | 向上>=、向下<=；边界突破自身的严格比较保留 | 4.3、7.2、7.4、7.6、7.7 | D08、D12 |
| 3.1：趋势快照下标 | trend_at(u)作为唯一函数；snapshot_u保存截至u的context_for_next_bar | 5.1、6.0 | D09、B06 |
| 3.2：B组历史确认还是当前有效 | 历史与当前列表分开；同区间、近期、可用、owner仍有效才计票；同源去重 | 7.10、8.3.2、8.4 | D10、D20 |
| 3.3：OI含义、缺失及零分母 | 只作描述；统计总量净变化不解释逐笔开平仓；缺项/零分母/晚到独立处理 | 1.2、4.6 | D17 |
| 3.4：不要持续扩大事件范围 | 保留V3既有形态及8个首轮参数；新增的是状态记录和验收，不新增市场形态 | 6.8、10.6、12 | A/B/C回归与D组 |

实施冻结条件：A/B/C/D与第11.1节工程检查全部通过，并形成可复现回放记录。市场研究通过与否另作判断，不用文档完整或构造用例通过替代样本外结果。

### A.4 V4.0 评审修复与口径澄清

V4.1 由 V4.0 当前实现逐项反例核验后修订。ER20/ER30 不是数学互斥，旧 LOW Spring 在深影线后不必价格失效，完整波描述存储也不等同于实时快照泄漏；修复采用正文的明确规则，不采用这些过强推论。

| 评审条目 | V4.1 处置 | 章节 |
|---|---|---|
| DOC-01 / CODE-04 | 同一当前有效接纳曾获得 Phase E 后，可在同向区间外整理完成时退役；历史记录绑定区间版本 | 5.5、9.1 |
| DOC-02 / CODE-02 | STRUCTURE 无确认期限，按联合条件退出；本根触发先刷新 | 7.1、8.4 |
| DOC-03 | JOC/SOS 跨阈值约束；允许 Test/SOS 的路径总期限取最大截止 | 7.3、7.4、10.6 |
| CODE-03 | 新分支/更深极值替换待确认测试路径，冻结新参照；保持收盘价格失效 | 7.1.2 |
| CODE-05 | 结构入口、来源、续期复用当前有效且可用触发器 | 8.4 |
| DOC-04 / CODE-06 | range_only 不创建内部 LPS；内部参照摆动限定原点后及对称价格范围 | 5.6、7.6、7.7 |
| DOC-05 / CODE-01 | 背离事实保留 onset，吸收假设冻结完成根当前区间，避免空边界 | 6.3 |
| CODE-07 / CODE-10 OI | 共享窗口计算和原因码；保留完整存储，按当前时间读实时视图 | 4.6 |
| CODE-08 | 宽度超限只拒绝路径 A 区间，继续止跌/止涨闭环 | 7.2 |
| CODE-09 | 单独 D 候选依附来源持续；有效且选中的新 LPS 给同向测试续期 | 7.10、9.1 |
| CODE-10 评价 | 原信号参照、pre_crossed、完整评价路径方向位移/回撤/MFE/MAE 和部分样本标记 | 11.4 |

## 附录 B：参考资料与来源边界

- [Wyckoff Analytics：The Wyckoff Method](https://www.wyckoffanalytics.com/wyckoff-method/)：三大定律、事件关系、阶段与非唯一结构路径的概念来源。本轮核对了LPS/BU、Phase D及供应减弱后较低需求也可推动价格的相关段落；本文的离散阈值、内部子类型和确认期限仍是工程选择。
- Richard D. Wyckoff 原始课程、Hank Pruden *The Three Skills of Top Trading*、David H. Weis *Trades About to Happen*：理论学习来源；本次未逐页核对原书，本文的公式、阈值和状态机都是工程提案，不归属于这些作品。

V4 的交付判断：可以据此在加权指数上实现客观、可回放的识别流程；所有市场阈值和预测价值仍待验证。不可违背的是因果性、真实可用时间、边界冻结、输入来源可追溯与证据可追溯，而不是任何未经验证的倍数或根数。
