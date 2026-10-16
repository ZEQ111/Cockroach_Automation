# -*- coding: utf-8 -*-
"""由已知XY步数与4K图像配准，计算像素位移和机械步数的二维转换矩阵。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from abdomen_spot_detector import imread_unicode, locate_abdomen_window


def load_result_center(path: Path) -> np.ndarray:
    payload = json.loads(path.read_text(encoding="utf-8"))
    points = np.asarray(payload["points"], dtype=np.float64)
    return points.mean(axis=0)


def register_target_plane(reference: np.ndarray, sample: np.ndarray, point: np.ndarray) -> dict:
    """只用参考图的腹部区域做SIFT配准，避免机架远近平面干扰。"""
    _, _, (x, y, w, h) = locate_abdomen_window(reference)
    pad = 80
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(reference.shape[1], x + w + pad), min(reference.shape[0], y + h + pad)
    mask = np.zeros(reference.shape[:2], np.uint8)
    mask[y0:y1, x0:x1] = 255
    ref_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    sample_gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=5000, contrastThreshold=0.025)
    key_ref, desc_ref = sift.detectAndCompute(ref_gray, mask)
    key_sample, desc_sample = sift.detectAndCompute(sample_gray, None)
    if desc_ref is None or desc_sample is None:
        raise ValueError("图像特征不足，无法标定")
    matches = cv2.BFMatcher(cv2.NORM_L2).knnMatch(desc_ref, desc_sample, k=2)
    good = [first for first, second in matches if first.distance < 0.72 * second.distance]
    if len(good) < 12:
        raise ValueError(f"可靠匹配点不足: {len(good)}")
    src = np.float32([key_ref[item.queryIdx].pt for item in good])
    dst = np.float32([key_sample[item.trainIdx].pt for item in good])
    matrix, inlier_mask = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0,
        maxIters=5000, confidence=0.999, refineIters=20,
    )
    if matrix is None or inlier_mask is None:
        raise ValueError("RANSAC配准失败")
    inliers = inlier_mask.ravel().astype(bool)
    if int(inliers.sum()) < 10:
        raise ValueError(f"配准内点不足: {int(inliers.sum())}")
    predicted = cv2.transform(src[inliers, None, :], matrix)[:, 0, :]
    reprojection = np.linalg.norm(predicted - dst[inliers], axis=1)
    mapped = cv2.transform(point.reshape(1, 1, 2).astype(np.float32), matrix)[0, 0]
    return {
        "mapped_point": mapped.astype(float),
        "matches": len(good),
        "inliers": int(inliers.sum()),
        "median_reprojection_error_px": float(np.median(reprojection)),
        "affine": matrix,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="从标定图片计算像素/XY步数转换矩阵")
    parser.add_argument("samples", help="标定样本JSON")
    parser.add_argument("--output", default="config/xy_pixel_calibration.json")
    args = parser.parse_args()
    spec_path = Path(args.samples)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root = spec_path.parent
    reference_image = imread_unicode(root / spec["reference"]["image"])
    reference_steps = np.asarray(spec["reference"]["steps"], dtype=np.float64)
    reference_point = load_result_center(root / spec["reference"]["result"])

    step_deltas = []
    pixel_deltas = []
    reports = []
    for sample_spec in spec["samples"]:
        sample_image = imread_unicode(root / sample_spec["image"])
        registered = register_target_plane(reference_image, sample_image, reference_point)
        steps = np.asarray(sample_spec["steps"], dtype=np.float64)
        step_delta = steps - reference_steps
        pixel_delta = registered["mapped_point"] - reference_point
        report = {
            "name": sample_spec["name"],
            "use_for_fit": bool(sample_spec.get("fit", True)),
            "steps": steps.tolist(),
            "step_delta": step_delta.tolist(),
            "mapped_reference_point": registered["mapped_point"].tolist(),
            "pixel_delta": pixel_delta.tolist(),
            "matches": registered["matches"],
            "inliers": registered["inliers"],
            "median_reprojection_error_px": round(registered["median_reprojection_error_px"], 4),
        }
        reports.append(report)
        if np.linalg.norm(step_delta) > 0 and sample_spec.get("fit", True):
            step_deltas.append(step_delta)
            pixel_deltas.append(pixel_delta)

    q = np.asarray(step_deltas, dtype=np.float64)
    p = np.asarray(pixel_deltas, dtype=np.float64)
    coefficients, _, _, _ = np.linalg.lstsq(q, p, rcond=None)
    pixels_per_step = coefficients.T
    steps_per_pixel = np.linalg.inv(pixels_per_step)
    predicted = q @ coefficients
    residuals = np.linalg.norm(predicted - p, axis=1)
    for report in reports:
        delta = np.asarray(report["step_delta"], dtype=np.float64)
        predicted_delta = delta @ coefficients
        measured_delta = np.asarray(report["pixel_delta"], dtype=np.float64)
        report["predicted_pixel_delta"] = predicted_delta.tolist()
        report["prediction_error_px"] = float(np.linalg.norm(predicted_delta - measured_delta))
    payload = {
        "version": 1,
        "coordinate_convention": {
            "pixel_origin": "full_4k_image_top_left",
            "pixel_axes": "+u right, +v down",
            "machine_axes": "Arduino absolute X/Y steps",
        },
        "reference_steps": reference_steps.tolist(),
        "reference_p2_center_px": reference_point.tolist(),
        "pixels_per_step": pixels_per_step.tolist(),
        "steps_per_pixel": steps_per_pixel.tolist(),
        "fit_rms_error_px": float(np.sqrt(np.mean(residuals ** 2))),
        "samples": reports,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
