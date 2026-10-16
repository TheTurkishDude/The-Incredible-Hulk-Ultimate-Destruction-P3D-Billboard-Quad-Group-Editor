#!/usr/bin/env python3
"""
p3d_bqg_tool.py - Billboard Quad Group viewer / editor / exporter / importer
for Radical Entertainment Pure3D (.p3d) files (The Incredible Hulk: Ultimate Destruction).

The Tool Is Made By TheTurkishDude

Only the Python standard library is required (tkinter for the GUI; numpy is optional and enables the
textured 3D preview). Texture formats supported: PS2 (P3DI), Xbox (DDS DXT1/3/5 + PNG) and GameCube (PNG + DDS).

USAGE
    python p3d_bqg_tool.py                       # open GUI
    python p3d_bqg_tool.py file.p3d              # open GUI with a file
    python p3d_bqg_tool.py --list file.p3d       # print all billboard quad groups
    python p3d_bqg_tool.py --export file.p3d out.json
    python p3d_bqg_tool.py --import file.p3d in.json out.p3d   # replace groups with same name, append new
    python p3d_bqg_tool.py --roundtrip file.p3d  # self test: parse -> write must be byte-identical
    python p3d_bqg_tool.py --textures file.p3d outdir          # dump every texture as PNG
    python p3d_bqg_tool.py --render file.p3d sheet.png [shared.p3d ...]   # textured contact sheet (needs numpy)
    python p3d_bqg_tool.py file.p3d [shared.p3d ...]           # GUI; extra files supply shaders/textures
    (--list also accepts extra .p3d files and then shows which texture every group's shader resolves to)

FILE FORMAT (as observed in c01m05.p3d)
    File   : "P3D\\xFF" u32 headerSize(=12) u32 fileSize, then top-level chunks.
    Chunk  : u32 id, u32 headerSize (incl. these 12 bytes), u32 totalSize (header + children),
             (headerSize-12) bytes of chunk data, then child chunks until totalSize.
    String : u8 length (already padded up to a multiple of 4), bytes, zero padded.

    0x17006 BillboardQuadGroup
        u32 version, str name, str shader, u32 zTest, u32 zWrite, u32 fog?, u32 ?, u32 numQuads
        (the 5 u32 after the shader are: [0]=0, [1]=1, [2]=0, [3]=0, [4]=numQuads in this file;
         only the last one is certain - it always equals the number of 0x17005 children)
        children: 0x17007 (group transform), 0x17005 * numQuads, 0x122000, [0x121204 anim link],
                  0x10003 (bbox), 0x10004 (bsphere)
    0x17005 BillboardQuad
        u32 version, str name, u32 flags(0), u32 one(1), char[4] mode ("AAX\\0","LYAX","NOAX"),
        u32 colour (0xAARRGGBB), f32 width, f32 height, f32 distance
        children: 0x17007 (display info), 0x17009 (uv block)
    0x17007 BillboardDisplayInfo : u32 version, quaternion xyzw, translation xyz
    0x17009 BillboardUVInfo      : u32 version, 6 x vec2  [extraA, uv0, uv1, uv2, uv3, extraB]
    0x122000                     : u32 version, f32 (0.45..0.5 here; probably a sort/draw value)
    0x121204 / 0x121201          : link to an animation named "BQG_<group>" (kept verbatim)
    0x10003 bbox : 6 f32 (no version)     0x10004 bsphere : 4 f32 (no version)

SHADER -> TEXTURE
    group.shader (a string such as SS_200050_XXELECDECAYGREY_66B001XX) names a 0x11000 shader chunk:
        str name, u32 version, str pddi ("simple"), u32 translucent, vertexNeeds, vertexMask, numParams
        children 0x11002 (char[4] tag + str)  tag "TEX\0" = texture name, e.g. "ELECDECAYGREY.BMP.0"
                 0x11003 (char[4] tag + u32)  FIMD (filter), BLMD (blend; 2 = additive), CBVP, CBVY
    The texture is the 0x19000 chunk with that name (str name, 9 x u32: version, w, h, bpp, alphaDepth,
    mips, type, usage, priority). Children 0x19001 = one image per mip level, each holding 0x19002 with a
    "P3DI" payload (see decode_texture). Shaders/textures that are not in the edited file usually live in
    another .p3d (shared/common); pass it as an extra file. If the shader chunk is missing the texture is
    guessed from the shader name (SS_<n>_XX<TEXTURE>_<hh>B<nnn>XX) - flagged as "name heuristic".
    Palette alpha is PS2 range (0..0x80) and is doubled; palettes use the PS2 CSM1 entry order.

Field names that are marked "?" are interpretations; the tool always preserves the raw bytes
of anything it does not understand, so a no-change save is byte identical.
"""
import json
import math
import os
import struct
import sys

CREDIT = "The Tool Is Made By TheTurkishDude"
SIG = b"P3D\xff"

ID_BQG = 0x17006
ID_BQ = 0x17005
ID_DISPLAY = 0x17007
ID_UV = 0x17009
ID_FLOAT = 0x122000
ID_ANIMLIST = 0x121204
ID_BBOX = 0x10003
ID_BSPHERE = 0x10004


# ----------------------------------------------------------------------------
# generic chunk tree (lossless)
# ----------------------------------------------------------------------------
class Chunk:
    __slots__ = ("id", "data", "children")

    def __init__(self, cid, data=b"", children=None):
        self.id = cid
        self.data = bytes(data)          # bytes between the 12 byte header and the first child
        self.children = children or []

    def size(self):
        return 12 + len(self.data) + sum(c.size() for c in self.children)

    def pack(self, out):
        hs = 12 + len(self.data)
        out += struct.pack("<III", self.id, hs, self.size())
        out += self.data
        for c in self.children:
            c.pack(out)

    def find(self, cid):
        return [c for c in self.children if c.id == cid]


def parse_chunks(buf, off, end):
    res = []
    while off < end:
        if off + 12 > end:
            raise ValueError("truncated chunk header at 0x%X" % off)
        cid, hs, ts = struct.unpack_from("<III", buf, off)
        if hs < 12 or ts < hs or off + ts > end:
            raise ValueError("bad chunk 0x%X at 0x%X (hs=%d ts=%d)" % (cid, off, hs, ts))
        res.append(Chunk(cid, buf[off + 12:off + hs], parse_chunks(buf, off + hs, off + ts)))
        off += ts
    return res


class P3DFile:
    def __init__(self):
        self.header_extra = b""
        self.chunks = []

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            buf = f.read()
        if buf[:4] != SIG:
            raise ValueError("not a Pure3D file (missing 'P3D\\xFF' signature)")
        hs, total = struct.unpack_from("<II", buf, 4)
        p = cls()
        p.header_extra = buf[12:hs]
        p.chunks = parse_chunks(buf, hs, min(total, len(buf)))
        return p

    def to_bytes(self):
        body = bytearray()
        for c in self.chunks:
            c.pack(body)
        hs = 12 + len(self.header_extra)
        return SIG + struct.pack("<II", hs, hs + len(body)) + self.header_extra + bytes(body)

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.to_bytes())


# ----------------------------------------------------------------------------
# string helpers
# ----------------------------------------------------------------------------
def read_str(buf, o):
    n = buf[o]
    return buf[o + 1:o + 1 + n].split(b"\0")[0].decode("latin1"), o + 1 + n


def pack_str(s):
    b = s.encode("latin1")
    n = (len(b) + 3) & ~3
    if n > 255:
        raise ValueError("string too long: " + s)
    return bytes([n]) + b + b"\0" * (n - len(b))


# ----------------------------------------------------------------------------
# typed billboard model
# ----------------------------------------------------------------------------
class Quad:
    def __init__(self):
        self.name = "newQuad"
        self.flags = 0
        self.one = 1
        self.mode = "AAX"                       # 4 char tag (stored NUL padded)
        self.colour = 0xFFFFFFFF                # 0xAARRGGBB
        self.width = 1.0
        self.height = 1.0
        self.distance = 0.0
        self.rot = [0.0, 0.0, 0.0, 1.0]         # quaternion x y z w
        self.pos = [0.0, 0.0, 0.0]
        self.uv = [[0, 0], [1, 0], [1, 1], [0, 1]]
        self.extra_a = [0.0, 0.0]
        self.extra_b = [0.0, 0.0]
        self.versions = [0, 0, 0]               # quad, display, uv
        self.disp_raw = None                    # original chunk objects (for unknown extras)
        self.unknown_children = []

    # -- load / store
    @classmethod
    def from_chunk(cls, ch):
        q = cls()
        d = ch.data
        q.versions[0] = struct.unpack_from("<I", d, 0)[0]
        q.name, o = read_str(d, 4)
        q.flags, q.one = struct.unpack_from("<II", d, o); o += 8
        q.mode = d[o:o + 4].rstrip(b"\0").decode("latin1"); o += 4
        q.colour = struct.unpack_from("<I", d, o)[0]; o += 4
        q.width, q.height, q.distance = struct.unpack_from("<3f", d, o)
        for c in ch.children:
            if c.id == ID_DISPLAY:
                v = struct.unpack_from("<I7f", c.data, 0)
                q.versions[1] = v[0]
                q.rot = list(v[1:5])
                q.pos = list(v[5:8])
            elif c.id == ID_UV:
                v = struct.unpack_from("<I12f", c.data, 0)
                q.versions[2] = v[0]
                f = v[1:]
                q.extra_a = list(f[0:2])
                q.uv = [list(f[2:4]), list(f[4:6]), list(f[6:8]), list(f[8:10])]
                q.extra_b = list(f[10:12])
            else:
                q.unknown_children.append(c)
        return q

    def to_chunk(self):
        data = struct.pack("<I", self.versions[0]) + pack_str(self.name)
        data += struct.pack("<II", self.flags, self.one)
        data += self.mode.encode("latin1")[:4].ljust(4, b"\0")
        data += struct.pack("<I3f", self.colour & 0xFFFFFFFF, self.width, self.height, self.distance)
        disp = Chunk(ID_DISPLAY, struct.pack("<I7f", self.versions[1], *self.rot, *self.pos))
        uvs = [self.extra_a[0], self.extra_a[1]]
        for p in self.uv:
            uvs += [p[0], p[1]]
        uvs += [self.extra_b[0], self.extra_b[1]]
        uvc = Chunk(ID_UV, struct.pack("<I12f", self.versions[2], *uvs))
        return Chunk(ID_BQ, data, [disp, uvc] + self.unknown_children)

    def corners(self):
        """Quad corners in group-local space (before the billboard is turned to face camera)."""
        w, h = self.width / 2.0, self.height / 2.0
        pts = [(-w, -h, 0), (w, -h, 0), (w, h, 0), (-w, h, 0)]
        return [vadd(qrot(self.rot, p), self.pos) for p in pts]


