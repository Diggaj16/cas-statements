import unittest
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from metrics import (calculate_xirr, build_portfolio_history,
                     calculate_capture_ratios,
                     compute_invested_withdrawn, prepare_base_cashflows,
                     calculate_xirr_fast, compute_daily_portfolio_value)

class TestMetrics(unittest.TestCase):
    def test_calculate_xirr_basic_positive(self):
        # Invest 10,000 on Jan 1, Valuation is 11,000 on Dec 31
        transactions = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'}
        ])
        val_date = pd.Timestamp('2023-12-31').date()
        xirr_val, _ = calculate_xirr(transactions, 11000.0, val_date)
        
        # 10% return roughly over 1 year (364 days). Exact is (11000/10000)^(365/364) - 1.
        self.assertAlmostEqual(xirr_val, 0.10027, places=4)
        
    def test_calculate_xirr_dividend_payout(self):
        # Invest 10,000, get 1,000 div, end value 10,000 (after 1 year)
        transactions = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-07-02'), 'Amount': 1000.0, 'Type': 'DIVIDEND_PAYOUT', 'Units': 0.0, 'Scheme': 'A'}
        ])
        val_date = pd.Timestamp('2023-12-31').date()
        xirr_val, _ = calculate_xirr(transactions, 10000.0, val_date)
        
        # Inflow at t=0.5, value at t=1. Total return is higher than 10% because of early cash.
        self.assertTrue(xirr_val > 0.10)
        
    def test_calculate_xirr_reinvestment_ignored(self):
        # Invest 10k, reinvest 1k, final value 11k
        transactions = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-07-01'), 'Amount': 1000.0, 'Type': 'DIVIDEND_REINVESTMENT', 'Units': 10.0, 'Scheme': 'A'}
        ])
        val_date = pd.Timestamp('2023-12-31').date()
        xirr_val, _ = calculate_xirr(transactions, 11000.0, val_date)
        # Should be exactly identical to basic_positive because reinvestment is internal!
        self.assertAlmostEqual(xirr_val, 0.10027, places=4)

    def test_calculate_xirr_empty(self):
        transactions = pd.DataFrame(columns=['Date', 'Amount', 'Type', 'Units', 'Scheme'])
        xirr_val, _ = calculate_xirr(transactions, 0.0, pd.Timestamp('2023-12-31').date())
        self.assertIsNone(xirr_val)



    def test_build_portfolio_history_twr(self):
        # Mock transactions
        transactions_df = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A', 'AMFI': '12345'}
        ])
        # Mock NAVs: Jan 1 = 100, Jan 2 = 110, Jan 3 = 110
        dates = pd.date_range('2023-01-01', '2023-01-03', freq='D')
        nav_df = pd.DataFrame({'nav': [100.0, 110.0, 110.0]}, index=dates)
        hist_navs = {'12345': nav_df}
        
        monthly_ret, debug_val, daily_ret, twr_debug = build_portfolio_history(transactions_df, hist_navs)
        
        # Check daily return for scheme A
        # Jan 1: 0% (purchased at end of day, no prior value)
        # Jan 2: 10%
        # Jan 3: 0%
        self.assertEqual(daily_ret['12345'].loc[pd.Timestamp('2023-01-01')], 0.0)
        self.assertAlmostEqual(daily_ret['12345'].loc[pd.Timestamp('2023-01-02')], 0.10)
        self.assertEqual(daily_ret['12345'].loc[pd.Timestamp('2023-01-03')], 0.0)

    def test_build_portfolio_history_twr_with_dividend(self):
        # Mock transactions
        transactions_df = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A', 'AMFI': '12345'},
            {'Date': pd.Timestamp('2023-01-02'), 'Amount': 1000.0, 'Type': 'DIVIDEND_PAYOUT', 'Units': 0.0, 'Scheme': 'A', 'AMFI': '12345'}
        ])
        # Mock NAVs: Jan 1 = 100, Jan 2 = 90 (dropped by dividend), Jan 3 = 90
        dates = pd.date_range('2023-01-01', '2023-01-03', freq='D')
        nav_df = pd.DataFrame({'nav': [100.0, 90.0, 90.0]}, index=dates)
        hist_navs = {'12345': nav_df}
        
        monthly_ret, debug_val, daily_ret, twr_debug = build_portfolio_history(transactions_df, hist_navs)
        
        # Check daily return for scheme A
        # Jan 1: 0%
        # Jan 2: 0% (NAV dropped by 10, but 10 was paid out as dividend, so total economic return is 0)
        self.assertEqual(daily_ret['12345'].loc[pd.Timestamp('2023-01-01')], 0.0)
        self.assertAlmostEqual(daily_ret['12345'].loc[pd.Timestamp('2023-01-02')], 0.0)

    def test_capture_ratios(self):
        dates = pd.date_range('2023-01-31', periods=3, freq='ME')
        port = pd.Series([0.10, -0.05, 0.05], index=dates)
        bench = pd.Series([0.05, -0.10, 0.02], index=dates)
        
        up, down, df = calculate_capture_ratios(port, bench)
        
        # Up market: months 1 and 3
        # Port geo = (1.10 * 1.05) - 1 = 0.155
        # Bench geo = (1.05 * 1.02) - 1 = 0.071
        # Up capture = 0.155 / 0.071 = 218.3%
        self.assertAlmostEqual(up, 218.3098, places=1)
        
        # Down market: month 2
        # Port = -0.05, Bench = -0.10
        # Down capture = -0.05 / -0.10 = 50.0%
        self.assertAlmostEqual(down, 50.0, places=1)

