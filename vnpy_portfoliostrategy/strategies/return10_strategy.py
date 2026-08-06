import math
from time import monotonic

from vnpy.trader.utility import ArrayManager
from vnpy.trader.object import TickData, BarData
from vnpy.trader.constant import Interval

from vnpy_portfoliostrategy import StrategyTemplate, StrategyEngine


class Return10Strategy(StrategyTemplate):
    """ """

    author = "Robin"

    price_add_percent = 0.005
    fixed_pos_value = 50000
    return_peroid = 10
    holding_peroid = 10
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
        "fixed_pos_value",
        "return_peroid",
        "holding_peroid",
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
        for vt_symbol in self.vt_symbols:
            self.ams[vt_symbol] = ArrayManager()

        # XT 为每个标的单独推送收盘 Tick，定时事件会在行情安静后
        # 将这些 Tick 汇总成一次组合日线切片。
        self.latest_ticks: dict[str, TickData] = {}
        self.closed_symbols: set[str] = set()
        self.collecting_date: str = ""
        self.last_close_tick_at: float = 0.0

    def on_init(self) -> None:
        """策略初始化回调"""
        self.write_log("策略初始化")

        self.load_bars(days=20, interval=Interval.DAILY)

    def on_start(self) -> None:
        """策略启动回调"""
        self.write_log("策略启动")

    def on_stop(self) -> None:
        """策略停止回调"""
        self.write_log("策略停止")

    def on_tick(self, tick: TickData) -> None:
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
        self.cancel_all()
        # 更新K线并计算收益率信号
        for vt_symbol, bar in bars.items():
            am: ArrayManager = self.ams[vt_symbol]
            am.update_bar(bar)

            # 信号 过去10天的收益率
            return10 = am.rocp(self.return_peroid)

            # 信号
            if isinstance(return10, (int, float)) and not math.isnan(return10) and return10 > 0:
                self.signal_ts[vt_symbol] = 1
            else:
                self.signal_ts[vt_symbol] = 0

        # 信号汇总：按 ROCP 排序，只保留 top max_positions 做多，其余平仓
        candidates: list[tuple[str, float]] = []
        for vt_symbol, bar in bars.items():
            self.signal_total[vt_symbol] = self.signal_ts[vt_symbol]
            if self.signal_ts[vt_symbol] > 0:
                am: ArrayManager = self.ams[vt_symbol]
                raw_val = am.rocp(self.return_peroid)
                if isinstance(raw_val, (int, float)) and not math.isnan(raw_val):
                    candidates.append((vt_symbol, raw_val))
        candidates.sort(key=lambda x: x[1], reverse=True)

        # 只保留 top max_positions
        selected: set[str] = {s for s, _ in candidates[: self.max_positions]}
        for vt_symbol in self.vt_symbols:
            if vt_symbol in selected:
                self.signal_total[vt_symbol] = 1
            else:
                self.signal_total[vt_symbol] = 0

        # 计算目标仓位
        for vt_symbol, bar in bars.items():
            if (isinstance(bar.close_price, (int, float))
                    and not math.isnan(bar.close_price)
                    and bar.close_price > 0):
                self.targets_pos[vt_symbol] = (
                    int(self.fixed_pos_value / bar.close_price/100)*100
                    * self.signal_total[vt_symbol]
                )
            else:
                self.targets_pos[vt_symbol] = 0
        # 交易执行
        if self.trade_day == 0 or not self.trade_day % self.holding_peroid:
            for vt_symbol in self.vt_symbols:
                bar = bars.get(vt_symbol)
                if not bar:
                    continue
                # 容错：跳过收盘价无效的标的
                if (not isinstance(bar.close_price, (int, float))
                        or math.isnan(bar.close_price)
                        or bar.close_price <= 0):
                    continue

                target_pos = self.targets_pos[vt_symbol]
                current_pos = self.get_pos(vt_symbol)

                pos_diff = target_pos - current_pos

                if pos_diff > 0:
                    price = bar.close_price * (1 + self.price_add_percent)
                    self.buy(vt_symbol, price, pos_diff)

                if pos_diff < 0:
                    price = bar.close_price * (1 - self.price_add_percent)
                    self.sell(vt_symbol, price, -pos_diff)

        self.trade_day += 1

        self.put_event()
