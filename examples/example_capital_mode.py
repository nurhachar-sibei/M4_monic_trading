"""
examples/example_capital_mode.py
资金模式示例：从 100 万本金出发，完整回测 511010.SH 债券ETF策略
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_simulator import TradingSimulator, load_config

# ------------------------------------------------------------------
# 1. 从 YAML 加载默认配置，并覆盖为资金模式
# ------------------------------------------------------------------
cfg = load_config(
    "config/default.yaml",
    mode             = "capital",
    initial_capital  = 1_000_000,   # 起始 100 万
    min_lot          = 100,         # 最低 1 手（100 股）
    commission_rate  = 0.0003,      # 双边手续费 0.03%
    stamp_duty       = 0.0,         # 债券 ETF 无印花税
    friction_cost    = 0.0001,      # 摩擦成本 0.01%
    exec_timing      = "next_open", # 次日开盘执行
    show_chart       = False,       # 不弹窗
    dir              = "output",
)

# ------------------------------------------------------------------
# 2. 创建模拟器并运行
# ------------------------------------------------------------------
sim = TradingSimulator(cfg)
result = sim.run(verbose=True)

# ------------------------------------------------------------------
# 3. 单独输出（run() 已自动保存，此处仅演示手动调用）
# ------------------------------------------------------------------
# sim.plot(save_path="output/capital_chart.png", show=False)
# sim.to_excel("output/capital_result.xlsx")

# ------------------------------------------------------------------
# 4. 访问结果
# ------------------------------------------------------------------
print("\n[前 5 行每日快照]")
print(sim.daily_df.head())

print("\n[前 5 条调仓记录]")
print(sim.trade_df.head())

print(f"\n[最终净值] {sim.nav_series.iloc[-1]:.4f}")
print(f"[总收益率] {sim.metrics['总收益率']:.2%}")
