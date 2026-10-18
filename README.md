# 期货 CTA 回测实验室

基于 **vnpy 4.5** 的国内期货本地回测环境。开箱即用：**免 token 的行情数据 + vnpy 数据库 + 无 GUI 回测引擎 + AI 策略分析**。

---

## 30 秒上手

```bash
# 1. 安装（Windows 双击 setup.bat 亦可）
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 2. 自检环境
.venv\Scripts\python check_env.py

# 3. 一条命令跑通全流程（下载数据 -> 回测 -> AI 分析）
.venv\Scripts\python demo.py
```

Linux / macOS 把 `.venv\Scripts\python` 换成 `.venv/bin/python`。

---

## 它解决什么问题

原始 vnpy 是个**交易平台框架**，本身不带数据、不带期货合约规格、只能用 GUI 点点点。本项目补齐了三个断点：

| 断点 | 本项目方案 |
|---|---|
| **没有数据** | `cta_lab.downloader` 直连东方财富公开行情接口，免 token、免注册、免第三方库，覆盖 6 大交易所 |
| **没有合约规格** | `cta_lab.contracts` 内置 82 个国内期货品种的乘数 / 最小变动价位 / 交易所映射 |
| **只能 GUI 操作** | `cta_lab.runner` + `cta_lab.cli` 提供无 GUI 的脚本化回测、参数寻优、多品种扫描 |

在 vnpy 之上补了一层 **AI 分析**：把回测结果翻译成可读的策略诊断（`cta_lab.ai`），并支持可选的 LLM 深度解读。

---

## 数据源说明（重要）

### 三个可用源，实测对比

| 源 | 覆盖 | 日线深度 | 分钟线 | 额外字段 | 频控 | 成本 |
|---|---|---|---|---|---|---|
| **新浪财经**（默认） | **82/82 全品种** | **2005 起**（部分品种） | 无 | **结算价** | 宽松 | 免费 |
| 东方财富 | 覆盖全，但易被封 | 2009 起 | 5m/15m/30m/1m | 成交额 | **很严，易临时封 IP** | 免费 |
| 天勤 TqSdk | 全品种 | 2016 起 | 1m 仅约 50 天 | — | 受套餐限制 | 免费账号仅 `get_kline_serial` |

```bash
# 日线（默认走新浪，最稳）
.venv\Scripts\python -m cta_lab.dataio rb i TA au

# 明确指定源
.venv\Scripts\python -m cta_lab.dataio rb --source sina
.venv\Scripts\python -m cta_lab.dataio rb --source em      # 需要分钟线时

# 自动降级（em -> sina -> tqsdk）
.venv\Scripts\python -m cta_lab.dataio rb --source auto
```

> ⚠️ `--source auto` 在东财被封时会**每品种空耗约 28 秒**（4 次重试 × 7 秒）。
> 日线批量下载请直接用默认的 `sina`。

### 覆盖率实测结论（2026-10 全量跑测）

**82 个品种全部取到日线，100% 覆盖，合计 221,485 根。**

| 交易所 | 成功率 | 最早可回溯 |
|---|---|---|
| 上期所 SHFE | 19/19 ✔ | 2005-01-04 |
| 能源中心 INE | 5/5 ✔ | 2018-03-26 |
| 大商所 DCE | 23/23 ✔ | 2005-01-04 |
| 郑商所 CZCE | 24/24 ✔ | 2005-01-04 |
| 广期所 GFEX | 3/3 ✔ | 2022-12-22 |
| 中金所 CFFEX | 8/8 ✔ | 2017-01-17 |

历史深度分布：≥4000 根 20 个品种，2000~4000 根 29 个，1000~2000 根 20 个，<1000 根 13 个。

### 需要注意的三类"看起来不正常"的数据

**1. 已停牌/退市品种（数据终点停在过去，属正常）**

| 品种 | 名称 | 数据截止 |
|---|---|---|
| PM | 普麦 | 2022-06-23 |
| RI | 早籼稻 | 2022-06-30 |
| ZC | 动力煤 | 2022-12-30 |
| JR | 粳稻 | 2022-12-30 |
| WH | 强麦 | 2023-02-28 |

这些品种实际已停止交易，回测时若区间超出会自然没有数据。

**2. 新品种历史很短**

`op` 胶版纸（2025-09 上市）仅 263 根、`bz` 纯苯（2025-07）309 根、
`ps` 多晶硅（2024-12）435 根、`lg` 原木（2024-11）463 根。
样本不足 300 根时，任何策略统计结论都不可信。

**3. 中金所只有 2017 起的数据**

IF/IH/IC/T/TF 在新浪上都只回溯到 **2017-01-17**（实际上市为 2010-04）。
这是新浪的数据保留限制，不是代码问题。需要 2010 年以来的股指期货数据请用天勤或付费源。

### 新浪源字段说明

| 新浪字段 | 含义 | 映射到 |
|---|---|---|
| `d` | 日期 | `datetime` |
| `o` / `h` / `l` / `c` | 开高低收 | `open/high/low/close_price` |
| `v` | 成交量（手） | `volume` |
| `p` | **持仓量（手）** | `open_interest` |
| `s` | **结算价** | 暂存为原始字段，未映射 |

**新浪没有成交额（turnover）**，本项目按 `收盘价 × 成交量` 估算。
若策略依赖真实成交额（例如按成交额算手续费），请改用东财源。

### 东方财富的 IP 频控（重要）

东财的 `push2his` / `push2` 两个行情接口有硬性 IP 级频控。表现是：

- HTTP 连接被**直接断开且不返回任何响应**（`RemoteDisconnected`）
- 同域名的 `searchapi`（搜索）仍正常，容易误判为"代码坏了"
- 恢复时间不确定，实测持续 1 小时以上仍未恢复

因此本项目把日线默认源改为新浪。只有当确实需要 5m/15m/30m 分钟线时才用东财，
且建议拉长请求间隔（`sync_many(..., sleep=1.5)`）。


### 覆盖范围

数据来自**东方财富**公开行情接口（`push2his.eastmoney.com` / `futuresapi.eastmoney.com`），实测可用。

