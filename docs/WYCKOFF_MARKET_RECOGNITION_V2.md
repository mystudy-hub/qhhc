# 维科夫实时行情识别规范 V2.0

版本日期：2026-10-21  
规则版本：`2.0`；参数版本：`v2-default-unvalidated`  
前置文档：[V1.0 规范](WYCKOFF_MARKET_RECOGNITION_V1.md)、[V1.0 评审](WYCKOFF_MARKET_RECOGNITION_V1_REVIEW.md)  
版本性质：完整替代 V1.0 的工程规范；阈值尚未完成样本外验证  
基础输入：OHLCV、最小跳动、交易日历、实际合约与数据版本；可选持仓量和涨跌停价

> 本规范要求识别器"确定、可复现"：相同数据、规则版本和参数版本产生相同输出。这不代表对吸筹、派发、反转或未来收益有确定判断。文中默认数字全部是研究起点。

## 0. V2 相对 V1 的变化

V2 保留 V1 的总体架构：事实与解释分离、真实可用时间、冻结边界、候选生命周期、识别与交易分离。修改来自三个来源：V1 评审报告（下称"评审"）中经核对成立的缺陷；评审报告未覆盖、但本次复核发现的问题；对评审建议的取舍。完整处置见附录 A。

| 类别 | 主要变化 | 章节 |
|---|---|---|
| 规范缺口修复 | Phase E 增加同向条件；所有普通突破都创建有效性假设；首根深穿先记事实再分流；多根模式冻结起始上下文；失效计数起点逐条登记；正文公式使用参数符号 | 6、7、9 |
| 基准污染 | 成交量基准改为截尾均值，ATR 改为截尾真实波幅，避免高潮根抬高基准后让 ST/Test 更容易满足"低量窄幅" | 4.2 |
| 期货数据 | 换月改为"按实际合约维护指标、用自身历史预热"；新增涨跌停状态与封板事件；持仓量作为描述特征接入，不作为默认判定条件 | 4.1、4.4、4.5、4.6 |
| 测试结构 | "测试形态"与"测试后反应"分两步记录；ST 和 Test 不再强制阳线；V1 严格规则保留为严格子类型 | 7.2、7.3 |
| 覆盖范围 | 新增多根高潮簇、深穿收回、跨区间复合事件、内部 Creek/Ice、内部 LPS/LPSY、边界上方 LPS | 5.6、6、7.5 |
| 方向证据 | 改为三组证据并要求价格结果组参与，消除 V1 四项投票中的重复计票；波浪增加净位移和回吐 | 8 |
| 参数治理 | 参数分为首轮校准、冻结基线、模式开关三级；首轮只开放 8 个 | 10 |
| 研究方法 | 明确事件单位、聚类重采样、多重检验记录与逐模块增量检验 | 11 |

V2 与 V1 的输出不能混用。任何 V1 回测结果不能视为 V2 已验证。

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

识别器只输出观察与研究信号。仓位、买卖、止损、成交模拟由独立策略层决定；结构标签不能自动解释为已经验证的买卖点。

### 1.2 三大定律的量化替代

| 理论概念 | V2 可观测替代量 | 解释边界 |
|---|---|---|
| 供求法则 | 波幅、收盘位置、净位移、波段推进与回吐 | OHLCV 只反映供求的表现，不能直接观测挂单与参与者意图 |
| 因果法则 | 区间年龄、宽度、触碰、内部量价结构 | 区间根数不决定后续涨跌幅；V2 不实现点数图目标价 |
| 努力与结果 | 标准化努力（相对成交量）与波幅、净位移、波段结果的组合 | 大努力小结果是背离证据，吸收方向需要上下文和后续反应 |

持仓量（OI）是期货特有信息。OI 增加说明多空双方同时新开仓，OI 减少说明双方同时平仓；它不能说明哪一方是主动方。V2 把 OI 变化作为事件的描述特征保存，默认不参与任何事件或假设的成立判定；作为判定条件属于需要独立验证的研究模式（第 4.4 节）。

Composite Man 是理解大资金行为的分析模型。V2 不输出"已证明某机构吸筹"之类的身份或订单行为判断。

### 1.3 操作流程

1. 登记研究对象：品种、实际合约选择规则、周期、交易时段、成交量单位、数据源；保存规则版本和参数版本。
2. 选择输入配置：日线或日内同槽基准；检查每个实际合约的预热与同槽历史，不足时输出具体缺项。
3. 按第 9.4 节顺序逐根处理已收盘 K 线，保存快照、事件和假设状态转换。
4. 阅读结果：先看质量与市场状态，再看仍有效的结构假设，最后看下一确认、失效条件和期限。
5. 收盘后复查新增事件和状态转换，检查数据修订、边界版本变化、换月与封板标记。
6. 按第 11 节验证，按第 10、12 节分批校准；通过以前一律标记"未验证基线"，不输出概率。

## 2. 约束分类

### 2.1 三类约束

| 类别 | 含义 | 能否更改 |
|---|---|---|
| I：不变原则 | 因果性、真实可用时间、事实与解释分离、可复现性 | 仍称为实时识别器就不能违背 |
| F：V2 固定约定 | 公式、枚举、时间计数、边界与波浪方法 | 同一版本内不能更改；修改需升级规则版本 |
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
| I-10 | 原始成交量和持仓量变化都不能直接称为主动买量、主动卖量或某方开仓；方向归属需要额外数据与定义 |
| I-11 | 多根模式中声明为"起始前已知"的上下文，必须读取模式第一根之前的快照（第 6.0 节） |
| I-12 | 识别器使用的实际合约选择规则只能依赖当时已知的数据（第 4.5 节） |

### 2.3 F 类 V2 固定约定

| 编号 | 内容 |
|---|---|
| F-01 | 正式输出只使用已收盘 K 线；盘中输出单列为 `provisional` |
| F-02 | `Spread=H-L`；真实波幅单独计算；成交量基准和 ATR 比较基准排除当前 K 线 |
| F-03 | ATR 使用 Wilder 平滑；真实波幅输入先按第 4.2 节截尾；成交量基准为截尾均值；均线用 SMA；中位数偶数样本取中间两数均值 |
| F-04 | 严格按公式使用 `>`、`>=`、`<`、`<=` |
| F-05 | 时间窗口按有效交易 K 线计数，上下界都包含；"N 个价格变化"需要 N+1 个收盘 |
| F-06 | 前台区间的 S/R 是固定水平区域；内部 Creek/Ice 使用第 5.6 节的因果摆动点，不使用曲线拟合 |
| F-07 | 所有候选采用相同生命周期，状态转换只追加不覆盖 |
| F-08 | 波浪使用第 8 节的因果划分，成交量不按事后极值重新分配 |
| F-09 | Spring/UT 的量能分组、深穿分界和收回窗口是工程分类，不宣称为 Wyckoff 或 Pruden 的固定数字 |
| F-10 | 自动识别的 LPS/LPSY 只覆盖第 7.5、7.6 节列出的子类型；其他形态标记为未覆盖 |
| F-11 | 跨根或跨时段比较量能时使用标准化努力（第 4.3 节），不直接比较原始成交量 |
| F-12 | 每条失效计数必须登记计数起点（第 7.1 节） |

### 2.4 P 类参数的使用要求

第 10 节登记全部默认数字和模式开关。调整时保存：参数名、旧值、新值、适用品种、周期、时段、训练区间、样本外结果和修改原因。运行中不能因信号失败而临时放宽；在线校准必须作为独立规则版本验证。

## 3. 术语与阶段

### 3.1 计算与数据术语

| 名称 | 含义 |
|---|---|
| TR（区间语境） | Trading Range，交易区间；代码中真实波幅写作 `true_range`，避免混淆 |
| ATR | 平均真实波幅；V2 默认使用截尾真实波幅输入 |
| `A_t` | 事件比较用 ATR，等于 `ATR_(t-1)`，不含当前根 |
| `A_ref` | 区间建立时冻结的 ATR，用于该区间的容忍度和宽度 |
| Bv | 成交量基准，排除当前根 |
| RVOL | 单根相对成交量 `V_t/Bv_t` |
| Effort | 标准化努力：一段 K 线的 `sum(V)/sum(Bv)`；单根时等于 RVOL |
| NS | 标准化波幅 `(H-L)/A_t` |
| CP | 收盘位置 `(C-L)/(H-L)`，0 接近低点，1 接近高点 |
| LW / UW | 下影 / 上影占比 |
| NetMove | `(C_t-C_(t-1))/A_t` |
| ER | 方向效率：净位移绝对值 / 逐根变化绝对值之和；不含方向 |
| SignedDelta | 有方向的净位移，`(C_end-C_start)/A`，用于补足 ER 的方向信息 |
| OI / ΔOI | 持仓量及其相邻变化；只在同一实际合约内计算 |
| 封板 | 整根 K 线锁定在涨停或跌停价，见第 4.6 节 |
| 起始上下文 | 多根模式第一根之前的快照，见第 6.0 节 |
| 因果摆动点 | 需要后续 k 根确认的局部高低点，确认前不可用，见第 5.6 节 |
| `not_evaluable` | 必要数据或指标不可用，不是"条件未触发" |
| `not_implemented` | 本版本未覆盖，不是"市场没有该事件" |

### 3.2 威科夫事件术语

| 缩写 | 中文 | V2 用法 |
|---|---|---|
| PS / PSY | 初步支撑 / 初步供应 | 仅解释性术语，不单独输出 |
| SC / BC | 抛售高潮 / 买入高潮 | 单根版本和多根高潮簇两种特征（第 6.1、6.2 节） |
| AR | 吸筹语境为自然反弹 `AR_RALLY`，派发语境为自然回落 `AR_REACTION` | 两个方向使用不同枚举 |
| ST | 二次测试 | 分为测试形态和测试后反应（第 7.2 节） |
| Spring | 弹簧，已知支撑被短暂下穿后收回 | 按收回位置、深度、量能分型（第 6.4 节） |
| Shakeout | 震仓，深度下穿后收回 | V2 用 `SHAKEOUT_RECLAIM` 表示，必须通过后续测试才增强证据 |
| Test | 测试 | 测试形态与测试后反应分两步（第 7.3 节） |
| UT / UTAD | 上冲回落 / 派发后上冲 | UTAD 需要成熟区间和起始上下文中的供应占优证据 |
| SOS / SOW | 力量信号 / 弱势信号 | 可以发生在区间内部 |
| JOC (JAC) | 越过小溪 | 越过事前已知阻力（前台 R 或内部 Creek）的强突破 |
| FTI | 跌穿冰面 | JOC 的向下对应 |
| BU / BUEC | 回撤 / 回到小溪边 | BUEC 是 LPS 的一个子类型 |
| LPS / LPSY | 最后支撑点 / 最后供应点 | 子类型见第 7.5、7.6 节 |

