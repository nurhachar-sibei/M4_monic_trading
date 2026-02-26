# 模拟交易系统 (Trading Simulator)

**推荐使用环境：研后-投前测试环境**

基于仓位信号的金融回测系统，支持 **2~N 个资产**、**资金模式**与**净值模式**，自动生成资金曲线图表和 Excel 详细报告。

---

## 目录结构

```
M4_monic_trading/
├── trading_simulator/          # 核心包
│   ├── __init__.py             # 公共 API
│   ├── config.py               # 配置管理（dataclass + YAML）
│   ├── data_loader.py          # 数据加载与对齐
│   ├── metrics.py              # 绩效指标计算
│   ├── engine.py               # 模拟引擎（CapitalEngine / NAVEngine）
│   ├── plotter.py              # 图表生成（matplotlib）
│   ├── excel_writer.py         # Excel 输出（openpyxl）
│   └── simulator.py            # 主门面类 TradingSimulator
├── config/
│   └── default.yaml            # 默认配置文件
├── examples/
│   ├── example_capital_mode.py # 资金模式示例
│   ├── example_nav_mode.py     # 净值模式示例
│   ├── example_custom_config.py# 自定义场景示例（分段/A股/YAML复用）
│   └── example_multi_asset.py  # 多资产合成数据验证示例
├── output/                     # 自动生成的输出目录
├── requirements.txt
└── README.md
```

---

## 安装依赖

```bash
pip install -r requirements.txt
```

依赖项：`numpy` `pandas` `matplotlib` `openpyxl` `pyyaml`

---

## 快速开始

### 方法一：一行快捷函数

```python
from trading_simulator import run_simulation

sim = run_simulation(
    position_csv = "f_bond_position.csv",
    price_csv    = "price_df.csv",
    mode         = "capital",      # capital | nav
    output_dir   = "output",
)
print(sim.metrics["总收益率"])
```

### 方法二：YAML 配置文件驱动

```python
from trading_simulator import TradingSimulator, load_config

cfg = load_config("config/default.yaml")
sim = TradingSimulator(cfg)
result = sim.run()
sim.print_metrics()
```

### 方法三：纯代码配置

```python
from trading_simulator import TradingSimulator, Config

cfg = Config()
cfg.simulation.mode         = "nav"
cfg.simulation.exec_timing  = "next_open"
cfg.capital.initial_capital = 500_000
cfg.costs.stamp_duty        = 0.001   # A 股印花税
cfg.output.show_chart       = False

sim = TradingSimulator(cfg)
result = sim.run()
sim.print_metrics()
sim.plot(save_path="output/nav_chart.png", show=False)
sim.to_excel("output/nav_result.xlsx")
```

---

## 输入文件格式

### 仓位文件（position_csv）

**单资产示例**

| datetime   | 511010.SH |
| ---------- | --------- |
| 2013-03-25 | 1.0       |
| 2013-04-26 | 0.0       |
| 2013-05-07 | 1.0       |

**多资产示例**（每列一个标的，各列仓位之和建议 ≤ 1）

| datetime   | ASSET_A | ASSET_B | ASSET_C |
| ---------- | ------- | ------- | ------- |
| 2022-01-04 | 1.0     | 0.0     | 0.0     |
| 2022-02-15 | 0.4     | 0.6     | 0.0     |
| 2022-03-29 | 0.0     | 0.4     | 0.6     |

- `datetime` 列：调仓信号日（YYYY-MM-DD），仅需写出**发生变化**的日期
- 其余列：各标的目标仓位比例，取值范围 `[0, 1]`
- 未出现的日期自动沿用上一行仓位（前向填充）
- 每一行代表某一次调仓中**一只或多只标的**的仓位变化

### 价格文件（price_csv）

| datetime   | OPEN   | CLOSE  | HIGH   | LOW    | wind_code |
| ---------- | ------ | ------ | ------ | ------ | --------- |
| 2013-03-25 | 99.500 | 99.408 | 99.500 | 98.814 | 511010.SH |

- 必须包含：`datetime`、`wind_code`、`OPEN`、`CLOSE`
- 可选列：`HIGH`、`LOW`
- 多资产时所有标的数据纵向堆叠在同一文件中，通过 `wind_code` 列区分

---

## 两种模式说明

### 资金模式（capital）