| 交易所 | 代码前缀 | 主力连续示例 |
|---|---|---|
| 上期所 | SHFE | `rbm` 螺纹钢主连 |
| 能源中心 | INE | `scm` 原油主连 |
| 大商所 | DCE | `im` 铁矿石主连 |
| 郑商所 | CZCE | `TAm` PTA 主连 |
| 广期所 | GFEX | `lcm` 碳酸锂主连 |
| 中金所 | CFFEX | `IFm` 沪深300主连 |

主力连续合约代码规则：`品种代码 + m`（如 `rbm`）。本项目在 `contracts.py` 里登记了 82 个品种，使用时**只写品种代码**（如 `rb`）即可，程序自动拼主力连续代码。

### 历史深度（实测 2026-10）

| 周期 | 深度 | 适用性 |
|---|---|---|
| **日线** | **2009 年至今（4000+ 根）** | ✅ CTA 回测主力 |
| 周线 | 900+ 根 | ✅ 可用 |
| 1 小时 | 约 120 根（1 个月） | ⚠️ 仅短周期验证 |
| 30m / 15m / 5m | 250 ~ 1400 根 | ⚠️ 仅短周期验证 |
| 1 分钟 | 约 70 根（仅当日） | ❌ 不适合长区间回测 |

**结论：长区间回测请用日线。** 这是免费接口的硬限制，不是代码问题。

### 数据字段

每根 K 线包含：开、高、低、收、成交量（手）、成交额（元）、**持仓量（手）**。

### 补充数据源:vnpy 数据服务(Datafeed)

东财接口只有日线全历史 + 短周期分钟线。若需要**长历史分钟线或 tick**,vnpy 有一套
独立于行情网关的「数据服务」机制,本项目已封装好配置工具。

| 适配器 | 数据商 | 成本 | 能力 |
|---|---|---|---|
| `vnpy_tqsdk` | 天勤 TqSdk(快期) | **基础版免费** | 日线 + 近 1 年 1 分钟线 |
| `vnpy_rqdata` | 米筐 RQData | 付费 | 日线/分钟/tick,质量高 |
| `vnpy_tinysoft` | 同花顺 iFinD | 付费 | 机构级 |
| `vnpy_wind` | Wind | 昂贵 | 机构级 |
| `vnpy_xt` | 迅投 QMT | 需券商开通 | 日线/分钟 |
| `vnpy_tts` | openctp TTS | 免费 | CTP 兼容,**仅实时** |

#### 配置天勤(已安装)

```bash
# 1) 免费注册快期账号: https://account.shinnytech.com/
# 2) 写入账号并联网验证
.venv\Scripts\python -m cta_lab.datafeed_setup --username 你的账号 --password 你的密码 --verify
#    或双击 scripts\configure_datafeed.bat set <账号> <密码>

# 查看配置(密码打码)
.venv\Scripts\python -m cta_lab.datafeed_setup --show

# 清空配置
.venv\Scripts\python -m cta_lab.datafeed_setup --clear
```

配置写入 `<项目根>/.vntrader/vt_setting.json`(即 `datafeed.name` / `username` / `password`),
之后 vnpy GUI 的「下载数据」按钮和 `CtaBacktester` 就都能用了。

#### 关于 CTP 与历史数据的重要事实

**`vnpy_ctp` 不含任何历史行情能力。** 拆包验证(vnpy_ctp 6.7.11.4)只有
`api/` 和 `gateway/`,`__init__.py` 仅导出 `CtpGateway`;全包检索
`query_history` / `ReqHistoryData` / `history_data` 均为 0 次命中。

原因在 CTP 协议本身:行情接口 `CThostFtdcMdApi` 只有
`SubscribeMarketData`(实时推送)和 `ReqQryDepthMarketData`(当前快照),
**没有历史 K 线接口**。每天早 6-8 点 CTP 重发前一日夜盘行情,那是重播而非历史查询。

所以:

- 做**日线 CTA 回测** → 用本项目的东财源即可,不需要 CTP
- 要**分钟线** → 装 `vnpy_tqsdk`(基础免费)
- 要**长历史 tick** → `vnpy_rqdata`(付费),或用 `vnpy_ctp` 实时订阅 + `vnpy_datarecorder` 自行落库累积
- `vnpy_ctp` 的价值在**实盘下单 + 实时行情 + SIMNOW 仿真**

### 已知缺失

- **无 tick 数据**（免费接口不提供逐笔）。需要 tick 请开通 CTP/SIMNOW 账号，装 `vnpy_ctp` 后走官方接口。
- **无夜盘分段标记**，时间戳按交易所原始口径。

---

## 存储层设计（含实测基准）

### 架构

| 项 | 值 |
|---|---|
| 引擎 | SQLite（vnpy `SqliteDatabase`，peewee ORM） |
| 位置 | `<项目根>/.vntrader/futures_lab.db`（自包含，不污染用户主目录） |
| 表 | `dbbardata`（K线主表）/ `dbbaroverview`（摘要）/ `dbtickdata` / `dbtickoverview` |

**日线与分钟线存在同一张表**，只靠 `interval` 列区分（`'d'` / `'1m'` / `'1h'` / `'w'`）。
唯一索引 `(symbol, exchange, interval, datetime)` 保证 upsert 幂等：重复下载只覆盖不重复。

### 全量日线实测（82 品种入库后）

| 指标 | 实测值 |
|---|---|
| 品种数 | 82（6 家交易所全覆盖） |
| K 线行数 | 221,656 |
| 数据库体积 | **23.22 MB** |
| 平均每行 | 110 字节 |
| 页利用率 | 100%（无空洞） |
| 单品种加载（ORM） | **171 ms** 平均（2,800 根约 181 ms） |
| 全市场 82 品种全读 | 12.2 s |
| 单次回测总耗时 | 1,116 ms，其中**存储仅占 16%** |

结论：**日线场景下 SQLite 完全不是瓶颈**，真正的耗时在 vnpy 的
`BarData` 对象构造上 —— 纯 SQL 取 4,309 行只需 54.5 ms，而 ORM 转成
BarData 要 374 ms，**ORM 开销占 85%**。

