import unittest

from grades import average_score


class GradeVerifier(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(average_score([]), 0.0)

    def test_float_average(self):
        self.assertAlmostEqual(average_score([1.0, 2.0]), 1.5)
