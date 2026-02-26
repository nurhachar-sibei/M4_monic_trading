"""
data_loader.py - 数据加载与对齐模块
"""

from __future__ import annotations

from typing import Tuple
import warnings

import numpy as np
import pandas as pd

from .config import Config


class DataLoader:
    """
    负责加载、验证并对齐仓位文件与价格文件。

    Usage
    -----
    pos_df, price_pivot = DataLoader.load(config)
    """

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, config: Config) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        加载并返回 (pos_df, price_pivot)。

        Returns
        -------
        pos_df : pd.DataFrame
            index = datetime，columns = 各标的代码，值为目标仓位 [0~1]
        price_pivot : pd.DataFrame
            MultiIndex columns = (field, code)，field ∈ {OPEN,CLOSE,HIGH,LOW}
            index = datetime（仅包含有效交易日）
        """
        pos_raw = cls._read_position(config.data.position_csv)
        price_raw = cls._read_price(config.data.price_csv)

        cls._validate(pos_raw, price_raw)

        trade_dates = sorted(price_raw.index.get_level_values("datetime").unique())

        # ---- 有效开始日计算 ----
        # 规则：有效开始日 = max(配置的 start_date, 调仓表首日)
        # 若 start_date 未设置或早于调仓表首日，则从调仓表首日开始。
        pos_first_date: pd.Timestamp = pos_raw.index[0]

        if config.simulation.start_date:
            sd = pd.to_datetime(config.simulation.start_date)
            if sd < pos_first_date:
                warnings.warn(
                    f"start_date ({sd.date()}) 早于调仓表首日 "
                    f"({pos_first_date.date()})，已自动调整为调仓表首日。",
                    UserWarning,
                    stacklevel=3,
                )
                sd = pos_first_date
        else:
            sd = pos_first_date

        trade_dates = [d for d in trade_dates if d >= sd]

        # ---- 结束日筛选 ----
        if config.simulation.end_date:
            ed = pd.to_datetime(config.simulation.end_date)
            trade_dates = [d for d in trade_dates if d <= ed]

        if not trade_dates:
            raise ValueError(
                "指定日期范围内无有效交易日，请检查 start_date / end_date 配置。"
            )

        # 将仓位对齐到交易日（前向填充）
        pos_aligned = cls._align_position(pos_raw, trade_dates)

        # 构建价格宽表 {(field, code): Series}
        price_pivot = cls._build_price_pivot(price_raw)
        price_pivot = price_pivot.reindex(trade_dates)

        return pos_aligned, price_pivot

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #

    @staticmethod
    def _read_position(path: str) -> pd.DataFrame:
        """读取仓位 CSV，返回以 datetime 为 index 的 DataFrame"""
        df = pd.read_csv(path)
        if "datetime" not in df.columns:
            raise ValueError(f"仓位文件缺少 'datetime' 列：{path}")
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        # 填充 NaN 仓位为 0
        df = df.fillna(0.0)
        return df

    @staticmethod
    def _read_price(path: str) -> pd.DataFrame:
        """
        读取价格 CSV，返回以 (datetime, wind_code) 为 MultiIndex 的 DataFrame，
        列包含 OPEN / CLOSE / HIGH / LOW。
        """
        df = pd.read_csv(path)
        required = {"datetime", "wind_code", "OPEN", "CLOSE"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"价格文件缺少列：{missing}")
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index(["datetime", "wind_code"]).sort_index()
        return df

    # ------------------------------------------------------------------ #
    # 验证
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate(pos_df: pd.DataFrame, price_df: pd.DataFrame) -> None:
        pos_codes = set(pos_df.columns)
        price_codes = set(price_df.index.get_level_values("wind_code").unique())
        missing = pos_codes - price_codes
        if missing:
            warnings.warn(
                f"仓位文件中的以下标的在价格文件中不存在，将被忽略：{missing}",
                UserWarning,
                stacklevel=3,
            )

        pos_dates = set(pos_df.index)
        price_dates = set(price_df.index.get_level_values("datetime").unique())
        common = pos_dates & price_dates
        if not common:
            raise ValueError("仓位文件与价格文件无共同日期，请检查数据。")

    # ------------------------------------------------------------------ #
    # 对齐
    # ------------------------------------------------------------------ #

    @staticmethod
    def _align_position(pos_df: pd.DataFrame, trade_dates: list) -> pd.DataFrame:
        """
        将仓位 DataFrame 对齐到 trade_dates：
        - 用前向填充补齐缺失日期
        - 缺失交易日前的历史仓位默认为 0
        """
        # 扩展到覆盖所有交易日
        all_dates = pd.DatetimeIndex(sorted(set(pos_df.index) | set(trade_dates)))
        pos_expanded = pos_df.reindex(all_dates).ffill().fillna(0.0)
        return pos_expanded.reindex(trade_dates)

    @staticmethod
    def _build_price_pivot(price_df: pd.DataFrame) -> pd.DataFrame:
        """
        将 (datetime, wind_code) MultiIndex 的长表转为宽表：
        columns = MultiIndex(field, code)
        index = datetime
        """
        fields = [c for c in ["OPEN", "CLOSE", "HIGH", "LOW"] if c in price_df.columns]
        return price_df[fields].unstack(level="wind_code")

    # ------------------------------------------------------------------ #
    # 信息打印
    # ------------------------------------------------------------------ #

    @staticmethod
    def print_info(pos_df: pd.DataFrame, price_pivot: pd.DataFrame) -> None:
        codes = list(pos_df.columns)
        print("\n[数据摘要]")
        print(f"  标的列表      : {codes}")
        print(f"  交易日数量    : {len(price_pivot):,}")
        print(f"  日期范围      : {price_pivot.index[0].date()} ~ {price_pivot.index[-1].date()}")
        for code in codes:
            pos_col = pos_df[code]
            changes = (pos_col.diff().fillna(pos_col.iloc[0]) != 0).sum()
            in_market = (pos_col > 0).sum()
            print(
                f"  {code:<15}: 仓位变化 {changes} 次 | "
                f"持仓天数 {in_market} / {len(pos_col)}"
            )
