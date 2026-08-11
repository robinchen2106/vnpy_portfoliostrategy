import unittest
from datetime import datetime

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import DB_TZ
from vnpy.trader.object import BarData

from vnpy_portfoliostrategy.backtesting import BacktestingEngine


VT_SYMBOL = "000001.SZSE"


def make_bar(dt: datetime, close: float) -> BarData:
    return BarData(
        gateway_name="TEST",
        symbol="000001",
        exchange=Exchange.SZSE,
        datetime=dt,
        interval=Interval.DAILY,
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
    )


class DummyStrategy:
    def __init__(self) -> None:
        self.inited = False
        self.trading = False
        self.bar_dates: list[datetime] = []

    def on_init(self) -> None:
        return

    def on_start(self) -> None:
        return

    def on_bars(self, bars: dict[str, BarData]) -> None:
        self.bar_dates.extend(bar.datetime for bar in bars.values())


class BacktestingWarmupTest(unittest.TestCase):
    def test_warmup_skips_matching_and_daily_results(self) -> None:
        warmup_dt = datetime(2024, 1, 1, tzinfo=DB_TZ)
        trading_dt = datetime(2024, 1, 2, tzinfo=DB_TZ)
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbols=[VT_SYMBOL],
            interval=Interval.DAILY,
            rates={VT_SYMBOL: 0.0},
            slippages={VT_SYMBOL: 0.0},
            sizes={VT_SYMBOL: 1.0},
            priceticks={VT_SYMBOL: 0.01},
            end=datetime(2024, 1, 5),
            warmup=warmup_dt,
            start=trading_dt,
        )
        first = make_bar(warmup_dt, 100)
        second = make_bar(trading_dt, 101)
        engine.history_data = {
            (first.datetime, VT_SYMBOL): first,
            (second.datetime, VT_SYMBOL): second,
        }
        engine.dts = {first.datetime, second.datetime}
        strategy = DummyStrategy()
        engine.strategy = strategy

        cross_calls: list[None] = []
        engine.cross_limit_order = lambda: cross_calls.append(None)

        engine.run_backtesting()

        self.assertEqual(strategy.bar_dates, [warmup_dt, trading_dt])
        self.assertEqual(len(cross_calls), 1)
        self.assertEqual(list(engine.daily_results), [trading_dt.date()])

    def test_configured_warmup_skips_matching_and_results(self) -> None:
        warmup_dt = datetime(2024, 1, 1, tzinfo=DB_TZ)
        trading_dt = datetime(2024, 1, 2, tzinfo=DB_TZ)
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbols=[VT_SYMBOL],
            interval=Interval.DAILY,
            warmup=warmup_dt,
            start=trading_dt,
            rates={VT_SYMBOL: 0.0},
            slippages={VT_SYMBOL: 0.0},
            sizes={VT_SYMBOL: 1.0},
            priceticks={VT_SYMBOL: 0.01},
            end=datetime(2024, 1, 5),
        )
        first = make_bar(warmup_dt, 100)
        second = make_bar(trading_dt, 101)
        engine.history_data = {
            (first.datetime, VT_SYMBOL): first,
            (second.datetime, VT_SYMBOL): second,
        }
        engine.dts = {first.datetime, second.datetime}
        engine.days = 1
        engine.strategy = DummyStrategy()

        cross_calls: list[None] = []
        engine.cross_limit_order = lambda: cross_calls.append(None)

        engine.run_backtesting()

        self.assertEqual(len(cross_calls), 1)
        self.assertEqual(list(engine.daily_results), [trading_dt.date()])

    def test_equal_warmup_and_start_skips_warmup(self) -> None:
        start = datetime(2024, 1, 2)
        bar_dt = start.replace(tzinfo=DB_TZ)
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbols=[VT_SYMBOL],
            interval=Interval.DAILY,
            warmup=start,
            start=start,
            rates={VT_SYMBOL: 0.0},
            slippages={VT_SYMBOL: 0.0},
            sizes={VT_SYMBOL: 1.0},
            priceticks={VT_SYMBOL: 0.01},
            end=datetime(2024, 1, 5),
        )
        bar = make_bar(bar_dt, 100)
        engine.history_data = {(bar.datetime, VT_SYMBOL): bar}
        engine.dts = {bar.datetime}
        engine.strategy = DummyStrategy()

        cross_calls: list[None] = []
        engine.cross_limit_order = lambda: cross_calls.append(None)

        engine.run_backtesting()

        self.assertEqual(engine.strategy.bar_dates, [bar_dt])
        self.assertEqual(len(cross_calls), 1)
        self.assertEqual(list(engine.daily_results), [start.date()])


if __name__ == "__main__":
    unittest.main()