class TestXirrStandardMethodology(unittest.TestCase):
    """Regression tests for the standard XIRR methodology fixes."""

    def test_negative_redemption_amounts_sign_safe(self):
        # casparser reports redemption amounts as negative (parentheses in PDF).
        # XIRR must be identical whether redemptions come signed or unsigned.
        txns_neg = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-07-01'), 'Amount': -5000.0, 'Type': 'REDEMPTION', 'Units': -45.0, 'Scheme': 'A'},
        ])
        txns_pos = txns_neg.copy()
        txns_pos['Amount'] = txns_pos['Amount'].abs()
        val_date = pd.Timestamp('2023-12-31').date()
        x_neg, _ = calculate_xirr(txns_neg, 6000.0, val_date)
        x_pos, _ = calculate_xirr(txns_pos, 6000.0, val_date)
        self.assertAlmostEqual(x_neg, x_pos, places=10)

    def test_switches_excluded_at_portfolio_level(self):
        # A PAIRED switch between two funds is internal: portfolio XIRR must
        # equal the XIRR of the same history without the switch legs.
        base = [
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
        ]
        switch_legs = [
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': -7000.0, 'Type': 'SWITCH_OUT', 'Units': -60.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-06-02'), 'Amount': 7000.0, 'Type': 'SWITCH_IN', 'Units': 50.0, 'Scheme': 'B'},
        ]
        val_date = pd.Timestamp('2023-12-31').date()
        x_with, _ = calculate_xirr(pd.DataFrame(base + switch_legs), 11000.0, val_date, include_switches=False)
        x_without, _ = calculate_xirr(pd.DataFrame(base), 11000.0, val_date)
        self.assertAlmostEqual(x_with, x_without, places=10)

    def test_orphan_switch_out_kept_at_portfolio_level(self):
        # A SWITCH_OUT with no matching SWITCH_IN (counterpart typed as
        # PURCHASE by the RTA, or in a folio outside this CAS) is a REAL flow.
        # Dropping it one-sided would understate XIRR.
        txns = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': -7000.0, 'Type': 'SWITCH_OUT', 'Units': -60.0, 'Scheme': 'A'},
            # counterpart mis-typed as PURCHASE: amounts differ enough not to pair
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': 7000.0, 'Type': 'PURCHASE', 'Units': 50.0, 'Scheme': 'B'},
        ])
        val_date = pd.Timestamp('2023-12-31').date()
        x_portfolio, _ = calculate_xirr(txns, 11000.0, val_date, include_switches=False)
        # Equivalent explicit ledger: -10000, +7000, -7000, +11000
        x_expected, _ = calculate_xirr(pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': 7000.0, 'Type': 'REDEMPTION', 'Units': -60.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': 7000.0, 'Type': 'PURCHASE', 'Units': 50.0, 'Scheme': 'B'},
        ]), 11000.0, val_date, include_switches=False)
        self.assertAlmostEqual(x_portfolio, x_expected, places=10)

    def test_paired_switch_with_orphan_mix(self):
        # One paired switch (cancels) + one orphan out (kept)
        txns = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-03-01'), 'Amount': -2000.0, 'Type': 'SWITCH_OUT', 'Units': -18.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-03-02'), 'Amount': 2000.0, 'Type': 'SWITCH_IN', 'Units': 15.0, 'Scheme': 'B'},
            {'Date': pd.Timestamp('2023-09-01'), 'Amount': -4000.0, 'Type': 'SWITCH_OUT', 'Units': -30.0, 'Scheme': 'B'},
        ])
        val_date = pd.Timestamp('2023-12-31').date()
        x_val, recs = calculate_xirr(txns, 7000.0, val_date, include_switches=False)
        # ledger should be: -10000 (Jan), +4000 (Sep orphan), +7000 (Dec val)
        flows = recs[recs['Type'] != 'VALUATION']
        self.assertEqual(len(flows), 2)
        self.assertAlmostEqual(flows['Amount'].sum(), -10000.0 + 4000.0, places=2)
        self.assertIsNotNone(x_val)

    def test_switches_included_at_fund_level(self):
        # At fund level a switch-in is a purchase: it must affect XIRR.
        txns = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'SWITCH_IN', 'Units': 100.0, 'Scheme': 'B'},
        ])
        val_date = pd.Timestamp('2023-12-31').date()
        x_val, _ = calculate_xirr(txns, 11000.0, val_date, include_switches=True)
        self.assertIsNotNone(x_val)
        self.assertAlmostEqual(x_val, 0.10027, places=4)



    def test_misc_no_unit_rows_excluded(self):
        # Unclassifiable zero-unit rows (MISC) must not become phantom inflows.
        base = [
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
        ]
        with_misc = base + [
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': 500.0, 'Type': 'MISC', 'Units': 0.0, 'Scheme': 'A'},
        ]
        val_date = pd.Timestamp('2023-12-31').date()
        x_base, _ = calculate_xirr(pd.DataFrame(base), 11000.0, val_date)
        x_misc, _ = calculate_xirr(pd.DataFrame(with_misc), 11000.0, val_date)
        self.assertAlmostEqual(x_base, x_misc, places=10)

    def test_stamp_duty_is_an_outflow(self):
        # CAS purchase rows are net of stamp duty (9999.50 + 0.50 = 10000 debit),
        # so the stamp line is real money out and must lower XIRR slightly.
        base = [
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 9999.50, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
        ]
        with_stamp = base + [
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 0.50, 'Type': 'STAMP_DUTY_TAX', 'Units': None, 'Scheme': 'A'},
        ]
        val_date = pd.Timestamp('2023-12-31').date()
        x_base, _ = calculate_xirr(pd.DataFrame(base), 11000.0, val_date)
        x_stamp, _ = calculate_xirr(pd.DataFrame(with_stamp), 11000.0, val_date)
        self.assertLess(x_stamp, x_base)

    def test_compute_invested_withdrawn(self):
        txns = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 9999.50, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 0.50, 'Type': 'STAMP_DUTY_TAX', 'Units': None, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': -7000.0, 'Type': 'SWITCH_OUT', 'Units': -60.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': 7000.0, 'Type': 'SWITCH_IN', 'Units': 50.0, 'Scheme': 'B'},
            {'Date': pd.Timestamp('2023-09-01'), 'Amount': -3000.0, 'Type': 'REDEMPTION', 'Units': -25.0, 'Scheme': 'B'},
            {'Date': pd.Timestamp('2023-10-01'), 'Amount': 1000.0, 'Type': 'DIVIDEND_REINVESTMENT', 'Units': 8.0, 'Scheme': 'A'},
        ])
        invested, withdrawn = compute_invested_withdrawn(txns)
        # stamp duty counts as invested; switches and reinvestment are internal
        self.assertAlmostEqual(invested, 10000.0, places=2)
        self.assertAlmostEqual(withdrawn, 3000.0, places=2)

    def test_xirr_fast_ignores_future_cashflows_and_groups_dates(self):
        txns = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-12-31'), 'Amount': 5000.0, 'Type': 'PURCHASE', 'Units': 40.0, 'Scheme': 'A'},
        ])
        base_cf = prepare_base_cashflows(txns)
        # Valuing on June 30: the Dec 31 purchase hasn't happened yet
        x_mid = calculate_xirr_fast(base_cf, 10500.0, date(2023, 6, 30))
        txns_only_first = pd.DataFrame([txns.iloc[0]])
        expected, _ = calculate_xirr(txns_only_first, 10500.0, date(2023, 6, 30))
        self.assertAlmostEqual(x_mid, expected, places=10)
        # Valuation date colliding with a cashflow date must not crash or skew
        x_end = calculate_xirr_fast(base_cf, 16000.0, date(2023, 12, 31))
        self.assertIsNotNone(x_end)

    def test_exclude_taxes_investwell_convention(self):
        # include_taxes=False must drop stamp/STT/TDS lines entirely, matching
        # an explicit ledger that never had them.
        with_taxes = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 9999.50, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 0.50, 'Type': 'STAMP_DUTY_TAX', 'Units': None, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': -3000.0, 'Type': 'REDEMPTION', 'Units': -28.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': 3.0, 'Type': 'STT_TAX', 'Units': None, 'Scheme': 'A'},
        ])
        without_taxes = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 9999.50, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-06-01'), 'Amount': -3000.0, 'Type': 'REDEMPTION', 'Units': -28.0, 'Scheme': 'A'},
        ])
        val_date = pd.Timestamp('2023-12-31').date()
        x_excl, _ = calculate_xirr(with_taxes, 8000.0, val_date, include_taxes=False)
        x_clean, _ = calculate_xirr(without_taxes, 8000.0, val_date)
        self.assertAlmostEqual(x_excl, x_clean, places=10)
        # and the standard mode must differ (taxes lower the return)
        x_std, _ = calculate_xirr(with_taxes, 8000.0, val_date)
        self.assertLess(x_std, x_excl)

    def test_zero_xirr_is_valid(self):
        # Flat portfolio: XIRR 0.0 must come back as 0.0, not None
        txns = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Amount': 10000.0, 'Type': 'PURCHASE', 'Units': 100.0, 'Scheme': 'A'},
        ])
        x_val, _ = calculate_xirr(txns, 10000.0, pd.Timestamp('2024-01-01').date())
        self.assertIsNotNone(x_val)
        self.assertAlmostEqual(x_val, 0.0, places=6)


