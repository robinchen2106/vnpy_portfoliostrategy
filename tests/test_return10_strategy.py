import json
import unittest
from datetime import datetime, timedelta

from vnpy.trader.constant import Direction, Exchange, Interval, Offset
from vnpy.trader.object import BarData, TickData

from vnpy_portfoliostrategy.strategies.return10_strategy import Return10Strategy


class DummyEngine:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.sync_count: int = 0
        self.orders: list[tuple[str, Direction, Offset, float, float]] = []

    def write_log(self, msg: str, strategy: Return10Strategy) -> None:
        self.logs.append(msg)

    def sync_strategy_data(self, strategy: Return10Strategy) -> None:
        self.sync_count += 1

    def send_order(
        self,
        strategy: Return10Strategy,
        vt_symbol: str,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
        lock: bool,
        net: bool,
    ) -> list[str]:
        self.orders.append((vt_symbol, direction, offset, price, volume))
        return [f"BACKTESTING.{len(self.orders)}"]

    def cancel_order(self, strategy: Return10Strategy, vt_orderid: str) -> None:
        return

    def put_strategy_event(self, strategy: Return10Strategy) -> None:
        return


class CollectingReturn10Strategy(Return10Strategy):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.received_slices: list[dict[str, BarData]] = []

    def on_bars(self, bars: dict[str, BarData]) -> None:
        self.received_slices.append(bars)


def make_tick(
    symbol: str,
    exchange: Exchange,
    dt: datetime,
    close_price: float,
    market_closed: bool,
) -> TickData:
    tick = TickData(
        gateway_name="XT",
        symbol=symbol,
        exchange=exchange,
        datetime=dt,
        volume=1000,
        turnover=10000,
        open_interest=15 if market_closed else 0,
        last_price=close_price,
        open_price=close_price - 1,
        high_price=close_price + 1,
        low_price=close_price - 2,
    )
    tick.extra = {"market_closed": market_closed}
    return tick


class Return10LiveBarTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = DummyEngine()
        self.strategy = CollectingReturn10Strategy(
            self.engine,
            "return10",
            ["600000.SSE", "000001.SZSE"],
            {"close_wait_seconds": 0},
        )
        self.strategy.inited = True
        self.strategy.trading = True

    def test_collects_latest_ticks_into_one_daily_slice(self) -> None:
        dt = datetime(2026, 8, 4, 15, 0)
        self.strategy.on_tick(
            make_tick("600000", Exchange.SSE, dt, 12.5, market_closed=False)
        )
        self.strategy.on_tick(
            make_tick("000001", Exchange.SZSE, dt, 10.5, market_closed=True)
        )

        self.strategy.on_timer()

        self.assertEqual(len(self.strategy.received_slices), 1)
        bars = self.strategy.received_slices[0]
        self.assertEqual(set(bars), {"600000.SSE", "000001.SZSE"})
        self.assertEqual(bars["600000.SSE"].interval, Interval.DAILY)
        self.assertEqual(bars["600000.SSE"].datetime, datetime(2026, 8, 4))
        self.assertEqual(bars["600000.SSE"].close_price, 12.5)
        self.assertEqual(self.strategy.last_daily_bar_date, "2026-08-04")
        self.assertEqual(self.engine.sync_count, 1)

        # 策略状态需要能够由实盘引擎直接写入 JSON。
        json.dumps(self.strategy.get_variables())

    def test_emits_at_most_once_per_trading_day(self) -> None:
        first_day = datetime(2026, 8, 4, 15, 0)
        first_tick = make_tick(
            "600000", Exchange.SSE, first_day, 12.5, market_closed=True
        )
        self.strategy.on_tick(first_tick)
        self.strategy.on_timer()

        self.strategy.on_tick(first_tick)
        self.strategy.on_timer()
        self.assertEqual(len(self.strategy.received_slices), 1)

        second_day = datetime(2026, 8, 5, 15, 0)
        self.strategy.on_tick(
            make_tick("600000", Exchange.SSE, second_day, 13, market_closed=False)
        )
        self.strategy.on_tick(
            make_tick("000001", Exchange.SZSE, second_day, 11, market_closed=True)
        )
        self.strategy.on_timer()

        self.assertEqual(len(self.strategy.received_slices), 2)
        self.assertEqual(self.strategy.last_daily_bar_date, "2026-08-05")
        self.assertEqual(self.engine.sync_count, 2)

    def test_close_slice_runs_original_signal_and_order_logic(self) -> None:
        strategy = Return10Strategy(
            self.engine,
            "return10-orders",
            ["600000.SSE", "000001.SZSE"],
            {"close_wait_seconds": 0, "max_positions": 1},
        )

        start = datetime(2026, 7, 1)
        for index in range(20):
            dt = start + timedelta(days=index)
            strategy.on_bars(
                {
                    "600000.SSE": BarData(
                        gateway_name="DB",
                        symbol="600000",
                        exchange=Exchange.SSE,
                        datetime=dt,
                        interval=Interval.DAILY,
                        open_price=10 + index,
                        high_price=11 + index,
                        low_price=9 + index,
                        close_price=10 + index,
                    ),
                    "000001.SZSE": BarData(
                        gateway_name="DB",
                        symbol="000001",
                        exchange=Exchange.SZSE,
                        datetime=dt,
                        interval=Interval.DAILY,
                        open_price=10 + index / 2,
                        high_price=11 + index / 2,
                        low_price=9 + index / 2,
                        close_price=10 + index / 2,
                    ),
                }
            )

        strategy.inited = True
        strategy.trading = True
        close_dt = datetime(2026, 8, 4, 15, 0)
        strategy.on_tick(
            make_tick("600000", Exchange.SSE, close_dt, 31, market_closed=True)
        )
        strategy.on_tick(
            make_tick("000001", Exchange.SZSE, close_dt, 21, market_closed=True)
        )
        strategy.on_timer()

        self.assertEqual(len(self.engine.orders), 1)
        vt_symbol, direction, offset, _, volume = self.engine.orders[0]
        self.assertEqual(vt_symbol, "600000.SSE")
        self.assertEqual(direction, Direction.LONG)
        self.assertEqual(offset, Offset.OPEN)
        self.assertGreater(volume, 0)


if __name__ == "__main__":
    unittest.main()
