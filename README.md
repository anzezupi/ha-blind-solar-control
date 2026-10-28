# Blind Control: your windows as a solar heater

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/anzezupi/ha-blind-solar-control/actions/workflows/validate.yml/badge.svg)](https://github.com/anzezupi/ha-blind-solar-control/actions/workflows/validate.yml)
[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-PayPal-00457C?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/anzezupi)

Most blind automations treat the sun as something to keep out. **Blind Control treats it as free energy.**

On a clear winter day a south-facing window lets in about as much heat as a small electric heater. Blind Control calculates that heat for every window, opens the blinds when a cold room can use it and closes them before a warm room overheats. It also learns how each room responds and can tell your thermostat to hold back while the sun does the work.

It works with roller blinds, exterior roller shutters, venetian blinds, louvred shutters, pergolas, curtains and awnings.

## What makes it different

| | |
|---|---|
| ☀️ **Solar heat per window in watts and kWh** | Built from a physical model: clear-sky irradiance, cloud cover, beam/diffuse split, incidence angle, glass g-value and the shading factor of the blind. |
| 💶 **Savings you can see** | Daily sensors for useful solar heat, heat kept out in summer and heat saved by closed shutters at night, plus the money saved at your price of heat. |
| 🔮 **Forecast-driven** | Uses the weather forecast to shade *before* a hot afternoon and to show how much solar heat is still to come today. |
| 🧠 **Learns your rooms** | Learns from your temperature sensor how many °C each kWh of sun adds to the room and how quickly the room loses heat, and uses that to predict overheating. |
| 🌡️ **Works with your heating** | Suggests, or sets, a lower heating setpoint while the sun is expected to heat the room. |
| 🌙 **Night insulation and ventilation hints** | Closed exterior shutters cut heat loss through the window; a binary sensor tells you when opening the window will cool the room. |
| 🗺️ **Sun-path card** | A Lovelace card that shows the sun's path across the sky relative to each window, marks when the sun is actually on the glass, and plots an hourly forecast of solar heat. |

The usual blind features are included too: per-window geometry (azimuth, field of view, roof overhang, obstacles), a roller position that limits how deep the sun reaches into the room, the venetian slat cut-off angle, schedule rules with sunrise/sunset offsets and template conditions, detection of manual changes, and wind, rain and door lock-out protection.

## Installation

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/anzezupi/ha-blind-solar-control`, category *Integration*.
2. Install **Blind Control** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → Blind Control*, then add every window.

## Setup per window

Each window covering is a separate entry and is set up in three steps:

1. **Cover**: the cover entity and its type.
2. **Window**:
   - orientation (azimuth, field of view, min/max sun elevation, obstacles)
   - size (width, height, sill height)
   - glass (single, old double, low-e double or triple, or your own g-value and U-value)
   - whether the blind is mounted inside or outside
3. **Climate**:
   - **the room's temperature sensor**. Every window needs one. It is what the integration learns from, and a room thermostat works too.
   - outdoor temperature
   - weather entity (clouds and forecast)
   - optionally an illuminance or irradiance sensor, and presence

Everything else is under **Configure**: *Energy and prediction*, positions, protection, behaviour and schedule rules.

> Mounting matters. An exterior shutter stops about 95 % of the sun's heat before it reaches the glass. An interior roller blind stops only about 40 %, because the heat is already inside the room.

## How decisions are made

Each check is tried in order; the first one that applies sets the target:

1. **Wind / rain alarm**: go to the safety position.
2. **Manual change**: pause the automation for a while, or until the next schedule rule.
3. **Schedule rule** (highest priority wins): `open`, `close`, `position`, `tilt`, `tilt_auto`, `shade_only`, `skip` or `auto`.
4. **Automatic**:
   - room colder than the heating threshold and sun on the window: **open** and harvest the heat.
   - room warmer than the cooling threshold: **shade**. If nobody is home, close fully.
   - room comfortable, but the forecast high, a hot day or the room model predicts overheating within the look-ahead window: **shade early** (*predictive shade*).
   - cold, sunless day and nobody home, with *Insulate when away* turned on: **close** to keep heat in.
   - otherwise: default position.
5. **Door contact open**: never close below the lockout position.

## Entities per window

| Entity | Purpose |
|---|---|
| `sensor.*_solar_gain` (W) | Heat entering through the window right now. Its attributes feed the card. |
| `sensor.*_solar_heat_today`, `*_useful_solar_heat_today`, `*_blocked_heat_today`, `*_insulation_savings_today` (kWh) | Daily energy counters. They reset at midnight. |
| `sensor.*_savings_today` | Useful heat plus insulation savings, multiplied by your price of heat. |
| `sensor.*_solar_heat_forecast_today` (kWh) | Solar heat still expected today if the window stays open. |
| `sensor.*_predicted_temperature` | Room temperature expected at the end of the look-ahead window. |
| `sensor.*_suggested_heating_setpoint` | Thermostat setpoint that makes use of the expected sun. |
| `sensor.*_room_solar_sensitivity` | Learned °C per kWh. Attributes: heat loss rate and number of samples. |
| `binary_sensor.*_ventilation_recommended` | It's cooler outside, so opening the window will help. |
| `sensor.*_target_position`, `*_reason`, `*_room_needs` | What the integration decided and why. |
| `switch.*_automation`, `select.*_strategy`, `button.*_resume_automation` | Controls |

## Sun-path card

The card is registered automatically:

```yaml
type: custom:blind-solar-card
entity: sensor.living_room_solar_gain
title: Living room   # optional
```

## Thermostat coordination

Under *Energy and prediction*, pick the room thermostat and a mode:

- **Suggest**: only the *Suggested heating setpoint* sensor is updated. Use it in your own automations.
- **Control**: the thermostat's target temperature is lowered by the warming the sun is expected to deliver, up to *Max. setpoint reduction*. The integration only touches the thermostat while it is in `heat` mode. Use *Control* on only one window per room.

## Schedule rule examples

| Goal | Rule |
|---|---|
| Closed at night (created by default) | sunset → sunrise, `close` |
| Not before 7:00 on workdays | add Mon–Fri, sunset → 07:00, `close`. Overlapping rules combine, so the blind opens at whichever is later, sunrise or 7:00. |
| Bedroom slats in the afternoon | 13:00 → 17:00, `tilt_auto` |
| Holiday mode | 00:00 → 23:59, `close`, priority 90, condition `{{ is_state('input_boolean.holiday', 'on') }}` |

## Tips

- Turn on **Simulation** (*Behaviour*) for the first few days and watch the *Reason*, *Solar gain* and *Predicted temperature* sensors.
- The room model needs about a day of temperature data before it trusts what it has learned (`trained: true`). Until then it uses sensible defaults.
- A pyranometer or weather-station irradiance sensor makes the watt figures exact. Without one, cloud cover from your weather entity is used.

## Development

```bash
pip install -r requirements_test.txt pytest-homeassistant-custom-component
pytest
```

- `engine.py` (decisions), `schedule.py` (rules) and `thermal.py` (physics and room model) are pure Python.
- `tests/ha` runs the integration inside a real Home Assistant instance. Those tests are skipped when `pytest-homeassistant-custom-component` isn't installed.

## Support

If Blind Control saves you some heating money, consider passing a little of it on:

[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-PayPal-00457C?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/anzezupi)

## License

MIT