即使再跑 20 年，日线总量也就约 30 万行 / 33 MB，仍然毫无压力。

### 分钟/tick 的规模预警

| 场景 | 行数 | 体积 | ORM 全读 | 纯 SQL |
|---|---|---|---|---|
| 日线 82 品种×10 年 | 205,000 | 22 MB | 11.3 s | 2.0 s |
| 分钟 单品种×1 年 | 60,000 | 6 MB | 3.3 s | 0.6 s |
| 分钟 82 品种×1 年 | 4,920,000 | 516 MB | 4.5 min | 49 s |
| 分钟 82 品种×5 年 | 24,600,000 | 2.5 GB | 22.6 min | 4.1 min |
| tick 82 品种×1 年 | 147,600,000 | 15.1 GB | 2.3 h | 24 min |

**决策线：**

| 行数 | 动作 |
|---|---|
| < 100 万 | SQLite 直接用（当前日线 22 万行） |
| 100 万 ~ 1000 万 | 外挂 Parquet 做研究，SQLite 只留回测所需窗口 |
| > 1000 万 | 换 `vnpy_duckdb` 或列式存储 |
| > 1 亿 | ArcticDB / DolphinDB / 按日 Parquet 分区 |

### ⚠️ PRAGMA 的会话级 vs 持久级

实测发现一个容易误解的点：

| PRAGMA | 作用域 | 新建连接后 |
|---|---|---|
| `journal_mode=WAL` | **持久**（写入库文件头） | 保持 |
| `synchronous` | 会话级 | **回到默认 2(FULL)** |
| `cache_size` | 会话级 | **回到默认 -2000(2MB)** |
| `temp_store` / `mmap_size` | 会话级 | 回到默认 |

所以调优只在**通过 `init_database()` 打开数据库的进程内**生效。
若用 DB Browser、裸 `sqlite3.connect()` 或别的工具直连，看到的是默认值 ——
这不是调优失效，而是 SQLite 的会话语义。

### 写入基准

写入 6 万根 1 分钟线，每次使用全新库，交错重复 3 轮取中位数：

| 配置 | 写入 | ORM 读取 | 纯 I/O 扫描 |
|---|---|---|---|
| 默认（无调优） | 3.97s (15,115/s) | 7.20s (8,335/s) | 43.4 ms |
| WAL only | 5.68s | 1.82s | 12.1 ms |
| WAL + synchronous=NORMAL | 4.59s | 1.81s | 12.2 ms |
| WAL + cache_size=64MB | 3.94s | 1.73s | 6.8 ms |
| **全量调优（默认启用）** | **3.89s** | **1.75s** | **7.3 ms** |
| 默认 + chunk_size=5000 | 5.29s | 2.09s | 18.0 ms |

**结论：PRAGMA 调优让读取快约 4.1 倍、纯 I/O 快约 6 倍，写入持平。**
三项 PRAGMA 互补：`WAL` 带来读收益但拖慢写（0.70x），
`synchronous=NORMAL` 把写补回到 0.87x，`cache_size` 再补到 1.02x。
故三者一起启用。

### 写入分片：越大越快（与直觉相反）

`save_bar_data` 每被调用一次，除了 upsert，**还会用 `COUNT(*)` 重算一次 Overview**，
而该 COUNT 随已存数据量线性增长。所以分片越小 → 计数次数越多 → 总体越慢。

实测 6 万根数据的一次重写：

| chunk_size | 重写耗时 |
|---|---|
| 500 | 19.13s |
| 5,000 | 21.55s |
| 20,000 | 20.34s |
| **100,000（默认）** | **7.86s** |

`DEFAULT_CHUNK_SIZE = 100_000`。只有写入千万级单批数据、担心单事务内存/WAL 体积时才需调小。

### ⚠️ vnpy 的一个破坏性行为（重要坑）

`save_bar_data` 会**改写调用方传入的 BarData 对象**：

| 字段 | 保存前 | 保存后 |
|---|---|---|
| `interval` | `Interval.MINUTE` | `'1m'`（字符串） |
| `datetime` | 带时区 `Asia/Shanghai` | **丢失时区**（naive） |
| `__dict__` | 含 `gateway_name` / `vt_symbol` | **被删除** |

根因是它拿 `bar.__dict__` 的引用直接改（`d["interval"] = d["interval"].value`）。

后果：
1. 同一批数据二次保存 → 字符串再取 `.value` 直接 `AttributeError`
2. 保存后用这批 bar 做分析/绘图 → **静默丢时区，时间偏移 8 小时**

`cta_lab.store.save_bars()` 默认 `protect_caller=True`，先浅拷贝再交给 vnpy，
调用方对象保持干净、二次保存幂等。关闭开关可复现上述崩溃，用于验证。

### ⚠️ interval 建模限制：5m/15m/30m 不能进表

vnpy 的 `Interval` 枚举只有 `1m / 1h / d / w / tick`：

```python
Interval("5m")   # ValueError: '5m' is not a valid Interval
```

若把 5 分钟线硬贴 `'1m'` 标签写入，其整点时刻（如 `21:05`）会与真正的
1 分钟线**撞唯一索引并互相覆盖**。因此：

- `store.save_bars()` 会**拒绝**非原生周期并给出明确指引
- 5m/15m/30m 走 `cta_lab.altstore`：列式文件（Parquet，无 pyarrow 时退化为 CSV），
  物理隔离、零污染，可直接被 pandas / DuckDB / polars 读取做向量化研究

```bash
# 非原生粒度 -> 列式文件（不入 bar 表）
.venv\Scripts\python -m cta_lab.dataio --alt rb --minutes 5
.venv\Scripts\python -m cta_lab.dataio --alt-list
```

若要**用 5m 做 vnpy 回测**，唯一合规做法是把粒度编码进 symbol（如 `rb_5m`），
仍用 `Interval.MINUTE` 入表。

### ⚠️ 回测不校验保证金：会「静默爆仓」

vnpy 的 `BacktestingEngine` **不做保证金校验** —— 只要信号触发就按
`fixed_size` 下单。合约乘数大的品种很容易被打爆：

