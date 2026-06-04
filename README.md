# sms-nut-bridge

Expose **SMS / PowerView-compatible UPS (no-break) devices** to
[Network UPS Tools (NUT)](https://networkupstools.org/) clients.

The vendor monitoring software is a heavy Java desktop application and the
units are not supported by NUT's stock drivers. This bridge is a small, single-
file Python daemon that talks to the UPS over its serial interface and re-serves
the data using the **native NUT network protocol** (TCP/3493). Any NUT client —
`upsc`, [NUT Monitor](https://networkupstools.org/projects.html), Home
Assistant's NUT integration, `upsmon`, Prometheus exporters, etc. — can then
read the UPS, with no vendor software and no recompiling of NUT.

## Features

- Reads live UPS telemetry over serial (direct port or a USB-to-serial adapter
  such as a Prolific PL2303 → `/dev/ttyUSB0`).
- Serves standard NUT variables (`ups.status`, `input.voltage`,
  `output.voltage`, `ups.load`, `battery.charge`, `output.frequency`,
  `ups.temperature`, `ups.beeper.status`).
- Built-in minimal `upsd`-compatible TCP server — nothing else to install.
- Background polling with value caching; clients always get the last good read.
- Single file, standard library + `pyserial` only. Runs as a systemd service.

## Requirements

- Python 3.8+
- `pyserial` (`pip install pyserial`)
- A serial connection to the UPS. With a USB-to-serial adapter the user running
  the bridge must be in the group that owns the device node
  (`dialout` on Debian/Ubuntu, `uucp` on Arch).

## Quick start

```bash
pip install pyserial

# 1. confirm the wiring with a single read (compare to your UPS's own display)
python3 sms_nut_bridge.py --device /dev/ttyUSB0 --once

# 2. run the NUT server
python3 sms_nut_bridge.py --device /dev/ttyUSB0

# 3. from any NUT client
upsc sms@localhost
# or, without NUT installed:
printf 'LIST VAR sms\nLOGOUT\n' | nc localhost 3493
```

Example output:

```
ups.status: OL
ups.load: 44.0
input.voltage: 128.8
output.voltage: 112.8
output.frequency: 59.9
battery.charge: 100
ups.temperature: 35.0
ups.beeper.status: enabled
```

## Serial settings

`2400` baud, `8N1`, **RTS asserted, DTR deasserted**. The control-line states
matter: they power the UPS's optocoupler interface, so getting them wrong
results in no reply. The bridge sets them for you.

## Run as a service (systemd)

```bash
sudo mkdir -p /opt/sms-nut && sudo cp sms_nut_bridge.py /opt/sms-nut/
sudo cp systemd/sms-nut.service /etc/systemd/system/
# edit User=, SupplementaryGroups= and --device in the unit if needed
sudo systemctl daemon-reload
sudo systemctl enable --now sms-nut.service
systemctl status sms-nut.service
```

### Stable device name (optional)

If you have more than one USB-serial adapter, the `ttyUSBn` number can change
across reboots. Install the included udev rule to get a stable `/dev/ttyUPS`:

```bash
sudo cp udev/99-sms-ups.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
# then use:  --device /dev/ttyUPS
```

Adjust the `idVendor`/`idProduct` in the rule to match your adapter
(`lsusb` shows them).

## Command-line options

| flag | default | description |
|------|---------|-------------|
| `--device` | `/dev/ttyUSB0` | serial device |
| `--baud` | `2400` | baud rate |
| `--once` | – | single read, print, exit |
| `--raw` | – | with `--once`, also print the raw hex frame |
| `--name` | `sms` | NUT UPS name (clients use `name@host`) |
| `--host` | `0.0.0.0` | bind address |
| `--port` | `3493` | bind port |
| `--interval` | `3.0` | seconds between polls |

## Automatic shutdown

The bridge is read-only/monitoring. For automatic clean shutdown on
`OB LB` (on battery + low battery), point a standard NUT `upsmon` at this
server (`MONITOR sms@<host> 1 <user> <pass> slave`). See the NUT docs.

## Protocol

The serial frame format and field decoding are documented in
[PROTOCOL.md](PROTOCOL.md).

## Disclaimer

This is an independent, community project. It is **not affiliated with,
authorized, or endorsed by** any UPS manufacturer. "SMS" and "PowerView" are
trademarks of their respective owners and are used here only to describe device
compatibility. Use at your own risk; verify behavior against your hardware
before relying on it for shutdown automation.

## License

MIT — see [LICENSE](LICENSE).
