import unittest

from server import selected_port


class PortTests(unittest.TestCase):
    def test_cli_wins_over_environment(self):
        self.assertEqual(selected_port(["--port", "9100"], {"APP_PORT": "9200"}), 9100)

    def test_environment_wins_over_default(self):
        self.assertEqual(selected_port([], {"APP_PORT": "9200"}), 9200)

    def test_default(self):
        self.assertEqual(selected_port([], {}), 8000)
