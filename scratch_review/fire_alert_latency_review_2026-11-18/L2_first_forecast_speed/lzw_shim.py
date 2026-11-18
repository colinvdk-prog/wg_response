"""Lane L2 measurement shim: a pure-Python TIFF LZW decoder registered with
tifffile, so real LFPS job zips (LZW GeoTIFFs) decode on this dev machine
without the `imagecodecs` wheel (the live image has it). Measurement only —
nothing under wgresponse/ imports this.

TIFF LZW: MSB-first codes of 9..12 bits, ClearCode 256, EOI 257, the code
width grows one code early (when the next free code reaches 511/1023/2047).
"""
from __future__ import annotations


def lzw_decode(encoded, out=None, **_kw):
    enc = bytes(encoded) + b"\x00\x00\x00"
    nbits = (len(enc) - 3) * 8
    table = [bytes((i,)) for i in range(256)] + [b"", b""]
    result = bytearray()
    bitpos = 0
    codelen = 9
    mask = 511
    prev = None
    append = table.append
    while bitpos + codelen <= nbits:
        byte = bitpos >> 3
        chunk = (enc[byte] << 16) | (enc[byte + 1] << 8) | enc[byte + 2]
        code = (chunk >> (24 - (bitpos & 7) - codelen)) & mask
        bitpos += codelen
        if code == 257:
            break
        if code == 256:
            del table[258:]
            codelen, mask, prev = 9, 511, None
            continue
        if prev is None:
            entry = table[code]
            result += entry
            prev = entry
            continue
        if code < len(table):
            entry = table[code]
            append(prev + entry[:1])
        else:
            entry = prev + prev[:1]
            append(entry)
        result += entry
        prev = entry
        n = len(table)
        if n == 511:
            codelen, mask = 10, 1023
        elif n == 1023:
            codelen, mask = 11, 2047
        elif n == 2047:
            codelen, mask = 12, 4095
    return bytes(result)


def install():
    """Register the decoder for COMPRESSION 5 in tifffile's codec map."""
    from tifffile import tifffile as tf
    tf.TIFF.DECOMPRESSORS._codecs[5] = lzw_decode
