import unittest

from polytrader.engine_loop import build_offline_engine
from polytrader.i18n import normalize_lang, tr
from polytrader.reporting import DailyReport


class TestI18n(unittest.TestCase):
    def test_normalize_lang(self):
        self.assertEqual(normalize_lang("zh"), "zh")
        self.assertEqual(normalize_lang("zh-CN"), "zh")
        self.assertEqual(normalize_lang("en"), "en")
        self.assertEqual(normalize_lang(None), "en")
        self.assertEqual(normalize_lang("fr"), "en")  # unsupported -> en

    def test_tr_lookup_and_fallback(self):
        self.assertEqual(tr("en", "sec_profit"), "Profit")
        self.assertEqual(tr("zh", "sec_profit"), "收益指标")
        # unknown key returns the key itself
        self.assertEqual(tr("zh", "does_not_exist"), "does_not_exist")

    def test_tr_interpolation(self):
        self.assertIn("第 3 天", tr("zh", "day_report_title", day=3))
        self.assertIn("Day 3", tr("en", "day_report_title", day=3))

    def test_daily_report_bilingual(self):
        dr = DailyReport(day=1, equity=1000.0, cash=800.0, realized_pnl=0.0,
                         unrealized_pnl=0.0, open_positions=1, drawdown_pct=0.0,
                         halt_level="RUNNING")
        self.assertIn("Equity", dr.render("en"))
        self.assertIn("权益", dr.render("zh"))


class TestBilingualReports(unittest.TestCase):
    def test_engine_report_english(self):
        engine = build_offline_engine(storage_path=":memory:", seed=42,
                                      verbose=False, lang="en")
        report = engine.run(days=7)
        self.assertIn("Experiment Report", report)
        self.assertIn("Success criteria", report)

    def test_engine_report_chinese(self):
        engine = build_offline_engine(storage_path=":memory:", seed=42,
                                      verbose=False, lang="zh")
        report = engine.run(days=7)
        self.assertIn("实验报告", report)
        self.assertIn("实验成功标准", report)
        # criteria labels are translated
        self.assertIn("无重复下单", report)


if __name__ == "__main__":
    unittest.main()
