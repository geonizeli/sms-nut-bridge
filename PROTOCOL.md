# Serial protocol reference

This document describes the serial wire protocol used by SMS / PowerView-
compatible UPS (no-break) units, as implemented by this bridge. It is a
Megatec/"Q1"-style command set with a compact **binary** reply.

## Link settings

| Setting | Value |
|---|---|
| Baud rate | 2400 |
| Data bits | 8 |
| Stop bits | 1 |
| Parity | none |
| RTS | asserted (high) |
| DTR | deasserted (low) |

RTS/DTR states are significant — they power the optocoupler in the UPS's
interface. With the wrong states the unit will not answer.

## Command frame (host → UPS)

Seven bytes, sent raw:

```
[ cmd, p1, p2, p3, p4, checksum, 0x0D ]

checksum = (256 - (cmd + p1 + p2 + p3 + p4)) & 0xFF
```

`cmd` is the ASCII code of the command letter; unused parameters are `0xFF`.

| Letter | cmd | Purpose | Reply |
|--------|-----|---------|-------|
| `Q` | 0x51 | meters + status | 18-byte binary |
| `I` | 0x49 | model + firmware | text |
| `F` | 0x46 | ratings | text |
| `M` | 0x4D | toggle buzzer | none |
| `L` | 0x4C | test until battery low | none |
| `T` | 0x54 | test for N seconds | none |
| `S` | 0x53 | shutdown in N seconds | none |
| `R` | 0x52 | shutdown + restore | none |
| `D` | 0x44 | cancel test | none |
| `C` | 0x43 | cancel shutdown/restore | none |

Example — the `Q` query on the wire:

```
51 FF FF FF FF B3 0D
```

A reply containing `5A 5A 5A` means "command not recognized".

## `Q` reply (UPS → host)

18 bytes. All multi-byte integers are **big-endian, unsigned**. Each meter is
the integer value divided by 10.

| Offset | Field | Bytes | Scaling | NUT variable |
|--------|-------|-------|---------|--------------|
| 0 | type indicator (`<` `=` `>`) | 1 | ASCII | — |
| 1–2 | reference voltage | 2 | ÷10 V | (auxiliary) |
| 3–4 | input voltage | 2 | ÷10 V | `input.voltage` |
| 5–6 | output voltage | 2 | ÷10 V | `output.voltage` |
| 7–8 | load | 2 | ÷10 % | `ups.load` |
| 9–10 | output frequency | 2 | ÷10 Hz | `output.frequency` |
| 11–12 | battery level | 2 | ÷10 % | `battery.charge` |
| 13–14 | temperature | 2 | ÷10 °C | `ups.temperature` |
| 15 | status byte | 1 | bit flags | `ups.status` |
| 16 | checksum | 1 | — | — |
| 17 | terminator | 1 | `0x0D` | — |

### Status byte (offset 15)

Standard Megatec/Q1 bit layout:

| Bit | Flag | NUT status |
|-----|------|-----------|
| 7 | on battery / utility fail | `OB` (else `OL`) |
| 6 | battery low | `LB` |
| 5 | bypass active | `BYPASS` |
| 4 | AVR boost/buck active | `BOOST`/`TRIM` |
| 3 | UPS healthy | clear → `ALARM` |
| 2 | self-test running | `CAL` |
| 1 | shutdown active | `FSD` |
| 0 | buzzer enabled | `ups.beeper.status` |

Bits 0–2, 6, 7 are well established; bits 3–5 (bypass / boost / ok) should be
confirmed against a specific unit.

## Notes

- The `I` (information) and `F` (features/ratings) replies are ASCII text; the
  bridge currently uses only the `Q` reply.
- Some models also speak a text Megatec variant (`QGS`/`Q1` with a `(` reply
  start character); those are handled by NUT's stock drivers and are out of
  scope here.
