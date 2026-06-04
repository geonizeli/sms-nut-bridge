#!/usr/bin/env python3
"""
sms_nut_bridge.py — expose an SMS / PowerView-compatible UPS (no-break) to
Network UPS Tools (NUT) clients.

It talks to the UPS over its serial interface (directly, or through a USB-to-
serial adapter such as a Prolific PL2303 presented as /dev/ttyUSB0) and serves
the data on the native NUT network protocol (TCP/3493). Any NUT client —
`upsc`, NUT Monitor, Home Assistant's NUT integration, `upsmon`, etc. — can then
read the UPS without the vendor software and without recompiling NUT.

Wire settings:
  Port    : 2400 baud, 8 data bits, 1 stop bit, no parity. RTS asserted, DTR deasserted.
  Command : 7 bytes  [cmd, p1, p2, p3, p4, checksum, 0x0D]
            checksum = (256 - (cmd + p1 + p2 + p3 + p4)) & 0xFF
  'Q' meters+status query -> 18-byte big-endian reply (see _decode_Q).
  A '5A 5A 5A' reply means the command was not recognized.

See PROTOCOL.md for the full frame/field reference.

Usage:
    pip install pyserial
    python3 sms_nut_bridge.py --device /dev/ttyUSB0            # run the NUT server
    python3 sms_nut_bridge.py --device /dev/ttyUSB0 --once     # single read, print, exit
    upsc sms@localhost
"""
import argparse
import socket
import struct
import sys
import threading
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("Missing dependency: pip install pyserial")

CR = 0x0D
READ_SIZE = 18  # bytes expected for the 'Q' reply


def checksum(cmd, params):
    """CK = (256 - (cmd + sum(params))) & 0xFF."""
    return (256 - (cmd + sum(params))) & 0xFF


def build_command(cmd, params=(0xFF, 0xFF, 0xFF, 0xFF)):
    """Raw 7-byte serial frame: cmd, 4 params, checksum, CR."""
    return bytes([cmd, *params, checksum(cmd, params), CR])


class SmsUps:
    """Serial transport + meters/status decode for SMS-compatible UPS units."""

    def __init__(self, device, baud=2400, read_timeout=1.0):
        self.device = device
        self.baud = baud
        self.read_timeout = read_timeout
        self.ser = None

    def open(self):
        self.ser = serial.Serial(
            port=self.device,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            timeout=self.read_timeout,
            rtscts=False,
            dsrdtr=False,
        )
        # Control-line states that power the UPS interface optocoupler.
        self.ser.setRTS(True)
        self.ser.setDTR(False)
        return self.device

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None

    def _read_exact(self, n, deadline_s=2.0):
        buf = bytearray()
        end = time.monotonic() + deadline_s
        while len(buf) < n and time.monotonic() < end:
            chunk = self.ser.read(n - len(buf))
            if chunk:
                buf += chunk
            elif buf and buf[-1] == CR:
                break
        return bytes(buf)

    def query(self):
        """Send 'Q' and return the decoded meters/status as a dict of NUT vars."""
        self.ser.reset_input_buffer()
        self.ser.write(build_command(0x51))  # 'Q'
        self.ser.flush()
        raw = self._read_exact(READ_SIZE)
        if b"\x5a\x5a\x5a" in raw:
            raise IOError("UPS replied 5A5A5A (command not recognized)")
        if len(raw) < 16:
            raise IOError(f"short reply ({len(raw)} bytes): {raw.hex()}")
        return self._decode_Q(raw)

    @staticmethod
    def _decode_Q(raw):
        be16 = lambda i: struct.unpack(">H", raw[i:i + 2])[0]
        last_v   = be16(1) / 10.0
        in_v     = be16(3) / 10.0
        out_v    = be16(5) / 10.0
        load     = be16(7) / 10.0
        out_hz   = be16(9) / 10.0
        batt_pct = be16(11) / 10.0   # per-mille: 1000 -> 100.0%
        temp     = be16(13) / 10.0
        st = raw[15]
        on_batt  = bool(st & 0x80)  # b7  on battery / utility fail
        low_batt = bool(st & 0x40)  # b6  battery low
        bypass   = bool(st & 0x20)  # b5  bypass active   (verify on hardware)
        boost    = bool(st & 0x10)  # b4  AVR boost/buck  (verify on hardware)
        ups_ok   = bool(st & 0x08)  # b3  UPS healthy     (verify on hardware)
        testing  = bool(st & 0x04)  # b2  self-test running
        shutdown = bool(st & 0x02)  # b1  shutdown active
        beep_on  = bool(st & 0x01)  # b0  buzzer enabled

        status = ["OB" if on_batt else "OL"]
        if low_batt: status.append("LB")
        if bypass:   status.append("BYPASS")
        if boost:    status.append("BOOST")
        if testing:  status.append("CAL")
        if shutdown: status.append("FSD")
        if not ups_ok and not on_batt: status.append("ALARM")

        return {
            "device.type": "ups",
            "driver.name": "sms_nut_bridge",
            "ups.status": " ".join(status),
            "ups.load": f"{load:.1f}",
            "ups.temperature": f"{temp:.1f}",
            "ups.beeper.status": "enabled" if beep_on else "disabled",
            "input.voltage": f"{in_v:.1f}",
            "output.voltage": f"{out_v:.1f}",
            "output.frequency": f"{out_hz:.1f}",
            "battery.charge": f"{batt_pct:.0f}",
            "_ultima_tensao": f"{last_v:.1f}",   # auxiliary field, not exported
        }


