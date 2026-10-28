"""Weather forecast cache (hourly, falling back to daily)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .thermal import CONDITION_CLOUDS

_LOGGER = logging.getLogger(__name__)

TTL = timedelta(minutes=30)


def _cloud(item: dict[str, Any]) -> float | None:
    if (cloud := item.get("cloud_coverage")) is not None:
        return float(cloud)
    return CONDITION_CLOUDS.get(item.get("condition") or "")


class Forecast:
    """Fetches ``weather.get_forecasts`` at most every 30 minutes."""

    def __init__(self, hass: HomeAssistant, entity_id: str | None) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.fetched: datetime | None = None
        self._hourly: dict[datetime, tuple[float | None, float | None]] = {}
        self._daily: dict[date, tuple[float | None, float | None]] = {}

    async def _fetch(self, kind: str) -> list[dict[str, Any]]:
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": self.entity_id, "type": kind},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("%s: %s forecast unavailable: %s", self.entity_id, kind, err)
            return []
        return (response or {}).get(self.entity_id, {}).get("forecast", []) or []

    async def async_update(self, now: datetime) -> bool:
        """Refresh when stale. Returns True when new data arrived."""
        if not self.entity_id or (self.fetched and now - self.fetched < TTL):
            return False
        self.fetched = now
        self._hourly, self._daily = {}, {}
        for item in await self._fetch("hourly"):
            if (when := dt_util.parse_datetime(item.get("datetime", ""))) is not None:
                hour = dt_util.as_local(when).replace(minute=0, second=0, microsecond=0)
                self._hourly[hour] = (item.get("temperature"), _cloud(item))
        if not self._hourly:
            for item in await self._fetch("daily"):
                if (when := dt_util.parse_datetime(item.get("datetime", ""))) is not None:
                    self._daily[dt_util.as_local(when).date()] = (item.get("temperature"), _cloud(item))
        return True

    def cloud_at(self, when: datetime, fallback: float) -> float:
        hour = when.replace(minute=0, second=0, microsecond=0)
        entry = self._hourly.get(hour) or self._daily.get(when.date())
        return entry[1] if entry and entry[1] is not None else fallback

    def max_temp_today(self, now: datetime) -> float | None:
        """Highest forecast temperature for the rest of today."""
        temps = [t for hour, (t, _) in self._hourly.items() if hour.date() == now.date() and t is not None]
        if not temps and (day := self._daily.get(now.date())):
            temps = [day[0]] if day[0] is not None else []
        return max(temps, default=None)

    def avg_temp(self, start: datetime, hours: float) -> float | None:
        end = start + timedelta(hours=hours)
        lo = start - timedelta(hours=1)
        temps = [t for hour, (t, _) in self._hourly.items() if lo < hour < end and t is not None]
        return sum(temps) / len(temps) if temps else None