Spring #1/#2/#3 等编号在不同教学体系中定义不一致。V2 不把编号作为分类依据，事件代码只描述可观测特征：`SPRING_LOW_VOLUME`、`SPRING_HIGH_VOLUME`、`SPRING_OTHER`、`SHAKEOUT_RECLAIM`。编号别名只能用于显示，并须在配置中声明所用的教学体系。

深穿收回不是最强信号。深穿伴随大量供应，说明供应尚未被吸收完毕；V2 要求它与高量 Spring 一样，必须通过后续测试才增强吸筹解释。

### 3.3 SOS、JOC、BU、BUEC、LPS 的关系

- SOS 描述力量表现，可在区间内部；JOC 额外要求越过事前已知的阻力。普通低量上破是 `BREAKOUT_UP`，不满足 JOC 的量价条件。
- BU 是突破后的回撤过程；BUEC 要求回到旧阻力附近。
- LPS 描述 SOS 或突破后的回调中供应减弱、随后需求重新推进的支撑点。V2 自动识别三种子类型：回到小溪边（`LPS_AT_CREEK`，即 BUEC）、在边界上方止跌（`LPS_ABOVE_CREEK`）、区间内部 SOS 之后（`LPS_INTERNAL`）。
- 突破后的有效性由第 7.4 节的延续或失效规则单独判断，不依赖是否出现 BUEC。

### 3.4 Phase A—E

| 阶段 | 吸筹方向 | 派发方向 | V2 输出原则 |
|---|---|---|---|
| A | 下跌停止：SC、AR、ST | 上涨停止：BC、AR、ST | 止跌或止涨证据，不等于永久反转 |
| B | 区间内部运行 | 区间内部运行 | 区间成熟不等于方向已知 |
| C | 剩余供应的测试，可能出现 Spring | 剩余需求的测试，可能出现 UT/UTAD | Spring 和 UTAD 都不是必经路径 |
| D | SOS 与较弱回调 / LPS | SOW 与较弱反弹 / LPSY | 可以仍在区间内向边界推进 |
| E | 离开区间后的同向拉升 | 离开区间后的同向下跌 | 必须满足方向条件（第 9.1 节） |

V2 保存 `phase_candidate` 与 `phase_supported_by_rules`，允许多次 ST、多次 LPS、无 Spring 的结构和方向暂不明的区间。

## 4. 数据输入、计算定义与时间口径

### 4.1 输入字段

```text
必需：symbol, actual_contract, timeframe, bar_start, bar_end, trading_day
      open, high, low, close, volume, tick_size
      is_closed, received_at, data_source, data_version, session_id, quality_flags
可选：open_interest, oi_available_at
      limit_up_price, limit_down_price, prev_settlement
```

基本合法性：`H>=max(O,C)`、`L<=min(O,C)`、`H>=L`、`V>=0`、`OI>=0`、时间有序、同一合约同一周期无重复主键。不要求期货价格永远大于零。

缺少可选字段时，相关特征输出 `not_evaluable`，其他规则照常运行。日线 OI 若在收盘后才发布，`oi_available_at` 按实际发布时间记录，OI 特征不早于该时间可用。

### 4.2 波幅、成交量基准与稳健化

令 t 为当前已收盘 K 线，`A_t=ATR_(t-1)`。

```text
true_range_t = max(H_t-L_t, abs(H_t-C_(t-1)), abs(L_t-C_(t-1)))

# ATR 输入截尾：避免高潮根把后续比较基准抬高
med_tr_t = median(true_range_(t-n_atr), ..., true_range_(t-1))
tr_capped_t = min(true_range_t, tr_cap_mult * med_tr_t)
ATR 初始值 = 前 n_atr 个 tr_capped 的算术平均
ATR_t = ((n_atr-1)*ATR_(t-1) + tr_capped_t) / n_atr

# 成交量基准：截尾均值，排除当前根
W_t = {V_(t-n_volume), ..., V_(t-1)} 中的合格 K 线
med_v_t = median(W_t)
Bv_t = mean( min(V_i, volume_cap_mult * med_v_t) for V_i in W_t )

RVOL_t = V_t / Bv_t
Spread_t = H_t-L_t
NS_t = Spread_t / A_t
CP_t = (C_t-L_t) / Spread_t
LW_t = (min(O_t,C_t)-L_t) / Spread_t
UW_t = (H_t-max(O_t,C_t)) / Spread_t
NetMove_t = (C_t-C_(t-1)) / A_t
Gap_t = abs(O_t-C_(t-1)) / A_t
```

截尾只作用于基准；事件本身的 RVOL、NS、真实波幅使用原始值。`tr_cap_mult`、`volume_cap_mult` 取无穷大时退化为 V1 口径。截尾的代价是波动率真实抬升时 ATR 适应变慢，约需 `n_atr/2` 根后中位数才跟上，需要在验证中报告。

合格 K 线：非封板、非 `data_gap`、成交量为真实值。`W_t` 中合格 K 线少于 `volume_min_samples` 时，Bv 不可用。

`H=L` 时 CP、LW、UW 不可用，不填 0.5。Bv 为 0 或 ATR 为 0 时相应指标不可用，相关规则返回 `not_evaluable`。

### 4.3 标准化努力与日内季节性

```text
Effort(a..b) = sum(V_i, i=a..b) / sum(Bv_i, i=a..b)
```

要求区间内每根 Bv 都可用。单根时 `Effort=RVOL`。所有跨根比较（ST 对 SC、Test 对 Spring、回调对突破）使用 Effort 之比，不使用原始成交量之比。这样日内配置下，不同时段槽位的正常成交量差异不会扭曲比较。

| 运行配置 | Bv 的取样集合 W_t |
|---|---|
| 日线 / 非日内 | 前 `n_volume` 根 |
| 日内 `session_matched` | 过去 `session_baseline_days` 个交易日中同一 session、同一 bar slot 的 K 线；至少 `session_min_samples` 个合格样本 |

日线配置可以额外保存原始量之比作为研究特征，但不参与 V2 默认判定。

### 4.4 持仓量特征（描述用）

```text
dOI_t = OI_t - OI_(t-1)                      # 同一实际合约内
OIz_t = dOI_t / median(|dOI_(t-n_oi)|, ..., |dOI_(t-1)|)
OIz(a..b) = sum(dOI_i, i=a..b) / median(|dOI_(a-n_oi)|, ..., |dOI_(a-1)|)
```

| 价格方向 | `OIz >= oi_flat_band` | `OIz <= -oi_flat_band` | 其他 |
|---|---|---|---|
| `C_t>C_(t-1)` | `up_oi_up` | `up_oi_down` | `up_oi_flat` |
| `C_t<C_(t-1)` | `down_oi_up` | `down_oi_down` | `down_oi_flat` |
| 平收 | `flat_oi_up` | `flat_oi_down` | `flat_oi_flat` |

每个事件都保存起止范围内的 `OIz` 和象限标签。常见解读（例如 `up_oi_down` 常与空头回补并存）只能作为显示说明，不能写成已确认的参与者行为。

`oi_mode` 默认 `annotate`：只记录，不参与任何判定。`filter` 模式把 OI 条件加入指定事件，属于新的研究配置，必须与 `annotate` 做同样本对比，并单独登记结果。

### 4.5 换月与实际合约

- 指标、区间、波浪和假设按实际合约维护，用该合约自身历史计算；连续合约代码不能当作永不变化的合约。
- 主力合约选择规则在研究开始前冻结。默认：候选合约的前一交易日收盘 OI 连续 `roll_confirm_days` 天超过当前主力，下一交易日起切换。只能使用 T-1 及以前的数据，禁止使用事后的主力判定。
- 候选合约从 OI 进入前两名起开始跟踪并计算状态。切换时直接使用其已有状态，不在切换当天清零重算。
- 合约日成交量首次达到当时主力的 `liquid_min_ratio` 倍之前的历史标记为 `illiquid_history`：参与价格指标，不进入成交量基准，不用于建立区间或波浪。预热从流动性合格之日计数。
- 预热不足时输出 `not_evaluable` 及缺项。日线上主力期通常只有数十个交易日，预热不足是预期情况，必须如实报告覆盖率，不能用旧合约数据补齐。
- 旧合约的区间、候选和波浪不迁移到新合约。
- 使用复权连续序列做研究属于独立的数据配置 `continuous_adjusted`：冻结复权方法，换月根标记 `is_roll_boundary`，跨换月的候选不能确认；结果不能与实际合约输出混用。

### 4.6 涨跌停、零量与数据缺口

涨跌停价来自数据源；没有时用 `prev_settlement` 和合约规格的涨跌停幅度按 tick 规则计算；都没有时 `limit_state=unknown`。

```text
locked_down: H_t = L_t = limit_down_price        # 整根锁跌停
locked_up:   H_t = L_t = limit_up_price
close_at_limit_down: C_t = limit_down_price 且非 locked_down
close_at_limit_up:   C_t = limit_up_price 且非 locked_up
```

- 封板 K 线输出 `LIMIT_LOCK_DOWN` / `LIMIT_LOCK_UP` 事实；不触发任何量能事件，不进入成交量基准。封板的低成交量是价格被锁定造成的，不能解释为供应枯竭。
- 连续封板结束后的第一根非封板 K 线 u 输出 `LIMIT_RELEASE_DOWN` / `LIMIT_RELEASE_UP`，并保存 `RVOL_u`、`CP_u`、`OIz_u` 和封板根数。u 可以作为 SC/BC 和高潮簇的检测对象；国内期货的抛售高潮常出现在跌停打开之后，不能因为前面有封板而排除。
- 收于涨跌停价的非封板 K 线可以参与事件检测，但标记 `limit_close`；强事件的确认根若带 `limit_close`，确认结果附带该标记。
- 真实零量 K 线保留，但不触发低量 Spring、Test、ST 或吸收事件；缺失 K 线不能填零。
- 交易时段内缺失应有的 K 线时，受影响窗口暂停并输出 `data_gap`，修复前不用不完整窗口确认事件。
- 数据修订时保存原实时版本与修订版本；实时回放使用当时的版本。

### 4.7 时间与数值细节

`t0+a～t0+b` 指第 a 至第 b 根后续有效 K 线，两端都包含。

单根首次可用时间不早于 `max(bar_end, received_at)+计算延迟`；多根条件取所有相关数据的最晚到达时间。迟到的 K 线只能在到达后影响实时判断，不能倒写回原收盘时刻。

