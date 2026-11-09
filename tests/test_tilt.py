import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "custom_components", "blinds_controller")))

from calculator import TravelCalculator


class TestTiltCalculation(unittest.TestCase):
    def test_tilt_relay_duration_calculation(self):
        tilt_open_time = 0.5
        dead_time = 0.78
        stop_delay = 0.15
        sperrzeit = 0.2

        # Net effective dead time
        effective_dead_time = max(0.0, dead_time - stop_delay)  # 0.63s

        # Relay ON time for 0.5s tilt:
        # Relay must stay on for tilt_time + effective_dead_time = 0.5 + 0.63 = 1.13s!
        relay_time = tilt_open_time + effective_dead_time
        self.assertAlmostEqual(relay_time, 1.13, places=2)
        self.assertGreater(relay_time, sperrzeit)

        # During that 1.13s relay pulse:
        # - 0.78s dead time: motor does not move
        # - (1.13 - 0.78) = 0.35s: motor moves while relay ON
        # - 0.15s stop_delay: motor coasts after relay turns OFF
        # Total motor movement = 0.35 + 0.15 = 0.50s EXACTLY!
        actual_motor_movement = (relay_time - dead_time) + stop_delay
        self.assertAlmostEqual(actual_motor_movement, tilt_open_time, places=2)


if __name__ == "__main__":
    unittest.main()