- 从指定起始资金（默认 100 万）开始
- 仓位变化时按总组合价值计算目标市值，再换算买卖股数（受最低手数约束）
- 多标的同日调仓时严格**先卖后买**：卖出释放现金后再执行买入
- 若现金不足以满足全部买入，按比例缩减各标的购买量
- 扣除买入/卖出手续费、印花税、摩擦成本
- 输出：总资产、现金、持仓市值、当日损益、累计损益

### 净值模式（nav）

- 从净值 1.0 开始，不受资金规模和手数约束
- 使用**加法**计算组合日收益：`portfolio_return = Σ(pos_i × ret_i)`
- 多资产不产生乘法误差（适合任意数量资产）
- 交易日扣除成本率，持仓日按收盘价/前收盘价计算涨跌
- 更适合纯策略评估、多策略横向对比

---

## 成交时机（exec_timing）

调仓信号触发后，可选择四种成交时机：

| 参数值         | 中文名   | 成交价格         | 说明                               |
| -------------- | -------- | ---------------- | ---------------------------------- |
| `prev_close` | 前日收盘 | 信号日前一日收盘 | 假设按昨收价成交，模拟 T-1 预埋单  |
| `same_open`  | 当日开盘 | 信号日开盘价     | 信号当日开盘集合竞价成交           |
| `same_close` | 当日收盘 | 信号日收盘价     | 尾盘成交，存在轻微前视偏差         |
| `next_open`  | 次日开盘 | 信号日次日开盘   | 默认值，最贴近实际；信号日维持原仓 |

**NAV 模式收益公式：**

```
买入日  ret = (close / exec_p) × (1 - buy_cost) - 1
卖出日  ret = (exec_p / prev_close) × (1 - sell_cost) - 1
持仓日  ret = close / prev_close - 1
```

---

## 多资产调仓规则

同一调仓日若存在多只标的同时变化，引擎按以下步骤执行：

1. 计算调仓前**总组合价值** V = 现金 + Σ(持仓量 × 执行价)
2. 按目标仓位算出各标的目标市值：`target_mv[i] = target_pos[i] × V`
3. **先执行所有卖出**（释放现金）
4. **再执行所有买入**（基于卖出后的现金）
5. 若现金不足，按比例同步缩减各买入标的的购买量

---

## 配置文件参考（config/default.yaml）

```yaml
data:
  position_csv: ./f_bond_position.csv
  price_csv:    ./price_df.csv

simulation:
  mode: capital          # capital | nav
  exec_timing: next_open # prev_close | same_open | same_close | next_open
  start_date: null       # YYYY-MM-DD 或 null（全量回测）
  end_date:   null

capital:
  initial_capital: 1000000
  min_lot: 100           # 每手股数（资金模式有效）

costs:
  commission_rate: 0.0003  # 双边手续费率
  stamp_duty:      0.0     # 卖出印花税（债券/期货=0；A股=0.001）
  friction_cost:   0.0001  # 额外摩擦成本率

metrics:
  risk_free_rate:   0.0
  periods_per_year: 252

output:
  dir:         ./output
  save_chart:  true
  show_chart:  false
  save_excel:  true
  chart_dpi:   150
```

---

## 输出文件说明

运行完成后，`output/` 目录下生成：

| 文件                    | 说明                                       |
| ----------------------- | ------------------------------------------ |
| `capital_chart.png`   | 净值曲线 + 回撤 + 仓位 + 指标 + 月度热力图 |
| `capital_result.xlsx` | 4 个 Sheet 的完整 Excel 报告               |
| `nav_chart.png`       | 同上（净值模式）                           |
| `nav_result.xlsx`     | 同上（净值模式）                           |

### Excel Sheet 说明

| Sheet    | 内容                                                           |
| -------- | -------------------------------------------------------------- |
| 仓位明细 | 每日资产/净值、各标的持仓股数与收盘价、当日损益、累计损益      |
| 调仓记录 | 每次买卖的标的、方向、成交时机、执行价、数量、手续费、净现金流 |
| 评价指标 | 总收益、年化收益、夏普、最大回撤、卡玛、胜率等                 |
| 逐年收益 | 每年度收益率与最大回撤（含色阶条件格式）                       |

---

## 评价指标说明

| 指标          | 说明                        |
| ------------- | --------------------------- |
| 总收益率      | 期末净值 / 期初净值 - 1     |
| 年化收益率    | 按交易日折算的复合年化收益  |
| 年化波动率    | 日收益率标准差 × √252     |
| 夏普比率      | (年化超额收益) / 年化波动率 |
| 索提诺比率    | (年化超额收益) / 下行波动率 |
| 最大回撤      | max((峰值 - 谷值) / 峰值)   |
| 卡玛比率      | 年化收益率 /\|最大回撤\|    |
| 月度/年度胜率 | 盈利月份/年份占比           |

