"""
engine.py - 核心模拟引擎（多资产版）

支持 2~N 个资产，仓位可为 0~1 的任意比例（总和 ≤ 1）。

多资产调仓规则
--------------
同一调仓日若存在多只标的变化，严格按"先卖后买"顺序执行：
  1. 计算调仓前总组合价值 V = 现金 + Σ(持仓量 × 执行价)
  2. 按各标的目标仓位算出目标市值: target_mv[i] = target_pos[i] × V
  3. 先卖出所有需减仓的标的（释放现金）
  4. 再买入所有需增仓的标的（按目标市值计算）
  5. 若现金不足以满足全部买入，按比例缩减购买量

成交时机（exec_timing）
-----------------------
  prev_close : 前日收盘价
  same_open  : 当日开盘价
  same_close : 当日收盘价
  next_open  : 次日开盘价（信号日保持旧仓，挂单次日开盘执行）

NAV 模式收益公式（加法，适用于多资产）
--------------------------------------
  portfolio_daily_return = Σ(pos[i] × ret[i])

  买入日  ret = (close / exec_p) × (1 - buy_cost) - 1
  卖出日  ret = (exec_p / prev_close) × (1 - sell_cost) - 1
  持仓日  ret = close / prev_close - 1

  nav[t] = nav[t-1] × (1 + portfolio_daily_return)

每日标的数据字段
----------------
资金模式下，daily_df 对每个标的 code 包含以下列：
  {code}_目标仓位   目标仓位比例
  {code}_实际仓位   已执行的实际仓位
  {code}_持仓股数   持仓股数（整数）
  {code}_昨收价     前一交易日收盘价
  {code}_开盘价     当日开盘价
  {code}_收盘价     当日收盘价
  {code}_持仓市值   当日收盘市值（元）
  {code}_买入成本   当前持仓的累计买入成本（全仓清空时归零）
  {code}_当日损益   当日该标的损益（元）
  {code}_累计损益   持仓期间累计损益（元），= 今日市值 - 买入成本
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import Config


# --------------------------------------------------------------------------- #
# 结果数据类
# --------------------------------------------------------------------------- #

@dataclass
class SimulationResult:
    mode: str
    daily_df: pd.DataFrame
    trade_df: pd.DataFrame
    nav_series: pd.Series
    metrics: dict = field(default_factory=dict)
    config: Optional[Config] = None

    @property
    def securities(self) -> list:
        return [c.split("_目标仓位")[0]
                for c in self.daily_df.columns if c.endswith("_目标仓位")]

    @property
    def n_trades(self) -> int:
        return len(self.trade_df)

    @property
    def total_return(self) -> float:
        return self.metrics.get("总收益率", np.nan)

    @property
    def annual_return(self) -> float:
        return self.metrics.get("年化收益率", np.nan)

    @property
    def max_drawdown(self) -> float:
        return self.metrics.get("最大回撤", np.nan)

    @property
    def sharpe(self) -> float:
        return self.metrics.get("夏普比率", np.nan)


# --------------------------------------------------------------------------- #
# 基类
# --------------------------------------------------------------------------- #

class _BaseEngine:

    def __init__(self, pos_df: pd.DataFrame, price_pivot: pd.DataFrame,
                 config: Config) -> None:
        self.pos_df = pos_df
        self.price_pivot = price_pivot
        self.cfg = config
        self.securities: List[str] = list(pos_df.columns)
        self.trade_dates: List[pd.Timestamp] = list(pos_df.index)

    def _price(self, date: pd.Timestamp, code: str, field: str) -> float:
        try:
            v = self.price_pivot.loc[date, (field, code)]
            return float(v) if pd.notna(v) else np.nan
        except KeyError:
            return np.nan

    def _pos(self, date: pd.Timestamp, code: str) -> float:
        try:
            return float(self.pos_df.loc[date, code])
        except KeyError:
            return 0.0

    def _buy_cost_rate(self) -> float:
        return self.cfg.costs.commission_rate + self.cfg.costs.friction_cost

    def _sell_cost_rate(self) -> float:
        return (self.cfg.costs.commission_rate
                + self.cfg.costs.stamp_duty
                + self.cfg.costs.friction_cost)

    def _get_exec_price(self, date: pd.Timestamp, code: str,
                        prev_close: float) -> float:
        """返回即时成交价（不含 next_open，next_open 用 pending 机制）"""
        timing = self.cfg.simulation.exec_timing
        if timing == "prev_close":
            return prev_close if not np.isnan(prev_close) else self._price(date, code, "OPEN")
        elif timing == "same_open":
            return self._price(date, code, "OPEN")
        elif timing == "same_close":
            return self._price(date, code, "CLOSE")
        raise ValueError(f"不支持的 exec_timing: {timing!r}")


# --------------------------------------------------------------------------- #
# 辅助：调仓后更新成本基准与当日资金流水
# --------------------------------------------------------------------------- #

def _update_cost_flows(
    exec_prices: Dict[str, float],
    old_hold_snap: Dict[str, int],
    holdings: Dict[str, int],
    buy_cost_r: float,
    sell_cost_r: float,
    cost_basis: Dict[str, float],
    day_buy_costs: Dict[str, float],
    day_sell_proceeds: Dict[str, float],
) -> None:
    """
    根据调仓前后持仓变化，更新：
    - cost_basis：当前持仓的总买入成本（部分卖出按比例缩减，全仓清空归零）
    - day_buy_costs：当日各标的实际买入金额（含成本）
    - day_sell_proceeds：当日各标的实际卖出回款
    """
    for code, ep in exec_prices.items():
        old_s = old_hold_snap.get(code, 0)
        new_s = holdings.get(code, 0)

        if new_s > old_s:          # 净买入
            bought   = new_s - old_s
            cost     = bought * ep * (1 + buy_cost_r)
            day_buy_costs[code]  = day_buy_costs.get(code, 0.0) + cost
            cost_basis[code]     = cost_basis.get(code, 0.0) + cost

        elif new_s < old_s:        # 净卖出
            sold     = old_s - new_s
            proceeds = sold * ep * (1 - sell_cost_r)
            day_sell_proceeds[code] = day_sell_proceeds.get(code, 0.0) + proceeds
            if new_s <= 0:
                cost_basis[code] = 0.0
            else:
                cost_basis[code] = cost_basis.get(code, 0.0) * (new_s / old_s)


# --------------------------------------------------------------------------- #
# 多资产调仓执行器（资金模式）
# --------------------------------------------------------------------------- #

def _execute_rebalance_capital(
    *,
    date: pd.Timestamp,
    changing_codes: List[str],       # 需要变化的标的列表
    target_pos: Dict[str, float],    # 当日目标仓位
    actual_pos: Dict[str, float],    # 当前实际仓位
    holdings: Dict[str, int],        # 当前持仓股数
    cash: float,
    exec_prices: Dict[str, float],   # 执行价格（only for changing_codes with valid price）
    close_prices: Dict[str, float],  # 收盘价（所有标的）
    min_lot: int,
    buy_cost_r: float,
    sell_cost_r: float,
    commission_rate: float,
    stamp_duty: float,
    friction_cost: float,
    timing_label: str,
    trade_records: List[dict],
) -> Tuple[float, Dict[str, int], Dict[str, float]]:
    """
    多资产一次性调仓：先卖后买。

    Returns
    -------
    (new_cash, new_holdings, new_actual_pos)
    """
    # ------------------------------------------------------------------ #
    # 1. 计算调仓前总组合价值
    #    优先用执行价（更接近交易时点），无执行价则用收盘价
    # ------------------------------------------------------------------ #
    all_prices: Dict[str, float] = {**close_prices, **exec_prices}
    total_value = cash
    for code in holdings:
        p = all_prices.get(code, np.nan)
        if holdings[code] > 0 and not np.isnan(p):
            total_value += holdings[code] * p

    # ------------------------------------------------------------------ #
    # 2. 分离卖出 / 买入
    # ------------------------------------------------------------------ #
    sell_codes = [c for c in changing_codes
                  if target_pos[c] < actual_pos[c]
                  and not np.isnan(exec_prices.get(c, np.nan))
                  and exec_prices[c] > 0]
    buy_codes  = [c for c in changing_codes
                  if target_pos[c] > actual_pos[c]
                  and not np.isnan(exec_prices.get(c, np.nan))
                  and exec_prices[c] > 0]

    # ------------------------------------------------------------------ #
    # 3. 先执行卖出
    # ------------------------------------------------------------------ #
    for code in sell_codes:
        ep      = exec_prices[code]
        new_p   = target_pos[code]
        pre_pos = actual_pos[code]   # 调仓前仓位（市值权重）

        if holdings[code] <= 0:
            actual_pos[code] = new_p
            continue

        if new_p <= 1e-9:
            # 全仓卖出
            shares = holdings[code]
        else:
            # 部分卖出：减仓到目标市值
            target_mv  = new_p * total_value
            excess_mv  = holdings[code] * ep - target_mv
            shares = max(0, int(excess_mv / ep // min_lot) * min_lot)

        if shares <= 0:
            continue

        revenue = shares * ep * (1 - sell_cost_r)
        comm    = shares * ep * commission_rate
        tax     = shares * ep * stamp_duty
        fric    = shares * ep * friction_cost
        cash   += revenue
        holdings[code] -= shares
        actual_pos[code] = new_p

        trade_records.append({
            "调仓日期":   date,
            "标的代码":   code,
            "成交时机":   timing_label,
            "方向":       "卖出",
            "目标仓位":   new_p,
            "前置仓位":   round(pre_pos, 6),
            "调整仓位":   round(new_p - pre_pos, 6),
            "执行价格":   round(ep, 4),
            "数量(股)":   shares,
            "成交金额":   round(ep * shares, 2),
            "手续费":     round(comm, 2),
            "印花税":     round(tax, 2),
            "摩擦成本":   round(fric, 2),
            "净现金流":   round(revenue, 2),
            "成交后现金": round(cash, 2),
        })

    # ------------------------------------------------------------------ #
    # 4. 再执行买入
    # ------------------------------------------------------------------ #
    # 先计算各买入标的理想购入量
    buy_orders: List[Tuple[str, int, float]] = []   # (code, shares, cost)
    total_ideal_cost = 0.0

    for code in buy_codes:
        ep      = exec_prices[code]
        new_p   = target_pos[code]

        target_mv   = new_p * total_value
        current_mv  = holdings[code] * ep
        additional  = max(0.0, target_mv - current_mv)

        ideal_shares = int(additional / ep / (1 + buy_cost_r) // min_lot) * min_lot
        if ideal_shares <= 0:
            continue

        ideal_cost = ideal_shares * ep * (1 + buy_cost_r)
        buy_orders.append((code, ideal_shares, ideal_cost))
        total_ideal_cost += ideal_cost

    # 若现金不足，按比例缩减
    scale = min(1.0, cash / total_ideal_cost) if total_ideal_cost > 0 else 0.0

    for code, ideal_shares, _ in buy_orders:
        ep      = exec_prices[code]
        new_p   = target_pos[code]
        pre_pos = actual_pos[code]   # 调仓前仓位（市值权重）

        shares = int(ideal_shares * scale // min_lot) * min_lot
        if shares <= 0:
            continue

        cost = shares * ep * (1 + buy_cost_r)
        if cost > cash + 1e-6:   # 浮点容差
            shares = int(cash / ep / (1 + buy_cost_r) // min_lot) * min_lot
            if shares <= 0:
                continue
            cost = shares * ep * (1 + buy_cost_r)

        comm = shares * ep * commission_rate
        fric = shares * ep * friction_cost
        cash -= cost
        holdings[code] += shares
        actual_pos[code] = new_p

        trade_records.append({
            "调仓日期":   date,
            "标的代码":   code,
            "成交时机":   timing_label,
            "方向":       "买入",
            "目标仓位":   new_p,
            "前置仓位":   round(pre_pos, 6),
            "调整仓位":   round(new_p - pre_pos, 6),
            "执行价格":   round(ep, 4),
            "数量(股)":   shares,
            "成交金额":   round(ep * shares, 2),
            "手续费":     round(comm, 2),
            "印花税":     0.0,
            "摩擦成本":   round(fric, 2),
            "净现金流":   round(-cost, 2),
            "成交后现金": round(cash, 2),
        })

    return cash, holdings, actual_pos


# --------------------------------------------------------------------------- #
# 资金模式引擎
# --------------------------------------------------------------------------- #

class CapitalEngine(_BaseEngine):

    def run(self) -> SimulationResult:
        cfg         = self.cfg
        timing      = cfg.simulation.exec_timing
        min_lot     = cfg.capital.min_lot
        buy_cost_r  = self._buy_cost_rate()
        sell_cost_r = self._sell_cost_rate()

        cash: float                    = cfg.capital.initial_capital
        holdings: Dict[str, int]       = {c: 0   for c in self.securities}
        # 市值权重：每日按收盘价重新计算，反映价格漂移后的真实仓位
        market_pos: Dict[str, float]   = {c: 0.0 for c in self.securities}
        # 上一交易日的调仓表目标（用于判断调仓表是否发生变化）
        prev_target_pos: Dict[str, float] = {c: 0.0 for c in self.securities}
        prev_closes: Dict[str, float]  = {c: np.nan for c in self.securities}

        # 成本基准：当前持仓的累计买入成本（按比例缩减，全仓清空时归零）
        cost_basis: Dict[str, float]   = {c: 0.0 for c in self.securities}
        # 前一日各标的收盘市值（用于计算当日损益）
        yesterday_mv: Dict[str, float] = {c: 0.0 for c in self.securities}

        # next_open 挂单 {code: (old_pos, new_pos)}
        pending: Dict[str, Tuple[float, float]] = {}

        daily_records: List[dict] = []
        trade_records: List[dict] = []

        for date in self.trade_dates:
            target_pos   = {c: self._pos(date, c)            for c in self.securities}
            open_prices  = {c: self._price(date, c, "OPEN")  for c in self.securities}
            close_prices = {c: self._price(date, c, "CLOSE") for c in self.securities}

            # 当日各标的资金流水（买入成本 / 卖出回款），用于计算当日损益
            day_buy_costs: Dict[str, float]     = {c: 0.0 for c in self.securities}
            day_sell_proceeds: Dict[str, float] = {c: 0.0 for c in self.securities}

            # ----------------------------------------------------------
            # 1. 执行 next_open 挂单（今日开盘价，多资产先卖后买）
            # ----------------------------------------------------------
            if pending:
                exec_p_pending = {
                    code: open_prices[code]
                    for code in pending
                    if not np.isnan(open_prices[code])
                }
                missing = [c for c in pending if np.isnan(open_prices.get(c, np.nan))]
                if missing:
                    warnings.warn(f"{date} next_open 挂单：{missing} 开盘价缺失，跳过")

                if exec_p_pending:
                    _old_snap = {c: holdings[c] for c in exec_p_pending}
                    cash, holdings, _ = _execute_rebalance_capital(
                        date=date,
                        changing_codes=list(exec_p_pending.keys()),
                        target_pos={c: pending[c][1] for c in exec_p_pending},
                        actual_pos=market_pos,
                        holdings=holdings,
                        cash=cash,
                        exec_prices=exec_p_pending,
                        close_prices=close_prices,
                        min_lot=min_lot,
                        buy_cost_r=buy_cost_r,
                        sell_cost_r=sell_cost_r,
                        commission_rate=cfg.costs.commission_rate,
                        stamp_duty=cfg.costs.stamp_duty,
                        friction_cost=cfg.costs.friction_cost,
                        timing_label="次日开盘",
                        trade_records=trade_records,
                    )
                    _update_cost_flows(
                        exec_p_pending, _old_snap, holdings,
                        buy_cost_r, sell_cost_r,
                        cost_basis, day_buy_costs, day_sell_proceeds,
                    )
                pending.clear()

            # ----------------------------------------------------------
            # 2. 检测当日仓位变化（漂移感知）
            #    仅当调仓表发生变化时触发，比较目标仓位 vs 昨日收盘市值权重
            # ----------------------------------------------------------
            pos_changed_today = any(
                abs(target_pos[c] - prev_target_pos[c]) > 1e-9
                for c in self.securities
            )
            if pos_changed_today:
                changing = [c for c in self.securities
                            if abs(target_pos[c] - market_pos[c]) > 1e-9]
            else:
                changing = []

            if changing:
                if timing == "next_open":
                    for code in changing:
                        pending[code] = (market_pos[code], target_pos[code])
                else:
                    label = _timing_cn(timing)
                    exec_prices_map: Dict[str, float] = {}
                    skipped = []
                    for code in changing:
                        ep = self._get_exec_price(date, code, prev_closes[code])
                        if np.isnan(ep) or ep <= 0:
                            skipped.append(code)
                        else:
                            exec_prices_map[code] = ep

                    if skipped:
                        warnings.warn(
                            f"{date} [{label}] {skipped} 执行价格缺失，跳过"
                        )

                    if exec_prices_map:
                        _old_snap = {c: holdings[c] for c in exec_prices_map}
                        cash, holdings, _ = _execute_rebalance_capital(
                            date=date,
                            changing_codes=list(exec_prices_map.keys()),
                            target_pos=target_pos,
                            actual_pos=market_pos,
                            holdings=holdings,
                            cash=cash,
                            exec_prices=exec_prices_map,
                            close_prices=close_prices,
                            min_lot=min_lot,
                            buy_cost_r=buy_cost_r,
                            sell_cost_r=sell_cost_r,
                            commission_rate=cfg.costs.commission_rate,
                            stamp_duty=cfg.costs.stamp_duty,
                            friction_cost=cfg.costs.friction_cost,
                            timing_label=label,
                            trade_records=trade_records,
                        )
                        _update_cost_flows(
                            exec_prices_map, _old_snap, holdings,
                            buy_cost_r, sell_cost_r,
                            cost_basis, day_buy_costs, day_sell_proceeds,
                        )

            # ----------------------------------------------------------
            # 3. 当日快照
            # ----------------------------------------------------------
            # 先汇总持仓市值（需 total_assets 才能算实际市值权重）
            code_mvs: Dict[str, float] = {}
            holding_mv = 0.0
            for code in self.securities:
                cp = close_prices[code]
                mv = holdings[code] * cp if not np.isnan(cp) else 0.0
                code_mvs[code] = mv
                holding_mv += mv

            total_assets = cash + holding_mv

            rec: dict = {"日期": date}
            for code in self.securities:
                mv  = code_mvs[code]
                cp  = close_prices[code]
                op  = open_prices[code]
                pc  = prev_closes[code]

                # 当日损益 = (今日市值 - 昨日市值) + 今日卖出回款 - 今日买入成本
                daily_pnl = (
                    (mv - yesterday_mv[code])
                    + day_sell_proceeds[code]
                    - day_buy_costs[code]
                )
                cum_pnl = mv - cost_basis[code]

                # 实际仓位 = 今日收盘市值权重（反映价格漂移后的真实仓位）
                actual_w = mv / total_assets if total_assets > 0 else 0.0

                rec[f"{code}_目标仓位"] = target_pos[code]
                rec[f"{code}_实际仓位"] = round(actual_w, 6)
                rec[f"{code}_持仓股数"] = holdings[code]
                rec[f"{code}_昨收价"]   = round(pc, 4) if not np.isnan(pc) else np.nan
                rec[f"{code}_开盘价"]   = round(op, 4) if not np.isnan(op) else np.nan
                rec[f"{code}_收盘价"]   = round(cp, 4) if not np.isnan(cp) else np.nan
                rec[f"{code}_持仓市值"] = round(mv, 2)
                rec[f"{code}_买入成本"] = round(cost_basis[code], 2)
                rec[f"{code}_当日损益"] = round(daily_pnl, 2)
                rec[f"{code}_累计损益"] = round(cum_pnl, 2)

                yesterday_mv[code] = mv       # 更新为今日市值，供次日使用
                market_pos[code]   = actual_w  # 更新市值权重，供次日调仓检测使用

            rec["总资产"]   = round(total_assets, 2)
            rec["现金"]     = round(cash, 2)
            rec["持仓市值"] = round(holding_mv, 2)
            rec["净值"]     = round(total_assets / cfg.capital.initial_capital, 6)
            daily_records.append(rec)

            prev_target_pos = dict(target_pos)  # 记录本日调仓表目标，供次日对比

            for code in self.securities:
                cp = close_prices[code]
                if not np.isnan(cp):
                    prev_closes[code] = cp

        if pending:
            warnings.warn(f"回测末日存在未执行 next_open 挂单：{list(pending)}")

        daily_df = pd.DataFrame(daily_records)
        daily_df["当日损益"] = (
            daily_df["总资产"].diff()
            .fillna(daily_df["总资产"].iloc[0] - cfg.capital.initial_capital)
            .round(2)
        )
        daily_df["累计损益"] = (daily_df["总资产"] - cfg.capital.initial_capital).round(2)

        trade_df   = pd.DataFrame(trade_records) if trade_records else pd.DataFrame()
        nav_series = daily_df.set_index("日期")["净值"].rename("净值")

        return SimulationResult(mode="capital", daily_df=daily_df, trade_df=trade_df,
                                nav_series=nav_series, config=cfg)


# --------------------------------------------------------------------------- #
# 净值模式引擎（多资产加法收益）
# --------------------------------------------------------------------------- #

class NAVEngine(_BaseEngine):
    """
    净值模式：加法组合收益，精确适用于多资产。

    portfolio_daily_return = Σ weight_i × ret_i

    组合中未分配的权重（1 - Σpos_i）视为现金，收益为 0。
    """

    def run(self) -> SimulationResult:
        cfg         = self.cfg
        timing      = cfg.simulation.exec_timing
        buy_cost_r  = self._buy_cost_rate()
        sell_cost_r = self._sell_cost_rate()

        nav: float = 1.0
        prev_closes: Dict[str, float]  = {c: np.nan for c in self.securities}
        actual_pos: Dict[str, float]   = {c: 0.0    for c in self.securities}
        # 上一交易日的调仓表目标（用于判断调仓表是否发生变化）
        prev_target_pos: Dict[str, float] = {c: 0.0 for c in self.securities}

        # 净值模式成本基准（各标的在进场时承担的净值份额 = position × nav_at_entry）
        cost_basis_nav: Dict[str, float] = {c: 0.0 for c in self.securities}

        # next_open 挂单 {code: (old_pos, new_pos)}
        pending: Dict[str, Tuple[float, float]] = {}

        daily_records: List[dict] = []
        trade_records: List[dict] = []

        for date in self.trade_dates:
            target_pos   = {c: self._pos(date, c)            for c in self.securities}
            open_p_map   = {c: self._price(date, c, "OPEN")  for c in self.securities}
            close_p_map  = {c: self._price(date, c, "CLOSE") for c in self.securities}

            portfolio_daily_return = 0.0   # 加法组合收益
            nav_before = nav               # 当日开始前净值，用于成本计算

            # 当日各标的净值贡献（用于逐标的损益记录）
            day_asset_ret: Dict[str, float] = {c: 0.0 for c in self.securities}

            # 调仓表是否发生变化（漂移感知：只在调仓表变化时触发交易）
            pos_changed_today = any(
                abs(target_pos[c] - prev_target_pos[c]) > 1e-9
                for c in self.securities
            )
            # 当日已调仓的标的（用于漂移更新时跳过）
            traded_today: set = set()

            # ----------------------------------------------------------
            # 1. 执行 next_open 挂单
            # ----------------------------------------------------------
            for code, (old_p, new_p) in list(pending.items()):
                ep      = open_p_map[code]
                pc      = prev_closes[code]
                close_p = close_p_map[code]

                if np.isnan(ep):
                    warnings.warn(f"{date} {code} next_open 挂单无法执行（开盘价缺失）")
                    continue

                if new_p > old_p:   # 买入
                    if not np.isnan(close_p) and ep > 0:
                        ret = (close_p / ep) * (1 - buy_cost_r) - 1
                        portfolio_daily_return += new_p * ret
                        day_asset_ret[code]    += new_p * ret
                    # 更新成本基准（按净值权重记录）
                    cost_basis_nav[code] += (new_p - old_p) * nav_before
                    actual_pos[code] = new_p
                    traded_today.add(code)
                    trade_records.append(_nav_rec(date, code, "买入", old_p, new_p, ep,
                                                  nav * (1 + portfolio_daily_return), "次日开盘"))
                else:               # 卖出（含隔夜缺口：pc→open）
                    if not np.isnan(pc) and pc > 0 and ep > 0:
                        ret = (ep / pc) * (1 - sell_cost_r) - 1
                        portfolio_daily_return += old_p * ret
                        day_asset_ret[code]    += old_p * ret
                    # 清空或缩减成本基准
                    if new_p <= 1e-9:
                        cost_basis_nav[code] = 0.0
                    else:
                        cost_basis_nav[code] *= new_p / old_p
                    actual_pos[code] = new_p
                    traded_today.add(code)
                    trade_records.append(_nav_rec(date, code, "卖出", old_p, new_p, ep,
                                                  nav * (1 + portfolio_daily_return), "次日开盘"))
            pending.clear()

            # ----------------------------------------------------------
            # 2. 处理各标的
            # ----------------------------------------------------------
            for code in self.securities:
                old_p   = actual_pos[code]
                new_p   = target_pos[code]
                pc      = prev_closes[code]
                open_p  = open_p_map[code]
                close_p = close_p_map[code]

                if np.isnan(close_p):
                    continue

                # 漂移感知：仅当调仓表发生变化时才考虑是否需要交易
                changed = pos_changed_today and abs(new_p - old_p) > 1e-9

                if not changed:
                    # 持仓不变（含空仓）
                    if old_p > 0 and not np.isnan(pc) and pc > 0:
                        ret = close_p / pc - 1
                        portfolio_daily_return += old_p * ret
                        day_asset_ret[code]    += old_p * ret
                    continue

                # ---- 仓位变化 ----
                if timing == "next_open":
                    # 今日继续持原仓
                    if old_p > 0 and not np.isnan(pc) and pc > 0:
                        ret = close_p / pc - 1
                        portfolio_daily_return += old_p * ret
                        day_asset_ret[code]    += old_p * ret
                    pending[code] = (old_p, new_p)
                    continue

                # ---- 即时成交 ----
                ep = self._get_exec_price(date, code, pc)
                if np.isnan(ep) or ep <= 0:
                    if old_p > 0 and not np.isnan(pc) and pc > 0:
                        ret = close_p / pc - 1
                        portfolio_daily_return += old_p * ret
                        day_asset_ret[code]    += old_p * ret
                    warnings.warn(f"{date} {code} [{_timing_cn(timing)}] 执行价格缺失，跳过交易")
                    continue

                label = _timing_cn(timing)

                if new_p > old_p:  # 买入
                    ret = (close_p / ep) * (1 - buy_cost_r) - 1
                    portfolio_daily_return += new_p * ret
                    day_asset_ret[code]    += new_p * ret
                    # 更新成本基准
                    cost_basis_nav[code] += (new_p - old_p) * nav_before
                    actual_pos[code] = new_p
                    traded_today.add(code)
                    trade_records.append(_nav_rec(date, code, "买入", old_p, new_p, ep,
                                                  nav * (1 + portfolio_daily_return), label))
                else:              # 卖出
                    if not np.isnan(pc) and pc > 0:
                        ret = (ep / pc) * (1 - sell_cost_r) - 1
                        portfolio_daily_return += old_p * ret
                        day_asset_ret[code]    += old_p * ret
                    # 清空或缩减成本基准
                    if new_p <= 1e-9:
                        cost_basis_nav[code] = 0.0
                    else:
                        cost_basis_nav[code] *= new_p / old_p
                    actual_pos[code] = new_p
                    traded_today.add(code)
                    trade_records.append(_nav_rec(date, code, "卖出", old_p, new_p, ep,
                                                  nav * (1 + portfolio_daily_return), label))

            nav *= (1 + portfolio_daily_return)

            # ----------------------------------------------------------
            # 漂移更新：非调仓标的权重随价格自然变动（漂移感知核心）
            #   actual_pos[code] *= (1 + r_code) / (1 + r_portfolio)
            # ----------------------------------------------------------
            for code in self.securities:
                if code in traded_today:
                    continue   # 调仓标的当日不做漂移更新
                cp = close_p_map[code]
                pc = prev_closes[code]
                if (actual_pos[code] > 0
                        and not np.isnan(cp)
                        and not np.isnan(pc)
                        and pc > 0):
                    r_code = cp / pc - 1
                    denom  = 1.0 + portfolio_daily_return
                    if abs(denom) > 1e-10:
                        actual_pos[code] = actual_pos[code] * (1 + r_code) / denom

            # ----------------------------------------------------------
            # 3. 当日快照
            # ----------------------------------------------------------
            rec: dict = {"日期": date, "净值": round(nav, 6)}
            rec["当日涨跌"] = round(portfolio_daily_return, 6)

            for code in self.securities:
                pos     = actual_pos[code]
                cp      = close_p_map[code]
                op      = open_p_map[code]
                pc      = prev_closes[code]

                # 当日该标的净值贡献 × 日初净值 = 绝对损益（净值单位）
                daily_pnl_nav = day_asset_ret[code] * nav_before
                # 持仓期间累计净值贡献 - 累计买入成本（均为净值单位）
                cum_pnl_nav   = pos * nav - cost_basis_nav[code]

                rec[f"{code}_目标仓位"] = target_pos[code]
                rec[f"{code}_实际仓位"] = actual_pos[code]
                rec[f"{code}_昨收价"]   = round(pc, 4) if not np.isnan(pc) else np.nan
                rec[f"{code}_开盘价"]   = round(op, 4) if not np.isnan(op) else np.nan
                rec[f"{code}_收盘价"]   = round(cp, 4) if not np.isnan(cp) else np.nan
                rec[f"{code}_买入成本"] = round(cost_basis_nav[code], 6)
                rec[f"{code}_当日损益"] = round(daily_pnl_nav, 6)
                rec[f"{code}_累计损益"] = round(cum_pnl_nav, 6)

            daily_records.append(rec)

            prev_target_pos = dict(target_pos)  # 记录本日调仓表目标，供次日对比
            for code in self.securities:
                cp = close_p_map[code]
                if not np.isnan(cp):
                    prev_closes[code] = cp

        if pending:
            warnings.warn(f"回测末日存在未执行 next_open 挂单：{list(pending)}")

        daily_df = pd.DataFrame(daily_records)

        trade_df   = pd.DataFrame(trade_records) if trade_records else pd.DataFrame()
        nav_series = daily_df.set_index("日期")["净值"].rename("净值")

        return SimulationResult(mode="nav", daily_df=daily_df, trade_df=trade_df,
                                nav_series=nav_series, config=cfg)


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #

def _timing_cn(timing: str) -> str:
    return {"prev_close": "前日收盘", "same_open": "当日开盘",
            "same_close": "当日收盘", "next_open": "次日开盘"}.get(timing, timing)


def _nav_rec(date, code, direction, pre_pos, target_pos, exec_p, nav_after, label) -> dict:
    return {
        "调仓日期":   date,
        "标的代码":   code,
        "成交时机":   label,
        "方向":       direction,
        "目标仓位":   target_pos,
        "前置仓位":   round(pre_pos, 6),
        "调整仓位":   round(target_pos - pre_pos, 6),
        "执行价格":   round(exec_p, 4) if not np.isnan(exec_p) else np.nan,
        "调仓后净值": round(nav_after, 6),
    }
