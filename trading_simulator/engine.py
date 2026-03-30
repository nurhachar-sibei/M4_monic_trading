"""
engine.py - 核心模拟引擎（多资产版）

支持 2~N 个资产，仓位可为 0~1 的任意比例（总和 ≤ 1）。

多资产调仓规则
--------------
同一调仓日若存在多只标的变化，严格按"先卖后买"顺序执行：
  1. 计算调仓定额基准：sizing_nav（上一日收盘净值 / 资金）
  2. 按各标的目标仓位算出目标市值: target_mv[i] = target_pos[i] × sizing_nav
  3. 先卖出所有需减仓的标的（释放现金）
  4. 再买入所有需增仓的标的（按目标市值计算）
  5. 若现金不足以满足全部买入，按比例缩减购买量

成交时机（exec_timing）
-----------------------
  prev_close : 前日收盘价
  same_open  : 当日开盘价
  same_close : 当日收盘价
  next_open  : 次日开盘价（信号日保持旧仓，挂单次日开盘执行）

NAV 模式（份额制）
------------------
以小数份额（fractional shares）持仓，自然反映价格漂移对实际仓位的影响：

  调仓时：target_shares[i] = target_pos[i] × sizing_nav / exec_price[i]
  日末净值：nav = Σ(shares[i] × close[i]) + cash
  实际仓位：actual_pos[i] = shares[i] × close[i] / nav（真实日末市值权重）
  当日涨跌：nav / nav_prev - 1

资金模式每日标的数据字段
------------------------
  {code}_目标仓位   目标仓位比例
  {code}_实际仓位   日末收盘市值权重
  {code}_持仓股数   持仓股数（整数）
  {code}_昨收价     前一交易日收盘价
  {code}_开盘价     当日开盘价
  {code}_收盘价     当日收盘价
  {code}_持仓市值   当日收盘市值（元）
  {code}_买入成本   当前持仓的累计买入成本（全仓清空时归零）
  {code}_当日损益   当日该标的损益（元）
  {code}_累计损益   持仓期间累计损益（元），= 今日市值 - 买入成本

净值模式每日标的数据字段
------------------------
  {code}_目标仓位   目标仓位比例
  {code}_实际仓位   日末收盘真实市值权重（区别于目标仓位，反映价格漂移）
  {code}_持仓份额   持仓小数份额（如 0.00396843 份）
  {code}_平均买价   加权平均执行买入价（元）
  {code}_昨收价     前一交易日收盘价
  {code}_开盘价     当日开盘价
  {code}_收盘价     当日收盘价
  {code}_买入成本   当前持仓累计买入成本（组合单位，初始净值=1.0）
  {code}_当日损益   当日该标的净值贡献（组合单位）
  {code}_累计损益   持仓期间累计净值贡献（组合单位）
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
    # 基准（bench）相关数据
    bench_nav_series: Optional[pd.Series] = None  # 基准净值序列
    bench_metrics: dict = field(default_factory=dict)  # 基准绩效指标
    excess_metrics: dict = field(default_factory=dict)  # 超额收益指标

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
                 config: Config,
                 initial_prev_closes: Optional[Dict[str, float]] = None) -> None:
        self.pos_df = pos_df
        self.price_pivot = price_pivot
        self.cfg = config
        self.securities: List[str] = list(pos_df.columns)
        self.trade_dates: List[pd.Timestamp] = list(pos_df.index)
        # 首日前收盘价（从价格表中预查，保证 prev_close 模式首次开仓价格正确）
        self.initial_prev_closes: Dict[str, float] = initial_prev_closes or {}

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
        # 前收盘价：优先使用价格表中首日前的真实价格，避免首次开仓时误用开盘价
        prev_closes: Dict[str, float]  = {
            c: self.initial_prev_closes.get(c, np.nan) for c in self.securities
        }

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
# NAV 模式调仓执行器（份额制）
# --------------------------------------------------------------------------- #

def _execute_rebalance_nav(
    *,
    date: pd.Timestamp,
    sizing_nav: float,                   # 定额基准（上一日收盘净值）
    target_pos: Dict[str, float],        # 各标的目标仓位
    shares: Dict[str, float],            # 当前持仓份额（就地修改）
    cash: float,
    exec_prices: Dict[str, float],       # 各标的执行价（仅含有效价格的标的）
    buy_cost_r: float,
    sell_cost_r: float,
    commission_rate: float,
    stamp_duty: float,
    friction_cost: float,
    timing_label: str,
    trade_records: List[dict],
    day_buy_costs: Dict[str, float],
    day_sell_proceeds: Dict[str, float],
    cost_basis: Dict[str, float],
    ep_weighted: Dict[str, float],       # 加权执行价分子：Σ(shares_bought × ep)
) -> Tuple[float, Dict[str, float]]:
    """
    净值模式精确份额调仓（先卖后买）。

    以 sizing_nav × target_pos[code] 为目标市值，
    将各标的持仓份额调整至恰好持有目标份额，不受手数限制。
    """
    # ------------------------------------------------------------------ #
    # 1. 区分卖出 / 买入
    # ------------------------------------------------------------------ #
    sell_codes: List[str] = []
    buy_codes:  List[str] = []
    for code, ep in exec_prices.items():
        current_mv = shares.get(code, 0.0) * ep
        target_mv  = target_pos.get(code, 0.0) * sizing_nav
        if current_mv > target_mv + 1e-8:
            sell_codes.append(code)
        elif current_mv < target_mv - 1e-8:
            buy_codes.append(code)

    # ------------------------------------------------------------------ #
    # 2. 先执行卖出
    # ------------------------------------------------------------------ #
    for code in sell_codes:
        ep         = exec_prices[code]
        old_sh     = shares.get(code, 0.0)
        target_mv  = target_pos.get(code, 0.0) * sizing_nav
        pre_pos    = old_sh * ep / sizing_nav   # 执行前近似仓位权重

        new_sh        = 0.0 if target_mv <= 1e-10 else target_mv / ep
        shares_to_sell = old_sh - new_sh

        if shares_to_sell <= 1e-10:
            continue

        proceeds = shares_to_sell * ep * (1 - sell_cost_r)
        comm     = shares_to_sell * ep * commission_rate
        tax      = shares_to_sell * ep * stamp_duty
        fric     = shares_to_sell * ep * friction_cost

        cash += proceeds
        day_sell_proceeds[code] = day_sell_proceeds.get(code, 0.0) + proceeds

        # 按比例缩减成本基准与加权执行价分子
        ratio = (new_sh / old_sh) if old_sh > 1e-10 else 0.0
        cost_basis[code]  = cost_basis.get(code, 0.0)  * ratio
        ep_weighted[code] = ep_weighted.get(code, 0.0) * ratio
        shares[code]      = new_sh

        new_pos_approx = new_sh * ep / sizing_nav
        trade_records.append({
            "调仓日期": date,   "标的代码": code,    "成交时机": timing_label,
            "方向":     "卖出", "目标仓位": target_pos.get(code, 0.0),
            "前置仓位": round(pre_pos, 6),
            "调整仓位": round(new_pos_approx - pre_pos, 6),
            "执行价格": round(ep, 4),
            "交易份额": round(shares_to_sell, 8),
            "成交金额": round(shares_to_sell * ep, 6),
            "手续费":   round(comm, 6),
            "印花税":   round(tax, 6),
            "摩擦成本": round(fric, 6),
            "净现金流": round(proceeds, 6),
        })

    # ------------------------------------------------------------------ #
    # 3. 再执行买入
    # ------------------------------------------------------------------ #
    buy_orders: List[Tuple[str, float, float]] = []  # (code, target_shares, ideal_cost)
    total_ideal_cost = 0.0

    for code in buy_codes:
        ep            = exec_prices[code]
        old_sh        = shares.get(code, 0.0)
        target_mv     = target_pos.get(code, 0.0) * sizing_nav
        new_sh_target = target_mv / ep
        sh_to_buy     = new_sh_target - old_sh

        if sh_to_buy <= 1e-10:
            continue

        ideal_cost = sh_to_buy * ep * (1 + buy_cost_r)
        buy_orders.append((code, sh_to_buy, ideal_cost))
        total_ideal_cost += ideal_cost

    # 现金不足时等比缩减
    scale = min(1.0, cash / total_ideal_cost) if total_ideal_cost > 1e-10 else 0.0

    for code, ideal_sh, _ in buy_orders:
        ep     = exec_prices[code]
        old_sh = shares.get(code, 0.0)
        pre_pos = old_sh * ep / sizing_nav

        sh_to_buy   = ideal_sh * scale
        actual_cost = sh_to_buy * ep * (1 + buy_cost_r)

        if sh_to_buy <= 1e-10:
            continue

        # 最终现金兜底
        if actual_cost > cash + 1e-10:
            sh_to_buy   = cash / ep / (1 + buy_cost_r)
            actual_cost = sh_to_buy * ep * (1 + buy_cost_r)
        if sh_to_buy <= 1e-10:
            continue

        comm = sh_to_buy * ep * commission_rate
        fric = sh_to_buy * ep * friction_cost

        cash               -= actual_cost
        shares[code]        = old_sh + sh_to_buy
        cost_basis[code]    = cost_basis.get(code, 0.0)  + actual_cost
        ep_weighted[code]   = ep_weighted.get(code, 0.0) + sh_to_buy * ep
        day_buy_costs[code] = day_buy_costs.get(code, 0.0) + actual_cost

        new_pos_approx = shares[code] * ep / sizing_nav
        trade_records.append({
            "调仓日期": date,   "标的代码": code,    "成交时机": timing_label,
            "方向":     "买入", "目标仓位": target_pos.get(code, 0.0),
            "前置仓位": round(pre_pos, 6),
            "调整仓位": round(new_pos_approx - pre_pos, 6),
            "执行价格": round(ep, 4),
            "交易份额": round(sh_to_buy, 8),
            "成交金额": round(sh_to_buy * ep, 6),
            "手续费":   round(comm, 6),
            "印花税":   0.0,
            "摩擦成本": round(fric, 6),
            "净现金流": round(-actual_cost, 6),
        })

    return cash, shares


# --------------------------------------------------------------------------- #
# 净值模式引擎（份额制）
# --------------------------------------------------------------------------- #

class NAVEngine(_BaseEngine):
    """
    净值模式：以小数份额持仓，精确反映价格漂移对实际仓位的影响。

    调仓时按上一日收盘净值（sizing_nav）和目标仓位比例计算目标份额：
      target_shares[i] = target_pos[i] × sizing_nav / exec_price[i]

    日末净值直接由持仓市值+现金汇总，实际仓位天然等于日末市值权重。
    """

    def run(self) -> SimulationResult:
        cfg         = self.cfg
        timing      = cfg.simulation.exec_timing
        buy_cost_r  = self._buy_cost_rate()
        sell_cost_r = self._sell_cost_rate()

        # 份额制状态
        shares:   Dict[str, float] = {c: 0.0 for c in self.securities}
        cash:     float            = 1.0    # 未投资现金（归一化，初始为1.0）
        nav_prev: float            = 1.0    # 上一日收盘净值（调仓定额基准）

        prev_closes: Dict[str, float] = {
            c: self.initial_prev_closes.get(c, np.nan) for c in self.securities
        }
        prev_target_pos: Dict[str, float] = {c: 0.0 for c in self.securities}

        # 成本基准（组合单位）与加权执行价分子（用于计算平均买价）
        cost_basis:  Dict[str, float] = {c: 0.0 for c in self.securities}
        ep_weighted: Dict[str, float] = {c: 0.0 for c in self.securities}

        # next_open 挂单：{code: new_target_pos}（含所有标的）及定额基准
        pending:             Dict[str, float] = {}
        pending_sizing_nav:  float            = 1.0

        daily_records: List[dict] = []
        trade_records: List[dict] = []

        for date in self.trade_dates:
            target_pos  = {c: self._pos(date, c)            for c in self.securities}
            open_p_map  = {c: self._price(date, c, "OPEN")  for c in self.securities}
            close_p_map = {c: self._price(date, c, "CLOSE") for c in self.securities}

            # 日初份额快照（用于当日损益计算）
            shares_start = dict(shares)

            day_buy_costs:     Dict[str, float] = {c: 0.0 for c in self.securities}
            day_sell_proceeds: Dict[str, float] = {c: 0.0 for c in self.securities}

            # ----------------------------------------------------------
            # 1. 执行 next_open 挂单（今日开盘，定额基准=上一日收盘净值）
            # ----------------------------------------------------------
            if pending:
                exec_p_pending: Dict[str, float] = {}
                for code in pending:
                    op = open_p_map[code]
                    if not np.isnan(op) and op > 0:
                        exec_p_pending[code] = op
                    else:
                        warnings.warn(
                            f"{date} next_open 挂单：{code} 开盘价缺失，跳过"
                        )
                if exec_p_pending:
                    cash, shares = _execute_rebalance_nav(
                        date=date,
                        sizing_nav=pending_sizing_nav,
                        target_pos=pending,
                        shares=shares,
                        cash=cash,
                        exec_prices=exec_p_pending,
                        buy_cost_r=buy_cost_r,
                        sell_cost_r=sell_cost_r,
                        commission_rate=cfg.costs.commission_rate,
                        stamp_duty=cfg.costs.stamp_duty,
                        friction_cost=cfg.costs.friction_cost,
                        timing_label="次日开盘",
                        trade_records=trade_records,
                        day_buy_costs=day_buy_costs,
                        day_sell_proceeds=day_sell_proceeds,
                        cost_basis=cost_basis,
                        ep_weighted=ep_weighted,
                    )
                pending.clear()

            # ----------------------------------------------------------
            # 2. 检测仓位文件变化 → 即时调仓
            # ----------------------------------------------------------
            pos_changed_today = any(
                abs(target_pos[c] - prev_target_pos[c]) > 1e-9
                for c in self.securities
            )

            if pos_changed_today and timing != "next_open":
                exec_prices_map: Dict[str, float] = {}
                for code in self.securities:
                    ep = self._get_exec_price(date, code, prev_closes[code])
                    if not np.isnan(ep) and ep > 0:
                        exec_prices_map[code] = ep
                    else:
                        warnings.warn(
                            f"{date} [{_timing_cn(timing)}] {code} 执行价格缺失，跳过"
                        )
                if exec_prices_map:
                    cash, shares = _execute_rebalance_nav(
                        date=date,
                        sizing_nav=nav_prev,
                        target_pos=target_pos,
                        shares=shares,
                        cash=cash,
                        exec_prices=exec_prices_map,
                        buy_cost_r=buy_cost_r,
                        sell_cost_r=sell_cost_r,
                        commission_rate=cfg.costs.commission_rate,
                        stamp_duty=cfg.costs.stamp_duty,
                        friction_cost=cfg.costs.friction_cost,
                        timing_label=_timing_cn(timing),
                        trade_records=trade_records,
                        day_buy_costs=day_buy_costs,
                        day_sell_proceeds=day_sell_proceeds,
                        cost_basis=cost_basis,
                        ep_weighted=ep_weighted,
                    )

            # ----------------------------------------------------------
            # 3. 计算日末净值（份额 × 收盘价 + 现金）
            # ----------------------------------------------------------
            V_close: float = cash
            for code in self.securities:
                cp = close_p_map[code]
                if not np.isnan(cp) and shares[code] > 0:
                    V_close += shares[code] * cp

            daily_return = (V_close / nav_prev - 1.0) if nav_prev > 1e-10 else 0.0

            # next_open：在日末净值确定后挂单（定额基准 = 今日收盘净值）
            if pos_changed_today and timing == "next_open":
                pending            = {c: target_pos[c] for c in self.securities}
                pending_sizing_nav = V_close

            nav_prev = V_close   # 更新次日定额基准

            # ----------------------------------------------------------
            # 4. 当日快照
            # ----------------------------------------------------------
            rec: dict = {
                "日期":   date,
                "净值":   round(V_close, 6),
                "当日涨跌": round(daily_return, 6),
            }

            for code in self.securities:
                cp  = close_p_map[code]
                op  = open_p_map[code]
                pc  = prev_closes[code]
                sh  = shares[code]
                mv  = sh * cp if (sh > 0 and not np.isnan(cp)) else 0.0

                # 实际仓位：日末真实市值权重
                actual_pos_w = mv / V_close if V_close > 1e-10 else 0.0

                # 当日损益（组合单位）
                #   = (日末市值 - 日初市值) + 卖出回款 - 买入成本
                start_mv = (shares_start[code] * pc
                            if (shares_start[code] > 0 and not np.isnan(pc))
                            else 0.0)
                daily_pnl = ((mv - start_mv)
                             + day_sell_proceeds[code]
                             - day_buy_costs[code])

                # 累计损益（组合单位）= 日末市值 - 累计成本
                cum_pnl = mv - cost_basis[code]

                # 平均买价（元）= 加权执行价 ÷ 持仓份额
                avg_ep = (ep_weighted[code] / sh) if sh > 1e-10 else np.nan

                rec[f"{code}_目标仓位"] = target_pos[code]
                rec[f"{code}_实际仓位"] = round(actual_pos_w, 6)
                rec[f"{code}_持仓份额"] = round(sh, 8)
                rec[f"{code}_平均买价"] = round(avg_ep, 4) if not np.isnan(avg_ep) else np.nan
                rec[f"{code}_昨收价"]   = round(pc, 4) if not np.isnan(pc) else np.nan
                rec[f"{code}_开盘价"]   = round(op, 4) if not np.isnan(op) else np.nan
                rec[f"{code}_收盘价"]   = round(cp, 4) if not np.isnan(cp) else np.nan
                rec[f"{code}_买入成本"] = round(cost_basis[code], 8)
                rec[f"{code}_当日损益"] = round(daily_pnl, 8)
                rec[f"{code}_累计损益"] = round(cum_pnl, 8)

            rec["现金"] = round(cash, 8)
            daily_records.append(rec)

            prev_target_pos = dict(target_pos)
            for code in self.securities:
                cp = close_p_map[code]
                if not np.isnan(cp):
                    prev_closes[code] = cp

        if pending:
            warnings.warn(f"回测末日存在未执行 next_open 挂单：{list(pending)}")

        daily_df   = pd.DataFrame(daily_records)
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
