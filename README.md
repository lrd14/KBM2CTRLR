# THIS WAS MADE TO WORK MAINLY ON ARCH LINUX WITH KDE PLASMA AND HASN'T BEEN TESTED ON OTHER OS'S

# Keyboard/Mouse → Virtual Controller

Grabs **all** physical keyboards and mice globally while running, and exposes them as a virtual Xbox 360 controller via `uinput`. The exclusive grab is kept for the whole session so games cannot steal devices and trap you — toggle/quit always work.

**Toggle mode:** `Ctrl+Shift+T` — switch between controller mapping and KBM passthrough (events re-injected via a virtual keyboard/mouse; physical grab stays on).

**Quit:** `Ctrl+Shift+Q` — release grab and exit.

## Permissions

1. Load the `uinput` module and make it persistent:

```bash
sudo modprobe uinput
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
```

2. Install the udev rule from this repo:

```bash
sudo cp udev/99-uinput.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

3. Add your user to the `input` group, then log out and back in:

```bash
sudo usermod -aG input "$USER"
```

4. Confirm access:

```bash
ls -l /dev/uinput
# should be group input, mode 0660
groups   # should list "input"
```

You can also run with `sudo` if you skip group setup.

## Install

System package (already common on Arch):

```bash
sudo pacman -S python-evdev
```

Or a venv:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python -m src.main --list                 # list keyboards / mice
python -m src.main                        # grab ALL keyboards + mice
python -m src.main --keyboard 20 --mouse 11   # optional: restrict devices
python -m src.main -c config.toml
```

Physical devices stay grabbed while the tool runs. In mapping mode only the virtual pad is visible; in passthrough mode a virtual KBM is fed instead. Use toggle or quit to get real control back.

## Config

Edit [`config.toml`](config.toml) for button mappings, WASD axes, mouse sensitivity / decay / deadzone, and the quit chord.

## Testing the virtual pad

With the tool running, in another TTY or via SSH:

```bash
# find the virtual device
cat /proc/bus/input/devices | grep -A5 "Xbox360"

# or
jstest /dev/input/js0
# / evtest on the event node for "Microsoft X-Box 360 pad"
```

## Some fixes I found whilst developing this

1. If you have any other controllers plugged in I've found that it causes issues with the left stick for some reason (this might be just a me issue with maybe stick drift?).

