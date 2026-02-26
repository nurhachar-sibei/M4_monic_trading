"""
simulator.py - 主门面类（Facade）

TradingSimulator 统一封装了数据加载、引擎运行、图表生成、Excel 输出
等全部流程，对外提供简洁的一站式接口。

快速开始
--------
from trading_simulator import TradingSimulator, load_config

cfg = load_config("config/default.yaml")
sim = TradingSimulator(cfg)
result = sim.run()
sim.print_metrics()
sim.plot(save_path="output/chart.png")
sim.to_excel("output/result.xlsx")
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from .config import Config, load_config
from .data_loader import DataLoader
from .engine import CapitalEngine, NAVEngine, SimulationResult
from .excel_writer import ExcelWriter
from .metrics import MetricsCalculator
from .plotter import ChartPlotter


class TradingSimulator:
    """
    一站式模拟交易系统

    Parameters
    ----------
    config : Config
        通过 load_config() 或 Config.from_yaml() 创建的配置对象

    Examples
    --------
    >>> cfg = load_config("config/default.yaml", mode="capital")
    >>> sim = TradingSimulator(cfg)
    >>> result = sim.run()
    >>> sim.print_metrics()
    >>> sim.plot(save_path="output/capital_chart.png", show=False)
    >>> sim.to_excel("output/capital.xlsx")
    """

    def __init__(self, config: Config) -> None:
        self.config = config.validate()
        self._result: Optional[SimulationResult] = None
        self._pos_df: Optional[pd.DataFrame] = None
        self._price_pivot: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #

    def run(self, verbose: bool = True) -> SimulationResult:
        """
        加载数据并运行模拟，返回 SimulationResult。

        Parameters
        ----------
        verbose : bool
            是否打印数据摘要
        """
        if verbose:
            print(f"\n[模拟交易系统] 模式={self.config.simulation.mode.upper()}")
            print(f"  加载数据...")

        # 1. 数据加载
        self._pos_df, self._price_pivot = DataLoader.load(self.config)
        if verbose:
            DataLoader.print_info(self._pos_df, self._price_pivot)

        # 2. 引擎选择
        EngineClass = (
            CapitalEngine
            if self.config.simulation.mode == "capital"
            else NAVEngine
        )
        engine = EngineClass(self._pos_df, self._price_pivot, self.config)

        if verbose:
            print(f"  运行引擎 ({EngineClass.__name__})...")

        # 3. 运行
        self._result = engine.run()

        # 4. 计算指标
        calc = MetricsCalculator(
            self._result.nav_series,
            rf=self.config.metrics.risk_free_rate,
            periods_per_year=self.config.metrics.periods_per_year,
        )
        self._result.metrics = calc.calculate()

        if verbose:
            calc.print_summary(
                title=f"绩效指标 - {'资金模式' if self.config.simulation.mode == 'capital' else '净值模式'}"
            )

        # 5. 自动输出
        cfg_out = self.config.output
        os.makedirs(cfg_out.dir, exist_ok=True)
        prefix = os.path.join(cfg_out.dir, self.config.simulation.mode)

        if cfg_out.save_chart:
            self.plot(
                save_path=f"{prefix}_chart.png",
                show=cfg_out.show_chart,
                dpi=cfg_out.chart_dpi,
            )
        if cfg_out.save_excel:
            excel_path = f"{prefix}_result.xlsx"
            writer = ExcelWriter(self._result)
            writer.write(excel_path)
            writer.write_daily_folder(cfg_out.dir)

        return self._result

    # ------------------------------------------------------------------ #
    # 图表
    # ------------------------------------------------------------------ #

    def plot(
        self,
        save_path: Optional[str] = None,
        show: bool = True,
        benchmark: Optional[pd.Series] = None,
        dpi: int = 150,
        include_monthly_heatmap: bool = True,
    ) -> None:
        """
        生成综合图表。

        Parameters
        ----------
        save_path : str, optional   保存路径（.png / .pdf）
        show      : bool            是否弹窗显示
        benchmark : pd.Series       基准净值（可选）
        dpi       : int             图像分辨率
        include_monthly_heatmap : bool
        """
        self._ensure_result()
        plotter = ChartPlotter(self._result, benchmark=benchmark)
        figsize = (
            self.config.output.chart_width,
            self.config.output.chart_height,
        )
        plotter.plot(
            save_path=save_path,
            show=show,
            figsize=figsize,
            dpi=dpi,
            include_monthly_heatmap=include_monthly_heatmap,
        )

    # ------------------------------------------------------------------ #
    # Excel
    # ------------------------------------------------------------------ #

    def to_excel(self, path: str) -> None:
        """导出汇总 Excel 结果文件（4 个 Sheet）"""
        self._ensure_result()
        ExcelWriter(self._result).write(path)

    def to_daily_folder(self, output_dir: Optional[str] = None) -> None:
        """
        在指定目录下生成"每日仓位明细"文件夹，每个交易日一个 Excel。

        Parameters
        ----------
        output_dir : str, optional
            目标目录；为 None 时使用 config.output.dir
        """
        self._ensure_result()
        folder = output_dir or self.config.output.dir
        os.makedirs(folder, exist_ok=True)
        ExcelWriter(self._result).write_daily_folder(folder)

    # ------------------------------------------------------------------ #
    # 指标
    # ------------------------------------------------------------------ #

    def print_metrics(self) -> None:
        """打印评价指标摘要"""
        self._ensure_result()
        mode_cn = "资金模式" if self.config.simulation.mode == "capital" else "净值模式"
        MetricsCalculator(
            self._result.nav_series,
            rf=self.config.metrics.risk_free_rate,
            periods_per_year=self.config.metrics.periods_per_year,
        ).print_summary(title=f"绩效指标 [{mode_cn}]")

    # ------------------------------------------------------------------ #
    # 属性访问
    # ------------------------------------------------------------------ #

    @property
    def result(self) -> SimulationResult:
        self._ensure_result()
        return self._result

    @property
    def daily_df(self) -> pd.DataFrame:
        self._ensure_result()
        return self._result.daily_df

    @property
    def trade_df(self) -> pd.DataFrame:
        self._ensure_result()
        return self._result.trade_df

    @property
    def nav_series(self) -> pd.Series:
        self._ensure_result()
        return self._result.nav_series

    @property
    def metrics(self) -> dict:
        self._ensure_result()
        return self._result.metrics

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _ensure_result(self) -> None:
        if self._result is None:
            raise RuntimeError("请先调用 sim.run() 执行模拟。")

    def __repr__(self) -> str:
        mode = self.config.simulation.mode
        ran = self._result is not None
        return (
            f"TradingSimulator(mode={mode!r}, ran={ran}, "
            f"securities={list(self._pos_df.columns) if self._pos_df is not None else '[]'})"
        )


# --------------------------------------------------------------------------- #
# 便捷函数
# --------------------------------------------------------------------------- #

def run_simulation(
    position_csv: str,
    price_csv: str,
    mode: str = "capital",
    output_dir: str = "./output",
    initial_capital: float = 1_000_000,
    min_lot: int = 100,
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.0,
    friction_cost: float = 0.0001,
    exec_timing: str = "next_open",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    rf: float = 0.0,
    show_plot: bool = False,
    save_chart: bool = True,
    save_excel: bool = True,
    verbose: bool = True,
    config_yaml: Optional[str] = None,
) -> TradingSimulator:
    """
    一行代码启动完整回测的快捷函数。

    Parameters
    ----------
    position_csv    : 仓位 CSV 路径
    price_csv       : 价格 CSV 路径
    mode            : "capital" | "nav"
    output_dir      : 输出目录
    initial_capital : 起始资金（资金模式）
    min_lot         : 最小手数（每手股数）
    commission_rate : 手续费率（双边）
    stamp_duty      : 印花税率（卖出，A股 0.001；债券 ETF 0）
    friction_cost   : 额外摩擦成本率（双边）
    exec_timing     : "next_open" 次日开盘 | "same_close" 当日收盘
    start_date      : 模拟开始日期（YYYY-MM-DD）
    end_date        : 模拟结束日期（YYYY-MM-DD）
    rf              : 年化无风险利率
    show_plot       : 是否弹窗显示图表
    save_chart      : 是否保存图表
    save_excel      : 是否保存 Excel
    verbose         : 是否打印过程信息
    config_yaml     : 可选，YAML 配置文件路径（会被上述参数覆盖）

    Returns
    -------
    TradingSimulator（已调用 run()）
    """
    # 优先用 YAML，再用参数覆盖
    base_cfg = load_config(config_yaml) if config_yaml else Config()

    cfg = Config.from_dict({
        "data":       {"position_csv": position_csv, "price_csv": price_csv},
        "simulation": {"mode": mode, "exec_timing": exec_timing,
                       "start_date": start_date, "end_date": end_date},
        "capital":    {"initial_capital": initial_capital, "min_lot": min_lot},
        "costs":      {"commission_rate": commission_rate,
                       "stamp_duty": stamp_duty, "friction_cost": friction_cost},
        "metrics":    {"risk_free_rate": rf},
        "output":     {"dir": output_dir, "show_chart": show_plot,
                       "save_chart": save_chart, "save_excel": save_excel},
    })

    sim = TradingSimulator(cfg)
    sim.run(verbose=verbose)
    return sim
