"""
plotter.py - 图表生成模块

生成包含以下面板的综合图表：
  1. 净值曲线（可叠加基准）
  2. 回撤曲线
  3. 仓位变化（面积图）
  4. 评价指标表格
  5. 月度收益热力图（可选）
"""

from __future__ import annotations

import calendar
from typing import Optional

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from matplotlib.gridspec import GridSpec

from .engine import SimulationResult

# 中文字体
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 配色
PALETTE = {
    "strategy": "#2878B5",
    "benchmark": "#F28522",
    "drawdown": "#D73027",
    "position": "#74C476",
    "header_bg": "#1F497D",
    "row_even": "#EBF1DE",
    "row_odd": "#FFFFFF",
}


class ChartPlotter:
    """
    绘图器

    Parameters
    ----------
    result      : SimulationResult
    benchmark   : pd.Series, optional
        基准净值序列（index=datetime，以 1.0 为基准）
    """

    def __init__(
        self,
        result: SimulationResult,
        benchmark: Optional[pd.Series] = None,
    ) -> None:
        self.result = result
        self.benchmark = benchmark

    # ------------------------------------------------------------------ #
    # 主绘图方法
    # ------------------------------------------------------------------ #

    def plot(
        self,
        save_path: Optional[str] = None,
        show: bool = True,
        figsize: tuple = (16, 10),
        dpi: int = 150,
        include_monthly_heatmap: bool = True,
    ) -> plt.Figure:
        """
        绘制综合图表。

        Parameters
        ----------
        save_path : str, optional  保存路径（png/pdf）
        show      : bool           是否弹窗显示
        figsize   : tuple          画布尺寸
        dpi       : int            图像分辨率
        include_monthly_heatmap : bool
                    是否包含月度收益热力图（需要足够历史数据）
        """
        if include_monthly_heatmap:
            fig = self._plot_full(figsize, dpi)
        else:
            fig = self._plot_compact(figsize, dpi)

        if save_path:
            fig.savefig(save_path, dpi=dpi, bbox_inches="tight")
            print(f"  图表已保存 → {save_path}")
        if show:
            plt.show()
        plt.close(fig)
        return fig

    # ------------------------------------------------------------------ #
    # 完整版（含热力图）
    # ------------------------------------------------------------------ #

    def _plot_full(self, figsize, dpi) -> plt.Figure:
        fig = plt.figure(figsize=figsize, dpi=dpi)
        gs = GridSpec(
            4, 2,
            figure=fig,
            height_ratios=[3, 1.5, 1.5, 2.5],
            hspace=0.45,
            wspace=0.35,
        )
        ax_nav    = fig.add_subplot(gs[0, :])
        ax_dd     = fig.add_subplot(gs[1, :])
        ax_pos    = fig.add_subplot(gs[2, 0])
        ax_metric = fig.add_subplot(gs[2, 1])
        ax_heatmap = fig.add_subplot(gs[3, :])

        self._draw_nav(ax_nav)
        self._draw_drawdown(ax_dd)
        self._draw_position(ax_pos)
        self._draw_metrics_table(ax_metric)
        self._draw_monthly_heatmap(ax_heatmap)
        self._set_suptitle(fig)
        return fig

    # ------------------------------------------------------------------ #
    # 紧凑版
    # ------------------------------------------------------------------ #

    def _plot_compact(self, figsize, dpi) -> plt.Figure:
        fig = plt.figure(figsize=figsize, dpi=dpi)
        gs = GridSpec(3, 2, figure=fig, height_ratios=[3, 1.5, 2], hspace=0.45, wspace=0.35)
        ax_nav    = fig.add_subplot(gs[0, :])
        ax_dd     = fig.add_subplot(gs[1, :])
        ax_pos    = fig.add_subplot(gs[2, 0])
        ax_metric = fig.add_subplot(gs[2, 1])

        self._draw_nav(ax_nav)
        self._draw_drawdown(ax_dd)
        self._draw_position(ax_pos)
        self._draw_metrics_table(ax_metric)
        self._set_suptitle(fig)
        return fig

    # ------------------------------------------------------------------ #
    # 各子图绘制
    # ------------------------------------------------------------------ #

    def _draw_nav(self, ax: plt.Axes) -> None:
        nav = self.result.nav_series
        dates = nav.index
        ax.plot(dates, nav.values, color=PALETTE["strategy"], lw=1.5, label="策略净值")

        if self.benchmark is not None:
            bm = self.benchmark.reindex(dates).ffill()
            bm = bm / bm.iloc[0]
            ax.plot(dates, bm.values, color=PALETTE["benchmark"], lw=1.2, ls="--", label="基准净值")

        ax.axhline(y=1.0, color="gray", lw=0.8, ls=":")
        mode_label = "资金净值" if self.result.mode == "capital" else "策略净值"
        # 如果有benchmark，标题改为"策略 vs 基准"
        if self.benchmark is not None:
            title = "策略 vs 基准 净值曲线"
        else:
            title = mode_label + "曲线"
        ax.set_title(title, fontsize=12, fontweight="bold", pad=6)
        ax.set_ylabel("净值")
        ax.legend(loc="upper left", fontsize=9)
        ax.grid(True, alpha=0.25)
        self._fmt_xaxis(ax, dates)

    def _draw_drawdown(self, ax: plt.Axes) -> None:
        nav = self.result.nav_series
        peak = nav.cummax()
        dd = (nav - peak) / peak * 100
        ax.fill_between(dd.index, dd.values, 0, color=PALETTE["drawdown"], alpha=0.55, label="回撤%")
        ax.set_title("回撤曲线", fontsize=11, fontweight="bold", pad=6)
        ax.set_ylabel("回撤 (%)")
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
        ax.legend(loc="lower left", fontsize=9)
        ax.grid(True, alpha=0.25)
        self._fmt_xaxis(ax, dd.index)

    def _draw_position(self, ax: plt.Axes) -> None:
        daily_df = self.result.daily_df.copy()
        if "日期" in daily_df.columns:
            daily_df = daily_df.set_index("日期")

        # 收集所有证券的仓位数据
        position_data = []
        labels = []
        for code in self.result.securities:
            col = f"{code}_目标仓位"
            if col in daily_df.columns:
                position_data.append(daily_df[col].values)
                labels.append(code)

        if position_data:
            # 使用 stackplot 绘制堆积面积图
            colors = plt.cm.tab10(np.linspace(0, 1, len(position_data)))
            ax.stackplot(
                daily_df.index,
                *position_data,
                labels=labels,
                alpha=0.7,
                colors=colors,
            )
        ax.set_title("仓位变化（堆积图）", fontsize=11, fontweight="bold", pad=6)
        ax.set_ylabel("仓位比例")
        ax.set_ylim(-0.05, 1.15)
        ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1))
        ax.legend(fontsize=8, loc="upper left")
        ax.grid(True, alpha=0.25)
        self._fmt_xaxis(ax, daily_df.index, step_years=2)

    def _draw_metrics_table(self, ax: plt.Axes) -> None:
        ax.axis("off")
        m = self.result.metrics
        if not m:
            ax.text(0.5, 0.5, "无指标数据", ha="center", va="center", transform=ax.transAxes)
            return

        def _pct(v): return f"{v:.2%}" if not np.isnan(v) else "N/A"
        def _f(v):   return f"{v:.4f}" if not np.isnan(v) else "N/A"

        rows = [
            ["总收益率",   _pct(m.get("总收益率", np.nan))],
            ["年化收益率", _pct(m.get("年化收益率", np.nan))],
            ["年化波动率", _pct(m.get("年化波动率", np.nan))],
            ["夏普比率",   _f(m.get("夏普比率", np.nan))],
            ["索提诺比率", _f(m.get("索提诺比率", np.nan))],
            ["卡玛比率",   _f(m.get("卡玛比率", np.nan))],
            ["最大回撤",   _pct(m.get("最大回撤", np.nan))],
            ["月度胜率",   _pct(m.get("月度胜率", np.nan))],
        ]
        tbl = ax.table(
            cellText=rows,
            colLabels=["指标", "值"],
            cellLoc="center",
            loc="center",
            bbox=[0, 0, 1, 1],
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9.5)
        for (r, c), cell in tbl.get_celld().items():
            if r == 0:
                cell.set_facecolor(PALETTE["header_bg"])
                cell.set_text_props(color="white", fontweight="bold")
            elif r % 2 == 0:
                cell.set_facecolor(PALETTE["row_even"])
            else:
                cell.set_facecolor(PALETTE["row_odd"])
            cell.set_edgecolor("#CCCCCC")
        ax.set_title("评价指标", fontsize=11, fontweight="bold", pad=6)

    def _draw_monthly_heatmap(self, ax: plt.Axes) -> None:
        """月度收益热力图"""
        nav = self.result.nav_series
        monthly = nav.resample("ME").last().pct_change().dropna()

        if len(monthly) < 3:
            ax.text(0.5, 0.5, "数据不足，无法生成月度热力图", ha="center", va="center",
                    transform=ax.transAxes, fontsize=10, color="gray")
            ax.axis("off")
            return

        years = sorted(monthly.index.year.unique())
        months = list(range(1, 13))
        month_names = [calendar.month_abbr[m] for m in months]

        data_matrix = np.full((len(years), 12), np.nan)
        for dt, val in monthly.items():
            yi = years.index(dt.year)
            mi = dt.month - 1
            data_matrix[yi, mi] = val

        # 颜色映射：红涨绿跌（A股惯例）
        vmax = np.nanpercentile(np.abs(data_matrix[~np.isnan(data_matrix)]), 95) if not np.all(np.isnan(data_matrix)) else 0.01
        im = ax.imshow(
            data_matrix,
            aspect="auto",
            cmap="RdYlGn",
            vmin=-vmax,
            vmax=vmax,
        )
        ax.set_xticks(range(12))
        ax.set_xticklabels(month_names, fontsize=8)
        ax.set_yticks(range(len(years)))
        ax.set_yticklabels(years, fontsize=8)
        ax.set_title("月度收益热力图", fontsize=11, fontweight="bold", pad=6)

        # 标注数值
        for yi in range(len(years)):
            for mi in range(12):
                val = data_matrix[yi, mi]
                if not np.isnan(val):
                    txt_color = "white" if abs(val) > vmax * 0.6 else "black"
                    ax.text(mi, yi, f"{val:.1%}", ha="center", va="center",
                            fontsize=7, color=txt_color)

        plt.colorbar(im, ax=ax, fraction=0.015, pad=0.01,
                     format=mticker.PercentFormatter(xmax=1, decimals=1))

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #

    @staticmethod
    def _fmt_xaxis(ax: plt.Axes, dates, step_years: int = 1) -> None:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_major_locator(mdates.YearLocator(step_years))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=8)

    def _set_suptitle(self, fig: plt.Figure) -> None:
        nav = self.result.nav_series
        mode_cn = "资金模式" if self.result.mode == "capital" else "净值模式"
        fig.suptitle(
            f"模拟交易系统  [{mode_cn}]  "
            f"{str(nav.index[0])[:10]} ~ {str(nav.index[-1])[:10]}",
            fontsize=13,
            fontweight="bold",
            y=1.01,
        )
