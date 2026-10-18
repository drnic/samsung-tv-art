"""Command-line tool for the art on a Samsung The Frame TV."""

import argparse
import io
import ipaddress
import json
import os
import socket
import sys
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ifaddr
import numpy as np
from PIL import Image, ImageChops
from samsungtvws import SamsungTVWS

from frame_art import calibrate

SCREEN = (3840, 2160)
CONFIG_DIR = Path.home() / ".config" / "frame-art"
CONFIG_FILE = CONFIG_DIR / "config.json"
TOKEN_FILE = CONFIG_DIR / "token"
CORRECTION_FILE = CONFIG_DIR / "correction.png"
CALIBRATION_FILE = CONFIG_DIR / "calibration.npz"

# The TV uses a self-signed certificate on port 8002.
warnings.filterwarnings("ignore", message="Unverified HTTPS request")


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except FileNotFoundError:
        return {}


def require_host(args):
    if not args.host:
        sys.exit("error: no TV configured. Run `frame discover`, "
                 "or pass --host or set FRAME_HOST.")
    return args.host


def connect(args):
    require_host(args)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tv = SamsungTVWS(
        host=args.host,
        port=8002,
        token_file=str(TOKEN_FILE),
        name="FrameArt",
        timeout=args.timeout,
    )
    return tv.art()


def emit(data):
    print(json.dumps(data, indent=2))


def cmd_info(args):
    art = connect(args)
    emit({
        "art_mode": art.get_artmode(),
        "api_version": art.get_api_version(),
        "current": art.get_current(),
        "slideshow": art.get_auto_rotation_status(),
    })


def cmd_list(args):
    for item in connect(args).available(args.category):
        print(f"{item['content_id']:<24} {item.get('category_id', ''):<10} "
              f"{item.get('matte_id', ''):<20} {item.get('width')}x{item.get('height')}")


def cmd_current(args):
    emit(connect(args).get_current())


def cmd_show(args):
    emit(connect(args).select_image(args.content_id, show=True))


def prepare_image(path, correction=None):
    """Return JPG bytes the TV accepts.

    Converts every other format to an RGB JPG, because the TV drops the
    connection on large PNGs with an alpha channel. Scales images that are
    smaller than the screen up to fit it. With a correction mask, places the
    image on a full screen canvas and multiplies it by the mask.
    """
    with Image.open(path) as im:
        scale = min(SCREEN[0] / im.width, SCREEN[1] / im.height)
        if im.format == "JPEG" and im.mode == "RGB" and scale <= 1 and correction is None:
            return Path(path).read_bytes(), "jpg"
        if scale > 1:
            size = (round(im.width * scale), round(im.height * scale))
            print(f"warning: {path} is {im.width}x{im.height}, scaling up to "
                  f"{size[0]}x{size[1]}", file=sys.stderr)
            im = im.resize(size, Image.LANCZOS)
        im = im.convert("RGB")
        if correction is not None:
            im = apply_correction(im, correction)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=95)
        return buf.getvalue(), "jpg"


def load_correction(path, strength):
    """Return the screen correction mask at full screen size.

    The mask holds a gain for each area of the screen and each colour, where
    255 is no change. It dims the bright areas to match the faded areas of
    the panel. A strength below 1 removes less of the fade but dims less.
    """
    with Image.open(path) as mask:
        mask = mask.convert("RGB").resize(SCREEN, Image.BICUBIC)
    if strength != 1:
        mask = mask.point([round(255 * (v / 255) ** strength) for v in range(256)] * 3)
    return mask