| 品种 | 乘数 | 一手名义（价格×乘数） | 5 手 | 100 万本金杠杆 |
|---|---|---|---|---|
| rb 螺纹钢 | 10 | 3 万 | 15 万 | 0.16 倍 |
| cu 沪铜 | 5 | 55 万 | 275 万 | 2.8 倍 |
| **IF 沪深300** | **300** | **123 万** | **615 万** | **6.45 倍** |

更麻烦的是 vnpy 的行为：权益一旦跌到 0 以下，
`calculate_statistics` 会**把所有指标静默置为 0**，只在日志里提一句
「回测中出现爆仓」。界面上就会显示「年化 0.00% / 回撤 0.00%」，
让人误以为策略根本没交易。

本项目已把这种情况显式暴露：

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  ⚠ 回测期间已爆仓（权益跌至 0 以下）
    因此下方收益/回撤/夏普均为 0——那不是「策略没交易」，
    而是 vnpy 在非正权益时静默归零。
    最低权益 -3,054,992.92   首次爆仓 2018-10-22
    估算名义杠杆 6.451 倍（本金 1,000,000）
    解决：调小 fixed_size，或调高 capital。
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

对应字段：`statistics["liquidated"]`、`["min_balance"]`、
`["liquidation_date"]`、`["est_leverage"]`；
稳健性评分直接给 `F（爆仓）`；
Web 界面顶部显示红色告警条。

**做股指期货回测时，请把 capital 提到 300 万以上并把 fixed_size 降到 1~2。**

### 何时该换存储（决策线）

| 触发条件 | 动作 |
|---|---|
| 只要日线（< 100 万行） | 不动 |
| 开始做分钟回测 | 分钟线外挂 Parquet，或装 `vnpy_duckdb` |
| 单品种分钟历史 > 5 年 | 必须外挂列式存储 |
| 要 tick（> 10⁸ 行） | 上 ArcticDB / DolphinDB / 按日 Parquet，别再考虑 SQLite |
| 需要 5m/15m/30m 进 vnpy 回测 | 先解决 interval 建模（见上） |

可替换驱动（均已核实 PyPI 可装）：`vnpy_duckdb`、`vnpy_arctic`、`vnpy_dolphindb`、
`vnpy_taos`、`vnpy_mongodb`、`vnpy_mysql`、`vnpy_postgresql`。

---

## 维科夫（Wyckoff）结构识别与交易

`cta_lab/wyckoff/` 是一个**增量式、零未来函数、双向**的维科夫状态机，
不是事后标注工具，可以直接接实盘。

```bash
# 单品种
.venv\Scripts\python -m cta_lab.cli wyckoff rb
.venv\Scripts\python -m cta_lab.cli wyckoff rb --events --trades

# 调参
.venv\Scripts\python -m cta_lab.cli wyckoff rb --vol-z 1.8 --min-cause 22 --target-r 1.5

# 多品种扫描
.venv\Scripts\python -m cta_lab.cli wyckoff --scan rb cu i au TA SA lc
```

Web 界面点「维科夫」按钮，或直接访问
`http://127.0.0.1:8765/?symbol=rb.SHFE&wyckoff=1`，
事件与冰线/小溪会叠加在 K 线上，**回测的进出场点也会标出来**：

- 绿色 ↑ = 右侧买点开多，红色 ↓ = 右侧卖点开空
- 灰色 ● = 离场，旁边标注该笔盈亏（如 `+2.9R` / `-1.0R`）
- 底部图例显示回测笔数、总收益与胜率

图上的进出场点与 `python -m cta_lab.cli wyckoff rb --trades` 打印的
成交明细是同一份数据（`/api/wyckoff` 的 `backtest.trades` 字段）。

### 结构识别

| 状态 | 事件 | 触发条件 |
|---|---|---|
| SEARCH_CLIMAX | **SC / BC** | 近 3 根内出现 N 日新低（新高的镜像）+ 量能 Z ≥ 1.5，且当根右侧止跌收阳 |
| WAIT_AR | **AR** | 从高潮极值反弹 ≥ 2 ATR（或 2%），再回落 ≥ 1 ATR |
| WAIT_ST | **ST** | 回到冰线 ±3% 且量能萎缩至高潮的 75% 以下 |
| PHASE_B | — | 横盘吸筹，**强制 ≥ 15 根**（因果法则） |
| SPRING_WINDOW | **SPRING** | 刺穿冰线，进入 8 根观察窗 |
| WAIT_TEST | **TEST** | 更高低点 + 靠近冰线 + 缩量 + 收阳 → 右侧买点 1 |
| WAIT_SOS | **SOS** | 放量跃过小溪 |
| WAIT_LPS | **LPS** | 回踩小溪缩量企稳 → 右侧买点 2 |
| 任意 | **FTI** | 结构失效（破冰未收复）→ 反向信号 |

### 已实施的改进（评审后）

| # | 问题 | 修复 |
|---|---|---|
| 1 | 设计文档承诺了 OI 协同校验，但代码只收不验 | SC 记录持仓回落、SOS 要求量增**且**(仓增 或 量暴 z≥1.5) |
| 2 | FTI 无量大阴线假破 | 破冰要求成交量 ≥ 20 日均量的 0.8 倍 |
| 3 | 派发形态刚见 BC 就被 FTI 打断（BC 10 / AR 8 / ST 1） | 派发独立参数 `DistributionConfig`：顶部放量阈值、回落幅度均放宽 |
| 4 | 盈利被 `target_r=1.0` 固定截断，无让利润奔跑 | 移动止损：浮盈 ≥1R 后启用 3×ATR ratchet 追踪 |
| 5 | 无跳空保护，可能以越停价位入场 | 开盘跳空 > 2×ATR 或直接越过止损 → 放弃该笔 |
| 6 | 成本模型只有比例手续费 | 增加 `fixed_fee_per_lot` 每手固定费 |
| 7 | API 与 CLI 各自拼一套识别+回测，结果漂移 | `/api/wyckoff` 直接调用 `runner.run_backtest`，保证与 CLI 完全一致 |

### 三个关键工程决策

**1. 增量式而非批量**

