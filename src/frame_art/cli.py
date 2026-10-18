"""Command-line tool for the art on a Samsung The Frame TV."""

import argparse
import io
import json
import os
import socket
import sys
import warnings
from pathlib import Path

from PIL import Image
from samsungtvws import SamsungTVWS

SCREEN = (3840, 2160)
DEFAULT_HOST = "192.168.86.91"
DEFAULT_MAC = "24:fc:e5:b6:34:19"
TOKEN_FILE = Path.home() / ".config" / "frame-art" / "token"

# The TV uses a self-signed certificate on port 8002.
warnings.filterwarnings("ignore", message="Unverified HTTPS request")


def connect(args):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
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


def prepare_image(path):
    """Return JPG or PNG bytes and a file type the TV accepts.

    Converts other formats (WebP, HEIC, ...) to JPG, and scales images that
    are smaller than the screen up to fit it.
    """
    with Image.open(path) as im:
        fmt = im.format
        scale = min(SCREEN[0] / im.width, SCREEN[1] / im.height)
        if fmt in ("JPEG", "PNG") and scale <= 1:
            return Path(path).read_bytes(), "jpg" if fmt == "JPEG" else "png"
        if scale > 1:
            size = (round(im.width * scale), round(im.height * scale))
            print(f"warning: {path} is {im.width}x{im.height}, scaling up to "
                  f"{size[0]}x{size[1]}", file=sys.stderr)
            im = im.resize(size, Image.LANCZOS)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=95)
        return buf.getvalue(), "jpg"


def cmd_upload(args):
    art = connect(args)
    content_id = None
    for path in args.files:
        data, file_type = prepare_image(path)
        content_id = art.upload(data, file_type=file_type,
                                matte=args.matte, portrait_matte=args.matte)
        print(f"{path} -> {content_id}")
    if args.show and content_id:
        art.select_image(content_id, show=True)
        print(f"showing {content_id}")


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


def cmd_wake(args):
    mac = bytes.fromhex(args.mac.replace(":", ""))
    packet = b"\xff" * 6 + mac * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, ("255.255.255.255", 9))
        s.sendto(packet, (args.host, 9))
    print(f"sent Wake-on-LAN to {args.mac}")


def main():
    parser = argparse.ArgumentParser(prog="frame", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("FRAME_HOST", DEFAULT_HOST))
    parser.add_argument("--timeout", type=int, default=30)
    sub = parser.add_subparsers(dest="command", required=True)

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
    p.set_defaults(func=cmd_upload)

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
    p.add_argument("--mac", default=os.environ.get("FRAME_MAC", DEFAULT_MAC))
    p.set_defaults(func=cmd_wake)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
