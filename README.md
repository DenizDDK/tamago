# Tamagotchi (Raspberry Pi Zero 2 W, 320x480 touchscreen)

Minimal Tamagotchi-like app built with `pygame` for a portrait 320x480 display.

## Features
- Fullscreen 320x480 portrait UI.
- 128x128 centered sprite at `x=96` in the upper half.
- 3 action buttons: **FEED**, **PLAY**, **CUDDLE**.
- 3 status bars with percent text: Hunger, Happiness, Energy.
  - Colors: green (>66), blue (33-66), red (<33).
- Animation sets per phase with 2-frame idle and 2-frame action animations.
- Input lock while action animation is running.
- Level phases:
  - 1-4 baby
  - 5-9 kid
  - 10-14 teen
  - 15-20 adult
- Save file at `/home/pi/tamagotchi/save.json`.
  - Robust save (temp file + atomic replace)
  - Autosave after each action and every 60 seconds
  - Save on SIGINT/SIGTERM and normal exit
- POWER button triggers save -> `sync` -> `sudo shutdown -h now`.

## Install
```bash
sudo apt update
sudo apt install -y python3-pygame
```

## Run
From project directory:
```bash
python3 main.py
```

## Sprite asset layout
Place sprite PNG files in this exact structure:

```text
sprites/
  baby/  idle_1.png idle_2.png feed_1.png feed_2.png play_1.png play_2.png cuddle_1.png cuddle_2.png
  kid/   idle_1.png idle_2.png feed_1.png feed_2.png play_1.png play_2.png cuddle_1.png cuddle_2.png
  teen/  idle_1.png idle_2.png feed_1.png feed_2.png play_1.png play_2.png cuddle_1.png cuddle_2.png
  adult/ idle_1.png idle_2.png feed_1.png feed_2.png play_1.png play_2.png cuddle_1.png cuddle_2.png
```

If a file is missing, the app logs the missing file and shows a visible placeholder rectangle instead of crashing.

## Autostart on boot (systemd)
Create `/etc/systemd/system/tamagotchi.service`:

```ini
[Unit]
Description=Tamagotchi App
After=multi-user.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/tamago
ExecStart=/usr/bin/python3 /home/pi/tamago/main.py
Restart=always
Environment=SDL_VIDEODRIVER=KMSDRM

[Install]
WantedBy=multi-user.target
```

Then enable/start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable tamagotchi.service
sudo systemctl start tamagotchi.service
```

Check logs:
```bash
journalctl -u tamagotchi.service -f
```
