import math
from datetime import date, datetime, time
from time import monotonic

from vnpy.trader.utility import ArrayManager, BarGenerator
from vnpy.trader.object import TickData, BarData
from vnpy.trader.constant import Interval
from vnpy.trader.constant import Interval

from vnpy_portfoliostrategy import StrategyTemplate, StrategyEngine
from vnpy_portfoliostrategy.utility import PortfolioBarGenerator
from vnpy_portfoliostrategy.base import EngineType

# 聚合超时（秒）：实盘各标的数据分批到达，超时后按已有数据处理
AGGREGATE_TIMEOUT: int = 180


class Return10Strategy(StrategyTemplate):
    """Return10 组合策略：T 日收盘计算信号，T+1 日开盘执行（决策价=开盘价）。

    调仓语义（回测与实盘一致）：
    - 信号周期：每 holding_period（默认 10）个交易日计算一次信号
    - 信号价格：T 日收盘价（ROCP 排序取 top max_positions）
    - 执行时机：T+1 日开盘（回测：T+1 日 bar 开盘价下单，引擎当日撮合；
      实盘：次日 9:30 第一个K线切片以开盘价下单）
    """

    author = "Robin"

    price_add_percent = 0.005
    allocation_mode = "equal_weight"
    cash_buffer_percent = 0.05
    max_single_weight = 0.30
    lot_size = 100
    return_period = 10
    holding_period = 10
    max_positions = 10
    close_wait_seconds = 3.0

    signal_ts: dict[str, int] = {}
    signal_total: dict[str, int] = {}
    last_tick_time: str = ""
    trade_day: int = 0
    targets_pos: dict[str, int] = {}
    last_daily_bar_date: str = ""

    parameters = [
        "price_add_percent",
        "allocation_mode",
        "cash_buffer_percent",
        "max_single_weight",
        "lot_size",
        "return_period",
        "holding_period",
        "max_positions",
        "close_wait_seconds",
    ]
    variables = [
        "signal_ts",
        "signal_total",
        "last_tick_time",
        "trade_day",
        "targets_pos",
        "last_daily_bar_date",
    ]

    def __init__(
        self,
        strategy_engine: StrategyEngine,
        strategy_name: str,
        vt_symbols: list[str],
        setting: dict,
    ) -> None:
        """构造函数"""
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        # 使用实例级容器，避免多个策略实例共享类变量中的可变状态。
        self.signal_ts = {}
        self.signal_total = {}
        self.last_tick_time = ""
        self.trade_day = 0
        self.targets_pos = {}
        self.last_daily_bar_date = ""

        self.ams: dict[str, ArrayManager] = {}
        self.bgs: dict[str, BarGenerator] = {}
        # 实盘：各标的日线生成器（从分钟K线合成日线）
        self.daily_bgs: dict[str, BarGenerator] = {}

        for vt_symbol in self.vt_symbols:
            # tick -> 1分钟K线（回调 _on_minute_bar 中转）
            self.bgs[vt_symbol] = BarGenerator(self._on_minute_bar)
            self.ams[vt_symbol] = ArrayManager()
            # 1分钟K线 -> 日线（15:00 合成完毕触发 on_daily_bar）
            self.daily_bgs[vt_symbol] = BarGenerator(
                self._on_minute_bar,
                on_window_bar=self.on_daily_bar,
                interval=Interval.DAILY,
                daily_end=time(15, 0),
            )

        # tick 合成K线（实盘数据入口）
        self.pbg = PortfolioBarGenerator(self.on_bars)

        # XT 为每个标的单独推送收盘 Tick，定时事件会在行情安静后
        # 将这些 Tick 汇总成一次组合日线切片。
        self.latest_ticks: dict[str, TickData] = {}
        self.closed_symbols: set[str] = set()
        self.collecting_date: str = ""
        self.last_close_tick_at: float = 0.0

        # ---- 信号与执行状态（实例级） ----
        self.pending_targets: dict[str, int] | None = (
            None  # 待执行目标仓位（上一交易日信号）
        )
        self.executed_date: date | None = None  # 最近一次开盘执行日期（去重）

        # ---- 实盘日线聚合状态 ----
        self.daily_pending: dict[str, BarData] = {}
        self.daily_agg_date: date | None = None
        self.daily_first_time: datetime | None = None

        if self.allocation_mode != "equal_weight":
            raise ValueError("allocation_mode must be 'equal_weight'")
        if not 0 <= self.cash_buffer_percent < 1:
            raise ValueError("cash_buffer_percent must be in [0, 1)")
        if not 0 < self.max_single_weight <= 1:
            raise ValueError("max_single_weight must be in (0, 1]")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")

    def on_init(self) -> None:
        """策略初始化回调"""
        self.write_log("策略初始化")

        self.load_bars(days=30, interval=Interval.DAILY)

    def on_start(self) -> None:
        """策略启动回调"""
        self.write_log("策略启动")

    def on_stop(self) -> None:
        """策略停止回调"""
        self.write_log("策略停止")

    def on_tick(self, tick: TickData) -> None:
        """行情推送回调

        - 喂日线生成器：当日 15:00 合成日线，触发 on_daily_bar 计算信号
        - 喂 PortfolioBarGenerator：生成分钟切片，次日开盘触发 on_bars 执行调仓
        """
        if tick.last_price <= 0:
            return
        self.last_tick_time = tick.datetime

        # tick -> 1分钟K线 -> 日线合成（15:00 触发 on_daily_bar）
        self.bgs[tick.vt_symbol].update_tick(tick)
        # tick -> 分钟切片（次日开盘触发 on_bars 执行调仓）
        self.pbg.update_tick(tick)

    def _on_minute_bar(self, bar: BarData) -> None:
        """1分钟K线完成回调 —— 喂给日线生成器继续合成"""
        self.daily_bgs[bar.vt_symbol].update_bar(bar)

        # ------------------------------------------------------------------
        # K线切片回调：回测（日线）与实盘（分钟切片）分流
        # ------------------------------------------------------------------
        """行情推送回调"""
        self.last_tick_time = tick.datetime.isoformat()
        self.latest_ticks[tick.vt_symbol] = tick

        extra: dict = tick.extra or {}
        if not extra.get("market_closed", False):
            return

        trading_date: str = tick.datetime.date().isoformat()
        if trading_date == self.last_daily_bar_date:
            return

        if trading_date != self.collecting_date:
            self.collecting_date = trading_date
            self.closed_symbols.clear()

        self.closed_symbols.add(tick.vt_symbol)
        self.last_close_tick_at = monotonic()

    def on_timer(self) -> None:
        """收齐收盘 Tick 后生成一次日线组合切片"""
        if not self.trading or not self.collecting_date:
            return

        if self.collecting_date == self.last_daily_bar_date:
            return

        all_closed: bool = len(self.closed_symbols) >= len(self.vt_symbols)
        wait_elapsed: bool = (
            monotonic() - self.last_close_tick_at >= self.close_wait_seconds
        )
        if not all_closed and not wait_elapsed:
            return

        bars: dict[str, BarData] = {}
        for vt_symbol in self.vt_symbols:
            tick: TickData | None = self.latest_ticks.get(vt_symbol)
            if not tick or tick.datetime.date().isoformat() != self.collecting_date:
                continue

            bar: BarData | None = self._create_daily_bar(tick)
            if bar:
                bars[vt_symbol] = bar

        if not bars:
            self.write_log(f"{self.collecting_date} 没有有效收盘行情，跳过日线计算")
            self._clear_close_collection()
            return

        self.last_daily_bar_date = self.collecting_date
        missing_count: int = len(self.vt_symbols) - len(bars)
        self.write_log(
            f"{self.collecting_date} 收盘日线切片完成："
            f"有效{len(bars)}，缺失{missing_count}"
        )

        self.on_bars(bars)
        self.sync_data()
        self._clear_close_collection()

    @staticmethod
    def _create_daily_bar(tick: TickData) -> BarData | None:
        """使用 XT Tick 中携带的当日 OHLC 构造日线"""
        close_price: float = tick.last_price
        if (
            not isinstance(close_price, (int, float))
            or math.isnan(close_price)
            or close_price <= 0
        ):
            return None

        open_price: float = tick.open_price if tick.open_price > 0 else close_price
        high_price: float = tick.high_price if tick.high_price > 0 else close_price
        low_price: float = tick.low_price if tick.low_price > 0 else close_price

        return BarData(
            symbol=tick.symbol,
            exchange=tick.exchange,
            datetime=tick.datetime.replace(hour=0, minute=0, second=0, microsecond=0),
            interval=Interval.DAILY,
            volume=tick.volume,
            turnover=tick.turnover,
            open_interest=tick.open_interest,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            gateway_name=tick.gateway_name,
        )

    def _clear_close_collection(self) -> None:
        """清理已完成的收盘行情缓存"""
        self.closed_symbols.clear()
        self.collecting_date = ""
        self.last_close_tick_at = 0.0

    def on_bars(self, bars: dict[str, BarData]) -> None:
        """K线切片回调"""
        if self.get_engine_type() == EngineType.BACKTESTING:
            self._on_backtest_bars(bars)
        else:
            self._on_live_bars(bars)

    def _on_backtest_bars(self, bars: dict[str, BarData]) -> None:
        """回测：每个交易日一次。先执行上一交易日信号（今日开盘价），再计算今日信号。

        时序（引擎已改为 on_bars -> cross）：
        - T 日 on_bars：用 T 日开盘价执行 T-1 日信号，然后计算 T 日信号
        - T 日 cross：撮合 T 日委托，成交价 = min(委托价, T 日开盘价) = 开盘价
        """
        latest = max(bar.datetime for bar in bars.values())
        d = latest.date()

        # 1) 开盘执行：上一交易日信号（决策价=今日开盘价），执行后清空
        if self.pending_targets is not None and self.executed_date != d:
            self._rebalance_by_targets(self.pending_targets, bars)
            self.executed_date = d
            self.pending_targets = None

        # 2) 收盘计算信号：调仓日（每 holding_period 个交易日）
        if self.trade_day % self.holding_period == 0:
            self.pending_targets = self._calc_targets(bars)

        self.trade_day += 1

    def _on_live_bars(self, bars: dict[str, BarData]) -> None:
        """实盘：分钟切片回调（开盘执行触发点）。

        日线合成已由 on_tick 直喂 daily_bgs 完成；此处仅用于
        次日开盘时用切片开盘价执行 pending_targets。
        """
        latest = max(bar.datetime for bar in bars.values())
        d = latest.date()

        # 丢弃前日 15:00 残留切片（pbg 在下一分钟第一个 tick 才推送，
        # 15:00 切片要等次日才触发；此时日线合成已完成，无需再处理）
        if self.last_tick_time is None or latest.date() != self.last_tick_time.date():
            return

        # 1) 开盘执行（决策价=开盘价，取切片 open_price），执行后清空
        if self.pending_targets is not None and self.executed_date != d:
            self._rebalance_by_targets(self.pending_targets, bars)
            self.executed_date = d
            self.pending_targets = None

    def on_daily_bar(self, bar: BarData) -> None:
        """实盘：单标的日线合成完毕（15:00）—— 聚合等齐后计算信号"""
        d = bar.datetime.date()

        # 跨日重置聚合缓冲
        if self.daily_agg_date != d:
            self.daily_pending = {}
            self.daily_agg_date = d
            self.daily_first_time = bar.datetime

        self.daily_pending[bar.vt_symbol] = bar

        all_received = all(vt in self.daily_pending for vt in self.vt_symbols)
        elapsed = (
            (bar.datetime - self.daily_first_time).total_seconds()
            if self.daily_first_time
            else 0
        )

        if all_received or elapsed > AGGREGATE_TIMEOUT:
            # 收盘计算信号：调仓日（与回测 trade_day 语义一致）
            if self.trade_day % self.holding_period == 0:
                self.pending_targets = self._calc_targets(self.daily_pending)
            self.trade_day += 1
            self.daily_pending = {}

    # ------------------------------------------------------------------
    # 信号计算与调仓执行
    # ------------------------------------------------------------------
    def _calc_targets(self, bars: dict[str, BarData]) -> dict[str, int]:
        """计算目标仓位并按余数分配整手，严格不超过目标资金。"""
        # 1) 更新指标，计算时序信号
        self.cancel_all()
        # 更新K线并计算收益率信号
        for vt_symbol, bar in bars.items():
            am: ArrayManager = self.ams[vt_symbol]
            am.update_bar(bar)

            return10 = am.rocp(self.return_period)
            if (
                isinstance(return10, (int, float))
                and not math.isnan(return10)
                and return10 > 0
            ):
                self.signal_ts[vt_symbol] = 1
            else:
                self.signal_ts[vt_symbol] = 0

        # 2) 横截面排序：按 ROCP 降序，取 top max_positions
        # 信号汇总：按 ROCP 排序，只保留 top max_positions 做多，其余平仓
        candidates: list[tuple[str, float]] = []
        for vt_symbol, bar in bars.items():
            self.signal_total[vt_symbol] = self.signal_ts[vt_symbol]
            if self.signal_ts[vt_symbol] > 0:
                am = self.ams[vt_symbol]
                raw_val = am.rocp(self.return_period)
                if isinstance(raw_val, (int, float)) and not math.isnan(raw_val):
                    candidates.append((vt_symbol, raw_val))
        candidates.sort(key=lambda x: x[1], reverse=True)

        selected: set[str] = {s for s, _ in candidates[: self.max_positions]}
        for vt_symbol in self.vt_symbols:
            self.signal_total[vt_symbol] = 1 if vt_symbol in selected else 0

        # 3) 按调仓日最新净资产等权分配，并限制单票集中度
        portfolio_value = self._portfolio_value()
        target_weight = 0.0
        if selected:
            target_weight = min(
                (1 - self.cash_buffer_percent) / len(selected),
                self.max_single_weight,
            )
        target_value = portfolio_value * target_weight
        targets: dict[str, int] = {vt_symbol: 0 for vt_symbol in bars}
        allocations: list[tuple[float, str, float]] = []
        base_value = 0.0
        investment_budget = 0.0

        # First take the floor lot for every selected symbol.  The budget is
        # the sum of selected target values, so a concentration cap cannot be
        # consumed by the remainder pass.
        for vt_symbol in selected:
            bar = bars.get(vt_symbol)
            if not bar or not (
                isinstance(bar.close_price, (int, float))
                and math.isfinite(bar.close_price)
                and bar.close_price > 0
            ):
                continue

            price = float(bar.close_price)
            ideal_lots = target_value / price / self.lot_size
            floor_lots = math.floor(ideal_lots)
            remainder = ideal_lots - floor_lots
            targets[vt_symbol] = floor_lots * self.lot_size
            base_value += targets[vt_symbol] * price
            investment_budget += target_value
            allocations.append((remainder, vt_symbol, price))

        # Keep the hard cash invariant even if the floor allocation ever
        # exceeds the calculated budget because of unusual caller input.
        trim_order = sorted(allocations, key=lambda item: (item[0], item[1]))
        while base_value > investment_budget + 1e-9:
            trimmed = False
            for _, vt_symbol, price in trim_order:
                if targets[vt_symbol]:
                    targets[vt_symbol] -= self.lot_size
                    base_value -= self.lot_size * price
                    trimmed = True
                if base_value <= investment_budget + 1e-9:
                    break
            if not trimmed:
                break

        remaining_value = max(investment_budget - base_value, 0.0)
        for _, vt_symbol, price in sorted(
            (item for item in allocations if item[0] > 1e-12),
            key=lambda item: (-item[0], item[1]),
        ):
            lot_value = self.lot_size * price
            if lot_value > remaining_value:
                continue
            targets[vt_symbol] += self.lot_size
            remaining_value -= lot_value

        self.targets_pos = targets
        return targets

    def _portfolio_value(self) -> float:
        """读取策略计算时点的最新组合净资产。"""
        getter = getattr(self.strategy_engine, "get_portfolio_value", None)
        if getter:
            try:
                value = getter(self)
            except TypeError:
                value = getter()
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                return float(value)

        main_engine = getattr(self.strategy_engine, "main_engine", None)
        if main_engine:
            balances = [
                float(account.balance)
                for account in main_engine.get_all_accounts()
                if math.isfinite(float(account.balance)) and float(account.balance) > 0
            ]
            if balances:
                return balances[0]

        capital = getattr(self.strategy_engine, "capital", None)
        if isinstance(capital, (int, float)) and math.isfinite(capital) and capital > 0:
            return float(capital)
        # Direct unit-test/standalone engines may expose no account object.
        return float(getattr(self, "initial_capital", 1_000_000))

    def _rebalance_by_targets(
        self,
        targets: dict[str, int],
        bars: dict[str, BarData],
    ) -> None:
        """按目标仓位调仓：决策价=开盘价（open × (1 ± price_add_percent)）"""
        self.cancel_all()

        for vt_symbol in self.vt_symbols:
            bar = bars.get(vt_symbol)
            if not bar:
                continue
            # 容错：跳过开盘价无效的标的
            if (
                not isinstance(bar.open_price, (int, float))
                or math.isnan(bar.open_price)
                or bar.open_price <= 0
            ):
                continue

            target_pos = targets.get(vt_symbol, 0)
            current_pos = self.get_pos(vt_symbol)
            pos_diff = target_pos - current_pos

            if pos_diff > 0:
                price = bar.open_price * (1 + self.price_add_percent)
                self.buy(vt_symbol, price, pos_diff)

            if pos_diff < 0:
                price = bar.open_price * (1 - self.price_add_percent)
                self.sell(vt_symbol, price, -pos_diff)

        self.put_event()
