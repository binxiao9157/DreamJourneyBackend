# 3900-second relay test assumptions

This is a synthetic capacity check, not an exact measurement of a real Provider or device session.

- Duration: 3,900 seconds.
- Direction model: symmetric upstream and downstream binary audio.
- Sampling assumption per direction: 16,000 samples/second, 16-bit mono PCM.
- Framing: 50 frames/second, therefore 640 PCM bytes/frame.
- Synthetic Provider envelope: 16 bytes/frame, producing 656-byte encoded frames.
- Payload formula: `656 * (3900 * 50) * 2 = 255,840,000` bytes.
- Synthetic RFC 6455 wire estimate: upstream frame `656 + 8`, downstream frame `656 + 4`; total `258,180,000` bytes.
- Both totals remain below the configured 1 GiB synthetic session budget.

The check does not claim the real SDK uses identical bidirectional sample rates, a 16-byte envelope, or this exact traffic total.