def apply_correction(im, mask):
    # The mask maps to the panel, so the image must fill the screen in the same place.
    if im.size != SCREEN:
        im.thumbnail(SCREEN, Image.LANCZOS)
        canvas = Image.new("RGB", SCREEN)
        canvas.paste(im, ((SCREEN[0] - im.width) // 2, (SCREEN[1] - im.height) // 2))
        im = canvas
    return ImageChops.multiply(im, mask)


def cmd_upload(args):
    art = connect(args)
    content_id = None
    correction = None
    if args.correct:
        if not args.mask.exists():
            sys.exit(f"error: no correction mask at {args.mask}")
        correction = load_correction(args.mask, args.strength)
    for path in args.files:
        data, file_type = prepare_image(path, correction)
        content_id = art.upload(data, file_type=file_type,
                                matte=args.matte, portrait_matte=args.matte)
        print(f"{path} -> {content_id}")
    if args.show and content_id:
        art.select_image(content_id, show=True)
        print(f"showing {content_id}")


def load_calibration():
    try:
        with np.load(CALIBRATION_FILE) as f:
            return dict(f)
    except FileNotFoundError:
        return None


def show_card(art, state):
    """Upload the test card with the current mask, show it, and remove the last card."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    calibrate.gain_to_mask(state["gain"]).save(CORRECTION_FILE)
    card = apply_correction(calibrate.make_card(SCREEN), load_correction(CORRECTION_FILE, 1))
    buf = io.BytesIO()
    card.save(buf, "JPEG", quality=95)
    content_id = art.upload(buf.getvalue(), file_type="jpg", matte="none", portrait_matte="none")
    art.select_image(content_id, show=True)
    if "card" in state:
        art.delete_list([str(state["card"])])
    state["card"] = content_id
    np.savez(CALIBRATION_FILE, **state)
    print(f"showing test card {content_id}")


def cmd_calibrate_start(args):
    art = connect(args)
    state = None if args.reset else load_calibration()
    if state is None:
        if CORRECTION_FILE.exists() and not args.reset:
            with Image.open(CORRECTION_FILE) as mask:
                gain = calibrate.mask_to_gain(mask)
        else:
            gain = np.ones((*reversed(calibrate.GRID), 3))
        state = {"gain": gain}
    show_card(art, state)
    print("Take a photo of the whole screen, straight on, in a dark room, then run:\n"
          "  frame calibrate photo <photo.jpg>")


def cmd_calibrate_photo(args):
    state = load_calibration()
    if state is None:
        sys.exit("error: run `frame calibrate start` first")
    photo = calibrate.load_photo(args.photo)
    try:
        markers = calibrate.find_markers(photo, SCREEN)
    except ValueError as e:
        sys.exit(f"error: {e}. Make sure that the whole screen and its four corner "
                 "squares are in the photo.")
    grid = calibrate.measure(photo, markers, SCREEN)
    print(f"the dim areas are {calibrate.unevenness(grid):.0%} darker than the bright areas")
    if calibrate.unevenness(grid) < 0.10:
        print("This is close to even. Some of what is left can come from the camera lens, "
              "so more steps can dim the screen for little gain.")
    previous = (state["prev_g"], state["prev_r"]) if "prev_g" in state else None
    gain, (state["prev_g"], state["prev_r"]), response = calibrate.update(state["gain"], grid, previous)
    state["gain"] = gain
    print(f"response {response:.2f}, new mask dims the screen to between "
          f"{gain.min():.0%} and 100% of full brightness")
    show_card(connect(args), state)
    print("Take another photo from the same place, and run this command again. "
          "When the screen looks even, run `frame calibrate finish`.")


def cmd_calibrate_finish(args):
    state = load_calibration()
    if state is None:
        sys.exit("error: no calibration in progress")
    if "card" in state:
        connect(args).delete_list([str(state["card"])])
    CALIBRATION_FILE.unlink()
    print(f"saved the mask to {CORRECTION_FILE}. Use `frame upload --correct`.")


def cmd_delete(args):
    ok = connect(args).delete_list(args.content_ids)
    print("deleted" if ok else "delete failed")
    sys.exit(0 if ok else 1)


def cmd_mattes(args):
    emit(connect(args).get_matte_list())


def cmd_matte(args):
    emit(connect(args).change_matte(args.content_id, args.matte_id))


def cmd_slideshow(args):
    # This firmware (API 0.97) rejects set_slideshow_status, so use auto rotation.
    emit(connect(args).set_auto_rotation_status(
        duration=args.minutes, type=not args.ordered, category_id=args.category))


def cmd_artmode(args):
    emit(connect(args).set_artmode(args.state))


def tv_info(host, timeout=1):
    """Return the REST API device info of a Samsung TV, or None."""
    try:
        with urllib.request.urlopen(f"http://{host}:8001/api/v2/", timeout=timeout) as r:
            return json.load(r)["device"]
    except (OSError, ValueError, KeyError):
        return None


def ssdp_hosts(timeout=2):
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
           'MAN: "ssdp:discover"\r\nMX: 1\r\n'
           "ST: urn:samsung.com:device:RemoteControlReceiver:1\r\n\r\n").encode()
    hosts = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(msg, ("239.255.255.250", 1900))
            while True:
                hosts.add(s.recvfrom(4096)[1][0])
        except OSError:
            pass
    return hosts


def subnet_hosts():
    """Every address on the local private networks, at most a /24 each."""
    hosts = set()
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if not isinstance(ip.ip, str):
                continue
            net = ipaddress.ip_network(f"{ip.ip}/{max(ip.network_prefix, 24)}", strict=False)
            if net.is_private and not net.is_loopback and net.prefixlen < 31:
                hosts.update(str(h) for h in net.hosts())
    return hosts


def find_frames():
    hosts = ssdp_hosts() or subnet_hosts()
    with ThreadPoolExecutor(64) as pool:
        infos = pool.map(tv_info, sorted(hosts, key=ipaddress.ip_address))
    return [i for i in infos if i and i.get("FrameTVSupport") == "true"]


def cmd_discover(args):
    if args.host:
        info = tv_info(args.host, timeout=args.timeout)
        if not info:
            sys.exit(f"error: no Samsung TV answered at {args.host}")
        frames = [info]
    else:
        print("searching the local network...", file=sys.stderr)
        frames = find_frames()
    for f in frames:
        print(f"{f['ip']:<16} {f.get('wifiMac', ''):<18} {f.get('modelName', '')}  {f['name']}")
    if not frames:
        sys.exit("error: found no Frame TV. Make sure the TV is on, "
                 "then run `frame --host <ip> discover`.")
    if len(frames) > 1:
        sys.exit("found more than one Frame TV. Run `frame --host <ip> discover` to pick one.")
    f = frames[0]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(
        {"host": f["ip"], "mac": f.get("wifiMac", "").lower(), "name": f["name"]}, indent=2))
    print(f"saved to {CONFIG_FILE}")


def cmd_wake(args):
    if not args.mac:
        sys.exit("error: no MAC address. Run `frame discover`, or pass --mac or set FRAME_MAC.")
    mac = bytes.fromhex(args.mac.replace(":", ""))
    packet = b"\xff" * 6 + mac * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, ("255.255.255.255", 9))
        if args.host:
            s.sendto(packet, (args.host, 9))
    print(f"sent Wake-on-LAN to {args.mac}")


def main():
    config = load_config()
    parser = argparse.ArgumentParser(prog="frame", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("FRAME_HOST"))
    parser.add_argument("--timeout", type=int, default=30)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="find the TV on the network and save it")
    p.set_defaults(func=cmd_discover)

    sub.add_parser("info", help="art mode, current image, slideshow").set_defaults(func=cmd_info)

    p = sub.add_parser("list", help="list images on the TV")
    p.add_argument("--category", help="e.g. MY-C0002 (my photos), MY-C0004 (favourites)")
    p.set_defaults(func=cmd_list)

    sub.add_parser("current", help="show the current image").set_defaults(func=cmd_current)

    p = sub.add_parser("show", help="show an image by content id")
    p.add_argument("content_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("upload", help="upload images (converted to JPG if needed)")
    p.add_argument("files", nargs="+")
    p.add_argument("--matte", default="none", help="e.g. none, shadowbox_polar, modern_navy")
    p.add_argument("--show", action="store_true", help="show the last uploaded image")
    p.add_argument("--correct", action="store_true", help="apply the screen fade correction mask")
    p.add_argument("--mask", type=Path, default=CORRECTION_FILE,
                   help=f"correction mask (default {CORRECTION_FILE})")
    p.add_argument("--strength", type=float, default=1.0,
                   help="correction strength, 0 to 1 (default 1)")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("calibrate", help="measure the screen fade from photos of a test card")
    cal = p.add_subparsers(dest="step", required=True)
    q = cal.add_parser("start", help="show the test card")
    q.add_argument("--reset", action="store_true", help="start again from no correction")
    q.set_defaults(func=cmd_calibrate_start)
    q = cal.add_parser("photo", help="measure a photo of the test card and refine the mask")
    q.add_argument("photo")
    q.set_defaults(func=cmd_calibrate_photo)
    cal.add_parser("finish", help="remove the test card").set_defaults(func=cmd_calibrate_finish)

    p = sub.add_parser("delete", help="delete images by content id")
    p.add_argument("content_ids", nargs="+")
    p.set_defaults(func=cmd_delete)

    sub.add_parser("mattes", help="list matte types and colours").set_defaults(func=cmd_mattes)

    p = sub.add_parser("matte", help="change the matte of an image")
    p.add_argument("content_id")
    p.add_argument("matte_id", help="<type>_<colour>, or none")
    p.set_defaults(func=cmd_matte)

    p = sub.add_parser("slideshow", help="start or stop a slideshow")
    p.add_argument("minutes", type=int, help="minutes per image, 0 turns it off")
    p.add_argument("--category", default="MY-C0002", help="default MY-C0002 (my photos)")
    p.add_argument("--ordered", action="store_true", help="in order, not shuffled")
    p.set_defaults(func=cmd_slideshow)

    p = sub.add_parser("artmode", help="turn art mode on or off")
    p.add_argument("state", choices=["on", "off"])
    p.set_defaults(func=cmd_artmode)

    p = sub.add_parser("wake", help="send Wake-on-LAN to the TV")
    p.add_argument("--mac", default=os.environ.get("FRAME_MAC", config.get("mac")))
    p.set_defaults(func=cmd_wake)

    args = parser.parse_args()
    if args.func is not cmd_discover:
        args.host = args.host or config.get("host")
    args.func(args)


if __name__ == "__main__":
    main()