class NutServer:
    """Minimal NUT (upsd) TCP server: enough for upsc and NUT clients to read vars."""

    def __init__(self, ups_name, desc, get_vars, host="0.0.0.0", port=3493):
        self.ups_name = ups_name
        self.desc = desc
        self.get_vars = get_vars      # callable -> {var: value}
        self.host, self.port = host, port

    def serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(8)
        print(f"NUT server on {self.host}:{self.port}, ups '{self.ups_name}'")
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        f = conn.makefile("rwb", buffering=0)
        try:
            for line in f:
                line = line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                parts = line.split()
                cmd = parts[0].upper()
                if cmd == "LOGOUT":
                    f.write(b"OK Goodbye\n"); break
                f.write(self._handle(cmd, parts).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()

    def _handle(self, cmd, parts):
        u = self.ups_name
        if cmd == "VER":
            return "sms_nut_bridge 1.0\n"
        if cmd == "NETVER":
            return "1.3\n"
        if cmd in ("LOGIN", "USERNAME", "PASSWORD"):
            return "OK\n"
        if cmd == "STARTTLS":
            return "ERR FEATURE-NOT-SUPPORTED\n"
        if cmd == "LIST" and len(parts) >= 2:
            sub = parts[1].upper()
            if sub == "UPS":
                return f'BEGIN LIST UPS\nUPS {u} "{self.desc}"\nEND LIST UPS\n'
            if sub == "VAR" and len(parts) >= 3:
                if parts[2] != u:
                    return "ERR UNKNOWN-UPS\n"
                lines = [f"BEGIN LIST VAR {u}"]
                for k, v in self.get_vars().items():
                    if not k.startswith("_"):
                        lines.append(f'VAR {u} {k} "{v}"')
                lines.append(f"END LIST VAR {u}\n")
                return "\n".join(lines)
            if sub in ("RW", "CMD", "ENUM", "RANGE"):
                kind = parts[1]
                return f"BEGIN LIST {kind} {u}\nEND LIST {kind} {u}\n"
        if cmd == "GET" and len(parts) >= 4 and parts[1].upper() == "VAR":
            if parts[2] != u:
                return "ERR UNKNOWN-UPS\n"
            v = self.get_vars().get(parts[3])
            return f'VAR {u} {parts[3]} "{v}"\n' if v is not None else "ERR VAR-NOT-SUPPORTED\n"
        if cmd == "GET" and len(parts) >= 3 and parts[1].upper() == "NUMLOGINS":
            return f"NUMLOGINS {u} 1\n"
        if cmd == "GET" and len(parts) >= 3 and parts[1].upper() == "UPSDESC":
            return f'UPSDESC {u} "{self.desc}"\n'
        return "ERR UNKNOWN-COMMAND\n"


def main():
    ap = argparse.ArgumentParser(description="SMS-compatible UPS -> NUT bridge")
    ap.add_argument("--device", default="/dev/ttyUSB0", help="serial device")
    ap.add_argument("--baud", type=int, default=2400)
    ap.add_argument("--once", action="store_true", help="query once, print, exit")
    ap.add_argument("--raw", action="store_true", help="with --once, also print the raw hex frame")
    ap.add_argument("--name", default="sms", help="NUT ups name (clients use name@host)")
    ap.add_argument("--host", default="0.0.0.0", help="NUT bind address")
    ap.add_argument("--port", type=int, default=3493)
    ap.add_argument("--interval", type=float, default=3.0, help="poll seconds")
    args = ap.parse_args()

    ups = SmsUps(args.device, args.baud)
    ups.open()
    print(f"Opened {args.device} @ {args.baud} 8N1 (RTS=1, DTR=0)")

    if args.once:
        from pprint import pprint
        if args.raw:
            ups.ser.reset_input_buffer()
            ups.ser.write(build_command(0x51)); ups.ser.flush()
            raw = ups._read_exact(READ_SIZE)
            print("raw:", raw.hex(" "))
            pprint(SmsUps._decode_Q(raw))
        else:
            pprint(ups.query())
        ups.close()
        return

    cache = {"vars": {}, "ts": 0.0}
    lock = threading.Lock()

    def poll_loop():
        while True:
            try:
                v = ups.query()
                with lock:
                    cache["vars"], cache["ts"] = v, time.time()
            except Exception as e:
                print(f"poll error: {e}", file=sys.stderr)
            time.sleep(args.interval)

    def get_vars():
        with lock:
            return dict(cache["vars"])

    threading.Thread(target=poll_loop, daemon=True).start()
    NutServer(args.name, "SMS-compatible UPS", get_vars, args.host, args.port).serve()


if __name__ == "__main__":
    main()