`engine.update(high, low, close, volume, oi, time)` 每根 K 线调用一次，
内部只保留滚动窗口，**结构上不可能看到未来**。
同一个对象既能回测也能实盘，不存在"回测一套、实盘一套"。

因果性做了硬验证 —— 把数据截断到第 2500 根后重跑，
事件序列必须是完整数据的前缀：

```
吸筹: 完整=63 截断=63  前缀一致=True
派发: 完整=20 截断=20  前缀一致=True
```

**2. 派发用镜像复用，不写第二套逻辑**

派发是吸筹的镜像。与其维护两套状态机（必然行为漂移），不如把
`open→-open, close→-close, high→-low, low→-high` 后跑同一套逻辑，
再把事件名/方向映射回来（SC→BC、Spring→UTAD、SOS→SOW、LPS→LPSY）。

镜像后 OHLC 仍然自洽（`-open ∈ [-high, -low]`），已做 0 违例校验。

> 踩过的坑：曾把镜像结果直接写回引擎正在使用的 `tr.ice/creek`，
> 导致后续比较全部失效 —— 表现为「48 个 BC 却 0 个 AR」。
> 现在只在输出边界映射，引擎内部状态始终留在镜像空间。

**3. 交易层与识别层分离**

状态机只说"这是 Spring"，不知道止损放哪、目标多远、下多少手。
`trading.py` 负责：

| 项 | 规则 |
|---|---|
| 入场 | 信号在 t 根收盘确认，**t+1 根开盘成交**（实盘唯一可行时点） |
| 止损 | 弹簧低点 / 冰线外侧，再加 1.5% 缓冲 |
| 目标 | 小溪 + 1.0 × 区间宽度（测量移动） |
| 时间止损 | 60 根未达目标离场 |
| 仓位 | 按风险定额：`手数 = 本金×1% / (入场-止损) / 合约乘数` |
| 防爆仓 | 名义金额上限 5× 本金 |

### 实测结果（82 品种日线，2009–2026，优化后）

```
总成交笔数     1,532
胜率           45.04%
平均 avgR      +0.135
总 R           +207.3
正收益品种     52 / 82
平均收益       +2.61%   中位 +1.07%
```

分区间一致性（**默认参数直接跑，未做任何历史拟合**）：

| 区间 | 品种 | 交易 | 胜率 | avgR | 总 R |
|---|---|---|---|---|---|
| 样本内 2009–2017 | 49 | 595 | 48.07% | **+0.210** | +125 |
| 样本外 2018–2026 | 82 | 1239 | 45.20% | **+0.153** | +189 |

样本内 avgR 从 +0.150 升到 +0.210，而样本外基本持平（+0.159 → +0.153），
说明优化是**结构性的（不拟合历史）**：它们帮的是识别质量与风控，
不是把某段行情里的参数调到最优。

参数敏感性（6 个主力品种，单变量扫描）：

| 参数 | 取值 | avgR |
|---|---|---|
| vol_z_thresh | 1.2 / 1.5 / 1.8 / 2.2 | +0.288 / +0.309 / +0.368 / +0.266 |
| min_cause_bars | 8 / 15 / 22 / 30 | +0.388 / +0.309 / +0.336 / +0.312 |
| lookback | 10 / 20 / 30 / 40 | +0.365 / +0.309 / +0.273 / +0.317 |
| target_r | 0.8 / 1.0 / 1.5 / 2.0 | +0.309 / +0.309 / +0.301 / +0.296 |

**没有悬崖**，全部参数邻域内 avgR 都在 +0.27~+0.39，说明不是拟合出来的尖峰。

### 必须知道的三件事

**1. 边际很薄，不是印钞机**

avgR ≈ 0.15、胜率 42.5% 意味着这是"小优势 + 低频"系统。
按 1% 风险定额，单品种 17 年只有约 2% 收益 —— 因为交易太少（中位 19 笔/品种）。

要提高绝对收益，正确做法是**多品种组合 + 提高单笔风险**，
而不是把参数调到历史最优点。

**2. 大部分信号来自 FTI，不是经典路径**

rb 的 14 笔交易里 7 笔是 FTI（结构失效反向），只有 2 笔 TEST、1 笔 LPS。
说明"A 阶段→B 阶段→弹簧→测试"的完整经典路径在日线上很少走完。

这不是 bug —— 真实的吸筹区间本来就有大量失败案例。
但它意味着：**这个系统本质是"结构失败后的反转" + "少量成功的经典突破"**，
而非教科书描述的纯 Wyckoff 交易。

**3. 派发形态已修复但仍偏少**

修复前派发只走到 ST（`BC 10 / AR 8 / ST 1`，UTAD 为 0）。
用 `DistributionConfig` 放宽顶部阈值后，已能识别到
`UTAD → UTAD_RECOVERED → TEST_SELL / SOW` 完整链。

但派发区间数仍少于吸筹（10 vs 15），因为商品期货的顶部分布
本来就更分散、更不典型。**不是 bug，是市场特征**。

### 代码结构

```
cta_lab/wyckoff/
├── engine.py    WyckoffEngine / DistributionEngine / 事件与区间数据结构
├── trading.py   交易计划、止损止盈、仓位、逐 K 模拟
└── runner.py    从 SQLite 取数 -> 识别 -> 回测 -> 汇总 / 多品种扫描
```

---

## 目录结构

