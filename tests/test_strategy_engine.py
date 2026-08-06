import unittest
from pathlib import Path

from vnpy_portfoliostrategy.engine import StrategyEngine


class StrategyEnginePathTest(unittest.TestCase):
    def test_load_strategy_class_uses_captured_project_path(self) -> None:
        engine = StrategyEngine.__new__(StrategyEngine)
        captured_path = Path("D:/captured-project/strategies")
        engine.strategy_path = captured_path

        loaded: list[tuple[Path, str]] = []
        engine.load_strategy_class_from_folder = (
            lambda path, module_name="": loaded.append((path, module_name))
        )

        engine.load_strategy_class()

        self.assertEqual(loaded[1], (captured_path, "strategies"))


if __name__ == "__main__":
    unittest.main()
