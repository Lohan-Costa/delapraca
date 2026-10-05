#!/usr/bin/env python3
import avb
from avb.essence import PCMADescriptor, MediaFileDescriptor
from avb.ioctx import AVBIOContext as IO

UNKNOWN_LOG = []
EXT02_LOG = []
_installed = False


def _pcma_read(self, f):
    MediaFileDescriptor.read(self, f)
    ctx = self.root.ictx
    ctx.read_assert_tag(f, 0x02)
    ctx.read_assert_tag(f, 0x01)
    self.channels = ctx.read_u16(f)
    self.quantization_bits = ctx.read_u16(f)
    self.sample_rate = ctx.read_exp10_encoded_float(f)
    self.locked = ctx.read_bool(f)
    self.audio_ref_level = ctx.read_s16(f)
    self.electro_spatial_formulation = ctx.read_s32(f)
    self.dial_norm = ctx.read_u16(f)
    self.coding_format = ctx.read_u32(f)
    self.block_align = ctx.read_u32(f)
    self.sequence_offset = ctx.read_u16(f)
    self.average_bps = ctx.read_u32(f)
    self.has_peak_envelope_data = ctx.read_bool(f)
    for k in ('peak_envelope_version', 'peak_envelope_format', 'points_per_peak_value',
              'peak_envelope_block_size', 'peak_channel_count', 'peak_frame_count'):
        setattr(self, k, ctx.read_s32(f))
    self.peak_of_peaks_offset = ctx.read_u64(f)
    self.peak_envelope_timestamp = ctx.read_s32(f)
    while True:
        pos = f.tell()
        mk = IO.read_u8(f)
        if mk != 0x01:
            f.seek(pos)
            break
        tag = IO.read_u8(f)
        if tag == 0x01:
            ctx.read_assert_tag(f, 77); self.ebu_timestamp = ctx.read_s64(f)
        elif tag == 0x03:
            ctx.read_assert_tag(f, 76); self.timecode_framerate = ctx.read_string(f)
        elif tag == 0x02:
            ctx.read_assert_tag(f, 0x47)
            val = ctx.read_u32(f)
            self.property_data["ext_unknown_0x02"] = val
            EXT02_LOG.append({"value": val})
        else:
            rest = f.read()
            UNKNOWN_LOG.append({"class_id": str(self.class_id), "tag": tag,
                                "payload_hex": bytes(rest[:64]).hex()})
            return
    ctx.read_assert_tag(f, 0x03)


def install():
    global _installed
    if _installed:
        return
    PCMADescriptor.read = _pcma_read
    _installed = True


def topen(path):
    import io

    install()
    fh = io.open(path, "rb", buffering=io.DEFAULT_BUFFER_SIZE)
    try:
        return avb.open(fh)
    except BaseException:
        fh.close()
        raise
