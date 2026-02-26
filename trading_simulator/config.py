"""
config.py - 配置管理模块

支持从 YAML 文件、Python dict 或关键字参数加载配置。
配置分为 6 个子块：data / simulation / capital / costs / metrics / output
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# 子配置块
# --------------------------------------------------------------------------- #

@dataclass
class DataConfig:
    """数据文件路径配置"""
    position_csv: str = "./f_bond_position.csv"
    price_csv: str = "./price_df.csv"


# 所有合法的执行时机选项
EXEC_TIMING_OPTIONS = ("prev_close", "same_open", "same_close", "next_open")

# 各选项说明（用于帮助信息）
EXEC_TIMING_DESCRIPTIONS = {
    "prev_close":  "前日收盘成交 - 信号当日以前一交易日收盘价执行，前视偏差最高",
    "same_open":   "当日开盘成交 - 信号当日开盘价执行，适合隔夜策略",
    "same_close":  "当日收盘成交 - 信号当日收盘价执行，轻微前视偏差",
    "next_open":   "次日开盘成交 - 信号次日开盘价执行，最保守无前视偏差（默认）",
}


@dataclass
class SimulationConfig:
    """模拟运行配置"""
    mode: str = "capital"           # "capital" | "nav"
    exec_timing: str = "next_open"  # 见 EXEC_TIMING_OPTIONS
    start_date: Optional[str] = None
    end_date: Optional[str] = None


@dataclass
class CapitalConfig:
    """资金模式专用配置"""
    initial_capital: float = 1_000_000.0
    min_lot: int = 100              # 每手股数（债券ETF: 100）


@dataclass
class CostConfig:
    """交易成本配置"""
    commission_rate: float = 0.0003   # 双边手续费率
    stamp_duty: float = 0.0           # 卖出印花税（债券ETF=0；A股=0.001）
    friction_cost: float = 0.0001     # 额外摩擦成本（双边）


@dataclass
class MetricsConfig:
    """评价指标配置"""
    risk_free_rate: float = 0.0       # 年化无风险利率
    periods_per_year: int = 252       # 年化交易日数


@dataclass
class OutputConfig:
    """输出配置"""
    dir: str = "./output"
    save_chart: bool = True
    show_chart: bool = False
    save_excel: bool = True
    chart_dpi: int = 150
    chart_width: int = 16
    chart_height: int = 10


# --------------------------------------------------------------------------- #
# 主配置类
# --------------------------------------------------------------------------- #

@dataclass
class Config:
    """
    全局配置类，聚合所有子配置块。

    创建方式
    --------
    1. 从 YAML 文件::
        cfg = Config.from_yaml("config/default.yaml")

    2. 从 Python dict::
        cfg = Config.from_dict({
            "simulation": {"mode": "nav"},
            "capital": {"initial_capital": 500000}
        })

    3. 直接实例化（使用默认值）::
        cfg = Config()

    4. 使用快捷方法并覆盖部分参数::
        cfg = Config.from_yaml("config/default.yaml")
        cfg.simulation.mode = "nav"
    """

    data: DataConfig = field(default_factory=DataConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    capital: CapitalConfig = field(default_factory=CapitalConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    # ------------------------------------------------------------------ #
    # 工厂方法
    # ------------------------------------------------------------------ #

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        """从 YAML 文件加载配置"""
        try:
            import yaml
        except ImportError as e:
            raise ImportError(
                "加载 YAML 配置需要安装 PyYAML：pip install pyyaml"
            ) from e

        if not os.path.exists(path):
            raise FileNotFoundError(f"配置文件不存在：{path}")

        with open(path, "r", encoding="utf-8") as f:
            raw: dict = yaml.safe_load(f) or {}

        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        """从字典加载配置，仅覆盖存在的键"""
        cfg = cls()
        mapping = {
            "data": DataConfig,
            "simulation": SimulationConfig,
            "capital": CapitalConfig,
            "costs": CostConfig,
            "metrics": MetricsConfig,
            "output": OutputConfig,
        }
        for key, sub_cls in mapping.items():
            if key in d and isinstance(d[key], dict):
                defaults = asdict(getattr(cfg, key))
                defaults.update(d[key])
                setattr(cfg, key, sub_cls(**defaults))
        return cfg

    def to_dict(self) -> dict:
        """导出为字典"""
        return asdict(self)

    def save_yaml(self, path: str) -> None:
        """将当前配置保存为 YAML 文件"""
        try:
            import yaml
        except ImportError as e:
            raise ImportError("保存 YAML 需要安装 PyYAML：pip install pyyaml") from e

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                self.to_dict(),
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )

    # ------------------------------------------------------------------ #
    # 验证
    # ------------------------------------------------------------------ #

    def validate(self) -> "Config":
        """校验配置合法性，返回自身（链式调用）"""
        errors: list[str] = []

        if self.simulation.mode not in ("capital", "nav"):
            errors.append(f"simulation.mode 必须为 'capital' 或 'nav'，当前：{self.simulation.mode}")

        if self.simulation.exec_timing not in EXEC_TIMING_OPTIONS:
            opts = " | ".join(EXEC_TIMING_OPTIONS)
            errors.append(
                f"simulation.exec_timing 必须为 {opts}，"
                f"当前：{self.simulation.exec_timing!r}"
            )

        if self.capital.initial_capital <= 0:
            errors.append(f"capital.initial_capital 必须 > 0，当前：{self.capital.initial_capital}")

        if self.capital.min_lot <= 0:
            errors.append(f"capital.min_lot 必须 > 0，当前：{self.capital.min_lot}")

        for rate_name, rate_val in [
            ("costs.commission_rate", self.costs.commission_rate),
            ("costs.stamp_duty", self.costs.stamp_duty),
            ("costs.friction_cost", self.costs.friction_cost),
        ]:
            if not (0.0 <= rate_val <= 0.1):
                errors.append(f"{rate_name} 应在 [0, 0.1] 范围内，当前：{rate_val}")

        if errors:
            raise ValueError("配置验证失败：\n" + "\n".join(f"  - {e}" for e in errors))

        return self

    def __repr__(self) -> str:
        lines = ["Config("]
        for key in ("data", "simulation", "capital", "costs", "metrics", "output"):
            obj = getattr(self, key)
            lines.append(f"  {key}={obj!r},")
        lines.append(")")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 便捷函数
# --------------------------------------------------------------------------- #

def load_config(path: Optional[str] = None, **overrides: Any) -> Config:
    """
    加载配置并可选地覆盖部分字段。

    Parameters
    ----------
    path : str, optional
        YAML 配置文件路径；为 None 时使用全部默认值。
    **overrides : Any
        扁平化键值对覆盖，支持 "section.key=value" 格式。

        例：load_config("config/default.yaml", mode="nav", initial_capital=500000)

    Returns
    -------
    Config（已通过 validate()）
    """
    cfg = Config.from_yaml(path) if path else Config()

    # 简便覆盖：支持直接写字段名（从各 section 中查找）
    section_fields = {
        "data": set(DataConfig.__dataclass_fields__),
        "simulation": set(SimulationConfig.__dataclass_fields__),
        "capital": set(CapitalConfig.__dataclass_fields__),
        "costs": set(CostConfig.__dataclass_fields__),
        "metrics": set(MetricsConfig.__dataclass_fields__),
        "output": set(OutputConfig.__dataclass_fields__),
    }
    for k, v in overrides.items():
        placed = False
        for sec, keys in section_fields.items():
            if k in keys:
                setattr(getattr(cfg, sec), k, v)
                placed = True
                break
        if not placed:
            raise KeyError(f"未知的配置键：{k!r}")

    return cfg.validate()
