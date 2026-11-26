import unittest

from hanabi.partner_probe import paired_report


class PartnerProbeTests(unittest.TestCase):
    def test_bootstrap_pairs_both_seats_on_the_same_deal(self):
        before = [{"first": (2, "deck"), "second": (2, "deck")}] * 2
        after = [{"first": (8, "deck"), "second": (0, "fuse")},
                 {"first": (0, "fuse"), "second": (8, "deck")}]
        result = paired_report(before, after, before, 1, "spark")
        self.assertEqual(result["ci95"], [2, 2])
        self.assertEqual(result["mean_lift"], 2)
        self.assertEqual(result["adaptive"]["fuse"], 2)
        self.assertEqual(result["group"]["games"], 4)
