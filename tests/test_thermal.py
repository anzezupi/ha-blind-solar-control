"""Tests for the thermal model."""

import random

from engine import BlindType, Geometry, TiltMode, beta_to_percent
import pytest
from thermal import (
    RoomModel,
    Window,
    erbs,
    ghi_clear,
    ghi_cloudy,
    incidence_modifier,
    open_fraction,
    percent_to_beta,
    setpoint_suggestion,
    window_irradiance,
)

SOUTH = Geometry(azimuth=180)


def test_clear_sky_is_plausible():
    assert ghi_clear(-5) == 0
    assert 700 < ghi_clear(60) < 1000  # summer noon in central Europe
    assert ghi_clear(20) < ghi_clear(40)


def test_clouds_reduce_irradiance():
    assert ghi_cloudy(40, 0) == pytest.approx(ghi_clear(40))
    assert ghi_cloudy(40, 100) == pytest.approx(ghi_clear(40) * 0.25)


def test_erbs_split_is_consistent():
    ghi = ghi_clear(40)
    _, dhi = erbs(ghi, 40)
    assert dhi < ghi * 0.3  # clear sky: mostly beam
    _, dhi_overcast = erbs(ghi_cloudy(40, 100), 40)
    assert dhi_overcast == pytest.approx(ghi_cloudy(40, 100), rel=0.15)  # overcast: mostly diffuse


def test_winter_sun_hits_south_window_hard():
    # Low winter sun hits a south facade almost head-on.
    winter = window_irradiance(SOUTH, 180, 20, ghi_clear(20))
    summer = window_irradiance(SOUTH, 180, 65, ghi_clear(65))
    assert winter.beam > summer.beam


def test_sun_behind_window_gives_only_diffuse():
    irr = window_irradiance(SOUTH, 0, 30, ghi_clear(30))
    assert irr.beam == 0
    assert irr.diffuse > 0


def test_incidence_modifier():
    assert incidence_modifier(1.0) == 1.0
    assert incidence_modifier(0.5) == pytest.approx(0.9)
    assert incidence_modifier(0.01) == 0


def test_window_gain_and_shading():
    win = Window.build(1.2, 1.5, "double", BlindType.ROLLER_SHUTTER, exterior=True)
    irr = window_irradiance(SOUTH, 180, 25, ghi_clear(25))
    open_gain = win.gain(irr)
    assert 400 < open_gain < 1200  # a sunny winter window really is a heater
    assert win.gain(irr, 0.0) == pytest.approx(open_gain * 0.05)


def test_interior_blind_lets_more_heat_in_than_exterior():
    irr = window_irradiance(SOUTH, 180, 50, ghi_clear(50))
    inside = Window.build(1.2, 1.5, "double", BlindType.ROLLER_BLIND, exterior=False)
    outside = Window.build(1.2, 1.5, "double", BlindType.ROLLER_BLIND, exterior=True)
    assert inside.gain(irr, 0.0) > 3 * outside.gain(irr, 0.0)


def test_custom_g_and_u_values():
    win = Window.build(1, 1, "double", BlindType.ROLLER_BLIND, False, g_value=0.3, u_value=0.8)
    assert (win.g_value, win.u_value) == (0.3, 0.8)


def test_insulation_saving():
    win = Window.build(1.2, 1.5, "double_old", BlindType.ROLLER_SHUTTER, exterior=True)
    assert win.insulation_saving(21, 0, 1.0) > 15  # W
    assert win.insulation_saving(21, 25, 1.0) == 0
    assert win.insulation_saving(21, 0, 0.0) == 0


@pytest.mark.parametrize("mode", [TiltMode.QUARTER, TiltMode.HALF])
def test_percent_to_beta_inverts(mode):
    for beta in (0, 30, 60, 90):
        assert percent_to_beta(beta_to_percent(beta, mode), mode) == pytest.approx(beta, abs=1)


def test_open_fraction():
    assert open_fraction(BlindType.ROLLER_BLIND, 40, None, TiltMode.NONE) == 0.4
    assert open_fraction(BlindType.AWNING, 100, None, TiltMode.NONE) == 0.0
    # Venetian lowered with horizontal slats is mostly open.
    assert open_fraction(BlindType.VENETIAN, 0, 100, TiltMode.QUARTER) == pytest.approx(1.0)
    assert open_fraction(BlindType.VENETIAN, 0, 0, TiltMode.QUARTER) == pytest.approx(0.0)


def test_room_model_learns_sensitivity():
    rng = random.Random(1)
    true_a, true_b, true_c = 1.2, 0.08, 0.3
    model = RoomModel()
    t_in = 20.0
    for _ in range(400):
        energy = max(0.0, rng.uniform(-0.1, 0.4))
        t_out = rng.uniform(-5, 15)
        dt = 0.5
        nxt = t_in + true_a * energy + true_b * (t_out - t_in) * dt + true_c * dt + rng.gauss(0, 0.02)
        model.update(energy, t_in, nxt, t_out, dt)
        t_in = min(26.0, max(17.0, nxt))
    assert model.trained
    assert model.sensitivity == pytest.approx(true_a, rel=0.15)
    assert model.loss_rate == pytest.approx(true_b, rel=0.3)


def test_room_model_roundtrip():
    model = RoomModel()
    model.update(0.2, 20, 20.3, 5, 0.5)
    restored = RoomModel.from_dict(model.as_dict())
    assert restored.theta == model.theta and restored.samples == 1
    assert RoomModel.from_dict({"broken": 1}).samples == 0


def test_untrained_model_uses_defaults_for_prediction():
    model = RoomModel()
    assert model.predict(22, 22, 2.0, 3) == pytest.approx(22 + 0.6 * 2.0)


def test_setpoint_suggestion():
    assert setpoint_suggestion(21, 0, 0.6, 1.5) == 21
    assert setpoint_suggestion(21, 1.0, 0.6, 1.5) == 20.5
    assert setpoint_suggestion(21, 10, 0.6, 1.5) == 19.5  # capped
