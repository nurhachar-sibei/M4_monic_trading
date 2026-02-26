"""
examples/example_nav_mode.py
净值模式示例：从 1.0 净值出发，回测相同策略
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_simulator import TradingSimulator, Config

# ------------------------------------------------------------------
# 1. 用 Config.from_dict 创建配置（不依赖 YAML 文件）
# ------------------------------------------------------------------
cfg = Config.from_dict({
    "data": {
        "position_csv": "f_bond_position.csv",
        "price_csv":    "price_df.csv",
    },
    "simulation": {
        "mode":        "nav",
        "exec_timing": "next_open",
    },
    "costs": {
        "commission_rate": 0.0003,
        "stamp_duty":      0.0,
        "friction_cost":   0.0001,
    },
    "output": {
        "dir":        "output",
        "show_chart": False,
    },
})

# ------------------------------------------------------------------
# 2. 运行
# ------------------------------------------------------------------
sim = TradingSimulator(cfg)
result = sim.run()

# ------------------------------------------------------------------
# 3. 查看逐年统计
# ------------------------------------------------------------------
from trading_simulator import MetricsCalculator

calc = MetricsCalculator(sim.nav_series, rf=0.0)
print("\n[逐年统计]")
print(calc.yearly_stats().to_string())

# ------------------------------------------------------------------
# 4. 与资金模式对比（可选）
# ------------------------------------------------------------------
cap_cfg = Config.from_dict({
    "data":       {"position_csv": "f_bond_position.csv", "price_csv": "price_df.csv"},
    "simulation": {"mode": "capital"},
    "output":     {"dir": "output", "show_chart": False, "save_chart": False, "save_excel": False},
})
sim_cap = TradingSimulator(cap_cfg)
sim_cap.run(verbose=False)

print("\n[模式对比]")
print(f"  净值模式总收益: {result.metrics['总收益率']:.2%}")
print(f"  资金模式总收益: {sim_cap.metrics['总收益率']:.2%}")
print(f"  净值模式夏普:   {result.metrics['夏普比率']:.4f}")
print(f"  资金模式夏普:   {sim_cap.metrics['夏普比率']:.4f}")