价格按合约 tick 规则用整数 tick 或十进制定点表示；比较前不先四舍五入阈值。

## 5. 趋势背景、交易区间与内部参照位

### 5.1 趋势背景

两个时间口径分开命名：

- `trend_context_t`：用于事件 t 的前置背景，只使用 t-1 及以前的数据。
- `trend_now_t`：用于第 9.1 节当前状态，使用截至 t 的数据。

以 `trend_context_t` 为例（窗口长度 n=`trend_window`）：

```text
Delta_n = (C_(t-1)-C_(t-1-n)) / A_t                         # 有方向
ER_n = abs(C_(t-1)-C_(t-1-n)) / sum(abs(C_i-C_(i-1)), i=t-n..t-1)
Slope_n = OLS_slope(C_(t-n), ..., C_(t-1)) / A_t             # n 个收盘
```

ER 分母为 0 时定义为 0。Delta 和 ER 使用 n 个价格变化（n+1 个收盘），OLS 使用 n 个收盘，跨度相差一根，均不含当前根。

```text
# SMA_fast / SMA_slow 的周期为 trend_sma_fast / trend_sma_slow
downtrend_context: C_(t-1) < SMA_fast_(t-1), C_(t-1) < SMA_slow_(t-1),
                   Delta_n <= -trend_min_displacement_atr,
                   Slope_n <= -trend_min_abs_slope_atr,
                   ER_n >= trend_min_er
uptrend_context:   上述条件的对称版本
```

`trend_now_t` 把以上窗口整体后移一根，以 t 结尾，比较基准使用 `ATR_t`。

### 5.2 支撑、阻力与容忍度

```text
S, R = 固定候选下沿、上沿；M = (S+R)/2；W = R-S
A_ref = 路径A沿用高潮的 A_SC 或 A_BC；路径B用建立区间前一根 ATR
epsilon = max(boundary_ticks*tick_size, boundary_atr_fraction*A_ref)
range_min_width_atr*A_ref <= W <= range_max_width_atr*A_ref
```

支撑区域 `[S-ε,S+ε]`，阻力区域 `[R-ε,R+ε]`。S、R、ε、A_ref 随区间版本冻结。

区间年龄 = 从原点到当前的有效 K 线数（含两端）；`bars_since_established` 单独保存。事件始终要求区间已经建立；新建区间从下一根开始用于检测。

### 5.3 路径 A：高潮—反应—测试

SC（或高潮簇）→ AR_RALLY → ST 建立区间，BC 方向对称；细则见第 7.2 节。原点为高潮第一根，建立时间为 ST 反应确认时间。

AR 确认时立即检查宽度：`W = H_AR - L_SC`。V2 取消 V1 中独立的 AR 最小幅度参数，AR 的最小幅度直接等于 `range_min_width_atr*A_SC`。这样 AR 幅度满足但区间宽度不满足的矛盾不会出现。`W > range_max_width_atr*A_SC` 时，AR 事实保留，区间候选以 `width_too_large` 拒绝。

### 5.4 路径 B：一般震荡区间

没有高潮时，使用截至建立时刻的最近 `generic_range_window` 根有效 K 线：

```text
S = min(L)，R = max(H)
ER_window <= generic_range_max_er          # 需要 window+1 个收盘
至少 range_interior_close_fraction 的收盘位于 [S+ε, R-ε]
上下边各至少 range_min_touches_per_side 次有效触碰
窗口内无 data_gap，且不含 illiquid_history
```

触碰：K 线范围与边界区域相交。每侧按时间贪心选取，相邻计数触碰间隔至少 `touch_min_separation_bars` 根，且中间至少一根离开该侧区域。定义极值的那一根算作一次触碰，因此一根孤立长影线最多贡献一次触碰，通常导致建立失败而不是抬高边界。

路径 B 只确认工程上的震荡区间，不能凭此确认吸筹、派发或 Phase A。

### 5.5 区间生命周期

每个"实际合约＋周期"最多一个前台区间。区分两种边界：

- 事件参照边界：事件创建时引用的区间版本 S/R/ε，永久保存，决定该事件后续的成功与失败。
- 当前分析边界：前台区间的 S/R/ε，可以因区间退出、超时或被替代而更换版本。

```text
building -> active -> exit_up_candidate / exit_down_candidate
                   -> exit_up_accepted / exit_down_accepted -> retired
active -> expired
active -> superseded
```

- 普通突破对应 `exit_*_candidate`；第 7.4 节外侧维持成立时转为 `exit_*_accepted`。突破失效回到 `active`，但不恢复原突破假设。
- `retired`：Phase E 获支持后，在区间外形成满足路径 B 的新整理时，旧区间退役，新区间下一根生效。
- `expired`：前台年龄达到 `range_max_age` 仍无明确外部趋势；下一根起允许新建区间。超时不代表方向。
- `superseded`：区间年龄至少 `range_stale_min_age`，最近 `range_stale_bars` 根没有触碰任一边界区域，且区间内部完成一个新的路径 B 区间。旧区间被替代，新区间作为前台，`parent_range_id` 指向旧区间。这解决了旧宽区间长期压住新结构的问题。

任何状态下，旧区间的边界继续服务于引用它的事件和假设，直到这些对象自身结束。

### 5.6 因果摆动点与内部 Creek / Ice

```text
摆动高点 j：H_j > max(H_(j-k), ..., H_(j-1))
           H_j >= max(H_(j+1), ..., H_(j+k))
           H_j - min(L_(j+1), ..., L_(j+k)) >= pivot_min_reaction_atr * A_j
确认时间：j+k 收盘；最早用于检测：j+k+1
摆动低点：对称
```

k=`pivot_confirm_bars`。摆动点在确认前不存在，确认后也不改变位置。

- 内部 Creek：前台区间原点之后、最近确认的、价格位于 `[M, R+ε]` 的摆动高点。
- 内部 Ice：前台区间原点之后、最近确认的、价格位于 `[S-ε, M]` 的摆动低点。
- 被事件引用时，Creek/Ice 的价格、确认时间与区间版本一起冻结在事件记录中。

`creek_mode=range_and_internal`（默认）时，内部 Creek/Ice 用于第 6.6 节的内部 JOC/FTI 与第 7.5 节的内部 LPS/LPSY；`range_only` 时这些事件输出 `not_implemented`。内部事件使用独立代码，便于逐模块对比。曲线或斜线 Creek 不在 V2 范围内。

## 6. 事实事件规则

本节判断"形态是否发生"；结构假设在第 7 节更新。所有默认数字属于 P 类，见第 10 节。

### 6.0 多根模式的通用约定

多根模式从第一根 p 开始，到检测完成根 r 结束（p≤r）。

```text
event_time  = bar_end(p)
detected_at = bar_end(r)
available_at = r 及所有相关数据的最晚可用时间 + 计算延迟
onset_snapshot = p-1 收盘后的快照：
    range_id, range_version, S, R, epsilon, range_age
    trend_context, direction_evidence_ids, dominance_labels
```

- 规则中写明"起始前已知"或"起始上下文"的条件，只读取 `onset_snapshot`，不读取 r-1 的快照。例如 p 刺破、p+1 收回的 UT：如果供应占优证据在 p 收盘才成立，它不属于起始上下文，不能用于创建 UTAD 候选。
- 检测完成时新增的证据可以用于检测当根的新解释，但必须记录为"确认时新增证据"，并带自身的可用时间。
- 观察对象在 p 收盘时创建并保存 `onset_snapshot`；在 r 收盘时只读取该快照，不重新计算。
- 单根事件视为 p=r，起始上下文就是 t-1 的快照，与第 9.4 节第 2 步一致。

### 6.1 SC / BC：单根版本

```text
trend_context_t = downtrend
L_t < min(L_(t-n_ext), ..., L_(t-1))          # n_ext = climax_new_extreme_window
RVOL_t >= climax_min_rvol
NS_t >= climax_min_ns
CP_t >= sc_min_cp
LW_t >= climax_min_rejection_wick
V_t > 0，且 t 不是封板 K 线
```

输出 `SC_FEATURE`，创建 `SC_BOTTOM_CANDIDATE`。不要求 SC 是阴线。BC 对称：上涨背景、新高、`CP_t<=bc_max_cp`、`UW_t>=climax_min_rejection_wick`，输出 `BC_FEATURE` 和止涨候选。

跌停打开后的第一根 K 线（`LIMIT_RELEASE_DOWN`）按本规则正常检测；新低比较包含此前的封板 K 线。

### 6.2 SC / BC：多根高潮簇

连续下跌中高潮常持续 2～3 根，最后一根才出现止跌特征。V2 增加高潮簇，以 t 为完成根，依次检查 m=2、m=3，取第一个满足的 m，令 p=t-m+1：

```text
起始上下文：trend_context_p = downtrend（onset_snapshot）
min(L_p, ..., L_t) < min(L_(p-n_ext), ..., L_(p-1))
Effort(p..t) >= cluster_min_effort
(max(H_p..H_t) - min(L_p..L_t)) / A_p >= cluster_min_span_atr
完成根 t：CP_t >= sc_min_cp 或 LW_t >= climax_min_rejection_wick
完成根 t：RVOL_t >= cluster_stop_min_rvol
p..t 内无封板、无 data_gap，各根 V>0
```

输出 `SC_CLUSTER_FEATURE`。若 t 同时满足第 6.1 节单根规则，只输出单根 SC，高潮簇作为别名关联，不计作两条证据。BC 高潮簇对称。

高潮（单根或簇）统一保存以下参照值，供第 7.2 节使用：

```text
t0 = t（检测完成根，所有窗口从这里计数）
L_SC = min(L_p..L_t)；A_SC = A_p；epsilon_SC = max(boundary_ticks*tick, boundary_atr_fraction*A_SC)
Effort_SC = Effort(p..t)；NS_SC_ref = max(NS_p..NS_t)
```

### 6.3 努力与结果背离

```text
单根窄幅高量：RVOL_t >= absorption_min_rvol，NS_t <= absorption_max_ns，V_t>0
三根低推进：  Effort(t-2..t) >= absorption_min_rvol
             abs(C_t-C_(t-3)) / A_(t-2) <= low_progress_max_move_atr
             三根 V>0，同一合约、数据连续
```

分别输出 `EFFORT_RESULT_NARROW_BAR`、`EFFORT_RESULT_LOW_PROGRESS`，并保存路径距离 `sum(abs(C_i-C_(i-1)))/A`。若存在前台区间且本根触及支撑或阻力区域，另建 `SUPPORT_ABSORPTION_CANDIDATE` 或 `RESISTANCE_ABSORPTION_CANDIDATE`。无区间时只报背离事实。