```
vnpy-lab/
├── README.md                 本文件
├── requirements.txt          依赖（vnpy 4.5 + 4 个官方 App）
├── setup.bat                 Windows 一键安装
├── check_env.py              环境自检（8 项）
├── demo.py                   全流程演示
├── gui.py                    vnpy 图形界面启动器
│
├── .vntrader/                vnpy 运行目录（数据库/日志，首次启动自动创建）
├── strategies/               GUI 自动扫描的策略目录（发现垫片）
├── cta_lab/                  核心库
│   ├── bootstrap.py          ★ vnpy 运行目录引导（必须最先执行）
│   ├── contracts.py          82 个品种的交易所 / 乘数 / 最小变动价位
│   ├── downloader.py         东方财富行情下载（免 token，有分钟线）
│   ├── sina_source.py        新浪日线源（默认，回溯 2005/带结算价）
│   ├── exchange.py           原始行情 -> vnpy BarData / ContractData
│   ├── store.py              vnpy 数据库读写（PRAGMA 调优 / 调用方保护 / 周期校验）
│   ├── altstore.py           非原生粒度(5m/15m/30m)列式分析通道
│   ├── dataio.py             下载 -> 入库编排 + CLI
│   ├── datafeed_setup.py     数据服务配置/验证（天勤等）
│   ├── runner.py             无 GUI 回测引擎封装 / 寻优 / 结果导出
│   ├── ai.py                 交易层面统计 + 稳健性评分 + 策略诊断 + LLM 解读
│   ├── strategies.py         4 个自包含 CTA 策略（单一实现源）
│   └── cli.py                命令行入口
│
├── scripts/
│   ├── run_web.bat           启动 TradingView 风格 Web 图表
│   ├── fetch_data.bat        下载数据
│   ├── backtest.bat          单品种回测
│   ├── optimize.bat          参数寻优
│   └── run_gui.bat           启动图形界面
│
├── data/alt/                 非原生粒度数据（列式文件，自动创建）
└── results/                  回测输出（自动创建）
```

---

## 使用方式

### 0. （可选）配置历史数据服务

需要长历史分钟线时，配置天勤（免费账号），详见上文「补充数据源」：

```bash
.venv\Scripts\python -m cta_lab.datafeed_setup --username 账号 --password 密码 --verify
```

### 1. 下载数据

```bash
# 下载指定品种日线
.venv\Scripts\python -m cta_lab.dataio rb i TA au SA cu m

# 下载全部 82 个登记品种（约 20 分钟）
.venv\Scripts\python -m cta_lab.dataio --all

# 查看本地已有数据
.venv\Scripts\python -m cta_lab.dataio --list

# 分钟线（注意历史深度限制）
.venv\Scripts\python -m cta_lab.dataio rb --interval 1m
```

数据落在 **`<项目根>/.vntrader/futures_lab.db`**，整个环境自包含在项目目录内（删项目即删环境，不污染用户主目录）。GUI 与命令行共用同一个库。

### 2. 单品种回测

```bash
# 默认策略：双均线
.venv\Scripts\python -m cta_lab.cli backtest rb

# 换策略 + 改参数
.venv\Scripts\python -m cta_lab.cli backtest au --strategy AtrChannel \
    --param atr_multiplier=2.0 --param fixed_size=3

# 指定区间与资金
.venv\Scripts\python -m cta_lab.cli backtest i --strategy Donchian \
    --start 2015-01-01 --end 2024-12-31 --capital 500000

# 启用 LLM 深度解读（自备 key）
.venv\Scripts\python -m cta_lab.cli backtest rb --strategy Donchian \
    --llm sk-xxx --llm-base https://api.deepseek.com/v1 --llm-model deepseek-chat
```

输出：控制台绩效摘要 + `results/` 下的 4 个文件（绩效 JSON、逐日盈亏 CSV、交易明细 CSV、权益曲线 SVG）。

### 3. 参数寻优

```bash
# 用预置网格
.venv\Scripts\python -m cta_lab.cli optimize rb --strategy MaCross --metric sharpe_ratio

# 自定义网格
.venv\Scripts\python -m cta_lab.cli optimize rb --strategy Donchian \
    --grid entry_window=10,20,30,55 --grid exit_window=5,10,20
```

> ⚠️ 网格寻优**一定会过拟合历史**。寻优出的最优参数必须用样本外区间或滚动窗口复核，否则不要实盘。

### 4. 多品种扫描

```bash
.venv\Scripts\python -m cta_lab.cli scan --strategy Donchian --symbols rb i TA au SA cu m
```

同一策略横向对比不同品种，按稳健性评分排序，用来找"策略-品种适配度"。

### 5. TradingView 风格 Web 图表

```bash
.venv\Scripts\python -m cta_lab.web.server
# 或双击 scripts\run_web.bat
```

浏览器自动打开 `http://127.0.0.1:8765`。

界面基于 **TradingView 官方开源库 `lightweight-charts` v5.2.1**（Apache-2.0），
已随项目打包在 `cta_lab/web/static/lightweight-charts.js` —— **无需 npm、无需联网**。

```
┌──────────┬────────────────────────────────────────────────────────┐
│ CTA LAB  │  rb  螺纹钢·上期所·4,309 根                             │
│          │  周期 [日线|1小时|1分钟]  指标 [MA|EMA|BOLL|MACD|RSI|ATR]│
│ 搜索…    │  对比 [多品种叠加]  回测 [Donchian ▾] [运行回测]        │
│ ┌──────┐ ├────────────────────────────────────────────────────────┤
│ │上期所│ │ O 3070  H 3083  L 3061  C 3074  +4.00(0.13%)          │
│ │ rb   │ │ 量 544,841   持仓 1,740,703                            │
│ │ cu   │ │                          ╷                             │
│ │ au   │ │      ╷    █   ╷         █                              │
│ │ ...  │ │  ╷   █    ╵   █    ╷    ╵    ← 买卖点直接标在 K 线上    │
│ ├──────┤ │  █   ╵        ╵    █                                   │
│ │大商所│ ├────────────────────────────────────────────────────────┤
│ │ i    │ │ 成交量  ▁▃▅▂▇▄▆▃▅▂▇▄▆                                 │
│ │ m    │ ├────────────────────────────────────────────────────────┤
│ ├──────┤ │ MACD    ▔▔╲╱▔▔╲╱▔▔   （副图随指标切换）                 │
│ │郑商所│ └────────────────────────────────────────────────────────┘
│ │ TA   │ ┌────────────────────────────────────────────────────────┐
│ └──────┘ │ 总收益率 -6.31% │ 年化 -0.57% │ 回撤 -13.05% │ 夏普 -0.23│
└──────────┴────────────────────────────────────────────────────────┘
```

**功能清单**