---

## API 参考

### `run_simulation()`（快捷函数）

```python
sim = run_simulation(
    position_csv    = "f_bond_position.csv",
    price_csv       = "price_df.csv",
    mode            = "capital",       # capital | nav
    output_dir      = "./output",
    initial_capital = 1_000_000,
    min_lot         = 100,
    commission_rate = 0.0003,
    stamp_duty      = 0.0,
    friction_cost   = 0.0001,
    exec_timing     = "next_open",     # prev_close|same_open|same_close|next_open
    start_date      = None,            # "YYYY-MM-DD" 或 None
    end_date        = None,
    rf              = 0.0,             # 无风险利率
    show_plot       = False,
    save_chart      = True,
    save_excel      = True,
    verbose         = True,
    config_yaml     = None,            # 可传入 YAML 路径作为基础配置
)
```

### `TradingSimulator`

```python
sim = TradingSimulator(config: Config)
sim.run(verbose=True)       -> SimulationResult
sim.plot(save_path, show, benchmark, dpi, include_monthly_heatmap)
sim.to_excel(path: str)
sim.print_metrics()

# 常用属性
sim.result      -> SimulationResult
sim.daily_df    -> pd.DataFrame    # 每日快照
sim.trade_df    -> pd.DataFrame    # 调仓记录
sim.nav_series  -> pd.Series       # 净值序列
sim.metrics     -> dict            # 绩效指标字典
```

### `Config`

```python
cfg = Config()                          # 全默认
cfg = Config.from_yaml("path.yaml")     # 从 YAML 文件
cfg = Config.from_dict({...})           # 从字典
cfg.save_yaml("output/my_config.yaml")  # 保存为 YAML
cfg.validate()                          # 参数校验

# 子配置块直接访问
cfg.simulation.mode         = "nav"
cfg.simulation.exec_timing  = "same_open"
cfg.capital.initial_capital = 500_000
cfg.costs.stamp_duty        = 0.001
cfg.output.show_chart       = True
```

### `MetricsCalculator`

```python
calc = MetricsCalculator(nav_series, rf=0.0, periods_per_year=252)
metrics = calc.calculate()     # dict，含所有绩效指标
calc.yearly_stats()            # pd.DataFrame，逐年统计
calc.print_summary()           # 控制台格式化打印
```

---

## 示例脚本

```bash
# 资金模式基础示例
python examples/example_capital_mode.py

# 净值模式基础示例
python examples/example_nav_mode.py

# 自定义场景（分段回测 / A 股成本 / 保存并复用 YAML 配置）
python examples/example_custom_config.py

# 多资产合成数据验证（先卖后买 / 组合价值分配 / NAV 加法收益）
python examples/example_multi_asset.py

# 快速测试（同时运行两种模式并对比结果）
python test.py
```

---

## 常见问题

**Q：仓位文件日期比价格文件早，会怎样？**
系统自动取两文件的交集日期进行回测，早于价格数据的仓位信号将被忽略。

**Q：某标的在某段时间无价格数据，会怎样？**
该标的在对应日期的调仓将被跳过，并输出 `UserWarning` 提示，不影响其他标的的正常运行。

**Q：如何添加基准对比曲线？**

```python
import pandas as pd
benchmark = pd.read_csv("benchmark.csv", index_col=0, parse_dates=True)["close"]
sim.plot(benchmark=benchmark / benchmark.iloc[0], show=True)
```

**Q：`exec_timing` 四个选项有什么区别？**

- `next_open`（默认）：信号日次日开盘执行，最贴近实际，无前视偏差
- `same_open`：信号日当日开盘执行，适合可在开盘前确定信号的策略
- `same_close`：信号日当日收盘执行，存在轻微前视偏差，收益略偏高
- `prev_close`：以信号日前收盘价成交，适合预埋单 / T-1 日决策场景

**Q：多标的仓位之和可以不等于 1 吗？**
可以。剩余仓位视为持有现金（资金模式：现金记入账户；净值模式：对应权重收益为 0）。

**Q：调仓记录里的"先卖后买"顺序如何保证？**
资金模式引擎在同一调仓日内，先批量执行全部卖出标的，将回款归入现金，再批量执行全部买入标的。若现金不足，按各买入标的目标金额比例等比缩减。
