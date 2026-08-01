import math
from datetime import datetime

from vnpy.trader.utility import BarGenerator
from vnpy.trader.utility import ArrayManager
from vnpy.trader.object import TickData, BarData
from vnpy.trader.constant import Direction, Interval

from vnpy_portfoliostrategy import StrategyTemplate, StrategyEngine
from vnpy_portfoliostrategy.utility import PortfolioBarGenerator


class Return10Strategy(StrategyTemplate):
    """ """

    author = "Robin"

    price_add_percent = 0.005
    fixed_pos_value = 50000
    return_peroid = 10
    holding_peroid = 10
    max_positions = 10

    signal_ts = {}
    signal_total = {}
    last_tick_time: datetime = None
    trade_day = 0
    targets_pos = {}

    parameters = [
        "price_add_percent",
        "fixed_pos_value",
        "return_peroid",
        "holding_peroid",
        "max_positions",
    ]
    variables = [
        "signal_ts",
        "signal_total",
        "last_tick_time",
        "trade_day",
        "targets_pos",
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

        self.bgs: dict[str, BarGenerator] = {}
        self.ams: dict[str, ArrayManager] = {}

        for vt_symbol in self.vt_symbols:

            def on_bar(bar: BarData):
                """"""
                pass

            self.bgs[vt_symbol] = BarGenerator(on_bar)
            self.ams[vt_symbol] = ArrayManager()

        self.pbg = PortfolioBarGenerator(self.on_bars)

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
        pass

    def on_bars(self, bars: dict[str, BarData]) -> None:
        """K线切片回调"""
        self.cancel_all()
        # 更新K线计算RSI数值
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

        # 信号汇总，总信号= 时序信号汇总 + 横界面信号汇总
        # 信号汇总：按 ROCP 排序，只保留 top max_positions 做多，其余平仓
        candidates: list[tuple[str, float]] = []
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
