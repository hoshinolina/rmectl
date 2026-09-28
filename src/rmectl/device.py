import struct
import sys

import usb1

# commands:
# Set ctl reg 1: (clocks etc)
#  self.handle.controlWrite(0x40, 16, val, mask, b'')
# Read status
#  status = self.handle.controlRead(0xc0, 17, 0, 0, 16)
# Set ctl reg 2: (output config etc)
#  self.handle.controlWrite(0x40, 18, val, mask, b'')
# Write mixer value via ctl req:
#   self.handle.controlWrite(0x40, 19, val, nodeid, b'')
# Write mixer route via ctl req:
#   self.handle.controlWrite(0x40, 20, (dst << 9) | src, nodeid, b'')
# Set output fader:
#   self.handle.controlWrite(0x40, 21, gain, 0x100 + ch, b'')
# Set loopback:
#   self.handle.controlWrite(0x40, 22, 1 if enabled else 0, 0x100 + ch, b'')


class OutOfNodesError(Exception):
    pass

class DeviceError(Exception):
    pass

class Mixer:
    def __init__(self, iface, max_nodes):
        self.max_nodes = max_nodes
        self.iface = iface
        self.free_nodes = list(range(max_nodes))[::-1]
        self.node_map = {}
        self.buf = []

    def init(self):
        for i in range(self.max_nodes):
            self._add_cmd(0xC000FFFF | (i << 16))
        self.flush()

    def _add_cmd(self, cmd):
        print(hex(cmd))
        self.buf.append(struct.pack("<I", cmd))

    def set(self, src, dst, value):
        assert src < 0x1FF
        assert dst < 0x7F
        assert value <= 0xFFFF

        key = (src, dst)
        node = self.node_map.get(key, None)
        configure = False
        if node is None:
            if value == 0:
                return
            if not self.free_nodes:
                raise OutOfNodesError()
            node = self.free_nodes.pop()
            self.node_map[key] = node
            configure = True
        elif value == 0:
            del self.node_map[key]
            self.free_nodes.append(node)

        self._add_cmd((node << 16) | value)
        if configure:
            self._add_cmd(0x40000000 | (node << 16) | (dst << 9) | src)

    def flush(self):
        self.iface.send_mixer(b"".join(self.buf))
        self.buf = []


