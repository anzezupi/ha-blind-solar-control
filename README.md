# Blind Control for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/anzezupi/ha-blind-solar-control/actions/workflows/validate.yml/badge.svg)](https://github.com/anzezupi/ha-blind-solar-control/actions/workflows/validate.yml)

Blind Control automates roller blinds, roller shutters, venetian blinds, louvred shutters, curtains and awnings, using the sun's position, room temperature and your own schedule.

Its main job is **passive solar heating and cooling**. When a cold room has the sun on its window, the blind opens so the sun warms the room for free. When the room is too warm, the blind blocks direct sun but still lets in as much daylight as it can.

- Calculates the sun's position locally (no cloud, no `sun.sun` polling) and checks it against each window's orientation, field of view, roof overhang and obstacles such as buildings or trees.
- Roller blinds stop at the height where direct sun reaches no further into the room than a depth you choose.
- Venetian slats are set to the **cut-off angle**, the most open angle that still blocks direct sun, so you keep the view and the daylight.
- Schedule rules accept fixed times or sunrise/sunset with an offset. A rule can run overnight, have a priority and depend on a template condition.
- Notices when you move a blind by hand (wall switch, remote, app) and pauses the automation for that blind.
- Protects against wind and rain, and keeps the blind open while a door or window contact reports it open, so you can't get locked out on the balcony.
- Simulation mode calculates and shows every decision without moving anything.

## Installation

### HACS (recommended)
1. HACS → ⋮ → *Custom repositories* → add `https://github.com/anzezupi/ha-blind-solar-control`, category *Integration*.
2. Install **Blind Control** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → Blind Control*.

### Manual
Copy `custom_components/blind_control` into your `config/custom_components/` folder and restart.

## Setup

Each window covering is added as its own entry, in three steps:

1. **Cover**: the cover entity and its type.
2. **Window**: orientation and dimensions.
3. **Climate**: optional sensors and temperature thresholds.

Everything else is under **Configure** on the entry: positions, protection, behaviour and schedule rules.

### Window geometry

| Setting | Meaning |
|---|---|
| Azimuth | Compass direction the window faces when you look out: 90° east, 180° south, 270° west. |
| Field of view left/right | How far either side of straight out the sun still reaches the glass. Deep window reveals make this smaller. |
| Min / max elevation | Ignore the sun below the hills, or above the angle where a balcony or overhang shades the window. |
| Obstacles | Azimuth ranges with a height, e.g. `90-130:15, 240-270:25`: between azimuth 90° and 130° anything below 15° elevation is hidden. |
| Window height, sill height, max sun depth | Used to calculate the roller blind position: direct sun may reach at most *depth* metres into the room. Set the depth to 0 to always use the fixed shade position. |
| Slat width / spacing, tilt mode | Used for venetian blinds. Choose the tilt mode that matches how your device reports tilt (0–100 % = closed→horizontal, or closed→horizontal→closed). |

## How a decision is made

Each check is tried in order; the first one that applies sets the target:

1. **Wind / rain alarm**: go to the safety position. The alarm is released only after the wind or rain has stayed below the threshold for *N* minutes.
2. **Manual override**: do nothing until the pause runs out, or until the next schedule rule change if that comes first.
3. **Active schedule rule** (highest priority wins):
   - `open`, `close`, `position`, `tilt` (lowered, slats at a fixed angle): run as written.
   - `tilt_auto`: lowered, with the slats following the sun.
   - `shade_only`: the automatic logic, but it never opens the blind to heat the room.
   - `skip`: hands the blind back to you.
   - `auto`: the automatic logic below.
4. **Automatic** (also used when no rule is active):
   - Sun not on the window, or too cloudy (illuminance sensor or weather entity): **default position**.
   - The room is colder than the heating threshold: **open** and let the sun heat the room. Venetian slats are set parallel to the sun's rays.
   - The room is warmer than the cooling threshold, or the day is hot outside: **shade**. If nobody is home, close fully instead.
   - The room is comfortable and glare protection is on: **shade**.
5. **Door contact open**: never close below the lockout position.

To stop the blind from moving every couple of minutes, the integration waits a minimum time between moves and ignores position changes smaller than a minimum step. Neither limit applies when the reason for the decision changes, for example when night starts or an alarm goes off.

The **Strategy** select switches the climate logic per blind: *Automatic*, *Winter* (always use the sun for heating), *Summer* (always block the sun), *Glare only* or *Off*.

## Schedule rule examples

| Goal | Rule |
|---|---|
| Closed at night (created by default) | sunset → sunrise, `close` |
| Not before 7:00 on workdays | add a second rule: Mon–Fri, sunset → 07:00, `close`. Overlapping rules with the same action combine, so the blind opens at whichever is later, sunrise or 7:00. |
| Sleep in at the weekend | Sat–Sun, sunset → 09:30, `close` |
| Bedroom slats in the afternoon | 13:00 → 17:00, `tilt_auto` |
| Holiday mode | 00:00 → 00:00 isn't allowed, so use 00:00 → 23:59, `close`, priority 90, condition `{{ is_state('input_boolean.holiday', 'on') }}` |
| Leave it alone while working at home | 08:00 → 16:00, `skip`, condition `{{ is_state('input_boolean.home_office', 'on') }}` |

## Entities per blind

| Entity | Purpose |
|---|---|
| `switch.*_automation` | Turns the automation for this blind on or off. Its state survives restarts. |
| `select.*_strategy` | Automatic / Winter / Summer / Glare only / Off |
| `sensor.*_target_position` | The calculated target. Attributes: tilt, active rule, door lockout, override end. |
| `sensor.*_reason` | Why the integration chose this target. |
| `sensor.*_room_needs` | Heat / Cooling / Comfortable |
| `sensor.*_sun_enters_window`, `sensor.*_sun_leaves_window` | Forecast timestamps, handy for dashboards. |
| `binary_sensor.*_sun_on_window`, `*_manual_override`, `*_wind_rain_alarm` | Status |
| `button.*_resume_automation`, `button.*_recalculate` | Controls |

Services: `blind_control.resume`, `blind_control.pause` (minutes) and `blind_control.recalculate`. Each one acts on a single blind, or on all blinds if you leave the blind empty.

## Tips

- Turn on **Simulation** under *Behaviour* for the first few days and watch the *Reason* and *Target position* sensors.
- To find the window azimuth, open a map in satellite view, or stand at the window with a phone compass.
- Covers that don't report their position are still supported: the integration sends open or close instead.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

The decision logic (`engine.py`) and the schedule (`schedule.py`) are pure Python with no Home Assistant imports, so the tests run in milliseconds.

## License

MIT