| 功能 | 说明 |
|---|---|
| K 线 + 成交量 | 蜡烛图、成交量副图、持仓量显示 |
| 十字光标 | 跟随显示 O/H/L/C/涨跌/量/持仓 |
| 缩放拖拽 | 滚轮缩放、拖拽平移、双击复位 |
| 技术指标 | MA(5/10/20)、EMA(12/26/60)、BOLL(20,2)、MACD(12,26,9)、RSI(14)、ATR(14) |
| 多周期 | 日线 / 1 小时 / 1 分钟（取决于本地已有数据） |
| 回测买卖点 | 开平仓箭头 + 数量，直接标注在 K 线上 |
| 多品种叠加 | 归一化到 100 的收盘价对比（同交易所默认取 3 个） |
| 绩效面板 | 13 个 KPI + 稳健性评分 + 规则诊断，随回测实时更新 |

**API 一览**（可在 `/api/docs` 查看交互式文档）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/symbols` | 本地已有的品种清单（含根数/起止日期） |
| GET | `/api/bars?vt_symbol=rb.SHFE&interval=d` | 单品种 K 线（含持仓量） |
| GET | `/api/compare?symbols=rb.SHFE,cu.SHFE` | 归一化多品种序列 |
| GET | `/api/strategies` | 可用策略与默认参数 |
| POST | `/api/backtest` | 跑回测，返回成交标记 + 绩效 + 权益 |

**为什么不用 vnpy 自带的 Qt 界面？**

vnpy 自带的是桌面程序（`python gui.py`），功能完整但：

| 维度 | vnpy Qt 界面 | 本项目 Web 界面 |
|---|---|---|
| 形态 | 桌面窗口 | 浏览器 |
| K 线 | 需点「K线图表」弹独立窗，只有蜡烛+成交量 | 主界面直接显示，多面板 |
| 指标 | 无内置指标库 | 6 类指标一键切换 |
| 买卖点 | 不叠加到 K 线 | 直接标注 |
| 多品种对比 | 不支持 | 归一化叠加 |
| 远程访问 | 不能 | 局域网可访问（`--host 0.0.0.0`） |

两者互补：**做正式回测与数据管理用 Qt 界面，看图与研究用 Web 界面**，共用同一个数据库。

### 6. 图形界面（vnpy 原生）

```bash
.venv\Scripts\python gui.py     # 或双击 scripts\run_gui.bat
```

挂载了 CTA 回测 + 数据管理两个 App，菜单栏「功能」里进入。启动时会打印发现的策略清单。

#### 为什么必须有 `.vntrader/` 目录

这是本项目最重要的一个架构决策，值得单独说明。

vnpy 在 `vnpy.trader.utility` **被 import 的瞬间**就决定了运行目录：

```python
cwd = Path.cwd()
if (cwd / ".vntrader").exists():
    TRADER_DIR = cwd        # 当前目录就是运行目录
else:
    TRADER_DIR = Path.home()  # 退化成用户主目录
```

而 `MainEngine.__init__` 里有 `os.chdir(TRADER_DIR)`，`CtaBacktester` 又用
`Path.cwd()/"strategies"` 扫描策略。所以项目根目录**没有** `.vntrader/` 时：

- GUI 一启动就把工作目录切到 `~/`
- 策略目录变成 `~/strategies`（永远找不到你的策略）
- 数据库落在 `~/.vntrader/`，与项目分离

本项目的 `cta_lab/bootstrap.py` 在导入任何 vnpy 模块之前先建好 `.vntrader/`，
于是 `TRADER_DIR == 项目根目录`，策略、数据库、日志全部自包含：

```
<项目根>/
├── .vntrader/
│   ├── futures_lab.db     ← 数据库
│   └── log/               ← 运行日志
└── strategies/            ← GUI 自动扫描这个目录
```

`cta_lab/__init__.py` 已经自动调用 `ensure_trader_dir()`，所以 `import cta_lab`
就会完成引导。若你写自己的入口脚本，请把 `from cta_lab.bootstrap import ensure_trader_dir`
放在**所有 vnpy 导入之前**。

---

## 内置策略

| 名称 | 类型 | 核心逻辑 | 默认参数 |
|---|---|---|---|
| `MaCross` | 趋势 | 快慢均线交叉，始终持仓 | `fast=10, slow=20, fixed_size=5` |
| `Donchian` | 趋势 | 唐奇安通道突破 + 反向通道止损 | `entry=20, exit=10, fixed_size=5` |
| `AtrChannel` | 趋势 | 均线 ± k 倍 ATR 通道突破，波动率自适应 | `ma=20, atr=20, k=1.5, fixed_size=5` |
| `BollingerRevert` | 均值回归 | 触上下轨开仓，回归中轨平仓 | `window=20, dev=2.0, fixed_size=5` |

同时可直接调用 vnpy 官方自带策略（用裸名）：`doublema`、`bollchannel`、`atrrsi`、`turtle`、`dualtrust`、`kingkeltner`。

> 本项目策略刻意不与官方同名（本项目的双均线叫 `MaCross`，官方叫 `DoubleMaStrategy`），避免在 GUI 策略列表里互相覆盖。

### 写自己的策略

把 `CtaTemplate` 子类放进 `cta_lab/strategies.py`，然后在 `STRATEGY_REGISTRY` 注册即可：

```python
class MyStrategy(CtaTemplate):
    author = "yourname"
    params_x: int = 10
    fixed_size: int = 5

    parameters = ["params_x", "fixed_size"]
    variables = []

    def on_init(self):
        self.am = ArrayManager(size=self.params_x + 20)
        self.load_bar(60)              # 预热指标

    def on_bar(self, bar):
        self.cancel_all()
        self.am.update_bar(bar)
        if not self.am.inited:
            return
        # ... 你的信号逻辑
        if 买入条件:
            self.buy(bar.close_price, self.fixed_size)
