"""
examples/example_custom_config.py
自定义场景示例：
  1. 分段回测（指定起止日期）
  2. A 股场景（含印花税）
  3. 使用 run_simulation 快捷函数一行启动
  4. 保存并读取 YAML 配置
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_simulator import run_simulation, Config, load_config, TradingSimulator

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(BASE_DIR, "..")


# ==================================================================
# 场景 1：分段回测（仅看 2020 年以后）
# ==================================================================
print("\n=== 场景 1：2020-01-01 起分段回测 ===")
sim1 = run_simulation(
    position_csv    = os.path.join(ROOT_DIR, "f_bond_position.csv"),
    price_csv       = os.path.join(ROOT_DIR, "price_df.csv"),
    mode            = "capital",
    output_dir      = os.path.join(ROOT_DIR, "output", "segment"),
    start_date      = "2020-01-01",
    end_date        = "2023-12-31",
    initial_capital = 500_000,
    show_plot       = False,
)
print(f"  区间收益: {sim1.metrics['总收益率']:.2%}  夏普: {sim1.metrics['夏普比率']:.4f}")


# ==================================================================
# 场景 2：模拟 A 股场景（开启印花税，调高手续费）
# ==================================================================
print("\n=== 场景 2：A 股成本设置（含印花税）===")
sim2 = run_simulation(
    position_csv    = os.path.join(ROOT_DIR, "f_bond_position.csv"),
    price_csv       = os.path.join(ROOT_DIR, "price_df.csv"),
    mode            = "nav",
    output_dir      = os.path.join(ROOT_DIR, "output", "astock"),
    commission_rate = 0.0003,
    stamp_duty      = 0.001,    # A 股印花税 0.1%
    friction_cost   = 0.0002,
    show_plot       = False,
    verbose         = False,
)
print(f"  A股成本下总收益: {sim2.metrics['总收益率']:.2%}  最大回撤: {sim2.metrics['最大回撤']:.2%}")


# ==================================================================
# 场景 3：保存当前配置为 YAML，下次直接加载
# ==================================================================
print("\n=== 场景 3：保存并复用 YAML 配置 ===")
cfg = Config.from_dict({
    "data":       {"position_csv": "f_bond_position.csv", "price_csv": "price_df.csv"},
    "simulation": {"mode": "capital", "exec_timing": "next_open"},
    "capital":    {"initial_capital": 2_000_000, "min_lot": 100},
    "costs":      {"commission_rate": 0.0003, "stamp_duty": 0.0},
    "output":     {"dir": "output/custom", "show_chart": False},
})

save_path = os.path.join(ROOT_DIR, "config", "my_config.yaml")
cfg.save_yaml(save_path)
print(f"  已保存配置至 {save_path}")

# 重新加载并运行
cfg_loaded = load_config(save_path)
sim3 = TradingSimulator(cfg_loaded)
result3 = sim3.run(verbose=False)
print(f"  复用配置后总收益: {result3.metrics['总收益率']:.2%}")


# ==================================================================
# 场景 4：仅打印指标，不保存任何文件
# ==================================================================
print("\n=== 场景 4：纯分析，不保存文件 ===")
cfg4 = Config()  # 全默认
cfg4.output.save_chart = False
cfg4.output.save_excel = False
cfg4.output.show_chart = False
sim4 = TradingSimulator(cfg4)
sim4.run(verbose=False)
sim4.print_metrics()
