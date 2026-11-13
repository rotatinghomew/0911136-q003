import unittest
from datetime import datetime, timedelta, timezone

from response_chain.events import AlertRevision, response_delay


class EventTests(unittest.TestCase):
    def test_invalid_or_missing_acknowledgement_is_unknown(self):
        issued = datetime.now(timezone.utc)
        alert = AlertRevision("e1", 1, issued, ("v1",))
        self.assertIsNone(response_delay(alert, None))
        self.assertEqual(30, response_delay(alert, issued + timedelta(seconds=30)))
