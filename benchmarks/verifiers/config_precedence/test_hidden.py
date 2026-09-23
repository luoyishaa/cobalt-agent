import unittest

from server import selected_port


class PortVerifier(unittest.TestCase):
    def test_explicit_zero_is_still_explicit(self):
        self.assertEqual(selected_port(["--port", "0"], {"APP_PORT": "9100"}), 0)

    def test_environment_when_flag_is_absent(self):
        self.assertEqual(selected_port([], {"APP_PORT": "9100"}), 9100)
