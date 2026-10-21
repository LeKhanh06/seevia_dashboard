"""Làn C (predict): xếp mức nguy hiểm theo THỜI GIAN TỚI HÀNH LANG (TTE) thay vì diện tích hộp.

Mỗi detection -> (X, Z) mét (`hazard_geom`) -> track Kalman (`hazard_track`) -> dự đoán quỹ đạo từng bước 0,125 s:
bước đầu tiên vật ở trong hành lang (Z < danger_z, |X| < lane_half) là TTE. Người đi chậm hơn vật tới thì TTE dài, vật
đi sượt qua không cắt hành lang thì không bíp; vật mới hiện đã sát người thì báo ngay (urgent), khỏi chờ phiếu.

Bật bằng HAZARD_LOGIC=predict (`hazard.make_tracker`). Cùng chữ ký `assess` với `HazardTracker`.
"""

import math
from dataclasses import dataclass

import config
from live import ego_motion, hazard, hazard_geom
from live.hazard_geom import Camera
from live.hazard_track import DT_DEFAULT, TrackSet

_PITCH_ALPHA = 0.1     # EMA của góc cúi
_PITCH_MAX = 0.1       # rad, kẹp góc cúi
_BLUR_MIN = 30.0       # phương sai Laplacian dưới mức này = ảnh nhoè (đang quay đầu nhanh)
_BLUR_R_SCALE = 4.0    # ảnh nhoè: nhân R lên chừng này lần, không tạo track mới
_NEAR_LEVEL1_Z = 8.0   # không cắt hành lang mà gần hơn mức này (m) -> mức 1


@dataclass
class PredictParams:
    tte_l3_s: float = 1.5
    tte_l2_s: float = 3.0
    horizon_s: float = 3.0
    new_near_m: float = 3.0
    coast: int = 3
    sigma_k: float = 0.0
    q: float = 1.0
    l3_vote: int = 2
    bypass_vote: bool = True
    down_hold: int = 2
    danger_z_m: float = 2.0
    lane_half_m: float = 0.6

    @staticmethod
    def from_config() -> "PredictParams":
        return PredictParams(
            tte_l3_s=config.HAZARD_TTE_L3_S, tte_l2_s=config.HAZARD_TTE_L2_S, horizon_s=config.HAZARD_HORIZON_S,
            new_near_m=config.HAZARD_NEW_NEAR_M, coast=config.HAZARD_COAST_FRAMES, sigma_k=config.HAZARD_SIGMA_K,
            q=config.HAZARD_KF_Q, l3_vote=config.HAZARD_L3_VOTE, bypass_vote=config.HAZARD_NEW_BYPASS_VOTE == "1",
            down_hold=config.HAZARD_DOWN_HOLD, danger_z_m=config.HAZARD_DANGER_Z_M,
            lane_half_m=config.HAZARD_LANE_HALF_M,
        )


def _tte(x: float, z: float, vx: float, vz: float, p: PredictParams, lane: float) -> float | None:
    """Bước 0,125 s đầu tiên (<= horizon) mà vật ở trong hành lang; None nếu không có. Giải kín, không lặp.

    Z_pred < danger và |X_pred| < lane là giao của hai khoảng thời gian mở; lấy bước lưới đầu tiên nằm trong giao.
    """
    lo, hi = 0.0, p.horizon_s + 1e-9
    if vz < -1e-9:
        lo = max(lo, (z - p.danger_z_m) / -vz)
    elif z >= p.danger_z_m:
        return None
    if abs(vx) < 1e-9:
        if abs(x) >= lane:
            return None
    else:
        a, b = (-lane - x) / vx, (lane - x) / vx
        lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
    t = max(math.floor(lo / DT_DEFAULT + 1e-9) + 1, 1) * DT_DEFAULT  # mở: bước đầu tiên LỚN HƠN lo, từ bước 1
    return t if t < hi else None


class PredictTracker:
    """Từ detection mỗi khung (+ mốc thời gian, chuyển động đầu) -> mức nguy hiểm cao nhất, theo quỹ đạo dự đoán."""

    def __init__(self, params: PredictParams | None = None, cam: Camera | None = None) -> None:
        self.params = params or PredictParams.from_config()
        self.cam = cam or Camera.default()
        self.tracks = TrackSet(self.params.coast, self.params.q)
        self.pitch_rad = 0.0

    def _learn_pitch(self, detections: list[hazard.Detection]) -> None:
        for det in detections:
            s = hazard_geom.pitch_sample(det, self.cam)
            if s is not None:
                s = min(max(s, -_PITCH_MAX), _PITCH_MAX)
                self.pitch_rad = min(max(self.pitch_rad + _PITCH_ALPHA * (s - self.pitch_rad), -_PITCH_MAX), _PITCH_MAX)

    def assess(
        self, detections: list[hazard.Detection], t: float | None = None, ego: "ego_motion.Ego | None" = None,
    ) -> hazard.Hazard:
        p, cam = self.params, self.cam
        ests = [hazard_geom.estimate(d, cam, self.pitch_rad) for d in detections]
        rad = shift = 0.0
        r_scale, allow_new = 1.0, True
        if ego is not None:
            shift = ego.shift_x
            rad = shift * cam.w / cam.f_px  # góc: tỉ lệ bề rộng · w / f
            if ego.blur < _BLUR_MIN:
                r_scale, allow_new = _BLUR_R_SCALE, False
        tracks = self.tracks.update(detections, ests, t, ego_shift_rad=rad, box_shift_x=shift,
                                    r_scale=r_scale, allow_new=allow_new)
        if allow_new:  # khung nhoè cho Z sai, không dạy góc cúi từ nó
            self._learn_pitch(detections)

        lane = p.lane_half_m
        by_id: dict[int, tuple[int, bool]] = {}  # id track -> (mức, urgent)
        best, best_tr, urgent = 0, None, False
        for tr in tracks:
            x, z, vx, vz = tr.x.tolist()
            lane_x = lane + p.sigma_k * tr.sigma_x()
            new_near = tr.hits == 1 and tr.misses == 0 and 0 < p.new_near_m and z < p.new_near_m and abs(x) < lane
            if new_near:
                lvl, urg = 3, True
            elif z < p.danger_z_m and abs(x) < lane:
                lvl, urg = 3, False
            else:
                tte = _tte(x, z, vx, vz, p, lane_x)
                if tte is not None and tte < p.tte_l3_s:
                    lvl = 3
                elif tte is not None and tte < p.tte_l2_s:
                    lvl = 2
                else:
                    lvl = 1 if z < _NEAR_LEVEL1_Z else 0
                urg = False
            by_id[tr.id] = (lvl, urg)
            urgent = urgent or urg
            if lvl > best:
                best, best_tr = lvl, tr
        levels = []
        for tr in self.tracks.last_matched:
            levels.append(by_id[tr.id][0] if tr is not None else 0)
        if best_tr is None:
            return hazard.Hazard(0, levels=levels)
        x = float(best_tr.x[0])
        direction = "left" if x < -lane else "right" if x > lane else "center"
        return hazard.Hazard(best, direction, best_tr.kind, levels, urgent)
