"""Minimal binary Android XML (AXML) parser - zero dependency.

Extracts element attributes from a compiled AndroidManifest.xml so the
self-heal pipeline can pull secrets straight out of an APK without
apktool/aapt. Handles string pools (UTF-8/UTF-16), resource maps and typed
attribute values. Only what meta-data extraction needs is implemented.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

RES_STRING_POOL_TYPE = 0x0001
RES_XML_RESOURCE_MAP = 0x0180
RES_XML_START_NAMESPACE = 0x0100
RES_XML_END_NAMESPACE = 0x0101
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103

UTF8_FLAG = 1 << 8
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOL = 0x12


class AxmlError(ValueError):
    pass


@dataclass
class Element:
    name: str
    attrs: Dict[str, str] = field(default_factory=dict)


@dataclass
class AxmlDocument:
    elements: List[Element] = field(default_factory=list)

    def find_all(self, name: str) -> List[Element]:
        return [e for e in self.elements if e.name == name]

    def find_first(self, name: str) -> Optional[Element]:
        for e in self.elements:
            if e.name == name:
                return e
        return None


def _parse_string_pool(buf: bytes, offset: int) -> List[str]:
    (chunk_type, header_size, chunk_size, string_count, _style_count,
     flags, strings_start, _styles_start) = struct.unpack_from("<HHIIIIII", buf, offset)
    if chunk_type != RES_STRING_POOL_TYPE:
        raise AxmlError(f"expected string pool chunk, got 0x{chunk_type:04x}")
    strings: List[str] = []
    utf8 = bool(flags & UTF8_FLAG)
    base = offset + strings_start
    for i in range(string_count):
        (str_off,) = struct.unpack_from("<I", buf, offset + header_size + i * 4)
        pos = base + str_off
        if utf8:
            # u16len, u8len then bytes (lengths may be 2-word when high bit set)
            n, pos = _decode_len8(buf, pos)
            raw = buf[pos:pos + n]
            strings.append(raw.decode("utf-8", errors="replace"))
        else:
            n, pos = _decode_len16(buf, pos)
            raw = buf[pos:pos + n * 2]
            strings.append(raw.decode("utf-16-le", errors="replace"))
    return strings


def _decode_len8(buf: bytes, pos: int) -> Tuple[int, int]:
    b = buf[pos]
    if b & 0x80:
        return ((b & 0x7F) << 8) | buf[pos + 1], pos + 2
    return b, pos + 1


def _decode_len16(buf: bytes, pos: int) -> Tuple[int, int]:
    (w,) = struct.unpack_from("<H", buf, pos)
    if w & 0x8000:
        (w2,) = struct.unpack_from("<H", buf, pos + 2)
        return ((w & 0x7FFF) << 16) | w2, pos + 4
    return w, pos + 2


def _resolve_typed(strings: List[str], raw_value: int, data_type: int, data: int) -> str:
    if raw_value != 0xFFFFFFFF and raw_value != -1:
        try:
            return strings[raw_value] if raw_value < len(strings) else ""
        except IndexError:
            return ""
    if data_type == TYPE_STRING:
        return strings[data] if data < len(strings) else ""
    if data_type == TYPE_INT_BOOL:
        return "true" if data != 0 else "false"
    if data_type == TYPE_INT_HEX:
        return f"0x{data:x}"
    if data_type == TYPE_INT_DEC:
        return str(data)
    if data_type == 0x01:  # reference
        return f"@ref/0x{data:08x}"
    return f"@typed/0x{data_type:02x}:{data}"


def parse(data: bytes) -> AxmlDocument:
    if len(data) < 8:
        raise AxmlError("truncated document")
    doc = AxmlDocument()
    strings: List[str] = []
    pos = 0
    while pos + 8 <= len(data):
        chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, pos)
        if chunk_size < 8 or pos + chunk_size > len(data):
            break
        if chunk_type == 0x0003:          # RES_XML_TYPE document wrapper
            pos += header_size            # skip via header only; children follow
            continue
        if chunk_type == RES_STRING_POOL_TYPE:
            strings = _parse_string_pool(data, pos)
        elif chunk_type == RES_XML_START_ELEMENT:
            # ResXMLTree_node: [chunk hdr 8][line u32][comment u32]
            # attrExt starts at pos+header_size:
            #   ns u32 | name u32 | attributeStart u16 | attributeSize u16
            #   | attributeCount u16 | idIndex/classIndex/styleIndex u16 x3
            # attributes begin at (pos + header_size + attributeStart)
            body = pos + header_size
            (_ns, name_idx) = struct.unpack_from("<II", data, body)
            (attr_start, attr_size, attr_count) = struct.unpack_from("<HHH", data, body + 8)
            name = strings[name_idx] if name_idx < len(strings) else "?"
            el = Element(name=name)
            abase = body + attr_start
            for i in range(attr_count):
                aoff = abase + i * attr_size
                if aoff + 20 > len(data):
                    break
                (_ns, aname_idx, raw_value, _tsize, _tres0, dtype, ddata) = \
                    struct.unpack_from("<IIIHBBI", data, aoff)
                key = strings[aname_idx] if aname_idx < len(strings) else f"attr{aname_idx}"
                el.attrs[key] = _resolve_typed(strings, raw_value, dtype, ddata)
            doc.elements.append(el)
        pos += chunk_size
    return doc