class QuadGroup:
    def __init__(self):
        self.name = "newGroup"
        self.shader = "SS_200050_XXSTAR_FLARE_66B000XX"
        self.version = 0
        self.params = [0, 1, 0, 0]              # the 4 u32 before numQuads
        self.group_rot = [0.0, 0.0, 0.0, 1.0]
        self.group_pos = [0.0, 0.0, 0.0]
        self.group_disp_ver = 0
        self.sort_value = 0.5                   # chunk 0x122000
        self.sort_ver = 0
        self.quads = []
        self.bbox = [0.0] * 6
        self.bsphere = [0.0] * 4
        self.anim_chunk = None                  # 0x121204 kept verbatim
        self.other_children = []                # anything else, kept verbatim (in order)
        self._src_order = None

    @classmethod
    def from_chunk(cls, ch):
        g = cls()
        d = ch.data
        g.version = struct.unpack_from("<I", d, 0)[0]
        g.name, o = read_str(d, 4)
        g.shader, o = read_str(d, o)
        vals = struct.unpack_from("<5I", d, o)
        g.params = list(vals[:4])
        for c in ch.children:
            if c.id == ID_DISPLAY:
                v = struct.unpack_from("<I7f", c.data, 0)
                g.group_disp_ver, g.group_rot, g.group_pos = v[0], list(v[1:5]), list(v[5:8])
            elif c.id == ID_BQ:
                g.quads.append(Quad.from_chunk(c))
            elif c.id == ID_FLOAT:
                g.sort_ver, g.sort_value = struct.unpack_from("<If", c.data, 0)
            elif c.id == ID_BBOX:
                g.bbox = list(struct.unpack_from("<6f", c.data, 0))
            elif c.id == ID_BSPHERE:
                g.bsphere = list(struct.unpack_from("<4f", c.data, 0))
            elif c.id == ID_ANIMLIST:
                g.anim_chunk = c
            else:
                g.other_children.append(c)
        return g

    def to_chunk(self):
        data = struct.pack("<I", self.version) + pack_str(self.name) + pack_str(self.shader)
        data += struct.pack("<5I", *self.params, len(self.quads))
        kids = [Chunk(ID_DISPLAY, struct.pack("<I7f", self.group_disp_ver, *self.group_rot, *self.group_pos))]
        kids += [q.to_chunk() for q in self.quads]
        kids.append(Chunk(ID_FLOAT, struct.pack("<If", self.sort_ver, self.sort_value)))
        if self.anim_chunk is not None:
            kids.append(self.anim_chunk)
        kids.append(Chunk(ID_BBOX, struct.pack("<6f", *self.bbox)))
        kids.append(Chunk(ID_BSPHERE, struct.pack("<4f", *self.bsphere)))
        kids += self.other_children
        return Chunk(ID_BQG, data, kids)

    def recompute_bounds(self):
        pts = [c for q in self.quads for c in q.corners()]
        if not pts:
            self.bbox, self.bsphere = [0.0] * 6, [0.0] * 4
            return
        mn = [min(p[i] for p in pts) for i in range(3)]
        mx = [max(p[i] for p in pts) for i in range(3)]
        c = [(mn[i] + mx[i]) / 2 for i in range(3)]
        r = max(math.dist(c, p) for p in pts)
        self.bbox, self.bsphere = mn + mx, c + [r]


# ----------------------------------------------------------------------------
# tiny vector helpers
# ----------------------------------------------------------------------------
def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def qrot(q, v):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    # v' = v + 2w(q x v) + 2 q x (q x v)
    cx = (y * v[2] - z * v[1], z * v[0] - x * v[2], x * v[1] - y * v[0])
    cx2 = (y * cx[2] - z * cx[1], z * cx[0] - x * cx[2], x * cx[1] - y * cx[0])
    return (v[0] + 2 * (w * cx[0] + cx2[0]),
            v[1] + 2 * (w * cx[1] + cx2[1]),
            v[2] + 2 * (w * cx[2] + cx2[2]))


def quat_from_euler(rx, ry, rz):
    """degrees -> quaternion (xyzw), applied Z * Y * X"""
    rx, ry, rz = (math.radians(a) / 2 for a in (rx, ry, rz))
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    return [sx * cy * cz - cx * sy * sz, cx * sy * cz + sx * cy * sz,
            cx * cy * sz - sx * sy * cz, cx * cy * cz + sx * sy * sz]


def quat_to_euler(q):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    sinr = 2 * (w * x + y * z); cosr = 1 - 2 * (x * x + y * y)
    sinp = max(-1.0, min(1.0, 2 * (w * y - z * x)))
    siny = 2 * (w * z + x * y); cosy = 1 - 2 * (y * y + z * z)
    return [math.degrees(math.atan2(sinr, cosr)), math.degrees(math.asin(sinp)), math.degrees(math.atan2(siny, cosy))]


# ----------------------------------------------------------------------------
# document level helpers
# ----------------------------------------------------------------------------
def find_groups(p3d):
    """Returns list of (index in p3d.chunks, QuadGroup)"""
    return [(i, QuadGroup.from_chunk(c)) for i, c in enumerate(p3d.chunks) if c.id == ID_BQG]


def groups_to_json(groups):
    out = {"format": "p3d-billboard-quad-groups", "version": 1, "tool": CREDIT, "groups": []}
    for g in groups:
        gd = {
            "name": g.name, "shader": g.shader, "version": g.version, "params": g.params,
            "group_rotation_xyzw": g.group_rot, "group_translation": g.group_pos,
            "sort_value_0x122000": g.sort_value, "bbox": g.bbox, "bsphere": g.bsphere,
            "animation_link_hex": g.anim_chunk and chunk_to_hex(g.anim_chunk),
            "quads": [],
        }
        for q in g.quads:
            gd["quads"].append({
                "name": q.name, "flags": q.flags, "one": q.one, "mode": q.mode,
                "colour_argb": "0x%08X" % q.colour, "width": q.width, "height": q.height,
                "distance": q.distance, "rotation_xyzw": q.rot, "translation": q.pos,
                "uv": q.uv, "extra_a": q.extra_a, "extra_b": q.extra_b,
            })
        out["groups"].append(gd)
    return out


def chunk_to_hex(c):
    b = bytearray(); c.pack(b); return bytes(b).hex()