后续确认：支撑侧在 1～`absorption_response_max_bars` 根内出现 SOS，或出现 `C>O, NetMove>=strong_bar_min_move_atr, CP>=sos_min_cp` 的向上反应，确认 `SUPPORT_RESPONSE_SEQUENCE`；连续两根收盘 `<S-ε` 使候选失效（计数起点见第 7.1 节）。阻力侧对称。

### 6.4 支撑下穿：Spring、深穿与复合事件

**步骤 1：下穿事实（无深度限制）。** 在 p 检测：

```text
起始上下文：前台区间已建立，range_age >= event_min_range_age
L_p < S-epsilon
C_(p-1) >= S-epsilon
```

输出 `SUPPORT_UNDERCUT`，记录 `depth_p=(S-L_p)/A_p`。若 p 为封板 K 线，事实标记 `limit_affected`，不进入下面的收回观察（`not_implemented`）。

**步骤 2：按深度分流。**

```text
depth_p <= spring_max_depth_atr：创建 SPRING_RECLAIM_WATCH，收回根 r ∈ {p, ..., p+spring_max_pattern_bars-1}
depth_p >  spring_max_depth_atr：输出 DEEP_UNDERCUT，创建 SHAKEOUT_RECLAIM_WATCH，r ∈ {p, ..., p+shakeout_max_pattern_bars-1}
```

Spring 观察期间若后续根使 `(S-min(L_p..L_t))/A_p > spring_max_depth_atr`，在该根输出 `DEEP_UNDERCUT`，观察对象转为深穿观察，起点仍为 p，窗口仍从 p 计数。

**步骤 3：收回。** 收回根 r 满足：

```text
C_r > S+epsilon
CP_r >= spring_min_cp
p..r 内各根 V>0，无封板
```

**步骤 4：按收回位置和量能分型。**

| 条件 | 事件代码 |
|---|---|
| 深穿分支，`C_r <= R+ε` | `SHAKEOUT_RECLAIM` |
| Spring 分支，`C_r <= R+ε`，p..r 每根 `RVOL < spring_low_volume_max_rvol` | `SPRING_LOW_VOLUME` |
| Spring 分支，`C_r <= R+ε`，`max(RVOL_p..RVOL_r) > spring_high_volume_min_rvol` | `SPRING_HIGH_VOLUME` |
| Spring 分支，`C_r <= R+ε`，其他 | `SPRING_OTHER` |
| 任一分支，`C_r > R+ε` | `UNDERCUT_RECLAIM_BREAKOUT`（复合事件） |

复合事件同时按第 6.5 节检测突破，并与产生的 `BREAKOUT_UP` 关联，不计作两条独立证据。

保存 `L_SP=min(L_p..L_r)`、`Effort_SP=Effort(p..r)`、`max_RVOL`、`OIz(p..r)`、分支与 `onset_snapshot`；`tS=r`。

观察窗口结束仍未收回，输出 `UNDERCUT_NOT_RECLAIMED`。这不等于 FTI；向下突破仍按第 6.5 节的收盘规则单独判断。

### 6.5 普通突破、JOC 与 FTI

```text
BREAKOUT_UP：前台区间已建立且 range_age >= event_min_range_age
             C_(t-1) <= R+epsilon，C_t > R+epsilon
BREAKOUT_DOWN：C_(t-1) >= S-epsilon，C_t < S-epsilon
```

**每个普通突破都创建方向有效性假设：** `BREAKOUT_UP` 创建 `UP_BREAKOUT_VALIDITY`，`BREAKOUT_DOWN` 创建 `DOWN_BREAKOUT_VALIDITY`（第 7.4 节）。同方向已有活动的有效性假设时（例如收盘曾回到容忍带后再次越界），只记录新的突破事实并关联到已有假设，不新建假设。

强度特征写在同一事件上：

```text
JOC：C_t>O_t，RVOL_t>=strong_break_min_rvol，NS_t>=strong_break_min_ns，
     CP_t>=joc_min_cp，NetMove_t>=strong_bar_min_move_atr
FTI：C_t<O_t，RVOL_t>=strong_break_min_rvol，NS_t>=strong_break_min_ns，
     CP_t<=fti_max_cp，NetMove_t<=-strong_bar_min_move_atr
Gap_t >= gap_flag_min_atr 时追加 gap_breakout
```

事件字段 `strength ∈ {ordinary, joc, fti}`。强度只用于分组评价，不改变第 7.4 节的有效性规则。`JOC implies SOS_features` 作为关联关系保存。

### 6.6 区间内部 SOS / SOW 与内部 JOC / FTI

```text
SOS：前台区间 range_age >= event_min_range_age，
     C>O，NS>=sos_sow_min_ns，RVOL>=sos_sow_min_rvol，CP>=sos_min_cp，NetMove>=strong_bar_min_move_atr
SOW：对称，C<O，CP<=sow_max_cp，NetMove<=-strong_bar_min_move_atr
```

无前台区间时，同样条件输出 `STRONG_UP_BAR` / `STRONG_DOWN_BAR`，不建立威科夫阶段。

内部 JOC（`creek_mode=range_and_internal`）：

```text
在 t-1 已可用的内部 Creek K（第 5.6 节），且 K < R-epsilon
C_(t-1) <= K+epsilon，C_t > K+epsilon，C_t <= R+epsilon
t 满足 SOS 条件
```

输出 `INTERNAL_JOC`，冻结 K。若 `C_t > R+ε`，按第 6.5 节记为前台突破，内部越过仅作关联。内部 FTI 对称，使用内部 Ice。

### 6.7 阻力上穿：UT、UTAD 与复合事件

第 6.4 节的价格镜像：

- `RESISTANCE_OVERSHOOT`：起始上下文中前台区间已建立且年龄达标，`H_p > R+ε`，`C_(p-1) <= R+ε`。
- 深度 ≤ `spring_max_depth_atr`：UT 观察，r ∈ {p, …, p+spring_max_pattern_bars-1}；更深：`DEEP_OVERSHOOT` 与深穿观察，r ∈ {p, …, p+shakeout_max_pattern_bars-1}。
- 收回：`C_r < R-ε`，`CP_r <= ut_max_cp`，模式内有真实成交、无封板。
- 分型：`UT_FEATURE`、`UPTHRUST_DEEP_REJECTION`、`C_r < S-ε` 时的复合事件 `UPTHRUST_REVERSAL_BREAKDOWN`。UT 不设成交量必要门槛，量能作为属性保存。

**UTAD 候选** 只在以下起始上下文同时成立时创建：`onset_snapshot.range_age >= mature_range_age`，且 `onset_snapshot` 中已有 `supply_dominance_by_rules`。刺破当根或收回当根才形成的供应占优证据不满足这个条件。

JOC 后的失败先记为 `FAILED_UP_BREAKOUT`（第 7.4 节）；只有失败模式本身满足 UT 条件才追加 UT，且只有满足 UTAD 起始上下文才追加 UTAD。

### 6.8 事件代码汇总

| 类别 | 代码 |
|---|---|
| 高潮 | `SC_FEATURE`、`SC_CLUSTER_FEATURE`、`BC_FEATURE`、`BC_CLUSTER_FEATURE` |
| 背离 | `EFFORT_RESULT_NARROW_BAR`、`EFFORT_RESULT_LOW_PROGRESS` |
| 下穿 | `SUPPORT_UNDERCUT`、`DEEP_UNDERCUT`、`SPRING_LOW_VOLUME`、`SPRING_HIGH_VOLUME`、`SPRING_OTHER`、`SHAKEOUT_RECLAIM`、`UNDERCUT_RECLAIM_BREAKOUT`、`UNDERCUT_NOT_RECLAIMED` |
| 上穿 | `RESISTANCE_OVERSHOOT`、`DEEP_OVERSHOOT`、`UT_FEATURE`、`UPTHRUST_DEEP_REJECTION`、`UPTHRUST_REVERSAL_BREAKDOWN`、`OVERSHOOT_NOT_REJECTED` |
| 突破 | `BREAKOUT_UP`、`BREAKOUT_DOWN`（strength 属性：ordinary / joc / fti）、`FAILED_UP_BREAKOUT`、`FAILED_DOWN_BREAKOUT`、`BREAKOUT_HOLD_CONFIRMED` |
| 强弱 | `SOS`、`SOW`、`INTERNAL_JOC`、`INTERNAL_FTI`、`STRONG_UP_BAR`、`STRONG_DOWN_BAR` |
| 测试 | `ST_OBSERVED`、`TEST_OBSERVED`、`LPS_PULLBACK_OBSERVED`、`LPSY_RALLY_OBSERVED` |
| 涨跌停 | `LIMIT_LOCK_UP`、`LIMIT_LOCK_DOWN`、`LIMIT_RELEASE_UP`、`LIMIT_RELEASE_DOWN` |
| 数据 | `data_gap`、`roll_switch`、`illiquid_history`、`warmup_incomplete` |

## 7. 后续确认、失效与超时

### 7.1 通用生命周期与计数起点

```text
candidate -> confirmed | invalidated | expired
confirmed -> invalidated | expired
```

`confirmed` 表示"截至确认时，规定的后续规则已经满足"，不代表吸筹等解释被永久证明。失效或超时的假设不复活，后续证据创建新假设并关联旧事件。

同一根 K 线的处理顺序：

1. 对已有假设执行失效检查。
2. 检测新事实，创建新假设。
3. 对本根新建的假设，执行计数起点为 `INCL` 的失效检查。
4. 对所有未失效的假设执行确认检查。
5. 对到期且未确认的假设执行超时。

同根失效与确认冲突时失效优先。窗口末根可以确认，末根处理完仍未满足才超时。所有转换追加记录。

每条失效计数必须登记起点：

| 起点 | 含义 |
|---|---|
| `PRE` | 创建前的必要条件，不满足就不创建 |
| `INCL` | 从创建根开始计数，创建根本身可以计入 |
| `POST` | 从创建根的下一根开始计数 |

| 失效条件 | 起点 |
|---|---|
| SC 闭环形成前收盘 `<L_SC-ε_SC`；BC 对称 | `INCL` |
| 吸收候选：连续收盘穿越边界外侧 | `INCL` |
| Spring/Shakeout：收盘 `<L_SP-ε`；连续收盘 `<S-ε` | `POST`（从 tS+1） |
| 突破有效性：连续回区间；深度回区间 | `POST`（从 tB+1） |
| LPS/LPSY：回调低点跌破参照位 | `POST`（从来源根+1） |
| UTAD：收盘超过 H_UT+ε；连续收盘 `>R+ε` | `POST`（从收回根+1） |
| 结构假设：相反方向占优连续成立 | `POST` |