```

需要 `Tick` 级回测时，加 `BarGenerator` 聚合（参见 `MaCrossStrategy.on_tick`）。

---

## AI 分析能力

`cta_lab.ai` 提供四层分析，**默认全部本地运行，不联网**：

### 1. 交易层面统计
vnpy 自带统计只到账户层面（总盈亏、回撤、夏普）。本模块按**开平 FIFO 配对**补齐单笔维度：

- 完成回合数、胜率、盈亏比、盈利因子、单笔期望、最大单笔盈亏、平均持仓天数

### 2. 稳健性评分（0~100）

| 维度 | 权重 | 评分依据 |
|---|---|---|
| 收益能力 | 30 | 年化收益 0%~30% 线性映射 |
| 风险控制 | 30 | 最大回撤 ≤10% 满分，≥50% 零分 |
| 风险调整收益 | 25 | 夏普比率 -1~3 线性映射 |
| 交易质量 | 15 | 胜率与盈亏比各半 |

输出 A~E 五档等级。

### 3. 规则诊断
基于指标组合生成自然语言结论，覆盖：收益能力、回撤水平、夏普、收益回撤比、样本量、胜率类型、盈亏比结构、持仓周期、**成本侵蚀比例**。

成本侵蚀是很多人忽略的：若手续费+滑点占净盈利超过 50%，说明该策略经不起真实执行摩擦。

### 4. LLM 深度解读（可选）

```bash
.venv\Scripts\python -m cta_lab.cli backtest rb --strategy Donchian \
    --llm $API_KEY --llm-base https://api.deepseek.com/v1 --llm-model deepseek-chat
```

仅用标准库 `urllib` 调用 OpenAI 格式的 `/chat/completions`，因此兼容 DeepSeek、Kimi、Qwen、本地 Ollama 等任何兼容端点。`build_llm_prompt()` 会把回测设置、账户指标、交易指标、最近成交明细整理成结构化提示词，要求模型输出策略画像、失效场景、过拟合判断、可验证的改进方向。

---

## 回测口径（务必了解）

回测结果的可信度取决于口径。本项目采用的默认值：

| 项目 | 取值 | 说明 |
|---|---|---|
| 手续费 | 按交易所 | SHFE/INE/DCE/GFEX 1bp；CZCE 每手 3 元；CFFEX 0.23bp |
| 滑点 | 1 个最小变动价位 | 保守但有代表性；可通过 `--slippage` 覆盖 |
| 撮合 | 信号价成交 | vnpy 默认：当前 K 线收盘价下单，下一根 K 线开盘附近撮合 |
| 合约乘数 | 按品种登记表 | 直接影响盈亏绝对值，可被 `--size` 覆盖 |
| 时区 | Asia/Shanghai | vnpy 官方口径，与本地时区无关 |

**主力连续合约的注意事项：**

本项目用 `品种m` 主力连续合约做长区间回测。这种做法有两个已知偏差：

1. **换月跳空**：主力切换时价格会出现跳空，可能虚增或虚减收益。东财的 `fqt=1` 参数做了前复权处理，但复权本身也会引入偏差。
2. **历史不可回溯交易**：主力连续是回看构建的序列，实盘中你无法在 2015 年就知道 2015 年 5 月的主力是谁。

严谨做法是**用具体合约 + 换月规则**回测。本项目提供 `main_continuous=False` 开关走具体合约，但换月逻辑需要自行实现（`cta_lab.exchange.raw_list_to_bars` 是接入点）。

---

## 常见问题

**Q: 下载报 "东财无数据"**
A: 该品种的主力连续代码不存在，或品种与交易所不匹配。先用 `suggest("中文名")` 查证：

```python
from cta_lab.downloader import suggest
for c in suggest("螺纹钢"):
    print(c)
```

**Q: 下载偶发 SSL 错误**
A: 东财接口有频次限制。`_http_get` 已内置 4 次重试 + 退避，批量下载时 `sync_many` 默认每次间隔 0.35 秒。若仍失败，调大间隔：

```python
from cta_lab.dataio import sync_many
sync_many(["rb", "i"], sleep=1.5)
```

**Q: 回测结果全是 0 / 收益极小**
A: 检查 `fixed_size`。默认 1 手对 100 万资金来说仓位太小，收益被淹没。内置策略默认 5 手。

**Q: 想用 DuckDB 存储**
A: `pip install vnpy_duckdb`，然后 `--duckdb` 参数或 `init_database(db_type="duckdb")`。

**Q: 数据库文件在哪**
A: `<项目根>/.vntrader/futures_lab.db`。删除该文件或整个 `.vntrader/` 目录即可清空重来。

**Q: 怎么换成 MySQL / MongoDB**
A: `pip install vnpy_mysql` 或 `vnpy_mongodb`，然后 `init_database(db_type="mysql")` 并在 `<项目根>/.vntrader/vt_setting.json` 补连接参数。

**Q: 想要 tick 级回测**
A: `vnpy_ctp` **没有历史行情**（CTP 行情接口只有实时订阅 + 当前快照，发一包验证：`vnpy_ctp` 里 `query_history` 出现 0 次）。获取 tick 只有两条路：
   - **付费**：`pip install vnpy_rqdata`（米筐，有 tick）
   - **自采**：`pip install vnpy_ctp vnpy_datarecorder`，连 SIMNOW 或实盘，订阅实时行情并持续落库累积

**Q: GUI 里看不到我的策略**
A: 确认项目根目录存在 `.vntrader/`（`cta_lab.bootstrap` 会自动创建），并且你的策略文件在
`<项目根>/strategies/*.py`。vnpy 只在 `TRADER_DIR/strategies` 里找策略，而 `TRADER_DIR`
只有在 `.vntrader/` 存在时才等于项目根目录。

---

## 免责声明

- 行情数据来自第三方公开接口，**不保证准确性、完整性、及时性**，仅供研究。
- 本项目是研究工具，**不构成任何投资建议**。
- 回测绩效不代表未来收益。过拟合的回测结果在实盘中通常迅速失效。
- 使用前请自行确认数据来源的服务条款。

---

## 技术参考

- [vnpy 官方仓库](https://github.com/vnpy/vnpy) — 本项目基于 vnpy 4.5.0
- `vnpy.trader.database` — 数据库抽象层（`BaseDatabase` / `get_database`）
- `vnpy_ctastrategy.backtesting.BacktestingEngine` — 回测引擎
- `vnpy_ctastrategy.template.CtaTemplate` — 策略基类
