"""
test.py - 快速验证脚本
同时运行资金模式与净值模式，打印指标，生成图表和 Excel。
"""
import os
import sys

# 确保包路径可用
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_simulator import run_simulation, TradingSimulator, load_config

BASE = os.path.dirname(os.path.abspath(__file__))
POS   = os.path.join(BASE, "f_bond_position.csv")
PRICE = os.path.join(BASE, "price_df.csv")
OUT   = os.path.join(BASE, "output")
CFG   = os.path.join(BASE, "config", "default.yaml")

# ================================================================
# 方式 A：快捷函数（最简单）
# ================================================================
print("=" * 60)
print("  资金模式 - run_simulation 快捷函数")
print("=" * 60)
sim_cap = run_simulation(
    position_csv    = POS,
    price_csv       = PRICE,
    mode            = "capital",
    output_dir      = OUT,
    initial_capital = 1_000_000,
    min_lot         = 100,
    commission_rate = 0.0003,
    stamp_duty      = 0.0,
    friction_cost   = 0.0001,
    exec_timing     = "next_open",
    show_plot       = False,
)

# ================================================================
# 方式 B：YAML 配置文件 + 参数覆盖
# ================================================================
print("\n" + "=" * 60)
print("  净值模式 - load_config + TradingSimulator")
print("=" * 60)
cfg = load_config(CFG, mode="nav", show_chart=False)
# 覆盖数据路径（绝对路径，避免工作目录问题）
cfg.data.position_csv = POS
cfg.data.price_csv    = PRICE
cfg.output.dir        = OUT

sim_nav = TradingSimulator(cfg)
result_nav = sim_nav.run()

# ================================================================
# 对比摘要
# ================================================================
print("\n" + "=" * 60)
print("  两种模式对比")
print("=" * 60)
for label, sim in [("资金模式", sim_cap), ("净值模式", sim_nav)]:
    m = sim.metrics
    print(f"\n  [{label}]")
    print(f"    总收益率   : {m['总收益率']:.2%}")
    print(f"    年化收益率 : {m['年化收益率']:.2%}")
    print(f"    夏普比率   : {m['夏普比率']:.4f}")
    print(f"    最大回撤   : {m['最大回撤']:.2%}")
    print(f"    卡玛比率   : {m['卡玛比率']:.4f}")
    print(f"    调仓次数   : {sim.result.n_trades}")

print(f"\n输出文件位于: {OUT}")
print("完成。")
