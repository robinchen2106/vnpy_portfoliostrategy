import unittest
from datetime import date, datetime

import numpy as np
from pandas import DataFrame

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from vnpy_portfoliostrategy.backtesting import BacktestingEngine


VT_SYMBOL = "000001.SZSE"
BENCHMARK_SYMBOL = "000300.SSE"


def make_benchmark_bar(
    bar_date: date,
    open_price: float,
    close_price: float,
) -> BarData:
    return BarData(
        gateway_name="TEST",
        symbol="000300",
        exchange=Exchange.SSE,
        datetime=datetime.combine(bar_date, datetime.min.time()),
        interval=Interval.DAILY,
        open_price=open_price,
        high_price=max(open_price, close_price),
        low_price=min(open_price, close_price),
        close_price=close_price,
    )


class BacktestingStatisticsTest(unittest.TestCase):
    def test_cta_aligned_statistics_and_benchmark_date_filter(self) -> None:
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbols=[VT_SYMBOL],
            interval=Interval.DAILY,
            warmup=datetime(2023, 12, 1),
            start=datetime(2024, 1, 1),
            rates={VT_SYMBOL: 0.0},
            slippages={VT_SYMBOL: 0.0},
            sizes={VT_SYMBOL: 1.0},
            priceticks={VT_SYMBOL: 0.01},
            capital=10_000,
            benchmark_symbol=BENCHMARK_SYMBOL,
            half_life=2,
        )
        engine.benchmark_data = [
            make_benchmark_bar(date(2023, 12, 29), 50.0, 50.0),
            make_benchmark_bar(date(2024, 1, 2), 100.0, 105.0),
            make_benchmark_bar(date(2024, 1, 5), 110.0, 120.0),
            make_benchmark_bar(date(2024, 1, 8), 120.0, 180.0),
        ]

        df = DataFrame(
            {
                "net_pnl": [100.0, -200.0, 150.0, 50.0],
                "commission": [0.0, 0.0, 0.0, 0.0],
                "broker_commission": [0.0, 0.0, 0.0, 0.0],
                "stamp_tax": [0.0, 0.0, 0.0, 0.0],
                "slippage": [0.0, 0.0, 0.0, 0.0],
                "turnover": [0.0, 0.0, 0.0, 0.0],
                "trade_count": [0, 0, 0, 0],
            },
            index=[
                date(2024, 1, 2),
                date(2024, 1, 3),
                date(2024, 1, 4),
                date(2024, 1, 5),
            ],
        )
        logs: list[str] = []
        engine.output = logs.append

        statistics = engine.calculate_statistics(df, output=True)

        self.assertAlmostEqual(df["return"].iloc[0], np.log(1.01))
        self.assertAlmostEqual(
            statistics["return_drawdown_ratio"],
            -statistics["total_return"] / statistics["max_ddpercent"],
        )

        ewm_mean = df["return"].ewm(halflife=2).mean() * 100
        ewm_std = df["return"].ewm(halflife=2).std() * 100
        expected_ewm_sharpe = (ewm_mean / ewm_std).iloc[-1] * np.sqrt(240)

        self.assertAlmostEqual(statistics["benchmark_return"], 20.0)
        self.assertAlmostEqual(statistics["excess_return"], -19.0)
        expected_annual_return = (
            (statistics["end_balance"] / statistics["capital"])
            ** (engine.annual_days / statistics["total_days"])
            - 1
        ) * 100
        self.assertAlmostEqual(statistics["annual_return"], expected_annual_return)
        annual_return_log = next(log for log in logs if "年化收益" in log)
        self.assertIn(f"{expected_annual_return:,.2f}%", annual_return_log)
        self.assertAlmostEqual(statistics["ewm_sharpe"], expected_ewm_sharpe)
        self.assertTrue(np.isfinite(statistics["rgr_ratio"]))
        self.assertNotEqual(statistics["rgr_ratio"], 0.0)
        self.assertTrue(any("EWM Sharpe" in log for log in logs))
        self.assertTrue(any("RGR Ratio" in log for log in logs))


if __name__ == "__main__":
    unittest.main()
