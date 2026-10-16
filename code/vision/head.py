import cv2
import numpy as np

IMAGE_PATH = r"C:\Users\Lily\Desktop\1.png"

SPLIT_RATIO = 0.62

def imread_unicode(path):
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_unicode(path, image):
    extension = "." + path.rsplit(".", 1)[-1]
    success, buffer = cv2.imencode(extension, image)
    if success:
        buffer.tofile(path)
    return success


def _find_fixture_bbox(image):
    """通过白色夹具找到整体外框。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    white_hsv = cv2.inRange(
        hsv,
        np.array([0, 0, 130], dtype=np.uint8),
        np.array([180, 80, 255], dtype=np.uint8),
    )
    white_gray = (gray > 175).astype(np.uint8) * 255
    white = cv2.bitwise_or(white_hsv, white_gray)
    white = cv2.morphologyEx(
        white, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)
    )

    contours, _ = cv2.findContours(
        white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        raise ValueError("未检测到白色夹具")

    fixture_contour = max(contours, key=cv2.contourArea)
    return cv2.boundingRect(fixture_contour)


def _left_head_roi(fixture_bbox):
    """
    根据夹具外框确定左侧头部槽。

    当前夹具中，蟑螂头部位于夹具左侧通道，中央竖梁在通道右侧。
    这些比例只描述夹具中的位置，不依赖蟑螂身体的主成分方向。
    """
    x, y, width, height = fixture_bbox
    left = int(round(x + 0.20 * width))
    right = int(round(x + 0.55 * width))
    top = int(round(y + 0.22 * height))
    bottom = int(round(y + 0.78 * height))
    return left, top, right, bottom


def _head_black_mask(image, fixture_bbox):
    """在左侧头部槽内提取最大的黑色连通域。"""
    left, top, right, bottom = _left_head_roi(fixture_bbox)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark = (gray < 95).astype(np.uint8) * 255

    roi_mask = np.zeros_like(dark)
    roi_mask[top:bottom, left:right] = 255
    dark = cv2.bitwise_and(dark, roi_mask)

    dark = cv2.morphologyEx(
        dark, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)
    )
    dark = cv2.morphologyEx(
        dark, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8)
    )

    count, labels, stats, _ = cv2.connectedComponentsWithStats(dark)
    if count <= 1:
        raise ValueError("左侧头部槽内未检测到黑色区域")

    label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = (labels == label).astype(np.uint8) * 255
    return mask, (left, top, right, bottom)


def _distance_center(region_mask):
    """在区域内部取离轮廓最远的点，避免点位贴边。"""
    distance = cv2.distanceTransform(region_mask, cv2.DIST_L2, 5)
    _, max_value, _, location = cv2.minMaxLoc(distance)
    if max_value <= 0:
        return None
    return location


def detect_head_points(image, split_ratio=SPLIT_RATIO):
    """
    检测头部两节的四个内部点。
    返回顺序：
        P1 前节上侧，P2 前节下侧，
        P3 后节上侧，P4 后节下侧
    """
    if not 0.0 < split_ratio < 1.0:
        raise ValueError("split_ratio 必须在 0 到 1 之间")

    fixture_bbox = _find_fixture_bbox(image)
    head_mask, head_roi = _head_black_mask(image, fixture_bbox)

    ys, xs = np.where(head_mask > 0)
    if len(xs) == 0:
        raise ValueError("头部黑色区域为空")

    x_low, x_high = np.percentile(xs, [2.0, 98.0])
    y_low, y_high = np.percentile(ys, [2.0, 98.0])
    split_x = x_low + split_ratio * (x_high - x_low)
    y_mid = 0.5 * (y_low + y_high)

    quadrant_ranges = [
        (x_low, split_x, y_low, y_mid),
        (x_low, split_x, y_mid, y_high),
        (split_x, x_high, y_low, y_mid),
        (split_x, x_high, y_mid, y_high),
    ]

    points = []
    for x0, x1, y0, y1 in quadrant_ranges:
        region = np.zeros_like(head_mask)
        selected = (
            (xs >= x0)
            & (xs <= x1)
            & (ys >= y0)
            & (ys <= y1)
        )
        region[ys[selected], xs[selected]] = 255
        point = _distance_center(region)
        if point is None:
            points.append((np.nan, np.nan))
            continue

        points.append(point)

    # P1/P2 是第一段的上下两点；P3/P4 使用各自区域的 x，
    # y 分别与 P1/P2 对齐，保证第二段两点的水平高度一致。
    if all(
        not np.isnan(point[0]) and not np.isnan(point[1])
        for point in points
    ):
        points[2] = (points[2][0], points[0][1])
        points[3] = (points[3][0], points[1][1])

    info = {
        "fixture_bbox": fixture_bbox,
        "head_roi": head_roi,
        "head_mask": head_mask,
        "x_low": float(x_low),
        "x_high": float(x_high),
        "y_low": float(y_low),
        "y_high": float(y_high),
        "split_x": float(split_x),
        "y_mid": float(y_mid),
    }
    return points, info


def draw_head_points(
    image,
    points,
    info,
    output_path="head_two_segments_points.jpg",
):
    """用红色标注四个点，并输出分界辅助线。"""
    result = image.copy()

    cv2.line(
        result,
        (int(round(info["split_x"])), int(round(info["y_low"]))),
        (int(round(info["split_x"])), int(round(info["y_high"]))),
        (0, 255, 255),
        2,
    )

    cv2.line(
        result,
        (int(round(info["x_low"])), int(round(info["y_mid"]))),
        (int(round(info["x_high"])), int(round(info["y_mid"]))),
        (255, 255, 0),
        2,
    )

    for index, point in enumerate(points, start=1):
        if np.isnan(point[0]) or np.isnan(point[1]):
            print(f"P{index}: 未检测到")
            continue

        x, y = int(round(point[0])), int(round(point[1]))
        cv2.circle(result, (x, y), 5, (0, 0, 255), -1)
        cv2.circle(result, (x, y), 12, (0, 0, 255), 3)
        cv2.putText(
            result,
            f"P{index} ({x}, {y})",
            (x + 15, y - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
        print(f"P{index}: ({x}, {y})")

    imwrite_unicode(output_path, result)
    print(f"结果图已保存: {output_path}")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    input_path = Path(sys.argv[1] if len(sys.argv) > 1 else IMAGE_PATH)
    image = imread_unicode(str(input_path))
    if image is None:
        raise SystemExit(f"读取失败: {input_path}")

    detected_points, debug_info = detect_head_points(
        image,
        split_ratio=SPLIT_RATIO,
    )
    output_path = input_path.with_name(f"{input_path.stem}_points.jpg")
    draw_head_points(
        image,
        detected_points,
        debug_info,
        output_path=str(output_path),
    )
