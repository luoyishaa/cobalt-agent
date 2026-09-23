import unittest

from grades import average_score


class GradeTests(unittest.TestCase):
    def test_normal_average(self):
        self.assertEqual(average_score([4, 6, 8]), 6.0)

    def test_empty_average(self):
        self.assertEqual(average_score([]), 0.0)