def chunk_from_hex(h):
    return parse_chunks(bytes.fromhex(h), 0, len(h) // 2)[0]


def groups_from_json(obj):
    groups = []
    for gd in obj["groups"]:
        g = QuadGroup()
        g.name, g.shader = gd["name"], gd["shader"]
        g.version = gd.get("version", 0)
        g.params = list(gd.get("params", [0, 1, 0, 0]))
        g.group_rot = gd.get("group_rotation_xyzw", [0, 0, 0, 1])
        g.group_pos = gd.get("group_translation", [0, 0, 0])
        g.sort_value = gd.get("sort_value_0x122000", 0.5)
        g.bbox = gd.get("bbox", [0] * 6)
        g.bsphere = gd.get("bsphere", [0] * 4)
        if gd.get("animation_link_hex"):
            g.anim_chunk = chunk_from_hex(gd["animation_link_hex"])
        for qd in gd["quads"]:
            q = Quad()
            q.name, q.flags, q.one, q.mode = qd["name"], qd.get("flags", 0), qd.get("one", 1), qd.get("mode", "AAX")
            c = qd["colour_argb"]
            q.colour = int(c, 16) if isinstance(c, str) else int(c)
            q.width, q.height, q.distance = qd["width"], qd["height"], qd.get("distance", 0.0)
            q.rot, q.pos = qd.get("rotation_xyzw", [0, 0, 0, 1]), qd.get("translation", [0, 0, 0])
            q.uv = qd.get("uv", q.uv)
            q.extra_a, q.extra_b = qd.get("extra_a", [0, 0]), qd.get("extra_b", [0, 0])
            g.quads.append(q)
        groups.append(g)
    return groups


def merge_groups(p3d, groups):
    """Replace same-named groups in place, append new ones after the last existing group
    (or at the end of the file). Returns (replaced, added)."""
    by_name = {}
    for i, c in enumerate(p3d.chunks):
        if c.id == ID_BQG:
            by_name[read_str(c.data, 4)[0]] = i
    rep = add = 0
    last = max(by_name.values()) if by_name else len(p3d.chunks) - 1
    for g in groups:
        ch = g.to_chunk()
        if g.name in by_name:
            p3d.chunks[by_name[g.name]] = ch; rep += 1
        else:
            last += 1
            p3d.chunks.insert(last, ch); add += 1
    return rep, add


def replace_all_groups(p3d, groups):
    """Replace the whole set of billboard quad groups (supports deletions)."""
    idx = [i for i, c in enumerate(p3d.chunks) if c.id == ID_BQG]
    pos = idx[0] if idx else len(p3d.chunks)
    p3d.chunks = [c for c in p3d.chunks if c.id != ID_BQG]
    for k, g in enumerate(groups):
        p3d.chunks.insert(pos + k, g.to_chunk())


# ----------------------------------------------------------------------------
# shaders / textures (0x11000 -> 'TEX' param -> 0x19000)  + software preview renderer
# ----------------------------------------------------------------------------
import re
import zlib

ID_SHADER = 0x11000
ID_SH_STR = 0x11002      # shader string parameter: char[4] tag, str value   ("TEX\0" = texture name)
ID_SH_INT = 0x11003      # shader integer parameter: char[4] tag, u32 value  ("BLMD" = blend mode, ...)
ID_TEXTURE = 0x19000
ID_IMAGE = 0x19001
ID_IMGDATA = 0x19002

BLEND_NAMES = {0: "none", 1: "alpha", 2: "additive", 3: "subtractive", 4: "modulate"}  # names = Pure3D convention, not verified here
SHADER_NAME_RE = re.compile(r"^SS_\d+_XX(.+?)_[0-9A-Fa-f]{2}B[0-9A-Fa-f]{3}XX$")


def parse_shader(c):
    d = c.data
    name, o = read_str(d, 0)
    version = struct.unpack_from("<I", d, o)[0]; o += 4
    pddi, o = read_str(d, o)
    translucent, vneeds, vmask, nparams = struct.unpack_from("<4I", d, o)
    info = {"name": name, "version": version, "pddi": pddi, "translucent": translucent,
            "tex": None, "blend": None, "params": {}}
    for k in c.children:
        tag = k.data[:4].rstrip(b"\0").decode("latin1")
        if k.id == ID_SH_STR:
            info["params"][tag] = read_str(k.data, 4)[0]
        elif k.id == ID_SH_INT:
            info["params"][tag] = struct.unpack_from("<I", k.data, 4)[0]
    info["tex"] = info["params"].get("TEX")
    info["blend"] = info["params"].get("BLMD")
    return info


def _unswizzle_palette(pal):
    """PS2 CSM1 palette order: inside every 32 entries, entries 8..15 and 16..23 are swapped."""
    out = bytearray(len(pal))
    for i in range(256):
        j = i
        k = (i >> 3) & 3
        if k == 1:
            j = i + 8
        elif k == 2:
            j = i - 8
        out[i * 4:i * 4 + 4] = pal[j * 4:j * 4 + 4]
    return bytes(out)


def _unswizzle8(buf, w, h):
    """PS2 GS PSMT8 -> linear (the commonly used 16x16 block / column layout)."""
    out = bytearray(w * h)
    for y in range(h):
        by = (y & ~0xF) * w
        sel_rows = (((y + 2) >> 2) & 1) * 4
        posy = (((y & ~3) >> 1) + (y & 1)) & 7
        row_base = by + posy * w * 2
        yb = (y >> 1) & 1
        o = y * w
        for x in range(w):
            idx = row_base + (x & ~0xF) * 2 + ((x + sel_rows) & 7) * 4 + yb + ((x >> 2) & 2)
            out[o + x] = buf[idx]
    return bytes(out)


def texture_info(chunk):
    name, o = read_str(chunk.data, 0)
    v = struct.unpack_from("<9I", chunk.data, o)
    return {"name": name, "width": v[1], "height": v[2], "bpp": v[3], "alpha_depth": v[4],
            "mips": len([k for k in chunk.children if k.id == ID_IMAGE])}


def _c565(c):
    return (((c >> 11) & 31) * 255 + 15) // 31, (((c >> 5) & 63) * 255 + 31) // 63, ((c & 31) * 255 + 15) // 31


def _dxt_colors(c0, c1, four):
    r0, g0, b0 = _c565(c0)
    r1, g1, b1 = _c565(c1)
    if four or c0 > c1:
        return [(r0, g0, b0, 255), (r1, g1, b1, 255),
                ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255),
                ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255)]
    return [(r0, g0, b0, 255), (r1, g1, b1, 255),
            ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255), (0, 0, 0, 0)]


def _decode_dxt(data, w, h, kind):
    """kind 1 = DXT1 (8 byte blocks), 3 = DXT3, 5 = DXT5 (16 byte blocks). -> rgba bytes or None"""
    bw, bh = (w + 3) // 4, (h + 3) // 4
    bs = 8 if kind == 1 else 16
    if len(data) < bw * bh * bs:
        return None
    out = bytearray(w * h * 4)
    pos = 0
    for by in range(bh):
        for bx in range(bw):
            blk = data[pos:pos + bs]; pos += bs
            c0, c1, idx = struct.unpack_from("<HHI", blk, bs - 8)
            cols = _dxt_colors(c0, c1, kind != 1)
            alphas = None
            if kind == 3:
                bits = int.from_bytes(blk[:8], "little")
                alphas = [((bits >> (4 * i)) & 15) * 17 for i in range(16)]
            elif kind == 5:
                a0, a1 = blk[0], blk[1]
                bits = int.from_bytes(blk[2:8], "little")
                if a0 > a1:
                    tbl = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
                else:
                    tbl = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
                alphas = [tbl[(bits >> (3 * i)) & 7] for i in range(16)]
            for py in range(4):
                y = by * 4 + py
                if y >= h:
                    break
                for px in range(4):
                    x = bx * 4 + px
                    if x >= w:
                        break
                    i = py * 4 + px
                    r, g, b, a = cols[(idx >> (2 * i)) & 3]
                    if alphas is not None:
                        a = alphas[i]
                    o = (y * w + x) * 4
                    out[o] = r; out[o + 1] = g; out[o + 2] = b; out[o + 3] = a
    return bytes(out)


def _decode_dds_raw(data, w, h, bitcount, masks):
    bpp = bitcount // 8
    if bpp not in (2, 3, 4) or len(data) < w * h * bpp:
        return None
    info = []
    for m in masks:
        if m == 0:
            info.append(None); continue
        sh = (m & -m).bit_length() - 1
        info.append((m, sh, (m >> sh)))
    out = bytearray(w * h * 4)
    for i in range(w * h):
        v = int.from_bytes(data[i * bpp:i * bpp + bpp], "little")
        for c in range(4):
            if info[c] is None:
                out[i * 4 + c] = 255 if c == 3 else 0
            else:
                m, sh, mx = info[c]
                out[i * 4 + c] = ((v & m) >> sh) * 255 // mx
    return bytes(out)


def decode_dds(b):
    """Microsoft DDS file (Xbox/GameCube builds) -> (w, h, rgba). DXT1/3/5 and uncompressed RGB(A)."""
    if len(b) < 128 or b[:4] != b"DDS ":
        return None
    h, w = struct.unpack_from("<2I", b, 12)
    pf_flags = struct.unpack_from("<I", b, 80)[0]
    fcc = b[84:88]
    data = b[128:]
    kinds = {b"DXT1": 1, b"DXT3": 3, b"DXT5": 5}
    if fcc in kinds:
        rgba = _decode_dxt(data, w, h, kinds[fcc])
    elif pf_flags & 0x40:
        bitcount, rm, gm, bm, am = struct.unpack_from("<5I", b, 88)
        rgba = _decode_dds_raw(data, w, h, bitcount, (rm, gm, bm, am))
    else:
        rgba = None
    return (w, h, rgba) if rgba else None


