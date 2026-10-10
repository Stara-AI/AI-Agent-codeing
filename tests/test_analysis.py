"""Baseline evidence must not inflate recursion or turn missing data into 0%."""
from collections import Counter
import unittest
from tools.analyze_simulation import compare, symbol_counts


class AnalysisTests(unittest.TestCase):
    def test_recursion_counts_once_and_delta_uses_each_window_total(self):
        target = Counter({('worker', 'main', 'hot', 'hot'): 3, ('worker', 'main', 'cold'): 1})
        baseline = Counter({('worker', 'main', 'cold'): 8})
        self.assertEqual(symbol_counts(target)['hot'], 3)
        result = compare(target, baseline)
        self.assertEqual(result[0], {'symbol': 'hot', 'target_pct': 75.0, 'baseline_pct': 0.0, 'delta_pp': 75.0})
        self.assertNotIn('worker', {row['symbol'] for row in result})

    def test_missing_baseline_does_not_claim_new_hotspot(self):
        self.assertEqual(compare(Counter({('worker', 'hot'): 3}), Counter()), [])


if __name__ == '__main__':
    unittest.main()