"连续"指相邻有效 K 线都满足。收盘落在边界容忍带内是中性状态，使连续计数归零。每种路径、每个区间同时最多一个活动候选。

### 7.2 SC→AR→ST：止跌闭环与路径 A 区间

使用第 6.2 节保存的 `t0, L_SC, A_SC, ε_SC, Effort_SC, NS_SC_ref`。

**AR_RALLY，确认根 t ∈ t0+ar_window_min ～ t0+ar_window_max：**

```text
j = t0+1..t 中最高 H 所在根（同值取最早）
H_j - L_SC >= range_min_width_atr * A_SC
最近 ar_reversal_close_steps 个收盘变化全部为负（需 steps+1 个收盘）
H_j - C_t >= ar_reversal_atr * A_SC
```

首个满足的 t 为 `ar_confirmed_at`，冻结 `S=L_SC`、`R=H_j`、`A_ref=A_SC`、`ε=ε_SC`。若 `H_j-L_SC > range_max_width_atr*A_SC`，AR 事实保留，区间候选以 `width_too_large` 拒绝。

**ST，测试形态 q ∈ ar_confirmed_at+st_window_min ～ ar_confirmed_at+st_window_max：**

```text
S-epsilon <= L_q <= S + st_near_support_atr * A_SC
C_q >= S-epsilon
RVOL_q <= st_max_effort_vs_climax * Effort_SC
NS_q <= st_max_ns_vs_climax * NS_SC_ref
V_q > 0，非封板
```

输出 `ST_OBSERVED`。ST 不要求是阳线；允许低点比 SC 略低，但不超过 ε。窗口内可以有多个 ST_OBSERVED；最新的一个成为待反应的 q。

**ST 后反应，r ∈ q ～ q+st_response_max_bars：**

```text
r = q： C_q > O_q，C_q >= S+epsilon，C_q > H_(q-1)+tick_size      # 同根严格子类型
r > q： C_r > max(H_q, ..., H_(r-1)) + tick_size，C_r > S+epsilon
```

首个满足的 r 确认 `SC_STOPPING_SEQUENCE`；若区间宽度有效，建立路径 A 区间（原点为高潮第一根，建立时间为 r）。Phase A 获规则支持，方向仍可演变为再派发。

**失效：** 闭环形成前任意收盘 `<L_SC-ε_SC`（`INCL`）。**超时：** t0+ar_window_max 仍未确认 AR；或已确认 AR，但在 `ar_confirmed_at+st_window_max+st_response_max_bars` 仍未确认 ST 反应。超时不转成派发结论。

BC 闭环对称：AR_REACTION 的最低 L、连续上升收盘、ST 接近上沿 `R-st_near_support_atr*A_BC <= H_q <= R+ε`、`C_q <= R+ε`，反应为收盘跌破测试以来最低价且 `<R-ε`。

V1 的 ST 规则（当根阳线、当根确认、原始量比例）对应本节 r=q 子类型，只是量能比较改为标准化努力。

### 7.3 Spring / Shakeout → Test / SOS

使用第 6.4 节保存的 `tS, L_SP, Effort_SP, 分支`。复合事件 `UNDERCUT_RECLAIM_BREAKOUT` 不进入本节，按突破路径处理。

**测试形态 q ∈ tS+test_window_min ～ tS+test_window_max：**

```text
L_q >= L_SP + epsilon                               # 较高低点
S-epsilon <= L_q <= S + test_near_support_atr * A_q  # 确实回到支撑附近
RVOL_q <= test_max_rvol
NS_q <= test_max_ns
分支为 HIGH / OTHER / SHAKEOUT 时：RVOL_q <= test_max_effort_vs_spring * Effort_SP
V_q > 0，非封板
```

输出 `TEST_OBSERVED`。测试形态可以是阴线、十字线或阳线。低量 Spring 本身努力已经很低，只使用绝对上限，不要求测试量再低于 Spring。

**测试后反应，r ∈ q ～ q+test_response_max_bars：** 规则与第 7.2 节 ST 反应相同。确认后输出 `TEST_RESPONSE_CONFIRMED` 与 `SPRING_TEST_CONFIRMED`；r=q 时加 `same_bar` 子类型（等同 V1 的单根 Test）。

| Spring 分支 | Phase C 候选 | 增强证据的方式 |
|---|---|---|
| `SPRING_LOW_VOLUME`、`SPRING_OTHER` | Spring 收回时即为候选 | Test 反应确认；或 tS+spring_sos_window_min～tS+spring_sos_window_max 出现 SOS，输出 `SPRING_SOS_SEQUENCE` |
| `SPRING_HIGH_VOLUME`、`SHAKEOUT_RECLAIM` | Spring 收回时即为候选 | 只能通过 Test 反应确认；SOS 不能替代测试 |

**失效（`POST`）：** 收盘 `<L_SP-ε`；或 `spring_fail_consecutive_closes` 根连续收盘 `<S-ε`。**超时：** `tS+test_window_max+test_response_max_bars` 仍未确认。Spring 事实始终保留。

### 7.4 突破有效性：维持、失败与超时

适用于所有 `UP_BREAKOUT_VALIDITY` / `DOWN_BREAKOUT_VALIDITY`，与突破强度无关。以向上为例，tB 为突破根，`f=breakout_fail_deep_fraction`：

```text
维持确认：包含 tB 在内连续 breakout_hold_closes 根 C > R+epsilon
          -> BREAKOUT_HOLD_CONFIRMED，假设 confirmed，区间 exit_up_accepted
回测确认：第 7.5 节以本突破为来源的 LPS 确认 -> 假设 confirmed
失败（POST）：连续 breakout_fail_inside_closes 根 C < R-epsilon
          或任意一根 C < S + f*(R-S)
          -> 假设 invalidated，输出 FAILED_UP_BREAKOUT，区间回到 active
超时：tB+breakout_validity_window 处理完仍未 confirmed -> expired
```

向下对称：维持为 `C < S-ε`；失败为连续 `C > S+ε` 或任意 `C > R - f*(R-S)`。

已确认的假设继续受失败条件监测，直到区间 `retired`/`superseded` 或发生反向突破。超时不等于失败；未出现 BUEC 也不等于假突破。

JOC 还可以记录附加证据 `shallow_body_retrace`：令 `pullback_low` 为 tB 之后至今最低价，`max(0,C_tB-pullback_low)/(C_tB-O_tB) <= body_retrace_fraction`。它不替代任何边界条件。

### 7.5 LPS 族：回调中的支撑点

LPS 描述 SOS 或向上突破之后、供应减弱的回调及其后的重新推进。V2 从三种来源创建 LPS 候选，每个来源最多一个活动候选：

| 来源（来源根 tX） | 参照位 L* | 子类型 |
|---|---|---|
| `UP_BREAKOUT_VALIDITY`（任意强度） | 前台 R | 回调低点在 `[R-ε,R+ε]`：`LPS_AT_CREEK`（即 BUEC）；低点 `>R+ε`：`LPS_ABOVE_CREEK` |
| `INTERNAL_JOC` | 冻结的内部 Creek K | `LPS_INTERNAL` |
| 区间内部 `SOS`（非突破、非内部 JOC） | tX 时已可用的最近摆动低点，且不低于 S；没有则用 S | `LPS_INTERNAL` |

容忍度统一使用前台区间的 ε。处理步骤：

1. **回调起点 b：** tX 之后第一个 `C_b < C_(b-1)` 的 K 线。若在反应确认前收盘创出来源以来新高，本轮回调结束，在之后寻找新的 b。
2. **回调低点 q：** b 至当前最低 L 所在根，同值取最早。
3. **回调证据**（在反应根 r 处检查）：

```text
q-b+1 >= lps_min_pullback_bars
Effort(b..q) <= lps_max_pullback_effort          # 不含来源根
mean(NS_b..NS_q) <= lps_max_pullback_ns
(max(H_tX..H_(b-1)) - L_q) / A_b >= lps_min_retrace_atr
min(L_b, ..., L_(r-1)) >= L* - epsilon
```

4. **反应根 r ∈ q+1 ～ q+lps_turn_max_bars，且 r <= tX+lps_window_max：**

```text
C_r > H_(r-1) + tick_size
C_r > L* + epsilon
NetMove_r >= lps_response_min_move_atr
```

反应不要求放量：供应减少后，较小的需求也可能推动价格。`RVOL_r` 作为属性保存。

5. **确认：** 首个满足的 r 确认对应子类型的 LPS。来源为突破时，同时确认该突破的有效性假设（若尚未确认）。

**严格子类型 `strict_v1`：** `LPS_AT_CREEK` 若另外满足 V1 的全部条件，加标 `strict_v1`：

```text
q-b+1 >= strict_backup_min_pullback_bars
mean(RVOL_b..RVOL_q) < strict_backup_max_mean_rvol
mean(NS_b..NS_q) <= strict_backup_max_mean_ns
OLS_slope(RVOL_b..RVOL_q) < 0
C_(r-1) < O_(r-1)，C_r > O_r，O_r <= C_(r-1)，C_r >= O_(r-1)     # 阳线实体吞没前一根阴线
RVOL_r >= backup_turn_min_rvol
```

这是策略研究用子集，不是 LPS 的定义。

**失效（`POST`）：** 反应确认前任一低点 `<L*-ε`。这只使 LPS 候选失效；突破有效性按第 7.4 节独立判断。**超时：** tX+lps_window_max 仍未确认。

### 7.6 LPSY 族

第 7.5 节的镜像。来源为 `DOWN_BREAKOUT_VALIDITY`（参照 S，子类型 `LPSY_AT_ICE` / `LPSY_BELOW_ICE`）、`INTERNAL_FTI`（参照内部 Ice）、区间内部 `SOW`（参照最近摆动高点，不高于 R）。反弹为回调的镜像，反应根要求 `C_r < L_(r-1)-tick`、`C_r < L*-ε`、`NetMove_r <= -lps_response_min_move_atr`。

多空使用相同初始参数只为建立基线，可以分别校准并登记，不假定对称。

### 7.7 UTAD 后续证据

UTAD 候选在收回根 tU 创建，保存 `H_UT=max(H_p..H_tU)`。tU+utad_response_window_min～tU+utad_response_window_max 内出现 SOW 或已确认的 LPSY，确认 `UTAD_WEAKNESS_SEQUENCE`。失效（`POST`）：收盘 `>H_UT+ε`，或连续 `breakout_fail_inside_closes` 根收盘 `>R+ε`。窗口结束未确认则超时。