def decode_png(b):
    """Minimal PNG decoder (non-interlaced, bit depth 1-16, colour types 0/2/3/4/6) -> (w, h, rgba)."""
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    o = 8
    idat = bytearray(); plte = b""; trns = b""; hdr = None
    while o + 8 <= len(b):
        ln = struct.unpack_from(">I", b, o)[0]
        typ = b[o + 4:o + 8]
        body = b[o + 8:o + 8 + ln]
        if typ == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            plte = body
        elif typ == b"tRNS":
            trns = body
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        o += 12 + ln
    if hdr is None:
        return None
    w, h, bd, ct, _, _, il = hdr
    if il != 0 or ct not in (0, 2, 3, 4, 6):
        return None
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    bits = ch * bd
    stride = (w * bits + 7) // 8
    bpp = max(1, bits // 8)
    raw = zlib.decompress(bytes(idat))
    if len(raw) < (stride + 1) * h:
        return None
    rows = []
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]; line = bytearray(raw[p + 1:p + 1 + stride]); p += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                bb = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(bb - c), abs(a - c), abs(a + bb - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (bb if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        rows.append(line); prev = line
    out = bytearray(w * h * 4)

    def sample(line, idx):          # idx-th sample of the row, scaled to 8 bit (palette: raw index)
        if bd == 8:
            return line[idx]
        if bd == 16:
            return line[idx * 2]
        per = 8 // bd
        v = (line[idx // per] >> (8 - bd * (idx % per + 1))) & ((1 << bd) - 1)
        return v if ct == 3 else v * 255 // ((1 << bd) - 1)
    for y in range(h):
        line = rows[y]
        for x in range(w):
            o = (y * w + x) * 4
            if ct == 3:
                i = sample(line, x)
                out[o:o + 3] = plte[i * 3:i * 3 + 3]
                out[o + 3] = trns[i] if i < len(trns) else 255
            elif ct == 0:
                g = sample(line, x); out[o] = out[o + 1] = out[o + 2] = g; out[o + 3] = 255
            elif ct == 4:
                g = sample(line, x * 2); out[o] = out[o + 1] = out[o + 2] = g; out[o + 3] = sample(line, x * 2 + 1)
            elif ct == 2:
                out[o] = sample(line, x * 3); out[o + 1] = sample(line, x * 3 + 1); out[o + 2] = sample(line, x * 3 + 2); out[o + 3] = 255
            else:
                for c in range(4):
                    out[o + c] = sample(line, x * 4 + c)
    return w, h, bytes(out)


def _image_payload(chunk, level=0):
    imgs = [k for k in chunk.children if k.id == ID_IMAGE]
    if not imgs:
        return None
    imgs.sort(key=lambda k: -struct.unpack_from("<I", k.data, read_str(k.data, 0)[1] + 4)[0])
    im = imgs[min(level, len(imgs) - 1)]
    dd = [k for k in im.children if k.id == ID_IMGDATA]
    return dd[0].data[4:] if dd else None


def texture_format(chunk):
    """Human readable payload format: 'PS2 P3DI 8bpp paletted', 'DDS DXT1', 'PNG paletted' ..."""
    d = _image_payload(chunk)
    if d is None:
        return "no image data"
    if d[:4] == b"P3DI":
        return "P3DI fmt %d (PS2)" % struct.unpack_from("<I", d, 4)[0]
    if d[:4] == b"DDS ":
        fcc = d[84:88]
        return "DDS " + (fcc.decode("latin1") if fcc.strip(b"\0") else "uncompressed %dbpp" % struct.unpack_from("<I", d, 88)[0])
    if d[:8] == b"\x89PNG\r\n\x1a\n":
        ct = d[25]
        return "PNG " + {0: "gray", 2: "RGB", 3: "paletted", 4: "gray+alpha", 6: "RGBA"}.get(ct, "type %d" % ct)
    return "unknown"


def decode_texture(chunk, swizzle="auto", level=0):
    """Decode one mip level of a 0x19000 texture chunk -> (w, h, rgba_bytes) or None.

    The 0x19002 payload is u32 size + one of three containers, one per console version:
      PS2      'P3DI': u32 format, w, h, bpp, paletteBytes, pixelBytes, palette (RGBA, alpha 0..0x80), pixels.
               format 14 = 8 bit paletted. SQUARE images are PS2-swizzled (PSMT8), NON-square are linear
               (verified: mip0 downsampled == mip1 only for those). format 27 = 32 bit RGBA, linear (unverified).
               swizzle: 'auto' | 'swizzled' | 'linear' applies to this format only.
      Xbox     a complete DDS file (DXT1 / DXT5, a few PNG) - decoded by decode_dds / decode_png.
      GameCube PNG files (8 bit paletted with PLTE+tRNS, RGB, RGBA) and DXT1 DDS.
    """
    d = _image_payload(chunk, level)
    if d is None:
        return None
    try:
        if d[:4] == b"DDS ":
            return decode_dds(d)
        if d[:8] == b"\x89PNG\r\n\x1a\n":
            return decode_png(d)
        magic, fmt, w, h, bpp, palb, pixb = struct.unpack_from("<4s6I", d, 0)
    except (struct.error, zlib.error, ValueError, IndexError):
        return None
    if magic != b"P3DI":
        return None
    pal = d[28:28 + palb]
    px = d[28 + palb:28 + palb + pixb]
    if fmt == 14 and bpp == 8 and palb == 1024 and len(px) >= w * h:
        sw = (w == h) if swizzle == "auto" else (swizzle == "swizzled")
        if sw and w % 16 == 0 and h % 16 == 0:
            px = _unswizzle8(px, w, h)
        pal = _unswizzle_palette(pal)
        out = bytearray(w * h * 4)
        for i in range(w * h):
            r, g, b, a = pal[px[i] * 4:px[i] * 4 + 4]
            out[i * 4:i * 4 + 4] = bytes((r, g, b, min(255, a * 2)))
        return w, h, bytes(out)
    if fmt == 27 and bpp == 32 and len(px) >= w * h * 4:
        out = bytearray(px[:w * h * 4])
        for i in range(w * h):
            out[i * 4 + 3] = min(255, out[i * 4 + 3] * 2)
        return w, h, bytes(out)
    return None


def png_bytes(w, h, rgba):
    raw = b"".join(b"\0" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def ch(t, data):
        c = struct.pack(">I", len(data)) + t + data
        return c + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(raw, 9)) + ch(b"IEND", b""))


def ppm_bytes(w, h, rgb):
    return b"P6 %d %d 255\n" % (w, h) + bytes(rgb)


class ResourcePool:
    """Shaders and textures gathered from the edited file plus any extra .p3d files
    (shared/common files usually hold the shaders and textures that groups refer to)."""

    def __init__(self):
        self.shaders = {}       # exact name -> info
        self.textures = {}      # UPPER name -> (name, chunk, source label)
        self.sources = []
        self._cache = {}

    def add(self, p3d, label):
        for c in p3d.chunks:
            if c.id == ID_SHADER:
                info = parse_shader(c); info["source"] = label
                self.shaders.setdefault(info["name"], info)
            elif c.id == ID_TEXTURE:
                n = read_str(c.data, 0)[0]
                self.textures.setdefault(n.upper(), (n, c, label))
        self.sources.append(label)
        self._cache.clear()

    def find_texture(self, name):
        if not name:
            return None
        u = name.upper()
        for cand in (u, u + ".BMP", u + ".TGA", u + ".BMP.0", u + ".TGA.0"):
            if cand in self.textures:
                return self.textures[cand]
        return None

    def resolve(self, shader_name):
        """-> dict(how, shader, tex_name, tex, blend). 'how' = 'shader chunk' | 'name heuristic' | 'unresolved'"""
        sh = self.shaders.get(shader_name)
        if sh is not None:
            tex = self.find_texture(sh["tex"])
            return {"how": "shader chunk", "shader": sh, "tex_name": sh["tex"], "tex": tex,
                    "blend": sh["blend"] if sh["blend"] is not None else 2}
        m = SHADER_NAME_RE.match(shader_name or "")
        stem = m.group(1) if m else None
        tex = self.find_texture(stem)
        if tex:
            return {"how": "name heuristic", "shader": None, "tex_name": stem, "tex": tex, "blend": 2}
        return {"how": "unresolved", "shader": None, "tex_name": stem, "tex": None, "blend": 2}

    def rgba(self, tex_entry, swizzle="auto", level=0):
        if not tex_entry:
            return None
        key = (tex_entry[0], swizzle, level)
        if key not in self._cache:
            self._cache[key] = decode_texture(tex_entry[1], swizzle, level)
        return self._cache[key]


def describe_resolution(pool, shader_name, swizzle="auto"):
    r = pool.resolve(shader_name)
    blend = BLEND_NAMES.get(r["blend"], str(r["blend"]))
    if r["tex"]:
        ti = texture_info(r["tex"][1])
        return "texture %s %dx%d, %s, %d mips  blend=%s  [%s, from %s]" % (
            r["tex"][0], ti["width"], ti["height"], texture_format(r["tex"][1]), ti["mips"], blend, r["how"], r["tex"][2])
    return "texture %s NOT FOUND - load the .p3d that contains it (File > Load resource)  [%s]" % (
        r["tex_name"] or "?", r["how"])


# ---- camera + software renderer (numpy optional; GUI falls back to flat polygons)
def project_pt(p, cam, w, h):
    yaw, pitch, dist, target = cam
    x, y, z = p[0] - target[0], p[1] - target[1], p[2] - target[2]
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    x, z = x * cy + z * sy, -x * sy + z * cy
    y, z = y * cp - z * sp, y * sp + z * cp
    z += dist
    f = min(w, h) * 0.9
    zz = max(z, 0.05)
    return (w / 2 + x / zz * f, h / 2 - y / zz * f, z)


def quad_corners(q, preview_zero=True):
    """Corners in group space. Quads whose size is ~0 are scaled by a runtime animation in the game;
    with preview_zero they are shown 2x2 so that they can be seen (data is not changed)."""
    w, h = q.width, q.height
    if preview_zero and abs(w) < 1e-3 and abs(h) < 1e-3:
        w = h = 2.0
    w, h = w / 2.0, h / 2.0
    return [vadd(qrot(q.rot, p), q.pos) for p in ((-w, -h, 0), (w, -h, 0), (w, h, 0), (-w, h, 0))]


def vtrans1(g, p):
    return vadd(qrot(g.group_rot, p), g.group_pos)


def have_numpy():
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def _tri(fb, P, Z, UV, tex, col, blend, flip_v):
    import numpy as np
    H, W, _ = fb.shape
    x0 = max(0, int(min(p[0] for p in P))); x1 = min(W - 1, int(max(p[0] for p in P)) + 1)
    y0 = max(0, int(min(p[1] for p in P))); y1 = min(H - 1, int(max(p[1] for p in P)) + 1)
    if x1 < x0 or y1 < y0:
        return
    (ax, ay), (bx, by), (cx, cy) = P
    den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(den) < 1e-9:
        return
    gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
    l0 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
    l1 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
    l2 = 1 - l0 - l1
    m = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
    if not m.any():
        return
    iw = [1.0 / max(z, 0.05) for z in Z]
    a0, a1, a2 = l0[m] * iw[0], l1[m] * iw[1], l2[m] * iw[2]
    s = a0 + a1 + a2
    u = (a0 * UV[0][0] + a1 * UV[1][0] + a2 * UV[2][0]) / s
    v = (a0 * UV[0][1] + a1 * UV[1][1] + a2 * UV[2][1]) / s
    ys_, xs_ = np.nonzero(m)
    ys_ += y0; xs_ += x0
    if tex is not None:
        th, tw = tex.shape[:2]
        ui = np.floor(u * tw).astype(int) % tw
        vi = np.floor(((1 - v) if flip_v else v) * th).astype(int) % th
        t = tex[vi, ui].astype(np.float32) / 255.0
        rgb = t[:, :3] * col[:3]
        a = t[:, 3] * col[3]
    else:
        rgb = np.tile(col[:3], (len(u), 1)); a = np.full(len(u), 0.5 * col[3], np.float32)
    if blend == 2:      # additive
        fb[ys_, xs_] += rgb * a[:, None] * 255.0
    else:               # alpha / anything else
        fb[ys_, xs_] = fb[ys_, xs_] * (1 - a[:, None]) + rgb * a[:, None] * 255.0


def render_scene(groups, pool, W, H, cam, textured=True, flip_v=True, swizzle="auto", tint=True,
                 preview_zero=True, bg=(28, 28, 34)):
    """Returns a uint8 (H, W, 3) numpy array. Quads drawn far->near, additive/alpha per shader."""
    import numpy as np
    fb = np.empty((H, W, 3), np.float32); fb[:] = bg
    items = []
    for g in groups:
        for q in g.quads:
            pts = [project_pt(vtrans1(g, p), cam, W, H) for p in quad_corners(q, preview_zero)]
            if min(p[2] for p in pts) < 0.05:
                continue
            items.append((sum(p[2] for p in pts) / 4, g, q, pts))
    items.sort(key=lambda t: -t[0])
    cache = {}
    for _, g, q, pts in items:
        tex, blend = None, 2
        if textured:
            if g.shader not in cache:
                r = pool.resolve(g.shader)
                rg = pool.rgba(r["tex"], swizzle)
                arr = np.frombuffer(rg[2], np.uint8).reshape(rg[1], rg[0], 4) if rg else None
                cache[g.shader] = (arr, r["blend"])
            tex, blend = cache[g.shader]
            if tex is None:
                continue        # texture not available: the GUI draws only an outline for it
        r_, g_, b_, a_ = ((q.colour >> 16) & 255) / 255, ((q.colour >> 8) & 255) / 255, (q.colour & 255) / 255, (q.colour >> 24) / 255
        if tint and (r_ + g_ + b_) > 0.001:
            col = np.array([r_, g_, b_, a_], np.float32)
        else:
            col = np.array([1, 1, 1, a_ if a_ > 0 else 1], np.float32)   # black/zero colours are runtime-animated: show white
        P = [(p[0], p[1]) for p in pts]; Z = [p[2] for p in pts]
        uv = [tuple(u) for u in q.uv]
        _tri(fb, [P[0], P[1], P[2]], [Z[0], Z[1], Z[2]], [uv[0], uv[1], uv[2]], tex, col, blend, flip_v)
        _tri(fb, [P[0], P[2], P[3]], [Z[0], Z[2], Z[3]], [uv[0], uv[2], uv[3]], tex, col, blend, flip_v)
    return np.clip(fb, 0, 255).astype(np.uint8)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def print_groups(groups, pool=None):
    for g in groups:
        print("%-36s shader=%s quads=%d sort=%.3f" % (g.name, g.shader, len(g.quads), g.sort_value))
        if pool is not None:
            print("      -> " + describe_resolution(pool, g.shader))
        for q in g.quads:
            print("   - %-34s mode=%-4s col=%08X size=%.3fx%.3f dist=%.2f pos=(%.2f,%.2f,%.2f)" %
                  (q.name, q.mode, q.colour, q.width, q.height, q.distance, *q.pos))


def roundtrip(path):
    with open(path, "rb") as f:
        raw = f.read()
    p = P3DFile.load(path)
    ok1 = p.to_bytes() == raw
    groups = find_groups(p)
    ok2 = True
    for i, g in groups:
        a = bytearray(); p.chunks[i].pack(a)
        b = bytearray(); g.to_chunk().pack(b)
        if a != b:
            ok2 = False
            print("typed re-encode differs for group", g.name)
    print("raw chunk round-trip identical :", ok1)
    print("typed group re-encode identical:", ok2, "(%d groups)" % len(groups))
    return ok1 and ok2


def make_pool(main_path, extra_paths=()):
    pool = ResourcePool()
    pool.add(P3DFile.load(main_path), os.path.basename(main_path))
    for e in extra_paths:
        pool.add(P3DFile.load(e), os.path.basename(e))
    return pool


def cli(argv):
    cmd, a = argv[0], argv[1:]
    if cmd == "--list":
        p = P3DFile.load(a[0])
        print_groups([g for _, g in find_groups(p)], make_pool(a[0], a[1:]))
    elif cmd == "--export":
        p = P3DFile.load(a[0])
        with open(a[1], "w") as f:
            json.dump(groups_to_json([g for _, g in find_groups(p)]), f, indent=2)
        print("exported to", a[1])
    elif cmd == "--import":
        p = P3DFile.load(a[0])
        with open(a[1]) as f:
            gs = groups_from_json(json.load(f))
        r, ad = merge_groups(p, gs)
        p.save(a[2])
        print("replaced %d, added %d -> %s" % (r, ad, a[2]))
    elif cmd == "--roundtrip":
        sys.exit(0 if roundtrip(a[0]) else 1)
    elif cmd == "--textures":          # --textures file.p3d outdir [--mips]
        p = P3DFile.load(a[0]); os.makedirs(a[1], exist_ok=True); n = 0
        for c in p.chunks:
            if c.id != ID_TEXTURE:
                continue
            name = read_str(c.data, 0)[0]
            r = decode_texture(c)
            if r:
                with open(os.path.join(a[1], re.sub(r"[^\w.\-]", "_", name) + ".png"), "wb") as f:
                    f.write(png_bytes(*r[:2], r[2]))
                n += 1
            else:
                print("cannot decode", name)
        print("wrote %d PNG files to %s" % (n, a[1]))
    elif cmd == "--render":            # --render file.p3d out.png [extra.p3d ...]  (contact sheet, one tile per group)
        if not have_numpy():
            print("numpy is required for --render"); sys.exit(1)
        import numpy as np
        p = P3DFile.load(a[0]); pool = make_pool(a[0], a[2:])
        gs = [g for _, g in find_groups(p)]
        cols, tw, th = 4, 300, 240
        rows = (len(gs) + cols - 1) // cols
        sheet = np.zeros((rows * th, cols * tw, 3), np.uint8)
        for i, g in enumerate(gs):
            pts = [vtrans1(g, c) for q in g.quads for c in quad_corners(q)]
            ctr = [sum(v[k] for v in pts) / len(pts) for k in range(3)] if pts else [0, 0, 0]
            ext = max([math.dist(ctr, v) for v in pts] + [0.5])
            tile = render_scene([g], pool, tw, th, (0.5, 0.25, ext * 2.6, ctr))
            sheet[(i // cols) * th:(i // cols + 1) * th, (i % cols) * tw:(i % cols + 1) * tw] = tile
        rgba = np.dstack([sheet, np.full(sheet.shape[:2], 255, np.uint8)])
        with open(a[1], "wb") as f:
            f.write(png_bytes(cols * tw, rows * th, rgba.tobytes()))
        print("rendered %d groups -> %s" % (len(gs), a[1]))
    else:
        return False
    return True


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------
def run_gui(initial=None, extra=()):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog, colorchooser

    NUMPY = have_numpy()

    def texture_ppm(rgba, w, h, box):
        """nearest-neighbour fit into box x box over a checkerboard -> (ppm bytes, dw, dh)"""
        sc = box / float(max(w, h))
        dw, dh = max(1, int(w * sc)), max(1, int(h * sc))
        out = bytearray(dw * dh * 3)
        for y in range(dh):
            sy = min(h - 1, int(y / sc))
            for x in range(dw):
                sx = min(w - 1, int(x / sc))
                i = (sy * w + sx) * 4
                a = rgba[i + 3] / 255.0
                chk = 70 if ((x // 8 + y // 8) & 1) else 105
                o = (y * dw + x) * 3
                out[o] = int(rgba[i] * a + chk * (1 - a))
                out[o + 1] = int(rgba[i + 1] * a + chk * (1 - a))
                out[o + 2] = int(rgba[i + 2] * a + chk * (1 - a))
        return ppm_bytes(dw, dh, out), dw, dh

    class App:
        def __init__(self, root):
            self.root = root
            root.title("Pure3D Billboard Quad Group Editor  -  " + CREDIT)
            root.geometry("1300x800")
            self.p3d = None
            self.path = None
            self.groups = []
            self.pool = ResourcePool()
            self.extra_files = []
            self.dirty = False
            self.sel_g = None
            self.sel_q = None
            self.cam = [0.6, 0.35, 12.0, [0.0, 0.0, 0.0]]
            self.vars = {}
            self._img = None
            self._uvimg = None
            self._pending = False
            self.o_tex = tk.BooleanVar(value=NUMPY)
            self.o_flipv = tk.BooleanVar(value=True)
            self.o_tint = tk.BooleanVar(value=True)
            self.o_zero = tk.BooleanVar(value=True)
            self.o_swz = tk.StringVar(value="auto")
            self.o_scale = tk.IntVar(value=2)
            self._build_menu()
            self._build_ui()
            if initial:
                self.open_path(initial, extra)

        # ---------------- UI
        def _build_menu(self):
            m = tk.Menu(self.root)
            f = tk.Menu(m, tearoff=0)
            f.add_command(label="Open .p3d...", command=self.open_dialog, accelerator="Ctrl+O")
            f.add_command(label="Save", command=self.save, accelerator="Ctrl+S")
            f.add_command(label="Save As .p3d...", command=self.save_as)
            f.add_separator()
            f.add_command(label="Load resource .p3d (shared shaders/textures)...", command=self.load_resource)
            f.add_separator()
            f.add_command(label="Export groups to JSON...", command=self.export_json)
            f.add_command(label="Import groups from JSON (merge into open file)...", command=self.import_json)
            f.add_separator()
            f.add_command(label="Exit", command=self.root.destroy)
            m.add_cascade(label="File", menu=f)
            e = tk.Menu(m, tearoff=0)
            e.add_command(label="New group", command=self.new_group)
            e.add_command(label="Duplicate group", command=self.dup_group)
            e.add_command(label="Delete group", command=self.del_group)
            e.add_separator()
            e.add_command(label="Add quad to group", command=self.new_quad)
            e.add_command(label="Delete quad", command=self.del_quad)
            e.add_separator()
            e.add_command(label="Recompute bounds of group", command=self.recompute)
            m.add_cascade(label="Edit", menu=e)
            v = tk.Menu(m, tearoff=0)
            v.add_checkbutton(label="Textured preview" + ("" if NUMPY else " (needs numpy)"), variable=self.o_tex,
                              command=self.redraw, state="normal" if NUMPY else "disabled")
            v.add_checkbutton(label="Flip V when sampling (v=0 is bottom)", variable=self.o_flipv, command=self.redraw)
            v.add_checkbutton(label="Tint with quad colour", variable=self.o_tint, command=self.redraw)
            v.add_checkbutton(label="Show ~zero-size quads at 2x2 (runtime-animated)", variable=self.o_zero, command=self.redraw)
            v.add_separator()
            for lab, val in (("Palette-indexed layout: auto (square = PS2 swizzled, else linear)", "auto"),
                             ("Force PS2 swizzled", "swizzled"), ("Force linear", "linear")):
                v.add_radiobutton(label=lab, variable=self.o_swz, value=val, command=self.redraw)
            v.add_separator()
            for lab, val in (("Render resolution: full", 1), ("Render resolution: 1/2 (faster)", 2), ("Render resolution: 1/4", 4)):
                v.add_radiobutton(label=lab, variable=self.o_scale, value=val, command=self.redraw)
            m.add_cascade(label="View", menu=v)
            t = tk.Menu(m, tearoff=0)
            t.add_command(label="Texture browser...", command=self.texture_browser)
            m.add_cascade(label="Tools", menu=t)
            hm = tk.Menu(m, tearoff=0)
            hm.add_command(label="About", command=lambda: messagebox.showinfo(
                "About", "Pure3D Billboard Quad Group Editor\n\n" + CREDIT + "\n\nTextures: PS2 / Xbox / GameCube"))
            m.add_cascade(label="Help", menu=hm)
            self.root.config(menu=m)
            self.root.bind("<Control-o>", lambda e: self.open_dialog())
            self.root.bind("<Control-s>", lambda e: self.save())

        def _build_ui(self):
            foot = ttk.Label(self.root, text=CREDIT, anchor="center", font=("", 9, "bold"), padding=3)
            foot.pack(side="bottom", fill="x")
            pan = ttk.PanedWindow(self.root, orient="horizontal")
            pan.pack(fill="both", expand=True)
            left = ttk.Frame(pan, width=280)
            mid = ttk.Frame(pan)
            right = ttk.Frame(pan, width=350)
            pan.add(left, weight=0); pan.add(mid, weight=1); pan.add(right, weight=0)
            self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
            self.tree.pack(fill="both", expand=True)
            self.tree.bind("<<TreeviewSelect>>", self.on_select)
            self.canvas = tk.Canvas(mid, bg="#1c1c22", highlightthickness=0)
            self.canvas.pack(fill="both", expand=True)
            self.canvas.bind("<Configure>", lambda e: self.redraw())
            self.canvas.bind("<ButtonPress-1>", self._m_down)
            self.canvas.bind("<B1-Motion>", self._m_orbit)
            self.canvas.bind("<B3-Motion>", self._m_pan)
            self.canvas.bind("<ButtonPress-3>", self._m_down)
            self.canvas.bind("<MouseWheel>", lambda e: self._zoom(0.9 if e.delta > 0 else 1.1))
            self.canvas.bind("<Button-4>", lambda e: self._zoom(0.9))
            self.canvas.bind("<Button-5>", lambda e: self._zoom(1.1))
            self.status = ttk.Label(mid, text="Left-drag: orbit   Right-drag: pan   Wheel: zoom")
            self.status.pack(fill="x")
            self.form = ttk.Frame(right, padding=6)
            self.form.pack(fill="both", expand=True)
            self.uvcanvas = tk.Canvas(right, width=240, height=240, bg="#2a2a30", highlightthickness=1)
            self.uvcanvas.pack(pady=6)

        def _field(self, key, label, row, width=22):
            ttk.Label(self.form, text=label).grid(row=row, column=0, sticky="w")
            v = tk.StringVar()
            e = ttk.Entry(self.form, textvariable=v, width=width)
            e.grid(row=row, column=1, sticky="ew", pady=1)
            e.bind("<Return>", lambda ev: self.apply())
            e.bind("<FocusOut>", lambda ev: self.apply())
            self.vars[key] = v

        def build_form(self):
            for w in self.form.winfo_children():
                w.destroy()
            self.vars = {}
            self.form.columnconfigure(1, weight=1)
            if self.sel_g is None:
                return
            r = 0
            g = self.sel_g
            title = "BILLBOARD QUAD GROUP (0x17006)" if self.sel_q is None else "BILLBOARD QUAD (0x17005)"
            ttk.Label(self.form, text=title, font=("", 10, "bold")).grid(row=r, columnspan=2, sticky="w"); r += 1
            self.texlabel = ttk.Label(self.form, wraplength=320, foreground="#2a6")
            self.texlabel.grid(row=r, columnspan=2, sticky="w", pady=2); r += 1
            if self.sel_q is None:
                fields = (("g_name", "Name"), ("g_shader", "Shader"), ("g_sort", "Sort value (0x122000)"),
                          ("g_params", "Params (4 u32)"), ("g_pos", "Group translation"), ("g_rot", "Group rotation xyzw"),
                          ("g_bbox", "BBox min/max"), ("g_bsph", "BSphere c/r"))
            else:
                fields = (("q_name", "Name"), ("q_mode", "Mode tag (4cc)"), ("q_flags", "Flags / One"),
                          ("q_w", "Width"), ("q_h", "Height"), ("q_dist", "Distance"), ("q_pos", "Translation xyz"),
                          ("q_rot", "Rotation xyzw"), ("q_euler", "Rotation euler deg (xyz)"),
                          ("q_uv0", "UV0"), ("q_uv1", "UV1"), ("q_uv2", "UV2"), ("q_uv3", "UV3"),
                          ("q_exa", "UV extra A"), ("q_exb", "UV extra B"), ("q_col", "Colour 0xAARRGGBB"))
            for k, l in fields:
                self._field(k, l, r); r += 1
            if self.sel_q is None:
                ttk.Label(self.form, text="Animation link: " + ("yes (kept verbatim)" if g.anim_chunk else "none")).grid(row=r, columnspan=2, sticky="w"); r += 1
                ttk.Button(self.form, text="Recompute bounds", command=self.recompute).grid(row=r, columnspan=2, sticky="ew")
                self.load_group_vals()
            else:
                self.zerolabel = ttk.Label(self.form, wraplength=320, foreground="#b70")
                self.zerolabel.grid(row=r, columnspan=2, sticky="w"); r += 1
                ttk.Button(self.form, text="Pick colour...", command=self.pick_colour).grid(row=r, columnspan=2, sticky="ew")
                self.load_quad_vals()
            self.update_texlabel()

        def update_texlabel(self):
            if self.sel_g is not None and getattr(self, "texlabel", None) is not None and self.texlabel.winfo_exists():
                self.texlabel.config(text=describe_resolution(self.pool, self.sel_g.shader))

        @staticmethod
        def fl(lst):
            return " ".join(("%.6g" % x) for x in lst)

        def load_group_vals(self):
            g, v = self.sel_g, self.vars
            v["g_name"].set(g.name); v["g_shader"].set(g.shader); v["g_sort"].set("%.6g" % g.sort_value)
            v["g_params"].set(" ".join(map(str, g.params))); v["g_pos"].set(self.fl(g.group_pos))
            v["g_rot"].set(self.fl(g.group_rot)); v["g_bbox"].set(self.fl(g.bbox)); v["g_bsph"].set(self.fl(g.bsphere))

        def load_quad_vals(self):
            q, v = self.sel_q, self.vars
            v["q_name"].set(q.name); v["q_mode"].set(q.mode); v["q_flags"].set("%d %d" % (q.flags, q.one))
            v["q_w"].set("%.6g" % q.width); v["q_h"].set("%.6g" % q.height); v["q_dist"].set("%.6g" % q.distance)
            v["q_pos"].set(self.fl(q.pos)); v["q_rot"].set(self.fl(q.rot)); v["q_euler"].set(self.fl(quat_to_euler(q.rot)))
            for i in range(4):
                v["q_uv%d" % i].set(self.fl(q.uv[i]))
            v["q_exa"].set(self.fl(q.extra_a)); v["q_exb"].set(self.fl(q.extra_b)); v["q_col"].set("0x%08X" % q.colour)
            self.zerolabel.config(text="Size is ~0: the game scales this quad with the BQG animation at runtime. "
                                       "Preview shows it 2x2." if abs(q.width) < 1e-3 and abs(q.height) < 1e-3 else "")

        def apply(self):
            if self.sel_g is None or not self.vars:
                return
            try:
                v = self.vars
                nums = lambda k, n: [float(x) for x in v[k].get().split()][:n]
                if self.sel_q is None:
                    g = self.sel_g
                    g.name = v["g_name"].get().strip() or g.name
                    g.shader = v["g_shader"].get().strip()
                    g.sort_value = float(v["g_sort"].get())
                    g.params = [int(x) for x in v["g_params"].get().split()][:4]
                    g.group_pos = nums("g_pos", 3); g.group_rot = nums("g_rot", 4)
                    g.bbox = nums("g_bbox", 6); g.bsphere = nums("g_bsph", 4)
                else:
                    q = self.sel_q
                    old_rot = list(q.rot)
                    q.name = v["q_name"].get().strip() or q.name
                    q.mode = v["q_mode"].get()[:4]
                    fo = v["q_flags"].get().split(); q.flags, q.one = int(fo[0]), int(fo[1])
                    q.width = float(v["q_w"].get()); q.height = float(v["q_h"].get()); q.distance = float(v["q_dist"].get())
                    q.pos = nums("q_pos", 3)
                    new_rot, new_eul = nums("q_rot", 4), nums("q_euler", 3)
                    if new_rot != old_rot:
                        q.rot = new_rot
                    elif [round(x, 3) for x in new_eul] != [round(x, 3) for x in quat_to_euler(q.rot)]:
                        q.rot = quat_from_euler(*new_eul)
                    for i in range(4):
                        q.uv[i] = nums("q_uv%d" % i, 2)
                    q.extra_a = nums("q_exa", 2); q.extra_b = nums("q_exb", 2)
                    q.colour = int(v["q_col"].get().strip(), 16)
                    self.load_quad_vals()
            except (ValueError, IndexError) as ex:
                self.status.config(text="Invalid value: %s" % ex)
                return
            self.dirty = True
            self.update_texlabel()
            self.refresh_tree(keep=True)
            self.redraw()

        def pick_colour(self):
            c = colorchooser.askcolor(color="#%06X" % (self.sel_q.colour & 0xFFFFFF))[1]
            if c:
                self.sel_q.colour = (self.sel_q.colour & 0xFF000000) | int(c[1:], 16)
                self.load_quad_vals(); self.dirty = True; self.redraw()

        # ---------------- files
        def open_dialog(self):
            p = filedialog.askopenfilename(filetypes=[("Pure3D", "*.p3d"), ("All", "*.*")])
            if p:
                self.open_path(p)

        def rebuild_pool(self):
            self.pool = ResourcePool()
            if self.p3d is not None:
                self.pool.add(self.p3d, os.path.basename(self.path or "open file"))
            for e in self.extra_files:
                try:
                    self.pool.add(P3DFile.load(e), os.path.basename(e))
                except Exception as ex:
                    messagebox.showwarning("Resource", "%s: %s" % (e, ex))
            self.update_texlabel()

        def open_path(self, path, extra=()):
            try:
                self.p3d = P3DFile.load(path)
            except Exception as ex:
                messagebox.showerror("Open failed", str(ex)); return
            self.path = path
            self.extra_files = list(extra)
            self.groups = [g for _, g in find_groups(self.p3d)]
            self.sel_g = self.sel_q = None
            self.dirty = False
            self.rebuild_pool()
            self.root.title("Pure3D Billboard Quad Group Editor - %s  -  %s" % (os.path.basename(path), CREDIT))
            self.refresh_tree(); self.build_form(); self.fit_view(); self.redraw()
            miss = sorted({g.shader for g in self.groups if not self.pool.resolve(g.shader)["tex"]})
            self.status.config(text="%d groups; %d shaders without texture (load the shared .p3d via File > Load resource)"
                                    % (len(self.groups), len(miss)))

        def load_resource(self):
            ps = filedialog.askopenfilenames(filetypes=[("Pure3D", "*.p3d"), ("All", "*.*")])
            if ps:
                self.extra_files += list(ps)
                self.rebuild_pool(); self.redraw()
                miss = sorted({g.shader for g in self.groups if not self.pool.resolve(g.shader)["tex"]})
                self.status.config(text="Loaded %d resource file(s); %d shaders still without texture" % (len(ps), len(miss)))

        def save(self):
            if self.p3d is not None:
                self.save_to(self.path)

        def save_as(self):
            if self.p3d is None:
                return
            p = filedialog.asksaveasfilename(defaultextension=".p3d", filetypes=[("Pure3D", "*.p3d")])
            if p:
                self.save_to(p)

        def save_to(self, path):
            self.apply()
            replace_all_groups(self.p3d, self.groups)
            self.p3d.save(path)
            self.path = path; self.dirty = False
            self.status.config(text="Saved " + path)

        def export_json(self):
            if not self.groups:
                return
            p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
            if p:
                self.apply()
                with open(p, "w") as f:
                    json.dump(groups_to_json(self.groups), f, indent=2)
                self.status.config(text="Exported " + p)

        def import_json(self):
            p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
            if not p:
                return
            try:
                with open(p) as f:
                    gs = groups_from_json(json.load(f))
            except Exception as ex:
                messagebox.showerror("Import failed", str(ex)); return
            if self.p3d is None:
                self.p3d = P3DFile()
            names = {g.name: i for i, g in enumerate(self.groups)}
            for g in gs:
                if g.name in names:
                    self.groups[names[g.name]] = g
                else:
                    self.groups.append(g)
            self.dirty = True
            self.refresh_tree(); self.redraw()
            self.status.config(text="Imported %d groups (same-named groups replaced)" % len(gs))

        # ---------------- tree / editing
        def refresh_tree(self, keep=False):
            sel = self.tree.selection()
            self.tree.delete(*self.tree.get_children())
            for gi, g in enumerate(self.groups):
                n = self.tree.insert("", "end", iid="g%d" % gi, text="%s  [%d]" % (g.name, len(g.quads)), open=True)
                for qi, q in enumerate(g.quads):
                    self.tree.insert(n, "end", iid="g%dq%d" % (gi, qi), text=q.name)
            if keep and sel and self.tree.exists(sel[0]):
                self.tree.selection_set(sel[0])

        def on_select(self, ev):
            s = self.tree.selection()
            if not s:
                return
            gi = int(s[0][1:].split("q")[0])
            self.sel_g = self.groups[gi]
            self.sel_q = self.sel_g.quads[int(s[0].split("q")[1])] if "q" in s[0] else None
            self.build_form(); self.redraw()

        def new_group(self):
            g = QuadGroup(); g.name = simpledialog.askstring("New group", "Name:", initialvalue="newGroup") or "newGroup"
            g.quads.append(Quad()); g.recompute_bounds()
            self.groups.append(g); self.dirty = True; self.refresh_tree()

        def dup_group(self):
            if self.sel_g is None:
                return
            g = groups_from_json(groups_to_json([self.sel_g]))[0]
            g.name = self.sel_g.name + "_copy"
            self.groups.append(g); self.dirty = True; self.refresh_tree()

        def del_group(self):
            if self.sel_g in self.groups and messagebox.askyesno("Delete", "Delete group %s?" % self.sel_g.name):
                self.groups.remove(self.sel_g); self.sel_g = self.sel_q = None
                self.dirty = True; self.refresh_tree(); self.build_form(); self.redraw()

        def new_quad(self):
            if self.sel_g is not None:
                self.sel_g.quads.append(Quad()); self.dirty = True; self.refresh_tree(); self.redraw()

        def del_quad(self):
            if self.sel_g and self.sel_q in self.sel_g.quads:
                self.sel_g.quads.remove(self.sel_q); self.sel_q = None
                self.dirty = True; self.refresh_tree(); self.build_form(); self.redraw()

        def recompute(self):
            if self.sel_g:
                self.sel_g.recompute_bounds(); self.dirty = True
                if self.sel_q is None and self.vars:
                    self.load_group_vals()

        # ---------------- 3D view
        def _m_down(self, e):
            self._lx, self._ly = e.x, e.y

        def _m_orbit(self, e):
            self.cam[0] += (e.x - self._lx) * 0.01
            self.cam[1] = max(-1.5, min(1.5, self.cam[1] + (e.y - self._ly) * 0.01))
            self._lx, self._ly = e.x, e.y
            self.redraw()

        def _m_pan(self, e):
            s = self.cam[2] * 0.002
            dx, dy = (e.x - self._lx) * s, (e.y - self._ly) * s
            cy, sy = math.cos(self.cam[0]), math.sin(self.cam[0])
            self.cam[3][0] -= dx * cy; self.cam[3][2] -= dx * sy; self.cam[3][1] += dy
            self._lx, self._ly = e.x, e.y
            self.redraw()

        def _zoom(self, f):
            self.cam[2] = max(0.2, self.cam[2] * f); self.redraw()

        def fit_view(self):
            pts = [vtrans1(g, c) for g in self.groups for q in g.quads for c in quad_corners(q, self.o_zero.get())]
            if pts:
                self.cam[3] = [0.0, 0.0, 0.0]
                self.cam[2] = max(2.0, max(max(abs(a) for a in p) for p in pts) * 3)

        def shown_groups(self):
            return self.groups if self.sel_g is None else [self.sel_g]

        def redraw(self):
            if self._pending:
                return
            self._pending = True
            self.root.after_idle(self._redraw)

        def _redraw(self):
            self._pending = False
            c = self.canvas
            W, H = max(2, c.winfo_width()), max(2, c.winfo_height())
            c.delete("all")
            groups = self.shown_groups()
            self._img = None
            if NUMPY and self.o_tex.get() and groups:
                s = self.o_scale.get()
                arr = render_scene(groups, self.pool, max(1, W // s), max(1, H // s), self.cam, True,
                                   self.o_flipv.get(), self.o_swz.get(), self.o_tint.get(), self.o_zero.get())
                img = tk.PhotoImage(data=ppm_bytes(arr.shape[1], arr.shape[0], arr.tobytes()))
                self._img = img.zoom(s) if s > 1 else img
                c.create_image(0, 0, image=self._img, anchor="nw")
            P = lambda p: project_pt(p, self.cam, W, H)[:2]
            for i in range(-10, 11):
                for a, b in (((i, 0, -10), (i, 0, 10)), ((-10, 0, i), (10, 0, i))):
                    c.create_line(*P(a), *P(b), fill="#3a3a44")
            for col, d in (("#e05050", (2, 0, 0)), ("#50e050", (0, 2, 0)), ("#5080e0", (0, 0, 2))):
                c.create_line(*P((0, 0, 0)), *P(d), fill=col, width=2)
            textured = self._img is not None
            for g in groups:
                has_tex = bool(self.pool.resolve(g.shader)["tex"])
                for q in g.quads:
                    pts = [P(vtrans1(g, p)) for p in quad_corners(q, self.o_zero.get())]
                    sel = q is self.sel_q
                    rgb = "#%06X" % (q.colour & 0xFFFFFF)
                    if textured:
                        c.create_polygon(pts, fill="", outline="#ffffff" if sel else ("#555566" if has_tex else "#cc5555"),
                                         width=2 if sel else 1, dash=() if has_tex else (4, 3))
                    else:
                        c.create_polygon(pts, fill=rgb, outline="#ffffff" if sel else rgb, width=2 if sel else 1, stipple="gray50")
                    cx = sum(p[0] for p in pts) / 4; cy = sum(p[1] for p in pts) / 4
                    c.create_text(cx, cy, text=q.name, fill="#aaaaaa", font=("", 8))
            self.draw_uv()

        def draw_uv(self):
            c = self.uvcanvas
            c.delete("all")
            self._uvimg = None
            if self.sel_q is None:
                c.create_text(120, 120, text="select a quad\nto see texture + UVs", fill="#aaa")
                return
            q = self.sel_q
            box, ox, oy = 140, 50, 50
            r = self.pool.resolve(self.sel_g.shader)
            rg = self.pool.rgba(r["tex"], self.o_swz.get()) if r["tex"] else None
            if rg:
                ppm, dw, dh = texture_ppm(rg[2], rg[0], rg[1], box)
                self._uvimg = tk.PhotoImage(data=ppm)
                ox, oy = 50 + (box - dw) // 2, 50 + (box - dh) // 2
                c.create_image(ox, oy, image=self._uvimg, anchor="nw")
            else:
                dw = dh = box
                c.create_rectangle(ox, oy, ox + dw, oy + dh, outline="#666", dash=(3, 3))
                c.create_text(120, 120, text="no texture", fill="#777")
            flip = self.o_flipv.get()
            m = lambda p: (ox + p[0] * dw, oy + ((1 - p[1]) if flip else p[1]) * dh)
            pts = [m(p) for p in q.uv]
            c.create_polygon(pts, outline="#4dc3ff", fill="", width=2)
            for i, p in enumerate(pts):
                c.create_text(p[0] + 6, p[1] - 6, text=str(i), fill="#ffd34d")

        # ---------------- texture browser
        def texture_browser(self):
            if not self.pool.textures:
                messagebox.showinfo("Textures", "No textures loaded (open a .p3d or load a resource file)."); return
            win = tk.Toplevel(self.root); win.title("Texture browser"); win.geometry("760x520")
            lb = tk.Listbox(win, width=34)
            lb.pack(side="left", fill="y")
            names = sorted(self.pool.textures.keys())
            for n in names:
                lb.insert("end", self.pool.textures[n][0])
            right = ttk.Frame(win); right.pack(side="left", fill="both", expand=True)
            info = ttk.Label(right, text="", wraplength=400); info.pack(anchor="w", padx=6, pady=4)
            lvl = tk.IntVar(value=0)
            bar = ttk.Frame(right); bar.pack(anchor="w", padx=6)
            ttk.Label(bar, text="Mip level").pack(side="left")
            sp = ttk.Spinbox(bar, from_=0, to=4, width=3, textvariable=lvl); sp.pack(side="left", padx=4)
            cv = tk.Canvas(right, bg="#2a2a30", width=400, height=400, highlightthickness=0); cv.pack(padx=6, pady=6)
            state = {"img": None, "rgba": None, "name": ""}

            def show(*_):
                s = lb.curselection()
                if not s:
                    return
                ent = self.pool.textures[names[s[0]]]
                ti = texture_info(ent[1])
                rg = self.pool.rgba(ent, self.o_swz.get(), lvl.get())
                cv.delete("all")
                if not rg:
                    info.config(text="%s: cannot decode this format" % ent[0]); state["rgba"] = None; return
                ppm, dw, dh = texture_ppm(rg[2], rg[0], rg[1], 380 if max(rg[0], rg[1]) >= 64 else 380)
                state["img"] = tk.PhotoImage(data=ppm); state["rgba"] = rg; state["name"] = ent[0]
                cv.create_image(10, 10, image=state["img"], anchor="nw")
                info.config(text="%s   %dx%d (level %d of %d)   %s   from %s" % (ent[0], rg[0], rg[1], lvl.get(), ti["mips"], texture_format(ent[1]), ent[2]))

            def save_png():
                if not state["rgba"]:
                    return
                p = filedialog.asksaveasfilename(defaultextension=".png", initialfile=state["name"] + ".png")
                if p:
                    w, h, d = state["rgba"]
                    with open(p, "wb") as f:
                        f.write(png_bytes(w, h, d))
            ttk.Button(right, text="Save as PNG...", command=save_png).pack(anchor="w", padx=6)
            lb.bind("<<ListboxSelect>>", show); sp.config(command=show)

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0].startswith("--"):
        if not cli(a):
            print(__doc__)
            print("\n" + CREDIT)
    else:
        run_gui(a[0] if a else None, a[1:])
