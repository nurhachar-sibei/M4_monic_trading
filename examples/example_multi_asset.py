"""
examples/example_multi_asset.py
多资产回测演示：使用合成数据验证先卖后买、组合价值分配等核心逻辑。

场景说明
--------
合成两个资产 ASSET_A 与 ASSET_B：
  - ASSET_A 单价约 100，每日随机波动
  - ASSET_B 单价约 50，每日随机波动

仓位策略（3 个阶段交替）：
  第 1 段 (前 30 日)  : A=1.0, B=0.0
  第 2 段 (中 30 日)  : A=0.4, B=0.6   ← 同日双资产调仓
  第 3 段 (后 30 日)  : A=0.0, B=1.0

验证点
------
1. 同日双资产调仓：第 1→2 段和第 2→3 段切换日，A 和 B 同时变化，引擎必须"先卖后买"。
2. 组合价值分配：目标买入金额 = target_pos × total_portfolio_value，而非 target_pos × cash。
3. NAV 加法收益：portfolio_return = Σ(pos_i × ret_i)，避免多资产乘法误差。
"""
import sys
import os
import io
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_simulator import run_simulation

# ------------------------------------------------------------------ #
# 1. 生成合成价格数据
# ------------------------------------------------------------------ #
np.random.seed(42)

dates = pd.bdate_range("2022-01-04", periods=90)   # 约 4 个月，90 个交易日

# ASSET_A：起始 100，日收益正态分布 μ=0.0003, σ=0.01
a_close = 100 * np.cumprod(1 + np.random.normal(0.0003, 0.01, 90))
a_open  = np.concatenate([[100.0], a_close[:-1]]) * np.random.uniform(0.997, 1.003, 90)

# ASSET_B：起始 50，日收益正态分布 μ=0.0005, σ=0.015
b_close = 50 * np.cumprod(1 + np.random.normal(0.0005, 0.015, 90))
b_open  = np.concatenate([[50.0], b_close[:-1]]) * np.random.uniform(0.997, 1.003, 90)

price_rows = []
for i, d in enumerate(dates):
    price_rows.append({
        "datetime": d, "wind_code": "ASSET_A",
        "OPEN": round(a_open[i], 4), "CLOSE": round(a_close[i], 4),
        "HIGH": round(max(a_open[i], a_close[i]) * 1.005, 4),
        "LOW":  round(min(a_open[i], a_close[i]) * 0.995, 4),
    })
    price_rows.append({
        "datetime": d, "wind_code": "ASSET_B",
        "OPEN": round(b_open[i], 4), "CLOSE": round(b_close[i], 4),
        "HIGH": round(max(b_open[i], b_close[i]) * 1.005, 4),
        "LOW":  round(min(b_open[i], b_close[i]) * 0.995, 4),
    })
price_df = pd.DataFrame(price_rows)

# ------------------------------------------------------------------ #
# 2. 生成合成仓位数据（3 阶段）
# ------------------------------------------------------------------ #
pos_rows = []
for i, d in enumerate(dates):
    if i < 30:
        a_pos, b_pos = 1.0, 0.0
    elif i < 60:
        a_pos, b_pos = 0.4, 0.6
    else:
        a_pos, b_pos = 0.0, 1.0
    pos_rows.append({"datetime": d, "ASSET_A": a_pos, "ASSET_B": b_pos})

pos_df = pd.DataFrame(pos_rows)

# ------------------------------------------------------------------ #
# 3. 写入临时 CSV
# ------------------------------------------------------------------ #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR  = os.path.join(BASE_DIR, "..", "output", "multi_asset_demo")
os.makedirs(TMP_DIR, exist_ok=True)

pos_csv   = os.path.join(TMP_DIR, "demo_position.csv")
price_csv = os.path.join(TMP_DIR, "demo_price.csv")

pos_df.to_csv(pos_csv, index=False)
price_df.to_csv(price_csv, index=False)

print("=" * 60)
print("  合成数据已生成")
print(f"  交易日数: {len(dates)}")
print(f"  阶段 1 (第1-30日)  : ASSET_A=100%, ASSET_B=0%")
print(f"  阶段 2 (第31-60日) : ASSET_A=40%,  ASSET_B=60%")
print(f"  阶段 3 (第61-90日) : ASSET_A=0%,   ASSET_B=100%")
print()

# ------------------------------------------------------------------ #
# 4. 资金模式回测（next_open）
# ------------------------------------------------------------------ #
print("=" * 60)
print("  场景 A: 资金模式 + 次日开盘")
print("=" * 60)
sim_cap = run_simulation(
    position_csv    = pos_csv,
    price_csv       = price_csv,
    mode            = "capital",
    output_dir      = TMP_DIR,
    initial_capital = 100_000,
    min_lot         = 1,           # 合成数据不限手数
    commission_rate = 0.0003,
    stamp_duty      = 0.0,
    friction_cost   = 0.0001,
    exec_timing     = "next_open",
    show_plot       = False,
)

# 打印前几笔调仓记录
td = sim_cap.result.trade_df
if not td.empty:
    print("\n  前 10 笔调仓记录（含先卖后买顺序）：")
    cols = ["调仓日期", "标的代码", "方向", "目标仓位", "执行价格", "数量(股)", "成交后现金"]
    print(td[cols].head(10).to_string(index=False))

# 切换日验证
switch_dates = [dates[30], dates[60]]   # 阶段切换日（信号日，next_open 在 +1 执行）
exec_dates   = [dates[31], dates[61]]
print("\n  调仓切换日验证（next_open 在信号日次日执行）：")
for sd, ed in zip(switch_dates, exec_dates):
    day_trades = td[td["调仓日期"] == ed]
    sells = day_trades[day_trades["方向"] == "卖出"]["标的代码"].tolist()
    buys  = day_trades[day_trades["方向"] == "买入"]["标的代码"].tolist()
    print(f"    执行日 {ed.date()}: 卖出 {sells}, 买入 {buys}")

# ------------------------------------------------------------------ #
# 5. 净值模式回测（same_open）
# ------------------------------------------------------------------ #
print("\n" + "=" * 60)
print("  场景 B: 净值模式 + 当日开盘")
print("=" * 60)
sim_nav = run_simulation(
    position_csv    = pos_csv,
    price_csv       = price_csv,
    mode            = "nav",
    output_dir      = TMP_DIR,
    commission_rate = 0.0003,
    exec_timing     = "same_open",
    show_plot       = False,
    verbose         = False,
)

# ------------------------------------------------------------------ #
# 6. 对比汇总
# ------------------------------------------------------------------ #
print("\n" + "=" * 60)
print("  多资产回测结果汇总")
print("=" * 60)
for label, sim in [("资金模式(next_open)", sim_cap), ("净值模式(same_open)", sim_nav)]:
    m = sim.metrics
    print(f"\n  [{label}]")
    print(f"    总收益率    : {m['总收益率']:.4%}")
    print(f"    年化收益率  : {m['年化收益率']:.4%}")
    print(f"    夏普比率    : {m['夏普比率']:.4f}")
    print(f"    最大回撤    : {m['最大回撤']:.4%}")
    print(f"    调仓笔数    : {sim.result.n_trades}")

print(f"\n  输出目录: {TMP_DIR}")
print("完成。")