### 7.8 事件重叠

- 同一量价事实的多个别名（JOC 与 SOS、复合事件与突破、单根 SC 与高潮簇）只计一条证据，保存关联关系。
- 已有活动 SC 候选时，新的 SC 特征只记事实，不更换原候选的低点；原候选失效后才允许新建。
- 已有前台区间时，新完成的高潮闭环作为该区间的附加证据，不覆盖 S/R。

### 7.9 结构解释的退出规则

| 假设 | 退出或降级 |
|---|---|
| SC/BC 止跌 / 止涨 | 闭环后连续两根收盘穿越高潮极值的失效侧，或发生相反方向的突破外侧维持时 `invalidated`；进入后续阶段时当前阶段标签 `expired` |
| 支撑 / 阻力吸收 | 第 6.3 节失效规则继续适用；最近反应证据超过 `absorption_current_evidence_max_age` 根则 `expired` |
| Spring / Shakeout 供应测试 | 第 7.3 节价格失效继续适用；超过 `bias_sequence_recent_bars` 根没有新的 Test、SOS 或 LPS 则 `expired` |
| UTAD | 第 7.7 节 |
| LPS / LPSY 位置解释 | 事实保留；来源突破的有效性失效时同步失效 |
| 吸筹 / 派发 / 再吸筹 / 再派发 | 相反方向占优连续 `structural_opposite_persistence_bars` 根成立，或发生相反方向的外侧维持时 `invalidated`；原方向证据不再满足且近期信号过期时 `expired` |
| Phase C / D / E | 底层引用的假设失效时同步撤销；没有独立证据时返回未知，不硬退回某个阶段 |

区间方向标签（第 8.3 节）是逐根重算的证据；结构假设是有历史的对象，二者不共用字段。

## 8. 因果波浪与区间方向证据

### 8.1 波浪划分（沿用 V1 方法）

本节受 Weis 波浪量启发，不宣称与任何商业指标一致。只使用收盘价。

- 初始化：以第一个有效收盘为锚点，冻结 `rho = wave_reversal_atr * ATR_previous`；收盘离锚点达到 rho 才确定第一波方向，此前输出 `wave_initializing`。锚点根的成交量不归第一波。
- 上行波维护已知最高收盘 E；`C_t <= E-rho` 时在 t 确认反转，旧波结束于 t-1，`confirmed_at=t`；反转根 t 的成交量归新波，新波基准价为 `C_(t-1)`。下行波对称。
- 每个新波的 rho 在建立时冻结。相等即满足反转；成交量不按事后极值重新分配。
- 形成中的波可以展示，但不进入统计。含封板、`data_gap` 或 `illiquid_history` 的波标记 `quality_limited`，不进入方向统计。

V2 不用成交量来确认波浪反转：用成交量划分波浪、再用这些波浪比较成交量是循环论证。震荡中的碎波风险通过 `wave_reversal_atr` 的敏感性分析（1.0 / 1.5 / 2.0）报告。

### 8.2 波浪字段

令 `d=+1`（上行）或 `-1`（下行），`A0=atr_at_start`，波内 K 线为 a..e：

```text
wave_id, direction, start_time, end_time, confirmed_at, available_at, status
start_reference_close, extreme_close, end_close=C_e, reversal_threshold
bar_count = e-a+1
raw_volume_sum, baseline_volume_sum
wave_effort = Effort(a..e)
peak_excursion = d*(extreme_close-start_reference_close)/A0        # 对已完成波恒 > 0
signed_end_displacement = d*(end_close-start_reference_close)/A0   # 可以为负
giveback = peak_excursion - signed_end_displacement
path_distance = sum(abs(C_i-C_(i-1)), i=a..e)/A0
directional_efficiency = signed_end_displacement / path_distance
wave_speed = peak_excursion / bar_count
mean_bar_ns = mean(NS_a..NS_e)
wave_oiz = OIz(a..e)                                              # 仅描述
```

按 8.1 节的反转规则，已完成波的 `giveback*A0 < reversal_threshold`。

### 8.3 区间方向证据：三组

评估前提：区间年龄 ≥ `mature_range_age`；取区间原点之后、所有收盘在 `[S-ε,R+ε]` 内的最近至多 `wave_stats_max_completed` 个已完成波，每个方向至少 `wave_min_completed_each_direction` 个。不满足时波浪相关的两组为 `not_evaluable`。统计量取各方向中位数，`k=wave_dominance_ratio`。

| 组 | 需求侧条件 | 供应侧条件 |
|---|---|---|
| E 努力 | `med_up(wave_effort) >= k*med_down(wave_effort)` | 上下互换 |
| P 价格结果 | `med_up(peak_excursion) >= k*med_down(peak_excursion)` 且 `med_up(signed_end_displacement) > med_down(signed_end_displacement)` | 上下互换 |
| B 边界响应 | 最近 `bias_sequence_recent_bars` 根内，需求侧已确认序列至少 1 个且多于供应侧 | 上下互换 |

B 组计入的序列：需求侧 `SUPPORT_RESPONSE_SEQUENCE`、`SPRING_TEST_CONFIRMED`、`SPRING_SOS_SEQUENCE`；供应侧 `RESISTANCE_RESPONSE_SEQUENCE`、`UTAD_WEAKNESS_SEQUENCE`、UT 后的对称测试序列。

每组返回 `demand / supply / neutral / not_evaluable`。

```text
demand_dominance_by_rules = (P = demand) AND (E = demand OR B = demand) AND 没有任何组 = supply
supply_dominance_by_rules = 对称
evidence_score = 取值为 demand 的组数 / 可评估的组数      # 仅作展示，不是概率
```

价格结果组必须参与：只有量能差异、没有价格结果差异时，不输出占优。V1 的单根波幅对比和速度对比与价格结果高度相关，V2 只保存为特征，不再单独投票；V1 的"强推进 / 弱回调"组合项被 E 组蕴含，删除。

占优同时输出路径标签（可多选），对应三种经典表现：

| 路径标签 | 条件 |
|---|---|
| `demand_push` | P = demand 且 E = demand |
| `supply_exhaustion` | P = demand 且 E 不为 demand：上行结果更好，但不靠更大的努力 |
| `absorption_response` | B = demand，且计入的序列中有 `SUPPORT_RESPONSE_SEQUENCE` |

OI 在 `oi_mode=annotate` 时只随波浪保存，不参与占优。

### 8.4 结构假设

| 假设 | 入口条件 |
|---|---|
| `phase_b_range` | 前台区间年龄 ≥ `mature_range_age`，方向可以未知 |
| `spring_supply_test_candidate` | 存在未失效的 Spring/Shakeout 假设；不要求方向占优 |
| `accumulation_candidate` | 成熟区间 + `demand_dominance_by_rules` + 最近 `bias_signal_recent_bars` 根内至少一个触发事件：`SOS`、`INTERNAL_JOC` 或已确认的 LPS |
| `distribution_candidate` | 对称：`supply_dominance_by_rules` + `SOW`、`INTERNAL_FTI` 或已确认的 LPSY |

重复计票控制：触发事件使用的事件 ID 不再计入 B 组；去掉这些 ID 后占优仍须成立。例如 `SPRING_SOS_SEQUENCE` 中的 SOS 作为触发事件时，该序列不再作为 B 组证据。

更大尺度背景在区间建立时计算并冻结：同周期前 `larger_context_window` 个收盘变化，`SignedDelta >= larger_context_min_displacement_atr` 且 `ER >= larger_context_min_er` 为上涨背景，对称为下跌背景。上涨背景中的吸筹候选显示为 `reaccumulation_candidate`，下跌背景中的派发候选显示为 `redistribution_candidate`。这是同周期长窗口，不冒充高周期趋势。

## 9. 实时状态、记录格式与处理顺序

### 9.1 市场状态与阶段

主状态 `uptrend / downtrend / range / transition / unknown`，按优先级：

1. 核心数据缺失或预热不足：`unknown`，说明缺项。
2. 前台区间内，收盘位于 `[S-ε,R+ε]`：`range`，另报 `trend_now`。
3. 区间外，但外侧维持未成立或 `trend_now` 与突破方向不一致：`transition`。
4. 外侧维持已成立且 `trend_now` 与突破同向：`uptrend` 或 `downtrend`。
5. 无前台区间：`trend_now` 成立则报方向，否则 `transition`。

非数据质量导致的状态切换，需要目标状态连续出现 `regime_persistence_bars` 次；切换前保存 `pending_regime`。数据失效立即切到 `unknown`。事实事件不等待状态防抖。

阶段证据映射：

- SC/BC 闭环：Phase A 获规则支持。
- `phase_b_range`：Phase B 候选。
- Spring / Shakeout / 成熟 UT：Phase C 候选；Test 反应或 UTAD 弱势序列增强证据。
- SOS（含内部 JOC）与已确认 LPS：Phase D 获规则支持；LPS 可以是任一子类型，包括 `LPS_INTERNAL`。单独的 JOC/FTI 只提供 Phase D 候选。向下对称。
- **Phase E（向上）获规则支持**，全部满足：

```text
区间状态 exit_up_accepted
最近 phase_e_min_outside_closes 根收盘全部 > R+epsilon
(C_t - R) / A_ref >= phase_e_min_distance_atr
trend_now: SignedDelta_n > 0 且 Slope_n > 0 且 ER_n >= phase_e_min_er
```

向下 Phase E 使用 `(S-C_t)/A_ref`、负向 SignedDelta 和负向 Slope。ER 不带方向，必须与有方向的位移和斜率同时使用。

多个阶段解释可以并存或未知。`phase_supported_by_rules` 必须附证据 ID、确认时间和失效条件。

### 9.2 事件与假设记录

```text
事件 occurrence：
  event_id, event_type, strength, aliases, linked_event_ids
  symbol, actual_contract, timeframe
  event_time, detected_at, available_at, data_asof
  pattern_start, pattern_end, onset_snapshot_id
  reference_range_id, reference_range_version, frozen_S, frozen_R, frozen_epsilon
  frozen_internal_level (如有)
  observed_metrics: RVOL, Effort, NS, CP, LW, UW, NetMove, Gap, depth, OIz, oi_quadrant
  limit_state, quality_flags, threshold_margins, rejection_reasons
  rule_version, parameter_version, data_version

结构 hypothesis：
  hypothesis_id, hypothesis_type, source_event_ids, competing_hypotheses
  status, created_at, confirmed_at, invalidated_at, expires_at, status_available_at
  state_transition_history
  confirmation_conditions, invalidation_conditions (含计数起点 PRE/INCL/POST)
  supporting_evidence_ids, opposing_evidence_ids, evidence_groups
```

