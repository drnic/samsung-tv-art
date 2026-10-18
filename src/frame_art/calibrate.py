"""Measure the brightness fade of a TV panel from a phone photo of a test card.

The test card is a white screen with five small black squares: one near each
corner and one in the centre. The calibration finds the squares in the photo,
maps the photo back to screen coordinates, and measures the brightness of each
area of the screen. The correction mask holds a gain for each area and colour
that dims the bright areas to match the faded ones.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageOps

GRID = (192, 108)        # mask cells across and down
MARKER = 24              # marker square size in screen pixels
MARKER_INSET = 40        # distance of the corner markers from the screen edge
DAMPING = 0.85           # take a little less than the full step, to avoid overshoot
DEFAULT_RESPONSE = 0.75  # photo brightness change per gain change, before we can measure it
MIN_GAIN = 0.1


def marker_positions(screen):
    w, h = screen
    i = MARKER_INSET
    return np.array([(i, i), (w - i, i), (i, h - i), (w - i, h - i), (w / 2, h / 2)], float)


def make_card(screen):
    im = Image.new("RGB", screen, (255, 255, 255))
    draw = ImageDraw.Draw(im)
    r = MARKER // 2
    for x, y in marker_positions(screen):
        draw.rectangle([x - r, y - r, x + r, y + r], fill=(0, 0, 0))
    return im


def _ring(gray, k):
    """The eight samples at distance k around every pixel, NaN past the edge."""
    padded = np.pad(gray, k, constant_values=np.nan)
    h, w = gray.shape
    return np.stack([padded[k + dy:k + dy + h, k + dx:k + dx + w]
                     for dy in (-k, 0, k) for dx in (-k, 0, k) if dy or dx])


def _clusters(ys, xs, size):
    """Group points into blobs: bin them into cells, then join touching cells."""
    bins = {}
    for x, y in zip(xs // size, ys // size):
        bins[x, y] = bins.get((x, y), 0) + 1
    parent = {b: b for b in bins}

    def root(b):
        while parent[b] != b:
            b = parent[b]
        return b

    for x, y in bins:
        for n in ((x + 1, y), (x, y + 1), (x + 1, y + 1), (x + 1, y - 1)):
            if n in bins:
                parent[root(n)] = root((x, y))
    blobs = {}
    for x, y, b in zip(xs, ys, zip(xs // size, ys // size)):
        blob = blobs.setdefault(root(b), [0, 0, 0])
        blob[0] += x; blob[1] += y; blob[2] += 1
    return [(sx / n, sy / n, n) for sx, sy, n in blobs.values()]


def _order(points):
    """Sort five points into top-left, top-right, bottom-left, bottom-right, centre."""
    p = np.array(points)
    s, d = p.sum(1), p[:, 0] - p[:, 1]
    corners = [s.argmin(), d.argmax(), d.argmin(), s.argmax()]
    if len(set(corners)) < 4:
        return None
    centre = [i for i in range(5) if i not in corners]
    return p[corners + centre]


def homography(src, dst):
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        b += [u, v]
    return np.append(np.linalg.solve(a, b), 1).reshape(3, 3)


def project(h, pts):
    pts = np.column_stack([pts, np.ones(len(pts))]) @ h.T
    return pts[:, :2] / pts[:, 2:]


def find_markers(photo, screen):
    """Return the photo positions of the five markers, in marker_positions order.

    Looks for small dark spots with an even, much brighter ring around them, so
    the black bezel and the wall do not count. Tries a range of marker sizes,
    because the TV can fill more or less of the photo.
    """
    gray = photo.mean(2)
    w = gray.shape[1]
    s = w * 0.0025
    while s < w * 0.015:
        k = int(1.5 * s) + 1
        ring = _ring(gray, k)
        with np.errstate(invalid="ignore"):
            lo, hi = np.nanmin(ring, 0), np.nanmax(ring, 0)
            spot = (gray < 0.5 * lo) & (lo > 0.6 * hi)
        ys, xs = np.nonzero(spot)
        if len(xs) < 200_000:
            found = [(x, y) for x, y, n in _clusters(ys, xs, max(2, int(s))) if 0.3 * s * s < n < 3 * s * s]
            if len(found) == 5 and (pts := _order(found)) is not None:
                pts = np.array([_refine(gray, x, y, s) for x, y in pts])
                h = homography(marker_positions(screen)[:4], pts[:4])
                if np.hypot(*(project(h, marker_positions(screen)[4:]) - pts[4])[0]) < s:
                    return pts
        s *= 1.15
    raise ValueError("could not find the five marker squares in the photo")


def _refine(gray, x, y, s):
    r = int(s) + 2
    x0, y0 = int(x) - r, int(y) - r
    win = gray[y0:y0 + 2 * r + 1, x0:x0 + 2 * r + 1]
    ys, xs = np.nonzero(win < (win.min() + win.max()) / 2)
    return x0 + xs.mean(), y0 + ys.mean()


def measure(photo, markers, screen):
    """Return the photo brightness of each grid cell and colour, from 0 to 1."""
    gw, gh = GRID
    sw, sh = screen
    h = homography(marker_positions(screen)[:4], markers[:4])
    cx = np.clip((np.arange(gw) + 0.5) * sw / gw, 30, sw - 30)
    cy = np.clip((np.arange(gh) + 0.5) * sh / gh, 30, sh - 30)
    pts = project(h, np.array([(x, y) for y in cy for x in cx])).round().astype(int)
    cell = abs(markers[1, 0] - markers[0, 0]) / gw
    r = max(1, int(cell / 3))
    grid = np.array([np.median(photo[v - r:v + r + 1, u - r:u + r + 1].reshape(-1, 3), 0)
                     for u, v in pts]).reshape(gh, gw, 3)
    # Replace the cells that hold a marker with the median of their neighbours.
    near = np.zeros((gh, gw), bool)
    for mx, my in marker_positions(screen):
        near |= (abs(cy[:, None] - my) < MARKER * 2) & (abs(cx[None, :] - mx) < MARKER * 2)
    masked = np.where(near[..., None], np.nan, grid)
    for j, i in zip(*np.nonzero(near)):
        grid[j, i] = np.nanmedian(masked[max(j - 4, 0):j + 5, max(i - 4, 0):i + 5].reshape(-1, 3), 0)
    return _smooth(grid)


def _smooth(grid, n=5):
    kernel = np.ones(n) / n
    for axis in (0, 1):
        grid = np.apply_along_axis(
            lambda r: np.convolve(np.pad(r, n // 2, mode="edge"), kernel, "valid"), axis, grid)
    return grid


def unevenness(grid):
    """How much darker the dim areas are than the bright areas, from 0 to 1."""
    y = grid.mean(2)
    lo, hi = np.percentile(y, [5, 95])
    return 1 - lo / hi


def update(gain, grid, previous=None):
    """Return the next gain grid, and the measured response of the photo to the gain.

    The photo of the card shows the panel fade multiplied by the current gain.
    In log terms, each step moves the gain by the gap between each area and
    the dimmest areas, divided by the response. The response is how much the
    photo brightness changes for a change in gain. It depends on the TV and
    the camera, so after the first step we measure it from the last two photos.
    """
    log_r = np.log(grid / grid.reshape(-1, 3).mean(0))
    log_g = np.log(gain)
    response = DEFAULT_RESPONSE
    if previous is not None:
        prev_g, prev_r = previous
        dg = log_g - prev_g
        dr = log_r - prev_r
        dg -= dg.mean((0, 1))
        dr -= dr.mean((0, 1))
        # A small gain change makes the estimate noisy, so keep the default then.
        if np.sqrt((dg * dg).mean()) > 0.05:
            response = float(np.clip((dr * dg).sum() / (dg * dg).sum(), 0.5, 1.5))
    target = np.percentile(log_r, 2, axis=(0, 1)).min()
    new = log_g + DAMPING * (target - log_r) / response
    new = np.clip(new - new.max(), np.log(MIN_GAIN), 0)
    return np.exp(new), (log_g, log_r), response


def gain_to_mask(gain):
    return Image.fromarray((gain * 255).round().clip(0, 255).astype(np.uint8))


def mask_to_gain(mask):
    mask = mask.convert("RGB").resize(GRID, Image.BICUBIC)
    return np.clip(np.asarray(mask, float) / 255, MIN_GAIN, 1)


def load_photo(path):
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        return np.asarray(im, float) / 255

