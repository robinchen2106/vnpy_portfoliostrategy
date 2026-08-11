import unittest
from datetime import date, datetime

from vnpy.trader.constant import Direction, Exchange, Interval, Offset
from vnpy.trader.object import TradeData

from vnpy_portfoliostrategy.backtesting import (
    DEFAULT_SLIPPAGE_RATE,
    BacktestingEngine,
    ContractDailyResult,
    PortfolioDailyResult,
)


VT_SYMBOL = "000001.SZSE"


def make_trade() -> TradeData:
    return TradeData(
        gateway_name="TEST",
        symbol="000001",
        exchange=Exchange.SZSE,
        orderid="order-1",
        tradeid="trade-1",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        price=100.0,
        volume=1_000,
        datetime=datetime(2024, 1, 2),
    )


class BacktestingFeeDefaultsTest(unittest.TestCase):
    def test_engine_uses_three_bps_default_slippage_rate(self) -> None:
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbols=[VT_SYMBOL],
            interval=Interval.DAILY,
            start=datetime(2024, 1, 1),
            rates={VT_SYMBOL: 0.0},
            slippages={VT_SYMBOL: 0.0},
            sizes={VT_SYMBOL: 1.0},
            priceticks={VT_SYMBOL: 0.01},
        )

        self.assertEqual(DEFAULT_SLIPPAGE_RATE, 0.0003)
        self.assertEqual(engine.slippage_rates, {VT_SYMBOL: 0.0003})

    def test_contract_result_uses_three_bps_default_slippage_rate(self) -> None:
        result = ContractDailyResult(date(2024, 1, 2), 100.0)
        result.add_trade(make_trade())
        result.calculate_pnl(
            pre_close=100.0,
            start_pos=0.0,
            size=1.0,
            rate=0.0,
            slippage=0.0,
        )

        self.assertEqual(result.slippage, 30.0)

    def test_explicit_zero_slippage_rate_overrides_default(self) -> None:
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbols=[VT_SYMBOL],
            interval=Interval.DAILY,
            start=datetime(2024, 1, 1),
            rates={VT_SYMBOL: 0.0},
            slippages={VT_SYMBOL: 0.0},
            sizes={VT_SYMBOL: 1.0},
            priceticks={VT_SYMBOL: 0.01},
            slippage_rates={VT_SYMBOL: 0.0},
        )

        self.assertEqual(engine.slippage_rates, {VT_SYMBOL: 0.0})

    def test_portfolio_result_uses_three_bps_default_slippage_rate(self) -> None:
        result = PortfolioDailyResult(date(2024, 1, 2), {VT_SYMBOL: 100.0})
        result.add_trade(make_trade())
        result.calculate_pnl(
            pre_closes={VT_SYMBOL: 100.0},
            start_poses={VT_SYMBOL: 0.0},
            sizes={VT_SYMBOL: 1.0},
            rates={VT_SYMBOL: 0.0},
            slippages={VT_SYMBOL: 0.0},
        )

        self.assertEqual(result.slippage, 30.0)


if __name__ == "__main__":
    unittest.main()
