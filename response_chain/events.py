"""预警版本和响应时点。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AlertRevision:
    event_id: str
    revision: int
    issued_at: datetime
    area_ids: tuple[str, ...]


def response_delay(alert: AlertRevision, acknowledged_at: datetime | None) -> float | None:
    if acknowledged_at is None or acknowledged_at < alert.issued_at:
        return None
    return (acknowledged_at - alert.issued_at).total_seconds()