class TestDailyPortfolioValue(unittest.TestCase):
    """The trend/Value-by-Date helper must value each past day with the units
    actually held THAT day (net cumulative), not today's holdings."""

    def _nav(self, date_range, value=10.0):
        return pd.DataFrame({'nav': [value] * len(date_range)}, index=date_range)

    def test_uses_historical_units_not_current(self):
        # Buy 100 on Jan 1, buy 100 more on Jan 3, sell 50 on Jan 5 -> hold 150.
        # NAV flat at 10 so value == units * 10 and we can read units off the value.
        date_range = pd.date_range('2023-01-01', '2023-01-06', freq='D')
        txns = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-01-03'), 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-01-05'), 'Units': -50.0, 'Scheme': 'A'},
        ])
        navs = {'111': self._nav(date_range)}
        daily, skipped = compute_daily_portfolio_value(txns, navs, date_range, {'A': '111'})

        self.assertEqual(skipped, [])
        # Before the 2nd buy: 100 units (NOT today's 150) -> 1000
        self.assertAlmostEqual(daily.loc['2023-01-02'], 1000.0)
        # After 2nd buy, before the sell: 200 units -> 2000
        self.assertAlmostEqual(daily.loc['2023-01-04'], 2000.0)
        # On/after the sell: 150 units -> 1500
        self.assertAlmostEqual(daily.loc['2023-01-05'], 1500.0)

    def test_final_day_reconciles_to_current_holdings(self):
        # The last day's units must equal the sum of every unit-changing row
        # (so it reconciles to the holdings the analyze endpoint reports).
        date_range = pd.date_range('2023-01-01', '2023-01-06', freq='D')
        txns = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-01-03'), 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-01-05'), 'Units': -50.0, 'Scheme': 'A'},
        ])
        navs = {'111': self._nav(date_range)}
        daily, _ = compute_daily_portfolio_value(txns, navs, date_range, {'A': '111'})
        current_units = txns['Units'].sum()  # 150
        self.assertAlmostEqual(daily.iloc[-1], current_units * 10.0)

    def test_multiplies_by_that_days_nav(self):
        # Value must track NAV, not just units.
        date_range = pd.date_range('2023-01-01', '2023-01-03', freq='D')
        txns = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Units': 10.0, 'Scheme': 'A'},
        ])
        nav_df = pd.DataFrame({'nav': [10.0, 20.0, 30.0]}, index=date_range)
        daily, _ = compute_daily_portfolio_value(txns, {'111': nav_df}, date_range, {'A': '111'})
        self.assertAlmostEqual(daily.loc['2023-01-01'], 100.0)
        self.assertAlmostEqual(daily.loc['2023-01-03'], 300.0)

    def test_fund_without_nav_is_skipped_not_zeroed_across_history(self):
        # A fund with no NAV must be reported as skipped and contribute nothing,
        # rather than back-projecting its current value onto every past day.
        date_range = pd.date_range('2023-01-01', '2023-01-03', freq='D')
        txns = pd.DataFrame([
            {'Date': pd.Timestamp('2023-01-01'), 'Units': 100.0, 'Scheme': 'A'},
            {'Date': pd.Timestamp('2023-01-01'), 'Units': 50.0, 'Scheme': 'B'},
        ])
        navs = {'111': self._nav(date_range)}  # only A has NAV
        daily, skipped = compute_daily_portfolio_value(
            txns, navs, date_range, {'A': '111', 'B': '222'})
        self.assertEqual(skipped, ['B'])
        # Only A (100 units * 10) contributes; B adds nothing.
        self.assertAlmostEqual(daily.iloc[-1], 1000.0)


if __name__ == '__main__':
    unittest.main()
