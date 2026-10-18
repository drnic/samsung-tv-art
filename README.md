# samsung-tv-art

A command-line tool that manages the art on a Samsung The Frame TV over the local network. It uses the Art Mode channel of the TV WebSocket API through the [samsungtvws](https://github.com/xchwarze/samsung-tv-ws-api) library.

## Setup

Turn on the TV, then run these commands on a computer on the same network:

```bash
uv sync
uv run frame discover
uv run frame info
```

`frame discover` searches the local network for a The Frame TV. It saves the IP address and MAC address of the TV to `~/.config/frame-art/config.json`. If it finds more than one TV, or none, give the IP address yourself:

```bash
uv run frame --host 192.168.1.50 discover
```

The `--host` option and the `FRAME_HOST` and `FRAME_MAC` environment variables override the saved configuration. If the TV gets a new IP address, run `frame discover` again. To keep the same address, give the TV a DHCP reservation on your router.

## Commands

```bash
uv run frame discover                      # find the TV and save it
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

The screen is 3840x2160. `frame upload` converts every image that is not an RGB JPG, such as WebP or PNG, to JPG. The TV drops the connection on large PNG files with transparency. It scales images smaller than the screen up to fit, which makes them soft, so use 3840x2160 sources when you can.
