"""Tests for the pure decision engine."""

import math

from engine import (
    Action,
    BlindType,
    Climate,
    ClimateConfig,
    Engine,
    EngineConfig,
    Geometry,
    Inputs,
    Positions,
    Reason,
    RuleTarget,
    Strategy,
    TiltMode,
    angle_diff,
    beta_to_percent,
    climate_state,
    cutoff_beta,
    horizon_blocked,
    parse_horizon,
    profile_angle,
    roller_shade_position,
    sun_in_window,
)
import pytest

SOUTH = Geometry(azimuth=180, fov_left=80, fov_right=80)


def test_angle_diff_wraps():
    assert angle_diff(10, 350) == 20
    assert angle_diff(350, 10) == -20
    assert angle_diff(180, 180) == 0


@pytest.mark.parametrize(
    ("az", "el", "expected"),
    [
        (180, 30, True),
        (100, 30, True),  # 80° left, edge of field of view
        (95, 30, False),
        (180, -1, False),  # below horizon
        (0, 30, False),  # behind the house
    ],
)
def test_sun_in_window(az, el, expected):
    assert sun_in_window(SOUTH, az, el) is expected


def test_max_elevation_overhang():
    geo = Geometry(azimuth=180, max_elevation=50)
    assert sun_in_window(geo, 180, 49)
    assert not sun_in_window(geo, 180, 55)


def test_horizon_profile():
    horizon = parse_horizon("150-200:20, garbage, 350-10:5")
    assert horizon == ((150, 200, 20), (350, 10, 5))
    assert horizon_blocked(horizon, 180, 15)
    assert not horizon_blocked(horizon, 180, 25)
    assert horizon_blocked(horizon, 5, 3)  # range wrapping north
    geo = Geometry(azimuth=180, horizon=horizon)
    assert not sun_in_window(geo, 180, 15)


def test_profile_angle():
    assert profile_angle(SOUTH, 180, 30) == pytest.approx(30)
    # Oblique sun: profile angle is steeper than elevation.
    assert profile_angle(SOUTH, 240, 30) > 30


def test_roller_shade_position():
    geo = Geometry(window_height=1.5, sill_height=0.9, shade_depth=0.5)
    # Low sun: must close almost fully.
    assert roller_shade_position(geo, 10) == 0
    # High sun: 0.5 * tan(75°) = 1.87 m -> (1.87-0.9)/1.5 = 64 %
    assert roller_shade_position(geo, 75) == pytest.approx((0.5 * math.tan(math.radians(75)) - 0.9) / 1.5 * 100)


def test_cutoff_beta_blocks_direct_sun():
    geo = Geometry(slat_width=80, slat_spacing=70)
    for gamma in (5, 20, 40, 60):
        beta = cutoff_beta(geo, gamma)
        # verify the defining equation sin(beta + gamma) = s/w cos(gamma)
        assert math.sin(math.radians(beta + gamma)) == pytest.approx(70 / 80 * math.cos(math.radians(gamma)))
    # Higher sun -> slats can be more open.
    assert cutoff_beta(geo, 60) < cutoff_beta(geo, 10)


def test_beta_to_percent():
    assert beta_to_percent(0, TiltMode.QUARTER) == 100
    assert beta_to_percent(90, TiltMode.QUARTER) == 0
    assert beta_to_percent(-30, TiltMode.QUARTER) == 100  # clamped
    assert beta_to_percent(0, TiltMode.HALF) == 50
    assert beta_to_percent(90, TiltMode.HALF) == 0
    assert beta_to_percent(90, TiltMode.HALF, invert=True) == 100


def test_climate_hysteresis():
    cfg = ClimateConfig(heat_setpoint=21, cool_setpoint=24.5, hysteresis=0.5)
    assert climate_state(Climate.NEUTRAL, 20.8, None, cfg) is Climate.HEAT
    assert climate_state(Climate.HEAT, 21.3, None, cfg) is Climate.HEAT  # still within hysteresis
    assert climate_state(Climate.HEAT, 21.6, None, cfg) is Climate.NEUTRAL
    assert climate_state(Climate.NEUTRAL, 25, None, cfg) is Climate.COOL
    assert climate_state(Climate.COOL, 24.2, None, cfg) is Climate.COOL
    assert climate_state(Climate.NEUTRAL, 22, 30, cfg) is Climate.COOL  # hot day pre-emptive
    assert climate_state(Climate.NEUTRAL, None, 5, cfg) is Climate.HEAT
    assert climate_state(Climate.NEUTRAL, None, None, cfg) is Climate.NEUTRAL


