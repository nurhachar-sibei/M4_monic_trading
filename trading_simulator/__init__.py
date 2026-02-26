"""
trading_simulator - 金融交易模拟系统

支持资金模式（capital）和净值模式（nav）的完整回测系统。

快速开始
--------
    from trading_simulator import TradingSimulator, load_config, run_simulation

    # 方法一：快捷函数（最简单）
    sim = run_simulation(
        position_csv="f_bond_position.csv",
        price_csv="price_df.csv",
        mode="capital",
        output_dir="output",
    )

    # 方法二：配置文件驱动
    cfg = load_config("config/default.yaml")
    sim = TradingSimulator(cfg)
    result = sim.run()
    sim.plot(save_path="output/chart.png", show=False)
    sim.to_excel("output/result.xlsx")

    # 方法三：代码配置
    from trading_simulator import Config
    cfg = Config()
    cfg.simulation.mode = "nav"
    cfg.capital.initial_capital = 500_000
    cfg.costs.stamp_duty = 0.001   # A 股印花税
    sim = TradingSimulator(cfg)
    sim.run()
"""

from .config import (Config, DataConfig, SimulationConfig, CapitalConfig,
                      CostConfig, MetricsConfig, OutputConfig, load_config,
                      EXEC_TIMING_OPTIONS, EXEC_TIMING_DESCRIPTIONS)
from .engine import SimulationResult, CapitalEngine, NAVEngine
from .metrics import MetricsCalculator
from .data_loader import DataLoader
from .plotter import ChartPlotter
from .excel_writer import ExcelWriter
from .simulator import TradingSimulator, run_simulation

__version__ = "2.0.0"
__author__ = "trading-simulator"

__all__ = [
    # 主类
    "TradingSimulator",
    "run_simulation",
    # 配置
    "Config",
    "load_config",
    "DataConfig",
    "SimulationConfig",
    "CapitalConfig",
    "CostConfig",
    "MetricsConfig",
    "OutputConfig",
    # 引擎 & 结果
    "CapitalEngine",
    "NAVEngine",
    "SimulationResult",
    # 工具类
    "MetricsCalculator",
    "DataLoader",
    "ChartPlotter",
    "ExcelWriter",
]
