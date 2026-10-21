"""HAZARD_LOGIC=predict: hình học ước lượng khoảng cách, track Kalman, xếp mức theo TTE."""

import importlib
import json
import math
import os

import numpy as np
import pytest

import config
from live import hazard
from live.hazard import Detection
from live import hazard_geom
from live.hazard_geom import Camera, CLASS_HEIGHT_M

CLASSES_VH = "/workingspace_aiclub/WorkingSpace/Personal/vannk/SeeVia_system/evaluations/train/classes_vh.json"

CAM = Camera(320, 240, 66.0, 1.6)


def _proj(X, Z, H, W, cam=CAM, pitch=0.0, base=0.0):
    """Chiếu pin-hole giống `bench/hazard_scenarios.Camera.project` (hộp tỉ lệ, đã cắt biên).

    `base` = độ cao đáy vật so với mặt đất; `pitch` đúng quy ước của `hazard_geom` (dương = cảnh trượt lên).
    """
    f = cam.f_px
    x0 = cam.w / 2 + f * (X - W / 2) / Z
    x1 = cam.w / 2 + f * (X + W / 2) / Z
    y_bot = cam.h / 2 + f * ((cam.height_m - base) / Z - math.tan(pitch))
    y_top = cam.h / 2 + f * ((cam.height_m - base - H) / Z - math.tan(pitch))
    c = lambda v, hi: min(max(v, 0.0), hi)
    return (c(x0, cam.w) / cam.w, c(y_top, cam.h) / cam.h, c(x1, cam.w) / cam.w, c(y_bot, cam.h) / cam.h)


def _person(X, Z, pitch=0.0):
    return Detection("person", "person", 0.9, _proj(X, Z, 1.65, 0.5, pitch=pitch))


# ---- Task 1: hình học ----

# Z = 3 m không dùng được: cam cao 1,6 m, VFOV ~52° thì chân người ở Z < 3,3 m đã chạm mép dưới (xem test sau).
@pytest.mark.parametrize("Z", [4.0, 6.0, 10.0])
def test_estimate_height_source_recovers_distance(Z):
    e = hazard_geom.estimate(_person(0.3, Z), CAM)
    assert abs(e.z_m - Z) / Z < 0.05
    assert abs(e.x_m - 0.3) < 0.1


def test_estimate_bottom_clipped_is_near():
    e = hazard_geom.estimate(_person(0.0, 1.0), CAM)
    assert e.z_m < 1.5
    assert e.source != "height"


def test_estimate_bottom_clipped_never_farther_than_truth():
    for Z in (1.0, 2.0, 3.0):  # chân bị cắt: Z kẹp <= 1,5 m, luôn gần hơn hoặc bằng thật (thiên về an toàn)
        e = hazard_geom.estimate(_person(0.0, Z), CAM)
        assert e.z_m <= max(Z, 1.5) and e.source == "near"


def test_estimate_unknown_kind_finite():
    det = Detection("xyz", "static", 0.5, _proj(0.0, 4.0, 1.0, 0.5))
    e = hazard_geom.estimate(det, CAM)
    assert math.isfinite(e.z_m) and abs(e.z_m - 4) < 1.0
    assert math.isfinite(e.z_sigma) and e.z_sigma > 0


def test_estimate_pitch_shifts_ground_only():
    ground = Detection("xyz", "static", 0.5, _proj(0.0, 4.0, 1.0, 0.5))  # lớp lạ: chỉ có nguồn ground
    a, b = hazard_geom.estimate(ground, CAM, 0.0), hazard_geom.estimate(ground, CAM, 0.03)
    assert a.source == b.source == "ground" and abs(a.z_m - b.z_m) > 0.05
    sign = Detection("stop_sign", "static", 0.9, _proj(0.0, 8.0, 0.75, 0.75, base=1.8))  # đáy trên đường chân trời
    a, b = hazard_geom.estimate(sign, CAM, 0.0), hazard_geom.estimate(sign, CAM, 0.03)
    assert a.source == b.source == "height" and a.z_m == b.z_m


