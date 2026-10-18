# samsung-tv-art

A command-line tool that manages the art on a Samsung The Frame TV over the local network. It uses the Art Mode channel of the TV WebSocket API through the [samsungtvws](https://github.com/xchwarze/samsung-tv-ws-api) library.

The TV is a 2022 55-inch The Frame (QA55LS03RAWXXY) at `192.168.86.91`.

## Setup

```bash
uv sync
uv run frame info
```

To use another TV, set `FRAME_HOST` and `FRAME_MAC`, or pass `--host`.

## Commands

```bash
uv run frame info                          # art mode, current image, slideshow
uv run frame list                          # all images on the TV
uv run frame list --category MY-C0002      # your uploaded photos only
uv run frame upload photo.jpg --show       # upload and show at once
uv run frame show MY_F0001                 # show an image by content id
uv run frame mattes                        # matte types and colours
uv run frame matte MY_F0001 modern_navy    # change the matte of an image
uv run frame slideshow 15                  # new image every 15 minutes
uv run frame slideshow 0                   # stop the slideshow
uv run frame delete MY_F0001               # delete an uploaded image
uv run frame artmode on
uv run frame wake                          # Wake-on-LAN
```

## Categories

| Category   | Content                  |
|------------|--------------------------|
| `MY-C0002` | Your uploaded photos     |
| `MY-C0004` | Favourites               |
| `MY-C0008` | Art Store                |

## TV configuration

The TV must be on, or in Art Mode, to answer. If the TV is fully off, its Wi-Fi sleeps. Turn on these settings on the TV:

```
Settings > General > Network > Expert Settings > Power On with Mobile = On
Settings > General > External Device Manager > Device Connection Manager > Access Notification = First Time Only
```

The Art Mode channel on this TV firmware does not ask for pairing. If the TV shows an "Allow" prompt, select "Allow" with the remote. The tool keeps the pairing token in `~/.config/frame-art/token`.

## Images

The screen is 3840x2160. Use JPG or PNG images at that size for a full-screen picture without a matte.