def _engine(bt=BlindType.ROLLER_BLIND, **kw) -> Engine:
    return Engine(EngineConfig(blind_type=bt, geometry=SOUTH, **kw))


def test_heat_gain_opens():
    d = _engine().decide(Inputs(180, 20, climate=Climate.HEAT))
    assert (d.position, d.reason) == (100, Reason.HEAT_GAIN)


def test_cooling_shades_roller():
    d = _engine().decide(Inputs(180, 20, climate=Climate.COOL))
    assert d.reason is Reason.COOLING_SHADE
    assert d.position is not None and d.position < 100


def test_no_sun_default():
    d = _engine().decide(Inputs(0, 20, climate=Climate.COOL))
    assert (d.position, d.reason) == (100, Reason.NO_SUN)


def test_cloudy_is_no_sun():
    d = _engine().decide(Inputs(180, 20, climate=Climate.COOL, bright=False))
    assert d.reason is Reason.NO_SUN


def test_away_closes_fully():
    d = _engine().decide(Inputs(180, 20, climate=Climate.COOL, present=False))
    assert (d.position, d.reason) == (0, Reason.AWAY_SHADE)


def test_venetian_shade_uses_tilt():
    eng = _engine(BlindType.VENETIAN, tilt_mode=TiltMode.QUARTER)
    d = eng.decide(Inputs(180, 30, climate=Climate.COOL))
    assert d.position == 0
    assert d.tilt is not None and 0 < d.tilt < 100


def test_tilt_only_has_no_position():
    eng = _engine(BlindType.TILT_ONLY, tilt_mode=TiltMode.HALF)
    d = eng.decide(Inputs(180, 30, climate=Climate.HEAT))
    assert d.position is None
    assert d.tilt is not None and d.tilt > 50  # slats tilted toward the sun rays


def test_strategy_overrides_climate():
    d = _engine().decide(Inputs(180, 20, climate=Climate.HEAT, strategy=Strategy.COOLING))
    assert d.reason is Reason.COOLING_SHADE
    d = _engine().decide(Inputs(180, 20, climate=Climate.COOL, strategy=Strategy.OFF))
    assert d.reason is Reason.STRATEGY_OFF


def test_priorities():
    eng = _engine(positions=Positions(safety_position=100))
    rule = RuleTarget(Action.CLOSE, name="Night")
    assert eng.decide(Inputs(180, 20, safety=True, rule=rule)).reason is Reason.SAFETY
    assert eng.decide(Inputs(180, 20, override=True, rule=rule)).position is None
    d = eng.decide(Inputs(180, 20, rule=rule))
    assert (d.position, d.reason, d.rule) == (0, Reason.RULE_CLOSE, "Night")


def test_lockout_keeps_door_open():
    d = _engine().decide(Inputs(180, 20, lockout=True, rule=RuleTarget(Action.CLOSE)))
    assert d.position == 100 and d.lockout and d.forced


def test_rule_skip_and_tilt_auto():
    eng = _engine(BlindType.VENETIAN, tilt_mode=TiltMode.QUARTER)
    assert eng.decide(Inputs(180, 20, rule=RuleTarget(Action.SKIP))).position is None
    d = eng.decide(Inputs(180, 30, climate=Climate.COOL, rule=RuleTarget(Action.TILT_AUTO)))
    assert d.position == 0 and d.tilt is not None


def test_awning_extends_for_shade():
    eng = _engine(
        BlindType.AWNING,
        positions=Positions(default_position=0, heat_position=0, shade_position=100),
    )
    assert eng.decide(Inputs(180, 40, climate=Climate.COOL)).position == 100
    assert eng.decide(Inputs(180, 40, climate=Climate.HEAT)).position == 0