`threshold_margins` 保存每个数值条件与阈值的差距，`rejection_reasons` 保存未成立的条件，供覆盖率和参数敏感性研究使用，避免只有真假标签。

例：t100 疑似 SC，t112 才完成 ST 反应：SC 事实时间保留 t100，结构确认在 t112；t100 的实时状态不能改写为"当时已确认止跌"。

### 9.3 实时输出示例

```json
{
  "asof": "2026-10-21T10:30:00+08:00",
  "symbol": "EXAMPLE",
  "actual_contract": "EXAMPLE2601",
  "timeframe": "15m",
  "rule_version": "2.0",
  "parameter_version": "v2-default-unvalidated",
  "market_regime": "range",
  "trend_now": "downtrend",
  "structure": {
    "primary": "spring_supply_test_candidate",
    "alternatives": ["phase_b_range", "redistribution_candidate"],
    "direction_dominance": "neutral",
    "evidence_groups": {"E": "neutral", "P": "demand", "B": "not_evaluable"},
    "phase_candidate": ["B", "C"]
  },
  "range": {"id": "R001", "version": 1, "support": 100.0, "resistance": 110.0, "epsilon": 0.2},
  "evidence": [{"event_id": "E0042", "type": "SPRING_LOW_VOLUME", "oi_quadrant": "up_oi_flat"}],
  "next_confirmation": ["TEST_OBSERVED 后的测试反应", "SOS（低量 Spring 允许）"],
  "invalidation": ["收盘低于 Spring 最低价减 epsilon", "连续两根收盘低于 S-epsilon"],
  "probability": null,
  "quality": "ok"
}
```

示例只说明格式。它显示：只有低量 Spring、方向占优不成立时，主结构是 `spring_supply_test_candidate`，不是吸筹候选。生产输出同时保存机器可检查的条件 ID。

### 9.4 每根 K 线的处理顺序

```text
1. 校验数据、实际合约、时段、涨跌停状态、换月与预热。
2. 取得 t-1 快照：指标、trend_context、前台区间、已可用的摆动点、方向证据；
   观察对象读取各自保存的 onset_snapshot。
3. 对已有假设执行失效检查。
4. 检测本根完成的单根与多根事实；创建允许的新候选和观察对象（新观察对象的 onset_snapshot = t-1 快照）。
5. 对本根新建候选执行 INCL 失效检查。
6. 对未失效候选执行确认检查，包括窗口末根。
7. 对到期未确认的候选追加超时。
8. 用本根更新指标状态（ATR、成交量基准输入、摆动点确认）、波浪和方向证据。
9. 更新市场状态与阶段；区间建立、退役、替代登记新版本，下一根生效。
10. 写出事件、假设转换、实时快照及实际可用时间。
```

第 8 步新形成的证据可以用于本根的新结构解释（第 9 步），但不能作为本根任何事件的起始上下文。

## 10. V2 参数登记表

全部数值标为 P：待验证。级别：**C** = 首轮校准（共 8 个）；**B** = 冻结基线，首轮不调；**M** = 模式开关，更换即为新研究配置。

### 10.1 数据、指标与背景

| 参数 | 默认 | 级别 |
|---|---:|:---:|
| `warmup_bars` | 60，且不低于所有启用窗口所需历史 | B |
| `atr_period`（n_atr） / `tr_cap_mult` | 14 / 3.0 | B |
| `volume_period`（n_volume） / `volume_cap_mult` / `volume_min_samples` | 20 / 3.0 / 15 | B |
| `intraday_volume_mode` | `session_matched` | M |
| `session_baseline_days` / `session_min_samples` | 20 / 10 | B |
| `oi_mode` | `annotate` | M |
| `oi_window`（n_oi） / `oi_flat_band` | 20 / 1.0 | B |
| `roll_confirm_days` / `liquid_min_ratio` | 3 / 0.30 | B |
| `trend_window` / `trend_sma_fast` / `trend_sma_slow` | 20 / 20 / 50 | B |
| `trend_min_displacement_atr` / `trend_min_abs_slope_atr` / `trend_min_er` | 2.0 / 0.05 / 0.30 | B |
| `larger_context_window` / `larger_context_min_displacement_atr` / `larger_context_min_er` | 50 / 3.0 / 0.30 | B |
| `regime_persistence_bars` | 2 | B |

### 10.2 区间与参照位

| 参数 | 默认 | 级别 |
|---|---:|:---:|
| `boundary_ticks` | 2 | B |
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
| `lps_min_pullback_bars` / `lps_max_pullback_effort` / `lps_max_pullback_ns` | 2 / 0.90 / 1.0 | B |
| `lps_min_retrace_atr` / `lps_turn_max_bars` / `lps_response_min_move_atr` | 0.5 / 3 / 0.3 | B |
| `lps_window_max` | 15 | **C** |
| `strict_backup_min_pullback_bars` / `strict_backup_max_mean_rvol` / `strict_backup_max_mean_ns` / `backup_turn_min_rvol` | 3 / 0.85 / 1.0 / 1.2 | B |
| `utad_response_window_min` / `utad_response_window_max` | 1 / 12 | B |
| `phase_e_min_outside_closes` / `phase_e_min_distance_atr` / `phase_e_min_er` | 5 / 2.0 / 0.40 | B |

### 10.5 波浪与结构

| 参数 | 默认 | 级别 |
|---|---:|:---:|
| `wave_module_enabled` | true（关闭时 E、P 组输出 `not_evaluable`） | M |
| `wave_reversal_atr` | 1.0（必须报告 1.0/1.5/2.0 敏感性） | B |
| `wave_stats_max_completed` / `wave_min_completed_each_direction` / `wave_dominance_ratio` | 6 / 2 / 1.2 | B |
| `bias_signal_recent_bars` / `bias_sequence_recent_bars` / `structural_opposite_persistence_bars` | 10 / 20 / 2 | B |

### 10.6 参数约束与 V1 对照

参数取值必须满足：所有 `*_min <= *_max`；`spring_low_volume_max_rvol < spring_high_volume_min_rvol`；`range_min_width_atr < range_max_width_atr`；`mature_range_age >= event_min_range_age`；`spring_max_pattern_bars ∈ {1,2}` 且 `shakeout_max_pattern_bars >= spring_max_pattern_bars`；`warmup_bars` 覆盖所有启用窗口。不满足时拒绝运行，不静默修正。

截尾基准使 RVOL 在肥尾样本中比 V1 略高，沿用 V1 的默认倍数只是起点，不能直接比较两个版本的事件频率。

| V1 参数 | V2 处理 |
|---|---|
| `ar_min_excursion_atr` | 删除，由 `range_min_width_atr` 代替（第 5.3 节） |
| `spring_test_window_*`、`spring_test_max_volume_ratio` | 改名为 `test_window_*`、`test_max_effort_vs_spring`，比较对象改为标准化努力 |
| `st_max_volume_vs_climax` | 改为 `st_max_effort_vs_climax` |
| `backup_*` | 通用 LPS 改用 `lps_*`；V1 严格回测条件保留为 `strict_backup_*`，只用于 `strict_v1` 子类型 |
| `backup_complete_window_*` | 拆分为 `breakout_validity_window` 与 `lps_window_max` |
| `wave_strong_rvol`、`wave_weak_rvol`、`wave_required_evidence_count` | 删除，方向证据改为第 8.3 节三组规则 |
| `confirmation_high_low_ticks` | 固定为 1 个 tick，归入 F 类 |

同一参数被多条规则复用时，调整会同时影响这些规则；拆分成独立参数属于参数模式变化，必须登记。

## 11. 验证方案与验收要求

### 11.1 工程正确性：必须通过

| 验收项 | 要求 |
|---|---|
| 前缀回放一致性 | 只输入到 t 的回放结果，与全量回放中保存的 t 时快照完全一致 |
| 未来扰动 | 修改 t 之后的数据，不改变截至 t 的任何输出 |
| 后续追认 | t+N 的确认不出现在 t 的历史可用状态中 |
| 边界冻结 | 当前 K 线不抬高自身阻力；失败后原 S/R/ε 不移动 |
| 窗口与冲突 | 窗口末根可确认，末根之后不确认；同根失效优先 |
| 数据质量 | 零量、缺失、H=L、ATR=0、换月、封板、流动性不足返回明确原因 |
| 版本 | 输出保存数据、规则和参数版本，可重放 |

### 11.2 构造路径回归用例

用人工构造的价格序列验证分支和时间顺序。这些用例验证规则实现，不说明市场预测价值。

| 编号 | 构造 | 期望 |
|---|---|---|
| T01 | R=100，突破后 20 个收盘从 130 单调降到 110 | 不得输出向上 Phase E |
| T02 | 低量普通上破，随后 3 根收于 R+ε 之上 | `UP_BREAKOUT_VALIDITY` 被 `BREAKOUT_HOLD_CONFIRMED` 确认 |
| T03 | 首根下穿 3 ATR | 输出 `SUPPORT_UNDERCUT` 与 `DEEP_UNDERCUT`，进入深穿观察 |
| T04 | 供应占优在 p 收盘才成立，p+1 收回形成 UT | 输出 `UT_FEATURE`，不创建 UTAD 候选 |
| T05 | 吸收候选创建根已收于 S-ε 下方，下一根再收于下方 | 第二根失效（`INCL`） |
| T06 | 日内：Spring Effort 0.5，Test RVOL 0.3，原始量 Test 高于 Spring | 量能条件通过 |
| T07 | SC 后 20 根正常量 | 截尾 Bv 明显低于未截尾 Bv；ST 判定使用截尾值 |
| T08 | 连续 2 根锁跌停，第 3 根打开、巨量收回 | 输出 `LIMIT_RELEASE_DOWN`；锁板根不触发量能事件；第 3 根可检测 SC |
| T09 | 主力切换 | 新合约使用自身历史；旧区间不迁移；切换日只依赖 T-1 数据 |
| T10 | AR 幅度 2.5 ATR | AR 不确认，不出现宽度矛盾 |
| T11 | 上行波 RVOL 1.5、下行 0.8，价格结果相同 | E 组为 demand，但 P 组非 demand，不输出占优 |
| T12 | 上行波回吐后收盘低于起点基准价 | `signed_end_displacement < 0` 被保存 |
| T13 | JOC 后在 R 上方 1 ATR 处缩量横盘，再推进 | 确认 `LPS_ABOVE_CREEK`，并确认突破有效性 |
| T14 | 阴线测试 + 下一根越过测试高点 | `TEST_OBSERVED` 后 `TEST_RESPONSE_CONFIRMED` |