def test_classes_json_overrides_coco(monkeypatch):
    if not os.path.exists(CLASSES_VH):
        pytest.skip("không có classes_vh.json")
    try:
        monkeypatch.setattr(config, "HAZARD_CLASSES_JSON", CLASSES_VH)
        importlib.reload(hazard)
        assert hazard._CLASSES[11] == ("pole", "static")
        monkeypatch.setattr(config, "HAZARD_CLASSES_JSON", "")
        importlib.reload(hazard)
        assert hazard._CLASSES[0] == ("person", "person")
    finally:
        monkeypatch.undo()
        importlib.reload(hazard)


def test_class_table_covers_all_kinds():
    kinds = {k for k, _ in hazard._CLASSES.values()}
    if os.path.exists(CLASSES_VH):
        kinds |= {v[0] for v in json.load(open(CLASSES_VH)).values()}
    assert not kinds - set(CLASS_HEIGHT_M)
    assert set(hazard_geom.CLASS_HEIGHT_REL_SIGMA) <= set(CLASS_HEIGHT_M)


# ---- Task 2: track + Kalman ----

from live import hazard_track
from live.hazard_geom import Estimate

DT = 0.125


def _obs(X, Z):
    det = _person(X, Z)
    return det, hazard_geom.estimate(det, CAM)


def _step(ts, t, objs, **kw):
    pairs = [_obs(X, Z) for X, Z in objs]
    return ts.update([d for d, _ in pairs], [e for _, e in pairs], t, **kw)


def test_track_velocity_converges():
    ts = hazard_track.TrackSet(coast=3, q=1.0)
    for k in range(16):
        tracks = _step(ts, k * DT, [(0.0, 12.0 - 1.2 * k * DT)])
    assert len(tracks) == 1 and abs(tracks[0].x[3] + 1.2) < 0.3


def test_track_dt_nonpositive_safe():
    ts = hazard_track.TrackSet(coast=3, q=1.0)
    for t in (1.0, 1.0, 0.5, None, 0.5, 100.0):  # trùng, lùi, None, nhảy xa
        tracks = _step(ts, t, [(0.0, 8.0)])
        for tr in tracks:
            assert np.isfinite(tr.x).all() and np.isfinite(tr.P).all()
    assert len(tracks) == 1


def test_track_coast_then_drop():
    ts = hazard_track.TrackSet(coast=3, q=1.0)
    for k in range(5):
        tracks = _step(ts, k * DT, [(0.0, 12.0 - 2.0 * k * DT)])
    old_id = tracks[0].id
    assert abs(tracks[0].x[3]) > 0.5  # đã học được vận tốc
    for k in range(5, 9):
        tracks = _step(ts, k * DT, [])
        assert len(tracks) == (1 if k < 8 else 0)  # coast 3 khung rồi xoá ở khung mất thứ 4
    tracks = _step(ts, 9 * DT, [(0.0, 10.0)])
    assert len(tracks) == 1 and tracks[0].id != old_id
    assert abs(tracks[0].x[3]) < 1e-6 and tracks[0].hits == 1


def test_track_two_objects_keep_ids():
    ts = hazard_track.TrackSet(coast=3, q=1.0)
    ids = None
    for k in range(10):
        tracks = _step(ts, k * DT, [(-1.0, 12.0 - 1.2 * k * DT), (1.0, 12.0 - 1.2 * k * DT)])
        cur = {round(tr.x[0]): tr.id for tr in tracks}
        assert len(tracks) == 2
        ids = ids or cur
        assert cur == ids


def test_ego_shift_removed():
    def run(shift):
        ts = hazard_track.TrackSet(coast=3, q=1.0)
        det = Detection("person", "person", 0.9, (0.4, 0.2, 0.6, 0.8))
        for k in range(20):
            tr, = ts.update([det], [Estimate(5.0, 5 * 0.04 * k, 0.3, "height")], k * DT, ego_shift_rad=shift)
        return tr
    assert abs(run(0.04).x[2]) < 0.3
    assert abs(run(0.0).x[2]) > 1.0  # đối chứng âm: không bù thì vật đứng yên bị đọc thành đang trượt ngang
