"""OpenCV chessboard calibration, independent of Qt, FAZE and camera acquisition.

The caller supplies *real*, unmirrored frames from one fixed camera mode.  Units
of the printed square size affect board pose translations, not focal pixels.
Low reprojection error is a camera-calibration diagnostic, not gaze accuracy.

API references:
https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html
https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable

import cv2
import numpy as np

Progress = Callable[[int, str], None]


@dataclass(frozen=True)
class BoardSpec:
    """Number of *inner corners*, not number of black/white squares."""

    columns: int = 9
    rows: int = 6
    square_mm: float = 20.0

    def __post_init__(self):
        for name in ('columns', 'rows'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 3 <= value <= 30:
                raise ValueError('棋盘格内角点的行列数应为3～30的整数')
        if isinstance(self.square_mm, bool) or not math.isfinite(self.square_mm) or not 1 <= self.square_mm <= 100:
            raise ValueError('棋盘格每格实测边长应在1～100mm之间')

    @property
    def pattern_size(self) -> tuple[int, int]:
        return self.columns, self.rows

    @property
    def corner_count(self) -> int:
        return self.columns * self.rows

    def object_points(self) -> np.ndarray:
        points = np.zeros((self.corner_count, 3), np.float32)
        points[:, :2] = np.mgrid[0:self.columns, 0:self.rows].T.reshape(-1, 2)
        points[:, :2] *= self.square_mm
        return points


def _resolution(value) -> tuple[int, int]:
    if not isinstance(value, (tuple, list, np.ndarray)) or len(value) != 2:
        raise ValueError('图像分辨率必须为[宽, 高]')
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, np.integer)) or v < 64 for v in value):
        raise ValueError('图像分辨率必须是大于等于64的整数')
    return int(value[0]), int(value[1])


def _corners(value, resolution, spec: BoardSpec) -> np.ndarray:
    w, h = _resolution(resolution)
    points = np.asarray(value, dtype=np.float32)
    if points.shape not in ((spec.corner_count, 2), (spec.corner_count, 1, 2)):
        raise ValueError(f'需要{spec.corner_count}个棋盘格内角点')
    points = points.reshape(-1, 1, 2)
    if not np.isfinite(points).all():
        raise ValueError('棋盘格角点包含非有限值')
    xy = points[:, 0]
    if (xy[:, 0] < 0).any() or (xy[:, 0] >= w).any() or (xy[:, 1] < 0).any() or (xy[:, 1] >= h).any():
        raise ValueError('棋盘格角点超出图像范围，请拍摄完整棋盘格')
    if abs(cv2.contourArea(cv2.convexHull(points))) < 100:
        raise ValueError('棋盘格过小或角点退化，请让棋盘格占据更大画面')
    return points.copy()


def detect_board(frame: np.ndarray, spec: BoardSpec | None = None) -> np.ndarray | None:
    """Return subpixel N×1×2 float32 corners, or None for an incomplete board.

    Frames are not flipped or resized. An SB detector is preferred where the
    installed OpenCV provides it; the classic detector refines to subpixels.
    """
    spec = BoardSpec() if spec is None else spec
    if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
        raise ValueError('相机画面为空')
    if frame.dtype != np.uint8:
        raise ValueError('标定画面必须为8位图像')
    if frame.ndim == 2:
        gray = frame
    elif frame.ndim == 3 and frame.shape[2] in (3, 4):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY if frame.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
    else:
        raise ValueError('相机画面格式无效')
    found = False
    corners = None
    if hasattr(cv2, 'findChessboardCornersSB'):
        found, corners = cv2.findChessboardCornersSB(gray, spec.pattern_size, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not found:
        flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_FAST_CHECK
        found, corners = cv2.findChessboardCorners(gray, spec.pattern_size, flags=flags)
        if found:
            corners = cv2.cornerSubPix(gray, corners, (5, 5), (-1, -1),
                                      (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 40, .001))
    if not found or corners is None:
        return None
    return _corners(corners, (gray.shape[1], gray.shape[0]), spec)


def view_descriptor(corners, resolution) -> np.ndarray:
    """Normalized image coordinates used to compare views, without estimated K."""
    w, h = _resolution(resolution)
    points = np.asarray(corners, np.float32).reshape(-1, 2)
    if not np.isfinite(points).all() or len(points) < 4:
        raise ValueError('无法描述无效棋盘格角点')
    return (points / np.array([w, h], np.float32)).ravel()


def _view_difference(first, second) -> float:
    a = np.asarray(first).reshape(-1, 2)
    b = np.asarray(second).reshape(-1, 2)
    # A checkerboard detector can choose the opposite 180-degree ordering.
    return min(float(np.sqrt(np.mean(np.sum((a - b) ** 2, axis=1)))),
               float(np.sqrt(np.mean(np.sum((a - b[::-1]) ** 2, axis=1)))))


class Calibrator:
    """Accumulate diverse views at one camera resolution; duplicate is False."""

    def __init__(self, spec: BoardSpec | None = None, min_views: int = 15):
        self.spec = BoardSpec() if spec is None else spec
        if isinstance(min_views, bool) or not isinstance(min_views, int) or min_views < 15:
            raise ValueError('相机标定至少需要15幅有效图像')
        self.min_views = min_views
        self.views: list[np.ndarray] = []
        self.resolution: tuple[int, int] | None = None

    def add_view(self, corners, resolution) -> bool:
        resolution = _resolution(resolution)
        if self.resolution is not None and self.resolution != resolution:
            raise ValueError('标定过程中分辨率发生变化，请清空样本后重新采集')
        points = _corners(corners, resolution, self.spec)
        descriptor = view_descriptor(points, resolution)
        for previous in self.views:
            if _view_difference(descriptor, view_descriptor(previous, resolution)) < .012:
                return False
        self.resolution = resolution
        self.views.append(points)
        return True

    def clear(self):
        self.views.clear()
        self.resolution = None

    def calibrate(self, on_progress: Progress | None = None) -> dict:
        return calibrate_views(self.views, self.resolution, self.spec, on_progress=on_progress,
                               min_views=self.min_views)


def _constraint(h, i, j):
    a, b = h[:, i], h[:, j]
    return np.array([a[0] * b[0], a[0] * b[1] + a[1] * b[0], a[1] * b[1],
                     a[2] * b[0] + a[0] * b[2], a[2] * b[1] + a[1] * b[2], a[2] * b[2]])


def _diversity(views, resolution, spec):
    """Reject fronto-parallel views using Zhang's intrinsic constraints.

    Translation/in-plane rotation/scale alone do not identify all intrinsics.
    Coordinates use one common scale on both axes to preserve orthogonality.
    """
    w, h = resolution
    objects = spec.object_points()[:, :2] / (max(spec.columns - 1, spec.rows - 1) * spec.square_mm)
    constraints = []
    all_points = []
    for view in views:
        xy = view[:, 0].astype(np.float64)
        H, _ = cv2.findHomography(objects, xy / w, method=0)
        if H is None or not np.isfinite(H).all():
            raise ValueError('棋盘格几何退化，请重新拍摄清晰完整的棋盘格')
        for vector in (_constraint(H, 0, 1), _constraint(H, 0, 0) - _constraint(H, 1, 1)):
            norm = np.linalg.norm(vector)
            if norm < 1e-12:
                raise ValueError('棋盘格视角退化')
            constraints.append(vector / norm)
        all_points.append(xy)
    singular = np.linalg.svd(np.asarray(constraints), compute_uv=False)
    strength = float(singular[4] / singular[0])
    points = np.concatenate(all_points).astype(np.float32)
    coverage = float(abs(cv2.contourArea(cv2.convexHull(points))) / (w * h))
    centres = np.array([v[:, 0].mean(axis=0) / [w, h] for v in views])
    centre_span = np.ptp(centres, axis=0)
    if strength < .003:
        raise ValueError('棋盘格倾斜角度不足，无法稳定求内参；请增加左右和上下倾斜，不要只平移或改变距离')
    if coverage < .10 or max(centre_span) < .10:
        raise ValueError('棋盘格覆盖位置不足；请移到画面中央、四角并改变距离后补拍')
    return dict(image_coverage_fraction=coverage, centre_span_fraction=centre_span.tolist(),
                intrinsic_constraint_strength=strength)


def _fit(views, resolution, spec):
    objects = [spec.object_points() for _ in views]
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 150, 1e-9)
    # flags=0 estimates exactly the standard k1,k2,p1,p2,k3 pinhole model;
    # no rational/thin-prism coefficients or assumed focal pixels are injected.
    result = cv2.calibrateCameraExtended(objects, views, resolution, None, None,
                                         flags=0, criteria=criteria)
    rms, matrix, distortion, rvecs, tvecs, std_intrinsics, _, per_view = result
    return dict(rms=float(rms), matrix=matrix, distortion=distortion.reshape(-1),
                rvecs=rvecs, tvecs=tvecs, std=std_intrinsics.reshape(-1),
                per_view=np.asarray(per_view).reshape(-1))


def _check_fit(fit, resolution):
    w, h = resolution
    K, dist = fit['matrix'], fit['distortion']
    if not np.isfinite(K).all() or not np.isfinite(dist).all() or not math.isfinite(fit['rms']):
        raise ValueError('内参计算未收敛，请改善棋盘格清晰度和拍摄角度')
    if K[0, 0] <= 0 or K[1, 1] <= 0 or not .5 <= K[0, 0] / K[1, 1] <= 2:
        raise ValueError('求得的相机焦距不可信，请增加不同倾斜视角后重试')
    fov = 2 * np.degrees(np.arctan(w / (2 * K[0, 0])))
    if not 10 <= fov <= 150 or not -.25 * w <= K[0, 2] <= 1.25 * w or not -.25 * h <= K[1, 2] <= 1.25 * h:
        raise ValueError('求得的相机内参明显异常，请检查角点数、图像模式和样本多样性')
    if fit['rms'] > 2:
        raise ValueError(f'重投影RMS过大（{fit["rms"]:.2f}px），请排除模糊、反光、弯曲棋盘格后重拍')
    if any(float(t[2, 0]) <= 0 for t in fit['tvecs']):
        raise ValueError('棋盘格姿态解不可信，请重新采集不同角度图像')
    return float(fov)


def _holdout_errors(views, indices, spec, matrix, distortion):
    objects = spec.object_points()
    xy = np.mgrid[0:spec.columns, 0:spec.rows].T.reshape(-1, 2)
    pose_mask = (xy.sum(axis=1) % 2) == 0
    errors = []
    for index in indices:
        view = views[index]
        ok, rvec, tvec = cv2.solvePnP(objects[pose_mask], view[pose_mask], matrix, distortion,
                                    flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok or tvec[2, 0] <= 0:
            raise ValueError('留出图像的棋盘格姿态求解失败，请重新采集')
        predicted = cv2.projectPoints(objects[~pose_mask], rvec, tvec, matrix, distortion)[0]
        residual = predicted.reshape(-1, 2) - view[~pose_mask].reshape(-1, 2)
        errors.append(float(np.sqrt(np.mean(np.sum(residual ** 2, axis=1)))))
    return errors


def calibrate_views(image_points, resolution, spec: BoardSpec | None = None,
                    on_progress: Progress | None = None, min_views: int = 15) -> dict:
    """Estimate standard pinhole intrinsics and validate on withheld views.

    Diagnostic hold-out fitting withholds every fifth image from K estimation.
    On each withheld image, half the corners estimate board pose and the other
    half measure residuals. After validation, the returned K is refit on *all*
    images. This checks camera geometry, never eye-gaze accuracy.
    """
    spec = BoardSpec() if spec is None else spec
    if len(image_points) < max(15, min_views):
        raise ValueError(f'至少需要{max(15, min_views)}幅有效且不同角度的棋盘格图像')
    resolution = _resolution(resolution)
    views = [_corners(v, resolution, spec) for v in image_points]
    def emit(percent, message):
        if on_progress is not None:
            on_progress(int(percent), message)
    emit(0, '检查棋盘格覆盖范围和倾斜角度…')
    diversity = _diversity(views, resolution, spec)
    test_indices = list(range(4, len(views), 5))
    train_indices = [i for i in range(len(views)) if i not in test_indices]
    emit(10, f'使用{len(train_indices)}幅图像估计验证用内参…')
    try:
        held_fit = _fit([views[i] for i in train_indices], resolution, spec)
        _check_fit(held_fit, resolution)
        emit(45, f'在{len(test_indices)}幅留出图像上检查独立角点…')
        held_errors = _holdout_errors(views, test_indices, spec, held_fit['matrix'], held_fit['distortion'])
        held_rms = float(np.sqrt(np.mean(np.asarray(held_errors) ** 2)))
        if not math.isfinite(held_rms) or held_rms > 3:
            raise ValueError(f'留出图像重投影RMS过大（{held_rms:.2f}px），请补充清晰、多角度样本')
        emit(60, f'使用全部{len(views)}幅图像重新估计最终内参…')
        fit = _fit(views, resolution, spec)
        fov = _check_fit(fit, resolution)
    except cv2.error as error:
        raise ValueError('OpenCV无法完成标定，请检查棋盘格尺寸和图像质量：' + str(error).splitlines()[0]) from error
    emit(90, '检查焦距稳定性并整理标定结果…')
    change = float(np.max(abs(np.diag(fit['matrix'])[:2] / np.diag(held_fit['matrix'])[:2] - 1)))
    if change > .25:
        raise ValueError('补入留出图像后焦距变化超过25%，标定尚不稳定，请增加位置与倾斜变化')
    warnings = []
    if fit['rms'] > .75:
        warnings.append('全样本重投影RMS高于0.75px，建议检查清晰度、反光和打印平整度')
    if held_rms > 1:
        warnings.append('留出图像RMS高于1px，建议增加不同位置和倾斜角度的样本')
    if diversity['image_coverage_fraction'] < .40:
        warnings.append('角点覆盖不到画面的40%，边缘畸变约束可能不足，建议补拍画面四周')
    if change > .10:
        warnings.append('验证用内参与全样本内参焦距差异超过10%，建议补拍提高稳定性')
    if np.max(abs(fit['distortion'])) > 5:
        warnings.append('部分畸变系数较大，请用未参与标定的照片检查去畸变效果')
    std = [float(v) if math.isfinite(v) else None for v in fit['std']]
    result = dict(
        schema_version=1, method='opencv_pinhole_chessboard_5coeff',
        camera_matrix=fit['matrix'].tolist(), distortion=fit['distortion'].tolist(),
        intrinsics_resolution=list(resolution),
        board=dict(columns=spec.columns, rows=spec.rows, square_mm=float(spec.square_mm),
                   count_type='inner_corners'),
        view_count=len(views), rms_px=fit['rms'], per_view_rms_px=fit['per_view'].tolist(),
        holdout_rms_px=held_rms, holdout_per_view_rms_px=held_errors,
        holdout_image_indices=test_indices,
        validation=dict(training_view_count=len(train_indices), test_view_count=len(test_indices),
                        method='Withhold every fifth view; estimate each test pose from checkerboard-parity half of corners; score other half',
                        final_matrix_uses_all_views=True, focal_relative_change_after_refit=change),
        intrinsic_std_deviations=std,
        quality=dict(status='review_recommended' if warnings else 'passed_camera_checks',
                     warnings=warnings, approximate_horizontal_fov_deg=fov, **diversity),
        note='Reprojection errors measure chessboard camera geometry, not gaze accuracy. Keep the camera, resolution, focus and crop mode fixed.')
    emit(100, f'相机标定完成：RMS {fit["rms"]:.3f}px，留出检查 {held_rms:.3f}px')
    return result