### 11.3 事件单位与相关性

同一区间的多次 SOS、Spring 与重叠的未来窗口高度相关。每项统计同时报告：原始事件数、独立候选数、交易区间数、确认链条数、去重后的评价样本数。

- 事先规定每个区间每种路径取第一个、最后一个或全部候选；选取规则只使用当时信息。
- 置信区间使用按区间聚类或时间块的重采样，不假设事件独立。
- Purge/embargo 只解决跨集合的标签重叠，不解决同一集合内部的相关性。
- 保留全部候选的分母（含失效、超时、`not_evaluable`），不能只统计已确认事件。

### 11.4 识别质量与预测价值分开

识别质量指标：候选数量、覆盖率、确认率、失效率、超时率、确认延迟、状态切换次数、`not_evaluable` 比例；按品种、年份、波动环境、日夜盘、多空方向分别报告。

预测价值使用独立的客观标签，并预先登记：

- 首版评价基线：信号可用后的下一根开始观察 20 根，锚点为 `descriptive_reference_price`（信号根收盘），上下各 2 个事前 ATR 障碍，记录先触及哪侧、都未触及、`ambiguous_intrabar`、`censored`。
- 补充指标：方向性净位移与回撤、边界外维持比例与失败深度、MFE/MAE。
- 只评价识别时使用 `descriptive_reference_price`；接入交易层后另存 `execution_reference_price`（下一可成交时刻和价格）。

### 11.5 逐模块增量检验

依次比较，每一步只加入一个模块：

1. 基线：趋势状态 + 普通区间突破。
2. 加事实事件（SC/BC、Spring/UT、JOC/FTI、SOS/SOW）。
3. 加测试与反应（ST、Test、LPS/LPSY）。
4. 加波浪方向证据。
5. 加内部 Creek/Ice（`creek_mode` 对比）。
6. 加 OI（`oi_mode=filter` 对比 `annotate`）。

比较对象是趋势、波动和区间位置相近的样本。如果某模块只重复均线和普通突破的信息，可以保留作解释用途，但不能声称有独立预测优势。

### 11.6 时间滚动、参数搜索与多重检验

- 按时间划分训练、验证与最终样本外，研究起点为 24/6/6 个月滚动；窗口长度待验证。
- 只在训练/验证区间选参；最终样本外前冻结规则与配置。
- 登记每一次试验的配置与结果（包括被放弃的配置），报告试验总数。
- 首轮只在 8 个 C 级参数上做邻域扰动，报告结果在邻域内是否稳定，不报告单点最优。
- 多个模块、多个参数配置的比较需要多重检验校正（如 White Reality Check、Hansen SPA，或对多个假设使用 Romano-Wolf / Holm 校正）。
- 只有建立客观预测对象、用独立样本校准后才输出概率，并报告校准曲线、Brier 分数和样本量；默认 `probability=null`。

### 11.7 接入交易层前的额外验证

需要明确交易时刻、下一可成交价、手续费、滑点、涨跌停成交限制、实际合约换月与风险预算。收盘后才能知道的信号不能按同一根收盘价成交。策略收益、风险和容量独立验证。

## 12. 实施顺序与范围

### 12.1 实施顺序

1. 数据层：实际合约状态、主力选择规则、流动性标记、涨跌停状态、OI 字段、版本记录。
2. 指标层：截尾 ATR 与成交量基准、Effort、OI 特征、因果摆动点。
3. 趋势与区间：路径 A/B、区间生命周期（含 `superseded`）、内部 Creek/Ice。
4. 事实事件：第 6 节全部，含多根模式的起始快照。
5. 生命周期：计数起点登记、处理顺序、ST/Test/LPS 两步结构、突破有效性。
6. 第 11.1、11.2 节验收全部通过后，接入波浪与方向证据。
7. 冻结默认版本，按第 11.5 节逐模块研究；通过后再设计交易层。

未实现的模块必须输出 `not_implemented`，不能当成没有发生。

### 12.2 V2 不覆盖的范围

- 曲线或斜线 Creek/Ice、嵌套区间、跨周期结构。
- 点数图横向计数与目标价。
- 封板 K 线本身的收回观察（只处理打开后的 K 线）。
- 由 OI 推断参与者身份或主动方向。
- 概率输出。

## 附录 A：评审意见处置

### A.1 V1 评审报告各项

核对方法：逐项对照 V1 原文并复算反例。

| 评审条目 | 核对结论 | V2 处置 |
|---|---|---|
| 3.1 Phase E 缺方向 | 成立，反例可复现 | 采纳，第 9.1 节；用例 T01 |
| 3.2 普通突破无假设入口 | 成立 | 采纳，第 6.5 节；用例 T02 |
| 3.3 首根深穿被过滤 | 成立 | 采纳，第 6.4 节；用例 T03 |
| 3.4 两根 UT 上下文未冻结 | 成立 | 采纳并推广到所有多根模式，第 6.0 节、I-11；用例 T04 |
| 3.5 失效计数起点歧义 | 成立 | 采纳，第 7.1 节登记表；用例 T05 |
| 3.6 正文写死默认公式 | 成立 | 采纳，正文全部使用参数符号 |
| 3.7 示例与入口不一致 | 成立 | 采纳，示例改为 `spring_supply_test_candidate` |
| 4.1 测试形态与反应分开 | 成立 | 采纳，并扩展到 ST（评审只提到 Test）；V1 规则保留为严格子类型 |
| 4.2 内部 LPS/LPSY | 成立 | 采纳实现路径，第 5.6、7.5 节 |
| 4.3 跨区间收回 | 成立 | 采纳，复合事件代码 |
| 4.4 方向证据偏向放量推进 | 成立 | 采纳三条路径标签，第 8.3 节 |
| 5.1 日内原始量比较 | 成立，反例数值正确 | 采纳，F-11 与 Effort；用例 T06 |
| 5.2 投票重复计票 | 成立（1.4/0.85≈1.647>1.2） | 采纳并更进一步：速度、波幅与价格结果也相关，改为三组规则且价格结果组必须参与 |
| 5.3 净位移与回吐 | 成立 | 采纳，第 8.2 节；用例 T12 |
| 5.4 事件边界与当前边界 | 成立 | 采纳，`superseded` 状态 |
| 5.5 换月与同槽样本叠加 | 方向成立，但低估了问题 | 修改：日线预热与主力期长度冲突更严重；改为按实际合约维护状态，第 4.5 节 |
| 6.1 参数收敛 | 成立（103 个参数名经复核无误） | 采纳，三级分类，C 级 8 个 |
| 6.2 硬条件与辅助证据分开 | 成立 | 采纳，`threshold_margins`、`rejection_reasons` |
| 6.3 事件单位与相关性 | 成立 | 采纳，第 11.3 节 |
| 6.4、6.5 描述与预测、锚点分开 | 成立 | 采纳，第 11.4 节 |

### A.2 V1 评审报告未覆盖、本次新增的问题

| 问题 | V2 处置 |
|---|---|
| 高潮根抬高 20 根均量和 ATR，使后续 ST、Test、LPS 回调更容易满足"低量窄幅"，造成系统性假阳性 | 截尾 Bv 与截尾 ATR，第 4.2 节；用例 T07 |
| V1 把锁板等受影响 K 线排除在强事件之外，会丢掉国内期货中跌停打开后的抛售高潮 | 封板事实与打开根检测，第 4.6 节；用例 T08 |
| AR 最小幅度 2 ATR 小于区间最小宽度 3 ATR，2~3 ATR 的 AR 候选会白跑整个 ST 流程后被拒 | 合并参数，第 5.3 节；用例 T10 |
| ST 也强制阳线且当根确认（评审只指出 Test） | ST 两步结构，第 7.2 节 |
| 单根 SC/BC 漏掉多根连续抛售高潮 | 高潮簇，第 6.2 节 |
| 深穿收回的语义：V1 只记录事实、没有后续流程；同时不能把深穿收回当最强信号 | `SHAKEOUT_RECLAIM` 必须经 Test 才增强证据，第 3.2、7.3 节 |
| JOC 后在旧阻力上方止跌的 LPS 无法识别 | `LPS_ABOVE_CREEK`；用例 T13 |
| OI 未接入 | 作为描述特征，默认不参与判定；`filter` 模式需单独验证 |
| 参数搜索空间大，但没有多重检验要求 | 第 11.6 节 |
| 主力合约判定可能事后化 | I-12 与第 4.5 节 |

### A.3 未采纳的建议（来自此前的非正式评审意见）

| 建议 | 不采纳的原因 |
|---|---|
| 用最高价的 90 分位定 R、最低价的 10 分位定 S | 默认 10% 的 K 线已经越过边界，Spring/UT/突破的含义失效 |
| ε = 0.5 × 区间收盘标准差 | 收盘近似均匀分布时标准差约 0.29W，6 ATR 宽的区间 ε 约 0.9 ATR，与"80% 收盘位于 [S+ε,R-ε]"冲突 |
| 用成交量确认波浪反转 | 用成交量划分波浪再比较波浪成交量，是循环论证 |
| 深穿收回作为最强信号 | 深穿伴随大量供应，需要后续测试 |
| 增仓上涨等同主动买盘吸收 | 增仓不说明主动方；违反 I-10 |
| 全部阈值改为滚动分位数 | 分位数按固定比例触发，失去绝对异常含义；保留为第 11.5 节可对比的规则版本，不作默认 |
| 确认反转后把旧波终点改回真实极值 | 可以作为新规则版本对比；V2 保持 F-08，并用 `giveback` 字段弥补信息损失 |
| 无依据的效果数字（如"误判率降低 30%"） | 删除；V2 不包含未经验证的效果声明 |

## 附录 B：参考资料与来源边界

- [Wyckoff Analytics：The Wyckoff Method](https://www.wyckoffanalytics.com/wyckoff-method/)：三大定律、事件关系、阶段与非唯一结构路径的概念来源。
- Richard D. Wyckoff 原始课程、Hank Pruden *The Three Skills of Top Trading*、David H. Weis *Trades About to Happen*：理论学习来源；本次未逐页核对原书，本文的公式、阈值和状态机都是工程提案，不归属于这些作品。

V2 的交付判断：可以据此实现客观、可回放的识别流程；所有市场阈值和预测价值仍待验证。不可违背的是因果性、真实可用时间、边界冻结、数据完整性与证据可追溯，而不是任何未经验证的倍数或根数。
