"""Daily energy counters and the learned room model, persisted across restarts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .thermal import RoomModel

STORAGE_VERSION = 1
SAVE_DELAY = 300  # s
MAX_STEP = timedelta(minutes=30)  # don't integrate over long gaps (HA was down)
SAMPLE_PERIOD = timedelta(minutes=30)
MAX_SAMPLE = timedelta(minutes=90)


@dataclass(slots=True)
class Counters:
    """Energy of the current day in kWh."""

    solar: float = 0.0  # heat that actually entered through the window
    useful: float = 0.0  # ... while the room needed heat
    blocked: float = 0.0  # heat kept out while the room needed cooling
    insulation: float = 0.0  # heat loss avoided by the closed blind


@dataclass(slots=True)
class _Sample:
    start: datetime
    t_in: float
    t_out_sum: float
    count: int = 1
    energy: float = 0.0


def store_for(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


class EnergyTracker:
    """Integrates power into kWh, feeds the room model and saves both."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = store_for(hass, entry_id)
        self.day: date | None = None
        self.today = Counters()
        self.model = RoomModel()
        self._last: tuple[datetime, float, float, float] | None = None  # time, gain, blocked, saving (W)
        self._sample: _Sample | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        if (day := data.get("day")) is not None:
            self.day = date.fromisoformat(day)
            self.today = Counters(**data.get("today", {}))
        self.model = RoomModel.from_dict(data.get("model"))

    def _data(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat() if self.day else None,
            "today": asdict(self.today),
            "model": self.model.as_dict(),
        }

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    def add(self, now: datetime, gain: float, blocked: float, saving: float, heating: bool, cooling: bool) -> float:
        """Integrate the powers (W) since the last call; returns kWh of solar gain."""
        if self.day != now.date():
            self.day, self.today = now.date(), Counters()
        energy = 0.0
        if self._last is not None:
            then, g0, b0, s0 = self._last
            step = now - then
            if timedelta(0) < step <= MAX_STEP:
                hours = step.total_seconds() / 3600 / 1000  # W·h -> kWh
                energy = (g0 + gain) / 2 * hours
                self.today.solar += energy
                if heating:
                    self.today.useful += energy
                    self.today.insulation += (s0 + saving) / 2 * hours
                if cooling:
                    self.today.blocked += (b0 + blocked) / 2 * hours
        self._last = (now, gain, blocked, saving)
        self._store.async_delay_save(self._data, SAVE_DELAY)
        return energy

    def learn(self, now: datetime, t_in: float | None, t_out: float | None, energy: float) -> None:
        """Collect 30-minute samples and update the room model."""
        if t_in is None or t_out is None:
            self._sample = None
            return
        s = self._sample
        if s is None or now - s.start > MAX_SAMPLE:
            self._sample = _Sample(now, t_in, t_out)
            return
        s.energy += energy
        s.t_out_sum += t_out
        s.count += 1
        if now - s.start >= SAMPLE_PERIOD:
            if abs(t_in - s.t_in) < 3:  # ignore window opening, sensor glitches
                hours = (now - s.start).total_seconds() / 3600
                self.model.update(s.energy, s.t_in, t_in, s.t_out_sum / s.count, hours)
            self._sample = _Sample(now, t_in, t_out)
