"""
metrics.py - 绩效评价指标计算模块
"""

from __future__ import annotations

from typing import Optional
import numpy as np
import pandas as pd


class MetricsCalculator:
    """
    绩效评价指标计算器

    Parameters
    ----------
    nav_series : pd.Series
        以 1.0 为基准的净值序列（index 为日期）
    rf : float
        年化无风险利率（默认 0）
    periods_per_year : int
        年化交易日数（默认 252）
    """

    def __init__(
        self,
        nav_series: pd.Series,
        rf: float = 0.0,
        periods_per_year: int = 252,
    ) -> None:
        self.nav = nav_series.dropna().sort_index()
        self.rf = rf
        self.n = periods_per_year
        self._result: Optional[dict] = None

    # ------------------------------------------------------------------ #
    # 主计算入口
    # ------------------------------------------------------------------ #

    def calculate(self) -> dict:
        """计算所有指标，返回指标字典"""
        if self._result is not None:
            return self._result

        nav = self.nav
        if len(nav) < 2:
            return {}

        daily_ret = nav.pct_change().dropna()
        n_days = len(nav) - 1

        # ---------- 收益类 ----------
        total_ret = nav.iloc[-1] / nav.iloc[0] - 1
        annual_ret = (1 + total_ret) ** (self.n / max(n_days, 1)) - 1

        # ---------- 风险类 ----------
        annual_vol = daily_ret.std() * np.sqrt(self.n)

        # ---------- 最大回撤 ----------
        peak = nav.cummax()
        dd_series = (nav - peak) / peak
        max_dd = dd_series.min()
        max_dd_end = dd_series.idxmin()
        max_dd_start = nav.loc[:max_dd_end].idxmax()

        # ---------- 水下时间 ----------
        underwater_days = int((dd_series < -0.001).sum())

        # ---------- 夏普比率 ----------
        rf_daily = (1 + self.rf) ** (1 / self.n) - 1
        excess = daily_ret - rf_daily
        sharpe = (
            excess.mean() / excess.std() * np.sqrt(self.n)
            if excess.std() > 1e-10
            else np.nan
        )

        # ---------- 索提诺比率 ----------
        downside = daily_ret[daily_ret < rf_daily]
        downside_std = downside.std()
        sortino = (
            excess.mean() / downside_std * np.sqrt(self.n)
            if downside_std > 1e-10
            else np.nan
        )

        # ---------- 卡玛比率 ----------
        calmar = (
            annual_ret / abs(max_dd) if abs(max_dd) > 1e-10 else np.nan
        )

        # ---------- 信息比率（年化超额收益/跟踪误差） ----------
        # 此处以无风险利率为基准
        ir = (
            excess.mean() / excess.std() * np.sqrt(self.n)
            if excess.std() > 1e-10
            else np.nan
        )

        # ---------- 月度胜率 ----------
        monthly_ret = nav.resample("ME").last().pct_change().dropna()
        win_rate_monthly = (monthly_ret > 0).mean() if len(monthly_ret) > 0 else np.nan

        # ---------- 年度胜率 ----------
        yearly_ret = nav.resample("YE").last().pct_change().dropna()
        win_rate_yearly = (yearly_ret > 0).mean() if len(yearly_ret) > 0 else np.nan

        self._result = {
            # 收益
            "总收益率": total_ret,
            "年化收益率": annual_ret,
            # 风险
            "年化波动率": annual_vol,
            "最大回撤": max_dd,
            "最大回撤开始": max_dd_start,
            "最大回撤结束": max_dd_end,
            "水下天数": underwater_days,
            # 风险调整收益
            "夏普比率": sharpe,
            "索提诺比率": sortino,
            "卡玛比率": calmar,
            "信息比率": ir,
            # 胜率
            "月度胜率": win_rate_monthly,
            "年度胜率": win_rate_yearly,
            # 基本信息
            "交易天数": n_days,
            "起始净值": float(nav.iloc[0]),
            "结束净值": float(nav.iloc[-1]),
            "开始日期": nav.index[0],
            "结束日期": nav.index[-1],
        }
        return self._result

    # ------------------------------------------------------------------ #
    # 分年度统计
    # ------------------------------------------------------------------ #

    def yearly_stats(self) -> pd.DataFrame:
        """返回逐年收益率 DataFrame"""
        nav = self.nav
        yearly_nav = nav.resample("YE").last()
        yearly_nav_start = nav.resample("YE").first()
        ret = (yearly_nav / yearly_nav_start - 1).rename("年度收益率")
        dd_list = []
        for year in ret.index.year:
            sub = nav[nav.index.year == year]
            if len(sub) < 2:
                dd_list.append(np.nan)
            else:
                peak = sub.cummax()
                dd_list.append(((sub - peak) / peak).min())
        df = pd.DataFrame({
            "年度收益率": ret.values,
            "年内最大回撤": dd_list,
        }, index=ret.index.year)
        return df

    # ------------------------------------------------------------------ #
    # 格式化打印
    # ------------------------------------------------------------------ #

    def print_summary(self, title: str = "绩效评价指标") -> None:
        m = self.calculate()
        if not m:
            print("数据不足，无法计算指标")
            return
        sep = "=" * 52
        print(f"\n{sep}")
        print(f"  {title}")
        print(sep)
        _fmt_row("总收益率",    m["总收益率"],    "pct")
        _fmt_row("年化收益率",  m["年化收益率"],  "pct")
        _fmt_row("年化波动率",  m["年化波动率"],  "pct")
        print()
        _fmt_row("夏普比率",    m["夏普比率"],    "f4")
        _fmt_row("索提诺比率",  m["索提诺比率"],  "f4")
        _fmt_row("卡玛比率",    m["卡玛比率"],    "f4")
        print()
        _fmt_row("最大回撤",    m["最大回撤"],    "pct")
        print(f"  {'最大回撤区间':<14}: {str(m['最大回撤开始'])[:10]} ~ {str(m['最大回撤结束'])[:10]}")
        _fmt_row("水下天数",    m["水下天数"],    "d")
        print()
        _fmt_row("月度胜率",    m["月度胜率"],    "pct")
        _fmt_row("年度胜率",    m["年度胜率"],    "pct")
        print()
        _fmt_row("交易天数",    m["交易天数"],    "d")
        print(f"  {'回测区间':<14}: {str(m['开始日期'])[:10]} ~ {str(m['结束日期'])[:10]}")
        print(sep)


def _fmt_row(label: str, value, fmt: str) -> None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        print(f"  {label:<14}: N/A")
        return
    if fmt == "pct":
        print(f"  {label:<14}: {value:.2%}")
    elif fmt == "f4":
        print(f"  {label:<14}: {value:.4f}")
    elif fmt == "d":
        print(f"  {label:<14}: {value:,}")
    else:
        print(f"  {label:<14}: {value}")