class RMEDigiface:
    INPUT_CHANNELS = 32
    OUTPUT_CHANNELS = 34
    INPUT_CHANNEL_NAMES = [
        [f"ADAT {i + 1}/{j + 1}"] for i in range(4) for j in range(8)
    ]
    INPUT_CHANNEL_NAMES[0].append("SPDIF 1L")
    INPUT_CHANNEL_NAMES[1].append("SPDIF 1R")
    INPUT_CHANNEL_NAMES[8].append("SPDIF 2L")
    INPUT_CHANNEL_NAMES[9].append("SPDIF 2R")
    INPUT_CHANNEL_NAMES[16].append("SPDIF 3L")
    INPUT_CHANNEL_NAMES[17].append("SPDIF 3R")
    INPUT_CHANNEL_NAMES[24].append("SPDIF 4L")
    INPUT_CHANNEL_NAMES[25].append("SPDIF 4R")
    OUTPUT_CHANNEL_NAMES = INPUT_CHANNEL_NAMES + [["PH L"], ["PH R"]]
    PLAYBACK_CHANNEL_NAMES = [[f"Playback {i + 1}"] for i in range(34)]

    INPUT_CHANNEL_MAP = {v: i for (i, x) in enumerate(INPUT_CHANNEL_NAMES) for v in x}
    OUTPUT_CHANNEL_MAP = {v: i for (i, x) in enumerate(OUTPUT_CHANNEL_NAMES) for v in x}
    PLAYBACK_CHANNEL_MAP = {
        v: i for (i, x) in enumerate(PLAYBACK_CHANNEL_NAMES) for v in x
    }

    INPUT_NAMES = ("In 1", "In 2", "In 3", "In 4")
    OUTPUT_NAMES = ("Out 1", "Out 2", "Out 3", "Out 4")
    SAMPLE_RATES = (
        32000,
        44100,
        48000,
        None,
        64000,
        88200,
        96000,
        None,
        128000,
        176400,
        192000,
        None,
    )
    VID = 0x2A39
    PID = 0x3F8C
    INTERFACE = 0x01
    EP_FEEDBACK = 0x83
    EP_LEVELS = 0x84
    EP_MIXER = 0x0B

    def __init__(self, handle, context=None):
        self.handle = handle
        self.context = context
        self.mixer = Mixer(self, 2048)
        self.iface = handle.claimInterface(self.INTERFACE)
        self.input_aliases = {}
        self.output_aliases = {}
        self.playback_aliases = {}

    @classmethod
    def open_first(cls):
        ctx = usb1.USBContext()
        handle = ctx.openByVendorIDAndProductID(
            cls.VID,
            cls.PID,
            skip_on_error=True,
        )
        if handle is None:
            raise DeviceError("Device not found")

        return cls(handle, ctx)

    def get_io_names(self):
        return {
            "inputs": self.INPUT_NAMES,
            "outputs": self.OUTPUT_NAMES,
        }

    def get_channel_names(self):
        return {
            "inputs": self.INPUT_CHANNEL_NAMES,
            "outputs": self.OUTPUT_CHANNEL_NAMES,
            "playbacks": self.PLAYBACK_CHANNEL_NAMES,
        }

    def get_sample_rates(self):
        return [i for i in self.SAMPLE_RATES if i]

    def _gain(self, gain, invert=False):
        if gain == float("-inf"):
            return 0

        val = min(0x10000, int(0x8000 * (10 ** (gain / 20.0))))
        assert val >= 0

        if val >= 0x4000:
            val >>= 3
            assert val < 0x4000
            val = val | 0x8000

        if invert:
            val = (val ^ 0x4FFF) + 1
        return val

    def _get_input(self, input):
        if isinstance(input, str):
            return self.INPUT_NAMES.index(input)
        else:
            return int(input)

    def _get_output(self, output):
        if isinstance(output, str):
            return self.OUTPUT_NAMES.index(output)
        else:
            return int(output)

    def _get_input_ch(self, input):
        if isinstance(input, str):
            input = self.input_aliases.get(input, input)
            return self.INPUT_CHANNEL_MAP[input]
        else:
            return int(input)

    def _get_output_ch(self, output):
        if isinstance(output, str):
            output = self.output_aliases.get(output, output)
            return self.OUTPUT_CHANNEL_MAP[output]
        else:
            return int(output)

    def init(self):
        # Keep the current sample rate set by driver
        self.handle.controlWrite(0x40, 16, 0x0000, 0x1F87, b"")
        self.handle.controlWrite(0x40, 18, 0x0004, 0xFFFF, b"")
        for i in self.OUTPUT_CHANNEL_NAMES:
            self.set_output_fader(i[0], 0, False)
        for i in self.INPUT_CHANNEL_NAMES:
            self.set_loopback(i[0], False)
        self.mixer.init()

    def set_sample_rate(self, rate):
        rate_idx = self.SAMPLE_RATES.index(rate)
        speed_mode = (rate_idx >> 2) + 2
        val = (rate_idx << 3) | (speed_mode << 12)
        self.handle.controlWrite(0x40, 16, val, 0x7078, b"")

    def set_clock_source(self, source):
        if source == "internal":
            idx = 0
        else:
            idx = self._get_input(source) + 1

        self.handle.controlWrite(0x40, 16, idx, 0x0007, b"")

    def set_output_mode(self, output, mode):
        idx = self._get_output(output)
        mask = [0x1, 0x2, 0x8, 0x10][idx]
        spdif = ["adat", "spdif"].index(mode)
        val = mask if spdif else 0
        self.handle.controlWrite(0x40, 18, val, mask, b"")

    def set_tms_enabled(self, enabled):
        self.handle.controlWrite(0x40, 18, 0x40 if not enabled else 0, 0x40, b"")

    def set_mixer_enabled(self, enabled):
        self.handle.controlWrite(0x40, 18, 0x100 if not enabled else 0, 0x100, b"")
        self.handle.controlWrite(0x40, 16, 0x400 if not enabled else 0, 0x400, b"")

    def set_mixer(self, *maps):
        maps2 = []
        for m in maps:
            src = m["src"]
            dst = m["dst"]
            if isinstance(src, str) and src.endswith(" LR"):
                assert dst.endswith(" LR")
                for ch in "LR":
                    m["src"] = src[:-2] + ch
                    m["dst"] = dst[:-2] + ch
                    maps2.append(dict(m))
            else:
                maps2.append(m)

        for m in maps2:
            src = m["src"]
            if src in self.playback_aliases:
                src = self.PLAYBACK_CHANNEL_MAP[self.playback_aliases[src]] + 0x100
            elif src in self.PLAYBACK_CHANNEL_MAP:
                src = self.PLAYBACK_CHANNEL_MAP[src] + 0x100
            else:
                src = self._get_input_ch(src)
            dst = self._get_output_ch(m["dst"])
            gain = self._gain(m["gain"], m.get("invert", False))
            self.mixer.set(src, dst, gain)
        self.mixer.flush()

    def set_output_fader(self, output, gain, invert=False):
        ch = self._get_output_ch(output)
        gain = self._gain(gain, invert)
        self.handle.controlWrite(0x40, 21, gain, 0x100 + ch, b"")

    def set_loopback(self, ch, enabled):
        ch = self._get_input_ch(ch)
        self.handle.controlWrite(0x40, 22, 1 if enabled else 0, 0x100 + ch, b"")

    def send_mixer(self, data):
        self.handle.bulkWrite(self.EP_MIXER, data)

    def alias_input(self, name, alias):
        assert name in self.INPUT_CHANNEL_MAP
        self.input_aliases[alias] = name

    def alias_output(self, name, alias):
        assert name in self.OUTPUT_CHANNEL_MAP
        self.output_aliases[alias] = name

    def alias_playback(self, name, alias):
        assert name in self.PLAYBACK_CHANNEL_MAP
        self.playback_aliases[alias] = name

    def get_status(self):
        status = self.handle.controlRead(0xC0, 17, 0, 0, 16)
        w0, w1, w2, w3 = struct.unpack("<4I", status)
        print(f"Raw status: {w0:08x} {w1:08x} {w2:08x} {w3:08x}")
        current_sync = (w0 >> 10) & 7
        selected_sync = w3 & 7
        ret = {
            "sync": {
                "configured": "internal" if selected_sync == 0 else (selected_sync - 1),
                "current": "internal" if current_sync == 7 else (current_sync - 1),
            },
            "sample_rate": {
                "configured": self.SAMPLE_RATES[(w3 >> 3) & 15],
                "current": self.SAMPLE_RATES[(w1 >> 20) & 15],
                "speed_mode": ["x1", "x2", "x4"][((w3 >> 12) & 15) - 2],
            },
            "tms_enabled": not bool((w3 >> 22) & 1),
            "mixer_enabled": not bool((w3 >> 24) & 1),
            "inputs": [],
            "outputs": [],
        }
        for i, name in enumerate(self.INPUT_NAMES):
            ret["inputs"].append(
                {
                    "name": name,
                    "status": "invalid"
                    if not (w0 & (1 << i))
                    else ("lock" if not (w0 & (1 << (5 + i))) else "sync"),
                    "format": "adat" if (w0 & (1 << [16, 13, 14, 15][i])) else "spdif",
                    "sample_rate": self.SAMPLE_RATES[(w1 >> (4 * i)) & 0xF],
                }
            )
        for i, name in enumerate(self.OUTPUT_NAMES):
            ret["outputs"].append(
                {
                    "name": name,
                    "format": "spdif" if (w3 & (1 << [16, 17, 19, 20][i])) else "adat",
                }
            )
        return ret

    def get_feedback(self):
        xfers = [self.handle.getTransfer() for i in range(16)]

        global cnt
        cnt = 0

        def cb(xfer):
            global cnt
            sys.stdout.write(xfer.getBuffer().hex() + " ")
            cnt += 1
            if cnt >= 9:
                cnt = 0
                print()
            xfer.submit()

        for xfer in xfers:
            xfer.setInterrupt(self.EP_FEEDBACK, 8, cb)
            xfer.submit()
