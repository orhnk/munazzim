from datetime import timedelta, time
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from munazzim.timeutils import parse_duration, parse_hhmm


class TimeUtilsTest(unittest.TestCase):
    def test_parse_hhmm_colon(self) -> None:
        self.assertEqual(parse_hhmm("05:30"), time(5, 30))

    def test_parse_hhmm_dot(self) -> None:
        self.assertEqual(parse_hhmm("5.45"), time(5, 45))

    def test_parse_duration_decimal(self) -> None:
        self.assertEqual(parse_duration("1.30"), timedelta(hours=1, minutes=30))

    def test_parse_duration_colon(self) -> None:
        self.assertEqual(parse_duration("0:45"), timedelta(minutes=45))

    def test_parse_duration_suffix(self) -> None:
        self.assertEqual(parse_duration("90m"), timedelta(minutes=90))


if __name__ == "__main__":
    unittest.main()
