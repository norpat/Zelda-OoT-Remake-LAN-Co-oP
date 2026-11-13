import ctypes, ctypes.wintypes as wt, json, math, os, queue, random, socket, struct, subprocess, sys, threading, time
import tkinter as tk
import tkinter.messagebox
from tkinter import ttk
import numpy as np
np.seterr(all='ignore')
ZELDA_FONT = 'Segoe UI'
try:
    _font_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'HyliaSerif.otf')
    if os.path.exists(_font_file):
        _gdi32 = ctypes.WinDLL('gdi32')
        if _gdi32.AddFontResourceExW(_font_file, 16, 0) > 0:
            ZELDA_FONT = 'Hylia Serif Beta'
except Exception:
    pass
try:
    import gcall
    from gdbc import Gdb
except ImportError:
    for _p in (os.path.dirname(os.path.abspath(__file__)),):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    try:
        import gcall
        from gdbc import Gdb
    except ImportError:
        gcall = None
        Gdb = None
VERSION = '0.45'
GAME_PORT, DISC_PORT = (27015, 27016)
CHUNK = 16 << 20
DEFAULT_POS_OFF = 14417455744
DEFAULT_RUPEE_OFF = 15019262260
DEFAULT_CAM_OFF = 8189508432
CAM_FOV = 45.0
GHOST_HEIGHT = 1.1
MARKER_HEIGHT = 1.4
CFG_DIR = os.path.join(os.getenv('APPDATA', '.'), 'OoT_Multiplayer')
CFG = os.path.join(CFG_DIR, 'config.json')
k32 = ctypes.WinDLL('kernel32', use_last_error=True)
u32w = ctypes.WinDLL('user32', use_last_error=True)
_nt = ctypes.WinDLL('ntdll')
_nt.NtWriteVirtualMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

class MBI(ctypes.Structure):
    _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p), ('AllocationProtect', wt.DWORD), ('PartitionId', wt.WORD), ('RegionSize', ctypes.c_size_t), ('State', wt.DWORD), ('Protect', wt.DWORD), ('Type', wt.DWORD)]

def find_pid():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq lichii.exe', '/FO', 'CSV', '/NH'], capture_output=True, text=True, creationflags=134217728).stdout
    for line in out.splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) > 1 and parts[0].lower() == 'lichii.exe':
            return int(parts[1])
    return None

class Proc:

    def __init__(self, pid):
        self.pid = pid
        self.h = k32.OpenProcess(16 | 32 | 8 | 1024, False, pid)
        if not self.h:
            raise OSError(f'kan ikke åpne Lichii (feil {ctypes.get_last_error()})')
        self.base, self.size = self._guest_region()

    def _guest_region(self):
        addr, mbi, best = (0, MBI(), (0, 0))
        while k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            b, s = (mbi.BaseAddress or 0, mbi.RegionSize)
            if mbi.State == 4096 and mbi.Protect & 255 in (4, 64) and (s > best[1]):
                best = (b, s)
            addr = b + s
        return best

    def alive(self):
        code = wt.DWORD()
        return k32.GetExitCodeProcess(self.h, ctypes.byref(code)) and code.value == 259

    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)) and got.value == 0:
            raise OSError('lesing feilet')
        return buf.raw[:got.value]

    def write(self, addr, data):
        data = bytes(data)
        n = ctypes.c_size_t()
        st = _nt.NtWriteVirtualMemory(self.h, ctypes.c_void_p(addr), data, len(data), ctypes.byref(n))
        if st == 0 and n.value == len(data):
            return
        w = ctypes.c_size_t()
        if not k32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), data, len(data), ctypes.byref(w)):
            raise OSError('skriving feilet')

    def f32(self, off, n=1):
        v = struct.unpack(f'<{n}f', self.read(self.base + off, 4 * n))
        return v if n > 1 else v[0]

    def u32(self, off, n=1):
        v = struct.unpack(f'<{n}I', self.read(self.base + off, 4 * n))
        return v if n > 1 else v[0]

    def chunks(self, lo=0, hi=None):
        hi = self.size if hi is None else min(hi, self.size)
        lo = max(0, lo) & ~15
        for off in range(lo, hi, CHUNK):
            d = self.read(self.base + off, min(CHUNK, hi - off))
            yield (off, np.frombuffer(d[:len(d) - len(d) % 4], dtype='<u4'))

def valid_pos(p, off):
    try:
        hdr, tag = p.u32(off - 64, 2)
        fx, fy, fz = p.f32(off - 16, 3)
        x, y, z = p.f32(off, 3)
    except OSError:
        return False
    return hdr >> 24 in (1, 2) and tag == 2147483648 and (abs(math.hypot(fx, fz) - 1) < 0.05) and (abs(fy) < 0.05) and all((math.isfinite(v) and abs(v) < 100000 for v in (x, y, z))) and (abs(x) + abs(y) + abs(z) > 1.0) and (not (y == 0.0 and x % 50 == 0 and (z % 50 == 0)))

def valid_rupee(p, off):
    try:
        v, disp, zero, mark = p.u32(off, 4)
        around = p.u32(off - 256, 128)
    except OSError:
        return False
    return v == disp and zero == 0 and (v < 1000) and is_marker(mark) and (around.count(mark) >= 4)

def is_marker(mark):
    return 2147483648 <= mark < 2415919104 and mark & 4095 == 1816

def valid_pos_light(p, off):
    try:
        hdr, tag = p.u32(off - 64, 2)
        x, y, z = p.f32(off, 3)
    except OSError:
        return False
    return hdr >> 24 in (1, 2) and tag == 2147483648 and all((math.isfinite(v) and abs(v) < 100000 for v in (x, y, z))) and (abs(x) + abs(y) + abs(z) > 1.0) and (not (y == 0.0 and x % 50 == 0 and (z % 50 == 0)))

def valid_rupee_light(p, off):
    try:
        v, disp, _z, mark = p.u32(off, 4)
    except OSError:
        return False
    return v < 1000 and abs(v - disp) < 1000 and is_marker(mark)

def read_cam(p, off):
    m = p.f32(off, 12)
    return ((m[0], m[4], m[8]), (m[1], m[5], m[9]), (-m[2], -m[6], -m[10]), (m[3], m[7], m[11]))

def valid_cam(p, off, near=None):
    try:
        inv = p.f32(off, 12)
        view = p.f32(off - 48, 12)
    except OSError:
        return False
    if not all((math.isfinite(v) for v in inv + view)):
        return False
    for i in range(3):
        if abs(sum((inv[i * 4 + j] ** 2 for j in range(3))) - 1) > 0.01:
            return False
        for j in range(3):
            if abs(inv[i * 4 + j] - view[j * 4 + i]) > 0.002:
                return False
    if near is not None:
        q = cam_quality(p, off, near)
        if q is None:
            return False
        dist, dot = q
        if not (1.0 < dist < 30 and dot > 0.3):
            return False
    return True

def _cam_readable(p, off):
    return valid_cam(p, off, None)

def cam_quality(p, off, near):
    try:
        r, u, f, eye = read_cam(p, off)
    except OSError:
        return None
    d = [near[i] - eye[i] for i in range(3)]
    nd = math.sqrt(sum((v * v for v in d))) or 1e-06
    return (nd, sum((d[i] * f[i] for i in range(3))) / nd)

def cam_scan(p, near, progress, regions=None):
    np.seterr(all='ignore')
    found = []
    regions = regions or [(0, p.size)]
    n_chunks = max(1, sum((max(1, (hi - lo) // CHUNK) for lo, hi in regions)))
    it = (c for lo, hi in regions for c in p.chunks(lo, hi))
    for k, (off, q) in enumerate(it):
        n = len(q) - 24
        if n > 0:
            n -= n % 4
            fq = q.view(np.float32)

            def a(d):
                return fq[d:d + n:4]

            def eq(x, y):
                return np.abs(x - y) < 0.0001
            r0 = a(0)
            axis = (a(1) == 0) & (a(2) == 0) & (a(4) == 0)
            m = (np.abs(r0) <= 1.0) & ~axis & eq(a(13), a(4)) & eq(a(14), a(8)) & eq(a(16), a(1)) & eq(a(18), a(9))
            m &= np.abs(r0 * r0 + a(1) * a(1) + a(2) * a(2) - 1) < 0.01
            for i in np.nonzero(m)[0].tolist():
                found.append(off + (i * 4 + 12) * 4)
        progress((k + 1) / n_chunks)
    groups = {}
    for o in found:
        if valid_cam(p, o, near):
            key = tuple((round(v, 2) for v in read_cam(p, o)[3]))
            groups.setdefault(key, []).append(o)
    out = []
    for eye, offs in groups.items():
        dist, dot = cam_quality(p, offs[0], near) if near is not None else (0.0, 0.0)
        out.append((len(offs), offs, eye, dist, dot))
    out.sort(key=lambda g: -g[0])
    return out
CAM_VT_OFF = 79862672

def cam_scan_vtable(p, main_va, p_act, near):
    if not main_va or not p_act or near is None:
        return None
    vt = main_va + CAM_VT_OFF
    lo = max(0, p_act - (32 << 20) & ~1048575)
    hi = min(p.size, p_act + (192 << 20))
    scored = []
    for off, q in p.chunks(lo, hi):
        n = len(q) - len(q) % 8
        if n <= 0:
            continue
        arr = np.frombuffer(q[:n], dtype=np.uint64)
        for i in np.nonzero(arr == vt)[0].tolist():
            cam = off + i * 8 + 768
            if valid_cam(p, cam, near):
                qd = cam_quality(p, cam, near)
                if qd and 1.5 < qd[0] < 12 and (qd[1] > 0.7):
                    scored.append((qd[1], cam, qd[0]))
    if not scored:
        return None
    try:
        snap = {cam: p.read(p.base + cam, 48) for _d, cam, _q in scored}
        time.sleep(0.3)
        moving = {cam for cam in snap if p.read(p.base + cam, 48) != snap[cam]}
    except OSError:
        moving = set()
    scored.sort(key=lambda t: (t[1] in moving, t[0]), reverse=True)
    return scored[0][1]
REGION_STEP = 268435456

def find_rupees_by_region(p, pos=None):
    shifts = []
    if pos is not None:
        shifts.append(round((pos - DEFAULT_POS_OFF) / REGION_STEP) * REGION_STEP)
    k_max = p.size // REGION_STEP + 1
    shifts += sorted((k * REGION_STEP for k in range(-k_max, k_max + 1)), key=abs)
    for s in dict.fromkeys(shifts):
        o = DEFAULT_RUPEE_OFF + s
        if 0 < o < p.size and valid_rupee(p, o):
            return o
    return None

def find_link_auto(p, progress):
    np.seterr(all='ignore')
    phys, structs = ([], [])
    n_chunks = max(1, p.size // CHUNK)
    for k, (off, q) in enumerate(p.chunks()):
        i = np.nonzero(((q[:-1] >> 24 == 1) | (q[:-1] >> 24 == 2)) & (q[1:] == 2147483648))[0]
        i = i[i % 4 == 0]
        phys.append(i.astype(np.int64) * 4 + off + 64)
        n = len(q) - 32
        if n > 0:
            j = np.nonzero((q[3:3 + n] == 1065353216) & (q[4:4 + n] == 1065353216) & (q[5:5 + n] == 1065353216) & (q[6:6 + n] == 0) & (q[7:7 + n] == 0) & (q[16:16 + n] > 1101004800) & (q[16:16 + n] < 1123024896))[0]
            structs += [off + int(x) * 4 for x in j]
        progress((k + 1) / n_chunks)
    phys = [int(o) for o in np.concatenate(phys) if valid_pos(p, int(o))]
    from collections import Counter
    targets = Counter()
    for s in structs:
        try:
            t = p.f32(s + 32, 3)
        except OSError:
            continue
        if abs(t[0]) + abs(t[1]) + abs(t[2]) > 1.0:
            targets[tuple((round(v, 2) for v in t))] += 1
    for tgt, _n in targets.most_common(3):
        for o in phys:
            try:
                if math.dist(p.f32(o, 3), tgt) < 0.05:
                    return (o, phys, len(structs))
            except OSError:
                pass
    return (None, phys, len(structs))

def relink_near(p, old_pos, cam_off, window=256 << 20):
    lo, hi = (max(0, old_pos - window), min(p.size, old_pos + window))
    cands = []
    for off, q in p.chunks(lo, hi):
        i = np.nonzero(((q[:-1] >> 24 == 1) | (q[:-1] >> 24 == 2)) & (q[1:] == 2147483648))[0]
        i = i[i % 4 == 0]
        cands += [int(x) * 4 + off + 64 for x in i]
    cands = [o for o in cands if valid_pos(p, o)]
    if not cands or cam_off is None:
        return None
    a = {o: p.f32(o, 3) for o in cands}
    time.sleep(0.6)
    r, u, f, eye = read_cam(p, cam_off)
    best = None
    for o in cands:
        try:
            b = p.f32(o, 3)
        except OSError:
            continue
        d = [b[i] - eye[i] for i in range(3)]
        dist = math.sqrt(sum((v * v for v in d))) or 1e-06
        dot = sum((d[i] * f[i] for i in range(3))) / dist
        if not (1.5 < dist < 12 and dot > 0.5):
            continue
        moving = math.dist(a[o], b) > 0.05
        score = dot + (0.5 if moving else 0) - 0.02 * abs(dist - 4)
        if best is None or score > best[0]:
            best = (score, o, dist, dot, moving)
    if best:
        log(f"gjenfinning: {len(cands)} fysikk-poster rundt gammel post; valgt guest+0x{best[1]:X} ({best[2]:.1f} m foran kamera, retning {best[3]:.2f}, {('beveger seg' if best[4] else 'står stille')})")
        return best[1]
    return None

def relink_joint(p, pos_anchor, cam_anchors, wait=2.0, full=False):
    lo, hi = (max(0, pos_anchor - (256 << 20)), min(p.size, pos_anchor + (256 << 20)))
    recs = []
    for off, q in p.chunks(lo, hi):
        i = np.nonzero(((q[:-1] >> 24 == 1) | (q[:-1] >> 24 == 2)) & (q[1:] == 2147483648))[0]
        i = i[i % 4 == 0]
        recs += [int(x) * 4 + off + 64 for x in i]
    recs = [o for o in recs if valid_pos(p, o)]
    span = 32 << 20
    regions = sorted({(max(0, a - span) & ~(CHUNK - 1), min(p.size, a + span)) for a in cam_anchors if a and 0 < a < p.size})
    if full or not regions:
        cams = [g[1][0] for g in cam_scan(p, None, lambda _f: None)]
    else:
        cams = [g[1][0] for g in cam_scan(p, None, lambda _f: None, regions)]
    if not recs or not cams:
        log(f"gjenfinning: ingen kandidater ({len(recs)} poster, {len(cams)} kameraer{(', fullt søk' if full else '')})")
        return (None, None)
    a_rec = {o: p.f32(o, 3) for o in recs}
    a_cam = {c: read_cam(p, c)[3] for c in cams}
    time.sleep(wait)
    best = None
    moved_cams = moved_recs = 0
    for o in recs:
        try:
            moved_recs += math.dist(p.f32(o, 3), a_rec[o]) >= 0.3
        except OSError:
            pass
    for c in cams:
        try:
            r, u, f, eye = read_cam(p, c)
        except OSError:
            continue
        de = [eye[i] - a_cam[c][i] for i in range(3)]
        if math.sqrt(sum((v * v for v in de))) < 0.3:
            continue
        moved_cams += 1
        for o in recs:
            try:
                b = p.f32(o, 3)
            except OSError:
                continue
            dr = [b[i] - a_rec[o][i] for i in range(3)]
            mr = math.sqrt(sum((v * v for v in dr)))
            if mr < 0.3:
                continue
            diff = math.sqrt(sum(((de[i] - dr[i]) ** 2 for i in range(3))))
            me = math.sqrt(sum((v * v for v in de)))
            hx = math.hypot(de[0], de[2]) * math.hypot(dr[0], dr[2])
            cosang = (de[0] * dr[0] + de[2] * dr[2]) / hx if hx > 1e-06 else 0
            if cosang < 0.7 or not 0.3 <= me / mr <= 3.0:
                continue
            d = [b[i] - eye[i] for i in range(3)]
            dist = math.sqrt(sum((v * v for v in d))) or 1e-06
            dot = sum((d[i] * f[i] for i in range(3))) / dist
            if not (1.5 < dist < 12 and dot > 0.5):
                continue
            score = dot + cosang - 0.1 * abs(math.log(me / mr))
            if best is None or score > best[0]:
                best = (score, o, c, dist, dot, mr, diff)
    if best:
        log(f'gjenfinning (bevegelse): {len(recs)} poster, {len(cams)} kameraer; Link guest+0x{best[1]:X} + kamera guest+0x{best[2]:X} flyttet {best[5]:.1f} m sammen (avvik {best[6]:.2f}), {best[3]:.1f} m foran, retning {best[4]:.2f}')
        return (best[1], best[2])
    try:
        mc = sorted(((math.dist(read_cam(p, c)[3], a_cam[c]), c) for c in cams), reverse=True)[:3]
        mrr = sorted(((math.dist(p.f32(o, 3), a_rec[o]), o) for o in recs), reverse=True)[:3]
        log('  mest bevegelse – kameraer: ' + ', '.join((f'0x{c:X} {d:.1f} m' for d, c in mc)) + ' | poster: ' + ', '.join((f'0x{o:X} {d:.1f} m' for d, o in mrr)))
    except OSError:
        pass
    log(f"gjenfinning: ingen par flyttet seg sammen ({len(recs)} poster/{moved_recs} beveget, {len(cams)} kameraer/{moved_cams} beveget{(', fullt søk' if full else '')}) – står spilleren stille?")
    return (None, None)

def phys_scan(p, progress):
    phys = []
    n = max(1, p.size // CHUNK)
    for k, (off, q) in enumerate(p.chunks()):
        i = np.nonzero(((q[:-1] >> 24 == 1) | (q[:-1] >> 24 == 2)) & (q[1:] == 2147483648))[0]
        i = i[i % 4 == 0]
        phys.append(i.astype(np.int64) * 4 + off + 64)
        progress((k + 1) / n)
    return [int(o) for o in np.concatenate(phys) if valid_pos(p, int(o))]

def rupee_scan(p, value, progress):
    hits = []
    n = max(1, p.size // CHUNK)
    for k, (off, q) in enumerate(p.chunks()):
        j = np.nonzero(q[:-6] == value)[0]
        if len(j):
            m = (q[j + 1] == value) & (q[j + 2] == 0) & (q[j + 3] > 16777215) & (q[j + 4] == 0) & np.isin(q[j + 5], (1, 8, 32))
            hits.extend((int(x) * 4 + off for x in j[m]))
        progress((k + 1) / n)
    return [o for o in hits if valid_rupee(p, o)]
APP_DIR = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
PC = os.getenv('COMPUTERNAME', socket.gethostname())
_LOG_FILES = [os.path.join(CFG_DIR, 'log.txt'), os.path.join(APP_DIR, 'logs', f'{PC}.log')]
_log_lock = threading.Lock()

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{PC}] v{VERSION} {msg}\n"
    with _log_lock:
        for path in _LOG_FILES:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'a', encoding='utf-8') as f:
                    f.write(line)
            except OSError:
                pass

def load_cfg():
    try:
        with open(CFG, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}

def save_cfg(c):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG, 'w', encoding='utf-8') as f:
        json.dump(c, f, indent=2)

def local_ips():
    ips = set()
    try:
        for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if not a[4][0].startswith(('127.', '169.254.')):
                ips.add(a[4][0])
    except OSError:
        pass
    return sorted(ips)
ITEMS = [('rupees', 0, 560, 'Rupees')]

class CounterSync:

    def __init__(self, p, off, maxv=999, name='rupees', cap_off=None):
        self.p, self.off, self.max, self.name, self.cap_off = (p, off, maxv, name, cap_off)
        self.expected = p.u32(off)
        self.my_cum = 0
        self.seen = None
        self.behind_since = None

    def cap(self):
        if self.cap_off is not None:
            try:
                c = self.p.u32(self.cap_off)
                if 0 < c < 1000:
                    return c
            except OSError:
                pass
        return self.max

    def tick(self):
        cur = self.p.u32(self.off)
        if cur != self.expected:
            self.my_cum += cur - self.expected
            self.expected = cur
        return cur

    def _set(self, new):
        for o in (self.off, self.off + 4):
            self.p.write(self.p.base + o, struct.pack('<I', new))
        self.expected = new

    def equalize(self, remote_val, now, force=False):
        if remote_val is None:
            return
        cur = self.tick()
        if remote_val <= cur:
            self.behind_since = None
            return
        if not force:
            if self.behind_since is None:
                self.behind_since = now
                return
            if now - self.behind_since < 3:
                return
        new = min(self.cap(), remote_val)
        if new > cur:
            log(f'{self.name}: utjevnet til vennens tall {cur} -> {new}')
            self._set(new)
        self.behind_since = None

    def apply(self, remote_cum, reset):
        if self.seen is None or reset:
            self.seen = remote_cum
            return
        inc, self.seen = (remote_cum - self.seen, remote_cum)
        if inc:
            before = self.tick()
            new = max(0, min(self.cap(), before + inc))
            log(f'{self.name}-synk fra venn: {inc:+d} ({before} -> {new})')
            self._set(new)

def lichii_window(pid):
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        wpid = wt.DWORD()
        u32w.GetWindowThreadProcessId(h, ctypes.byref(wpid))
        if wpid.value == pid and u32w.IsWindowVisible(h):
            n = u32w.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u32w.GetWindowTextW(h, b, n + 1)
                if 'Lichii' in b.value:
                    found.append((h, b.value))
        return True
    u32w.EnumWindows(cb, 0)
    for h, t in found:
        if t.startswith('Lichii |'):
            return h
    for h, t in found:
        if 'Configuration' not in t and 'Config' not in t:
            return h
    return None

def game_rect(h):
    r = wt.RECT()
    u32w.GetClientRect(h, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    u32w.ClientToScreen(h, ctypes.byref(pt))
    try:
        scale = u32w.GetDpiForWindow(h) / 96
    except (AttributeError, OSError):
        scale = 1.0
    menu, status = (22 * scale, 26 * scale)
    w, ah = (r.right, r.bottom - menu - status)
    if w <= 0 or ah <= 0:
        return None
    gw, gh = (ah * 16 / 9, ah) if w / ah > 16 / 9 else (w, w * 9 / 16)
    return (pt.x + (w - gw) / 2, pt.y + menu + (ah - gh) / 2, gw, gh)

def project(cam, P, rect, fov=CAM_FOV):
    r, u, f, eye = cam
    x0, y0, w, h = rect
    d = [P[i] - eye[i] for i in range(3)]
    vx = sum((d[i] * r[i] for i in range(3)))
    vy = sum((d[i] * u[i] for i in range(3)))
    vz = sum((d[i] * f[i] for i in range(3)))
    t = math.tan(math.radians(fov) / 2)
    if vz > 0.1:
        sx = x0 + w / 2 + vx / (vz * t * (w / h)) * w / 2
        sy = y0 + h / 2 - vy / (vz * t) * h / 2
        if x0 <= sx <= x0 + w and y0 <= sy <= y0 + h:
            return (sx, sy, True)
    if abs(vx) < 0.001 and abs(vy) < 0.001:
        vy = -1.0
    ang = math.atan2(-vy, vx)
    cx, cy, m = (x0 + w / 2, y0 + h / 2, 40)
    k = min((w / 2 - m) / max(abs(math.cos(ang)), 1e-06), (h / 2 - m) / max(abs(math.sin(ang)), 1e-06))
    return (cx + math.cos(ang) * k, cy + math.sin(ang) * k, False)

class Overlay:
    KEY = '#010203'

    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg=self.KEY)
        self.win.attributes('-transparentcolor', self.KEY)
        gx, gy = (u32w.GetSystemMetrics(76), u32w.GetSystemMetrics(77))
        gw, gh = (u32w.GetSystemMetrics(78), u32w.GetSystemMetrics(79))
        self.origin = (gx, gy)
        self.win.geometry(f'{gw}x{gh}{gx:+d}{gy:+d}')
        self.cv = tk.Canvas(self.win, width=gw, height=gh, bg=self.KEY, highlightthickness=0)
        self.cv.pack()
        self.win.update_idletasks()
        hw = u32w.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        u32w.SetWindowLongW(hw, -20, u32w.GetWindowLongW(hw, -20) | 524288 | 32 | 128)

    def clear(self):
        self.cv.delete('all')

    def draw_ghost(self, foot, head, dirpt, name, dist, hat_hex=None):
        ox, oy = self.origin
        fx, fy = (foot[0] - ox, foot[1] - oy)
        hx, hy = (head[0] - ox, head[1] - oy)
        h = max(18.0, min(700.0, fy - hy))
        c, cv = (hat_hex or ACC, self.cv)
        tunic = hat_hex or '#3fa34d'
        cap = hat_hex or '#2f8a3b'
        face_x = dirpt[0] - foot[0] if dirpt else 0.0
        sgn = 1 if face_x >= 0 else -1
        stp = 'gray50'
        if dirpt:
            dx, dy = (dirpt[0] - ox, dirpt[1] - oy)
            cv.create_line(fx, fy, dx, dy, fill='black', width=6, arrow='last', arrowshape=(14, 16, 6))
            cv.create_line(fx, fy, dx, dy, fill=c, width=3, arrow='last', arrowshape=(12, 14, 5))
        cv.create_rectangle(fx - 0.12 * h, fy - 0.3 * h, fx + 0.12 * h, fy, fill='#6b4b2a', stipple=stp, outline=c, width=2)
        cv.create_polygon(fx - 0.21 * h, fy - 0.3 * h, fx + 0.21 * h, fy - 0.3 * h, fx + 0.15 * h, fy - 0.62 * h, fx - 0.15 * h, fy - 0.62 * h, fill=tunic, stipple=stp, outline=c, width=2)
        cv.create_oval(fx - 0.15 * h, fy - 0.9 * h, fx + 0.15 * h, fy - 0.6 * h, fill='#e8c39a', stipple=stp, outline=c, width=2)
        cv.create_polygon(fx - 0.17 * h, fy - 0.78 * h, fx + 0.17 * h, fy - 0.78 * h, fx, fy - 1.0 * h, fx - sgn * 0.52 * h, fy - 0.62 * h, fill=cap, stipple=stp, outline=c, width=2)
        label = f'{name}  {dist:.0f} m'
        cv.create_text(fx + 1, hy - 0.2 * h - 11, text=label, fill='black', font=('Segoe UI', 12, 'bold'))
        cv.create_text(fx, hy - 0.2 * h - 12, text=label, fill=c, font=('Segoe UI', 12, 'bold'))

    def draw_friend(self, sx, sy, on_screen, name, dist, hat_hex=None):
        ox, oy = self.origin
        x, y = (sx - ox, sy - oy)
        c = hat_hex or ACC
        label = f'{name}  {dist:.0f} m'
        if on_screen:
            self.cv.create_polygon(x, y + 10, x - 9, y - 4, x + 9, y - 4, fill=c, outline='black', width=2)
            self.cv.create_text(x + 1, y - 21, text=label, fill='black', font=('Segoe UI', 12, 'bold'))
            self.cv.create_text(x, y - 22, text=label, fill=c, font=('Segoe UI', 12, 'bold'))
        else:
            self.cv.create_oval(x - 16, y - 16, x + 16, y + 16, fill='#1d232a', outline=c, width=3)
            self.cv.create_text(x, y, text='!', fill=c, font=('Segoe UI', 14, 'bold'))
            self.cv.create_text(x, y + 28, text=label, fill=c, font=('Segoe UI', 10, 'bold'))
BAG_FIRST, BAG_LAST, ENTRY = (320, 4920, 40)
BAG_TYPE = 32
LEARN_SPAN = 300

class BagSync:

    def __init__(self, p, rup, cfg, skip_rels):
        self.p, self.rup, self.cfg = (p, rup, cfg)
        self.delta = set((r for r in cfg.get('bag_delta', []) if BAG_FIRST <= r <= BAG_LAST))
        self.ignored = set(cfg.get('bag_ignored', []))
        self.rels = []
        for rel in range(BAG_FIRST, BAG_LAST + 1, ENTRY):
            if rel in skip_rels:
                continue
            try:
                if p.u32(rup + rel + 20) == BAG_TYPE and valid_rupee(p, rup + rel):
                    self.rels.append(rel)
            except OSError:
                pass
        self.last = {r: self._read(r) for r in self.rels}
        self.counters = {}
        for r in self.delta & set(self.rels):
            self._make_counter(r, seen0=None)
        self.changes = {}
        self.learn_last = {}
        log(f'bag: {len(self.rels)} poster i inventar-blokken, {len(self.delta)} kjente forbruksvarer, {len(self.ignored)} ignorert; eier nå {sum((1 for r in self.rels if self.last[r]))} ting')

    def _read(self, rel):
        return self.p.u32(self.rup + rel)

    def _write(self, rel, v):
        for o in (rel, rel + 4):
            self.p.write(self.p.base + self.rup + o, struct.pack('<I', v))
        self.last[rel] = v

    def _make_counter(self, rel, seen0):
        c = CounterSync(self.p, self.rup + rel, 999, f'bag{rel:+#x}')
        c.seen = seen0
        self.counters[rel] = c
        return c

    def _save(self):
        self.cfg['bag_delta'] = sorted(self.delta)
        self.cfg['bag_ignored'] = sorted(self.ignored)
        save_cfg(self.cfg)

    def mark_delta(self, rel, why):
        if rel in self.delta or rel in self.ignored or rel not in self.rels:
            return
        self.delta.add(rel)
        c = self._make_counter(rel, seen0=0)
        if why == 'lokal':
            c.expected = self.last[rel]
        log(f'bag: rupees{rel:+#x} er forbruksvare ({why}) – synkes som delta fra nå')
        self._save()

    def tick(self, now):
        vals = {}
        for rel in self.rels:
            if rel in self.ignored:
                continue
            if rel in self.counters:
                vals[rel] = self.counters[rel].tick()
                continue
            v = self._read(rel)
            old = self.last[rel]
            if v != old:
                t = self.changes.setdefault(rel, [])
                t.append(now)
                t[:] = [x for x in t if now - x < 60]
                log(f'bag: rupees{rel:+#x} endret {old} -> {v} (lokalt)')
                if len(t) >= 10:
                    self.ignored.add(rel)
                    self._save()
                    log(f'bag: rupees{rel:+#x} endrer seg {len(t)} ganger/min – ignoreres (trolig tilstand, ikke beholdning)')
                elif v < old:
                    self.last[rel] = old
                    self.mark_delta(rel, 'lokal')
                    vals[rel] = self.counters[rel].tick()
                    continue
                self.last[rel] = v
            vals[rel] = v
        cums = {rel: c.my_cum for rel, c in self.counters.items()}
        return (vals, cums)

    def apply(self, msg, reset, now):
        for r in msg.get('bag_delta', []):
            if r in self.rels and r not in self.delta:
                self.mark_delta(r, 'lært av vennen')
        for r in msg.get('bag_ignored', []):
            if r not in self.ignored and r in self.rels:
                self.ignored.add(r)
                self._save()
                log(f'bag: rupees{r:+#x} ignoreres (lært av vennen)')
        cums = {int(k): v for k, v in (msg.get('bag_cums') or {}).items()}
        vals = {int(k): v for k, v in (msg.get('bag') or {}).items()}
        for rel, c in self.counters.items():
            if rel in cums:
                c.apply(cums[rel], reset)
            c.equalize(vals.get(rel), now, force=reset)
        for rel, rv in vals.items():
            if rel in self.counters or rel in self.ignored or rel not in self.rels or (rel not in self.last):
                continue
            cur = self._read(rel)
            if isinstance(rv, int) and 0 <= rv <= 4294967295 and (rv > cur):
                merged = max(cur, rv)
                if merged > cur:
                    log(f'inventar: rupees{rel:+#x} {cur} -> {merged} (oppgradering/ting synket)')
                    self._write(rel, merged)

    def owned(self):
        return sum((1 for r in self.rels if r not in self.ignored and self.last.get(r)))

def learn_table(p, rup, state, now):
    try:
        words = p.u32(rup - LEARN_SPAN * ENTRY, 2 * LEARN_SPAN * ENTRY // 4)
    except OSError:
        return
    step = ENTRY // 4
    marker = p.u32(rup + 12)
    for k in range(2 * LEARN_SPAN):
        i = k * step
        if words[i + 3] != marker:
            continue
        rel = (k - LEARN_SPAN) * ENTRY
        if BAG_FIRST <= rel <= BAG_LAST or rel in (10040, 10080):
            continue
        v = words[i]
        old = state.get(rel)
        state[rel] = v
        if old is not None and old != v and (v < 100000) and (old < 100000):
            n = state.setdefault(('n', rel), 0) + 1
            state['n', rel] = n
            if n <= 20:
                log(f'lær: rupees{rel:+#x} {old} -> {v} (type {words[i + 5]:#x})')
BG, FG, MUTED, OK, WARN, ACC = ('#0b1410', '#efe6c8', '#8a9a85', '#5cc85c', '#e0a030', '#e8c35a')
PANEL, FIELD, BORDER, GOLD, GOLD_D, BTN, BTN_H, SEL, HEART = ('#13221a', '#0f1a14', '#2c4a35', '#e8c35a', '#9c7c2c', '#1d3324', '#2a4a33', '#2f6d3a', '#ff6b6b')
_PERF = {}
_PERF_LK = threading.Lock()
_PERF_T0 = [time.time()]

def perf_add(name, dt, n=1):
    with _PERF_LK:
        c = _PERF.setdefault(name, [0, 0.0, 0.0])
        c[0] += n
        c[1] += dt
        c[2] = max(c[2], dt)

def perf_report():
    now = time.time()
    span = now - _PERF_T0[0]
    if span < 30:
        return
    with _PERF_LK:
        items = sorted(_PERF.items(), key=lambda kv: -kv[1][1])
        _PERF.clear()
        _PERF_T0[0] = now
    parts = [f'{k} {v[0] / span:.1f}/s {1000 * v[1] / span:.1f}ms/s maks {1000 * v[2]:.0f}ms' for k, v in items]
    log('ytelse (' + f'{span:.0f} s): ' + ' | '.join(parts))
LOOKS = ['Normal', 'No tunic']
COLOR_EN = {'Grønn': 'Green', 'Rød': 'Red', 'Blå': 'Blue', 'Hvit': 'White', 'Gul': 'Yellow', 'Lilla': 'Purple', 'Rosa': 'Pink', 'Oransje': 'Orange', 'Svart': 'Black'}
HAT_COLORS = {'Grønn': '#2f8a3b', 'Blå': '#2e6fd8', 'Rød': '#d63c3c', 'Lilla': '#8e44ad', 'Gull': '#f0c040', 'Hvit': '#e6e9ee'}
HAT_RGB = {'Grønn': (0.18, 0.54, 0.23), 'Blå': (0.18, 0.44, 0.85), 'Rød': (0.84, 0.24, 0.24), 'Lilla': (0.56, 0.27, 0.68), 'Gull': (0.94, 0.75, 0.25), 'Hvit': (0.92, 0.94, 0.96)}

class FigureSync:
    SIG = bytes.fromhex('7f2303d5d1fdffd0308a43b91f362d71')
    V3 = bytes.fromhex('fd7bbea9')

    def __init__(self):
        self.p = None
        self.mbx = None
        self.actor = None
        self.state = 'av'
        self.active = False
        self._scan = None
        self._jt = 0
        self._msg = ''
        self.host = None
        self._off = self.Y_OFF
        self._lk = threading.Lock()
        self._samples = []
        self._want = False
        self._thr = None

    def _note(self, msg):
        if msg != self._msg:
            self._msg = msg
            log(f'figur: {msg}')

    def _find_mbx(self, p):
        off = 0
        while off < p.size:
            n = min(4 << 20, p.size - off)
            try:
                d = p.read(p.base + off, n)
            except (MemoryError, OSError):
                off += n
                continue
            i = d.find(self.SIG)
            if i != -1:
                base = off + i - 7618828
                ok = p.read(p.base + base + 7619212, 4) == self.V3
                return p.base + base + 7332744 if ok else 'gammel'
            off += n
        return None

    def tick(self, p, friend, alive, now, me=None, follow_me=False):
        if p is None:
            self.p = None
            self.mbx = None
            return
        if p is not self.p:
            self.p, self.mbx, self.actor = (p, None, None)
        if now - self._jt > 2:
            self._jt = now
            try:
                with open(os.path.join(APP_DIR, 'figur.json'), encoding='utf-8') as f:
                    j = json.load(f)
                self.actor = int(j['actor'], 16) if j.get('pid') == p.pid else None
                self.host = int(j['host'], 16) if self.actor and j.get('host') else None
                if self.actor is None:
                    self._note('figur.json hører til en annen Lichii-økt – lag figuren på nytt')
            except (FileNotFoundError, ValueError, KeyError):
                self.actor = None
        if self.actor is None:
            self._stop()
            return
        if self.mbx is None:
            if self._scan is None:
                self._note('leter etter patch v3 (postkasse) …')
                self._scan = {}
                threading.Thread(target=lambda: self._scan.update(r=self._find_mbx(p)), daemon=True).start()
                return
            if 'r' not in self._scan:
                return
            r = self._scan['r']
            self._scan = None
            if not isinstance(r, int):
                self._note('patch v3 er ikke lastet i Lichii' if r is None else 'gammel patch (ikke v3) – start Lichii på nytt')
                self.actor = None
                self._jt = now + 30
                return
            self.mbx = r
            self._note(f'postkasse funnet, figur 0x{self.actor:X}')
        pos = friend.get('pos') if alive and friend else None
        if pos is None and follow_me and me:
            fx, fz = (me[3], me[4] if len(me) > 4 else 0)
            n = math.hypot(fx, fz) or 1
            pos = [me[0] - fx / n * 2, me[1], me[2] - fz / n * 2]
        ok = bool(pos and len(pos) == 3 and all((math.isfinite(v) and abs(v) < 100000.0 for v in pos)))
        with self._lk:
            if ok:
                if not self._samples or list(self._samples[-1][1]) != list(pos):
                    self._samples.append((now, tuple(pos)))
                    del self._samples[:-8]
                self._want = True
            else:
                self._want = False
        if ok and (not self.active):
            self.active = True
            self._note('figuren følger vennen (jevn interpolering, 120 Hz)')
        if not ok:
            self._stop()
        if self._thr is None:
            self._thr = threading.Thread(target=self._writer, daemon=True)
            self._thr.start()
    Y_OFF = -0.58
    DELAY = 0.1

    def _interp(self, t):
        sm = self._samples
        if not sm:
            return None
        if t <= sm[0][0]:
            return sm[0][1]
        for (t0, a), (t1, b) in zip(sm, sm[1:]):
            if t0 <= t <= t1:
                k = (t - t0) / (t1 - t0 or 1e-06)
                return tuple((a[i] + (b[i] - a[i]) * k for i in range(3)))
        return sm[-1][1]

    def _writer(self):
        while True:
            time.sleep(1 / 120)
            try:
                with self._lk:
                    if not (self._want and self.mbx and self.p and self.actor):
                        continue
                    pos = self._interp(time.time() - self.DELAY)
                    p, mbx, actor = (self.p, self.mbx, self.actor)
                if pos is None:
                    continue
                y = pos[1] + self.Y_OFF
                host = self.host
                if host:
                    vt = struct.unpack('<Q', p.read(p.base + host, 8))[0]
                    if getattr(self, '_vt', None) is None or getattr(self, '_vt_host', None) != host:
                        self._vt, self._vt_host = (vt, host)
                    elif vt != self._vt:
                        with self._lk:
                            self.actor = None
                        p.write(mbx, bytes(4))
                        log('figur: figuren er borte (områdebytte?) – slutter å skrive. Lag den på nytt.')
                        continue
                if host:
                    cur = struct.unpack('<f', p.read(p.base + host + 800, 4))[0]
                    if math.isfinite(cur):
                        target = pos[1] + self._off
                        err = cur - target
                        if abs(err) < 3.0:
                            self._off += (0.03 if err < 0 else 0.003) * err
                            self._off = max(self._off, self.Y_OFF - 1.2)
                            now = time.time()
                            py, pt = getattr(self, '_py', (pos[1], now))
                            if now - pt > 0.05:
                                vy = (pos[1] - py) / (now - pt)
                                self._py = (pos[1], now)
                                self._vy = 0.4 * getattr(self, '_vy', 0.0) + 0.6 * vy
                            lead = max(0.0, min(getattr(self, '_vy', 0.0) * 0.6 + (0.15 if getattr(self, '_vy', 0.0) > 0.3 else 0.0), 1.5))
                            y = max(cur, pos[1] + self._off + lead)
                        elif err < 0:
                            y = target
                        else:
                            y = cur
                tx, tz = (pos[0], pos[2])
                now = time.time()
                lx, lz, lt = getattr(self, '_xz', (tx, tz, now))
                dt = min(now - lt, 0.1)
                under = host and math.isfinite(cur) and (y - cur > 0.25)
                vmax = 2.5 if under else 12.0
                dx, dz = (tx - lx, tz - lz)
                dist = math.hypot(dx, dz)
                if dist > 8.0:
                    lx, lz = (tx, tz)
                elif dist > vmax * dt:
                    k = vmax * dt / dist
                    lx, lz = (lx + dx * k, lz + dz * k)
                else:
                    lx, lz = (tx, tz)
                self._xz = (lx, lz, now)
                pos = (lx, pos[1], lz)
                p.write(mbx + 4, struct.pack('<3f', pos[0], y, pos[2]) + struct.pack('<Q', actor))
                p.write(mbx, struct.pack('<I', 2893))
            except OSError:
                with self._lk:
                    self.mbx = None
            except Exception as e:
                log(f'figur-skriver feil: {e!r}')
                time.sleep(1)

    def _stop(self):
        with self._lk:
            self._want = False
            self._samples.clear()
        if self.active and self.mbx and self.p:
            try:
                self.p.write(self.mbx, bytes(4))
            except OSError:
                pass
            self._note('figuren står (ingen venn)')
        self.active = False

class PadLink:
    MARK, R_OFF, MAGIC, ACT_VT = (1330597966, 81180928, 2894, 78798360)

    def __init__(self):
        self.hR = None
        self.R = None
        self.main = None
        self.player = None
        self.copy = None
        self.delta = None
        self.p_act = None
        self.p_x8 = None
        self.c_act = None
        self.c_x8 = None
        self.c_x9 = None
        self.p_x19_h = None
        self.c_x19_h = None
        self.scene_changed = False
        self.last_pos = None
        self.last_warp_t = 0
        self.cur_banc = None
        self.friend_banc = None
        self._banc_t = 0
        self._banc_since = 0
        self.h_blm = None
        self.h_evarg = None
        self._p_act_since = 0.0
        self._last_p_act = None
        self._pending_tp = None
        self.suppress_warp_until = 0
        self.seen = {}
        self.busy = False
        self.last_try = 0
        self.active = False
        self.has_v4 = False
        self._lk = threading.Lock()
        self._call_lk = threading.Lock()
        self._ev_lk = threading.Lock()
        self._call_timeouts = 0
        self._p = None
        self._cam_off = None
        self._alive = False
        self._hidden = False
        self._pad_hex = None
        self._f_cam_a = None
        self._my_cam_a = None
        self._t_pos = None
        self._t_rot = None
        self._thr = None
        self._last_zero_t = 0
        self._last_spawn_try = 0
        self._deleting = False
        self._last_del_try = 0.0
        self._deleted_x19 = None
        self._deleted_until = 0.0
        self._f_hearts = None
        self._c_sv_cur = None
        self._c_sv_max = None
        self._last_hp_sync = 0.0

    def _find(self, p):
        needle = struct.pack('<I', self.MARK)
        hi_fast = min(p.size, 16106127360)
        for low in (14203136, 1620224, 571648):
            for off in (*range(10737418240 + low, hi_fast, 2097152), *range(low, min(10737418240, hi_fast), 2097152)):
                try:
                    d = p.read(p.base + off + 664, 16)
                except OSError:
                    continue
                if d[:4] == needle:
                    va = struct.unpack_from('<Q', d, 8)[0]
                    if va & 4095 == 2304 and va > self.R_OFF:
                        return (off, va)
        for off in range(0, p.size, CHUNK):
            try:
                b = p.read(p.base + off, min(CHUNK, p.size - off))
            except OSError:
                continue
            i = b.find(needle)
            while i != -1:
                if 664 <= i and i + 16 <= len(b):
                    va = struct.unpack_from('<Q', b, i + 8)[0]
                    if va & 4095 == 2304 and va > self.R_OFF:
                        return (off + i - 664, va)
                i = b.find(needle, i + 1)
        return (None, None)

    def _u64(self, p, va):
        if not va or self.delta is None:
            return 0
        h = va - self.delta
        if not 0 <= h <= p.size - 8:
            return 0
        return struct.unpack('<Q', p.read(p.base + h, 8))[0]

    def _ensure_delta(self, p, x19):
        if self.delta is not None:
            try:
                if p.read(p.base + x19 - self.delta + 56, 16) == struct.pack('<2Q', x19 + 30280, x19 + 30224):
                    return True
            except OSError:
                pass
            self.delta = None
        exp = struct.pack('<2Q', x19 + 30280, x19 + 30224)
        for step in (2097152, 65536):
            low = x19 + 56 & step - 1
            hi_fast = min(p.size, 15569256448)
            for off in (*range(10737418240 + low, hi_fast, step), *range(low, p.size, step)):
                try:
                    if p.read(p.base + off, 16) == exp:
                        self.delta = x19 - (off - 56)
                        log(f'PadLink: heap-delta +0x{self.delta:X}')
                        return True
                except OSError:
                    continue
        return False

    def _is_valid_x19(self, p, x19):
        if not x19 or not self._ensure_delta(p, x19):
            return False
        try:
            a = self._u64(p, x19 + 24)
            if not a:
                return False
            vt_exp = (self.main or 0) + self.ACT_VT
            return self._u64(p, a) == vt_exp
        except OSError:
            return False

    def _resolve_actors(self, p):
        x0 = self.player or self.copy
        if not x0 or not self._ensure_delta(p, x0):
            return
        vt_exp = (self.main or 0) + self.ACT_VT
        if self.player:
            if not self._is_valid_x19(p, self.player):
                log('PadLink: spiller-actor er borte (områdebytte)')
                self.player = None
                self.p_act = self.p_x8 = self.p_x19_h = None
                self.scene_changed = True
            else:
                self.p_x19_h = self.player - self.delta
                a = self._u64(p, self.player + 24)
                self.p_act = a - self.delta
                p270 = self._u64(p, a + 624)
                p50 = self._u64(p, p270 + 80) if p270 else 0
                x8 = self._u64(p, p50 + 32) if p50 else 0
                if x8:
                    self.p_x8 = x8 - self.delta
        if self.copy:
            if not self._is_valid_x19(p, self.copy):
                log('PadLink: kopi-actor er borte – nullstiller kopi')
                self.copy = None
                self.c_act = self.c_x8 = self.c_x9 = self.c_x19_h = None
                if self.hR is not None:
                    try:
                        p.write(p.base + self.hR + 648, bytes(8))
                    except OSError:
                        pass
            else:
                self.c_x19_h = self.copy - self.delta
                a = self._u64(p, self.copy + 24)
                c270 = self._u64(p, a + 624)
                c50 = self._u64(p, c270 + 80) if c270 else 0
                x8 = self._u64(p, c50 + 32) if c50 else 0
                x9 = self._u64(p, x8 + 456) if x8 else 0
                if not x8:
                    if not getattr(self, '_c_bad_since', 0):
                        self._c_bad_since = time.time()
                    elif time.time() - self._c_bad_since > 3.0:
                        log(f'PadLink: kopi 0x{self.copy:X} mangler komponenter (zombie) – lager ny Link 2')
                        self._deleted_x19 = self.copy
                        self._deleted_until = time.time() + 1000000000.0
                        self.copy = None
                        self.c_act = self.c_x8 = self.c_x9 = self.c_x19_h = None
                        self._c_bad_since = 0
                        try:
                            p.write(p.base + self.hR + 648, bytes(8))
                        except OSError:
                            pass
                        return
                else:
                    self._c_bad_since = 0
                if x8:
                    if self.c_act != a - self.delta:
                        log(f'PadLink: kopi-actor 0x{a:X} (vert +0x{a - self.delta:X}), x8 0x{x8:X}')
                    self.c_act, self.c_x8, self.c_x9 = (a - self.delta, x8 - self.delta, x9 - self.delta if x9 else None)
                self._silence_copy_savevars(p, a)
    SAVEVAR_VT = 79049784

    def _silence_copy_savevars(self, p, c_act_va):
        if not self.copy or not self.delta or (not self.main):
            return
        try:
            c_sub = self.copy - 8000
            if self._u64(p, c_sub + 24) != c_act_va:
                return
            vt_exp = self.main + self.SAVEVAR_VT
            for rel in (7184, 7192):
                sv = self._u64(p, c_sub + rel)
                if sv and self._u64(p, sv) == vt_exp:
                    val_off = sv - self.delta + 8
                    if rel == 7184:
                        self._c_sv_cur = val_off
                    else:
                        self._c_sv_max = val_off
                    key_off = sv - self.delta + 12
                    key = struct.unpack('<I', p.read(p.base + key_off, 4))[0]
                    if key != 0:
                        p.write(p.base + key_off, bytes(4))
                        log(f'PadLink: deaktiverte kopi SaveVar +0x{rel:X} (gammel nøkkel 0x{key:08X} -> 0)')
        except OSError:
            pass
    CALL_OFF = 1792
    F_ALLOC, F_EVARG_CTOR, F_EV_START, GOT_EVMGR = (257184, 14130960, 14133280, 80208016)
    GOT_ACTMGR, F_ACT_CREATE, F_ACT_DELETE = (80212960, 3615344, 681104)
    START_POS = {'KokiriForest_Child': 'LostWoodBridge_FrontOfHyruleField', 'HyruleField_Child': 'HyruleField_FrontOfLostWoodBridge'}
    GOT_GDM = 80208256

    def read_door(self, p):
        if self.hR is None or self.main is None:
            return None
        try:
            if getattr(self, 'h_str64', None) is None:
                if time.time() - getattr(self, '_gdm_scan_t', 0) < 5.0:
                    return None
                self._gdm_scan_t = time.time()
                u64 = lambda o: struct.unpack('<Q', p.read(p.base + o, 8))[0]
                gdm_va = u64(self.hR + (u64(self.hR + (self.main + self.GOT_GDM - self.R)) - self.R))
                ok = lambda o: u64(o + 584) == self.main + 79820032 and u64(o + 1584) == 96
                h_gdm = self._find_host_ptr(p, gdm_va, ok)
                if h_gdm is None:
                    return None
                self.h_str64 = u64(h_gdm + 1560) - (gdm_va - h_gdm)
                self.h_s32 = u64(h_gdm + 216) - (gdm_va - h_gdm)
                self.h_bools = u64(h_gdm + 24) - (gdm_va - h_gdm)
                self.n_bools = struct.unpack('<I', p.read(p.base + h_gdm + 16, 4))[0]
            nul = bytes(1)
            rd = lambda i: p.read(p.base + self.h_str64 + i * 264 + 116, 64).split(nul, 1)[0]
            start, banc = (rd(33), rd(34))
            if not start or not banc:
                return None
            start, banc = (start.decode('ascii'), banc.decode('ascii'))
            if '_' in banc and '_' in start and banc.replace('_', '').isalnum() and start.replace('_', '').isalnum():
                return (banc, start)
        except (OSError, UnicodeDecodeError):
            self.h_str64 = None
        return None

    def read_bools(self, p):
        if getattr(self, 'h_bools', None) is None:
            self.read_door(p)
        if getattr(self, 'h_bools', None) is None or not getattr(self, 'n_bools', 0):
            return None
        try:
            raw = p.read(p.base + self.h_bools, self.n_bools * 24)
        except OSError:
            self.h_str64 = self.h_bools = None
            return None
        return raw[22::24]

    def set_bool(self, p, i):
        if getattr(self, 'h_bools', None) is None or not 0 <= i < self.n_bools:
            return False
        try:
            p.write(p.base + self.h_bools + i * 24 + 22, bytes((1, 1)))
            return True
        except OSError:
            return False
    SYNC_PREFIX = ('Enemy_', 'Obj_Pot', 'Obj_Rock', 'Obj_Stone', 'Obj_Boulder', 'Obj_Bush', 'Obj_Grass', 'Obj_Kusa', 'Obj_Barrel', 'Obj_Crate', 'Obj_WoodBox')

    def scan_actors(self, p):
        t0 = time.perf_counter()
        try:
            return self._scan_actors_raw(p)
        finally:
            perf_add('fiendesøk', time.perf_counter() - t0)

    def _scan_actors_raw(self, p):
        main, p_act, delta = (self.main, self.p_act, self.delta)
        if main is None or p_act is None or delta is None:
            return None
        vt = np.uint64(main + self.ACT_VT)
        found = {}
        lo = max(0, p_act - (512 << 20))
        hi = min(p.size, p_act + (1024 << 20))
        now = time.time()
        full = now - getattr(self, '_scan_full_t', 0) > 30 or not getattr(self, '_scan_pages', None) or getattr(self, '_scan_banc', None) != self.cur_banc
        if full:
            chunks = p.chunks(lo, hi)
        else:

            def _pages():
                for pg in sorted(self._scan_pages):
                    try:
                        yield (pg, p.read(p.base + pg, 1048576))
                    except OSError:
                        pass
            chunks = _pages()
        pages = set()
        for off, q in chunks:
            n = len(q) - len(q) % 8
            if n < 8:
                continue
            for i in np.nonzero(np.frombuffer(q[:n], dtype=np.uint64) == vt)[0].tolist():
                a = off + i * 8
                try:
                    nv = struct.unpack('<Q', p.read(p.base + a + 608, 8))[0]
                    nm = p.read(p.base + nv - delta, 48).split(bytes(1), 1)[0].decode('ascii')
                    if not nm.startswith(self.SYNC_PREFIX) or 'Boss' in nm:
                        continue
                    pos = p.f32(a + 796, 3)
                    if all((math.isfinite(v) for v in pos)) and abs(pos[0]) + abs(pos[1]) + abs(pos[2]) > 1.0:
                        found[a] = (nm, tuple(pos))
                        pg = a & ~1048575
                        pages.update((pg - 1048576, pg, pg + 1048576))
                except (OSError, UnicodeDecodeError, TypeError):
                    pass
            del q
        if full:
            self._scan_full_t = now
            self._scan_banc = self.cur_banc
        self._scan_pages = {pg for pg in pages if lo <= pg < hi} | (set() if full else self._scan_pages)
        return found

    def actor_alive(self, p, a):
        try:
            return struct.unpack('<Q', p.read(p.base + a, 8))[0] == self.main + self.ACT_VT
        except OSError:
            return False

    def actor_x8(self, p, a):
        try:
            va = a + self.delta
            c270 = self._u64(p, va + 624)
            c50 = self._u64(p, c270 + 80) if c270 else 0
            x8 = self._u64(p, c50 + 32) if c50 else 0
            return x8 - self.delta if x8 else None
        except OSError:
            return None

    def move_actor(self, p, a, x8, pos, rot):
        try:
            r = rot if rot and len(rot) == 9 else p.f32(a + 808, 9)
            h = p.base + x8
            p.write(h + 464, struct.pack('<12f', r[0], r[1], r[2], pos[0], r[3], r[4], r[5], pos[1], r[6], r[7], r[8], pos[2]))
            w = struct.unpack('<I', p.read(h + 552, 4))[0]
            p.write(h + 552, struct.pack('<I', w | 3))
            return True
        except OSError:
            return False

    def create_actor(self, p, name, pos):
        if self.hR is None or self.main is None:
            return None
        if getattr(self, 'en_stack', None) is None:
            va = self.game_call(p, self.F_ALLOC, (16, 65536))
            if not va:
                return None
            self.en_stack = va + 65536 - 256 & ~15
        R = self.R
        h = p.base + self.hR + 3072
        base = R + 3072
        arg = bytearray(256)
        struct.pack_into('<3f', arg, 0, pos[0], pos[1] + 0.5, pos[2])
        struct.pack_into('<9f', arg, 12, 1, 0, 0, 0, 1, 0, 0, 0, 1)
        struct.pack_into('<3f', arg, 48, 1, 1, 1)
        arg[152] = 3
        struct.pack_into('<Q', arg, 64, base + 48)
        nb = name.encode()[:63]
        p.write(h, nb + bytes(64 - len(nb)) + struct.pack('<Q', base) + bytes(184) + bytes(arg))
        got = struct.unpack('<Q', p.read(p.base + self.hR + (self.main + self.GOT_ACTMGR - R), 8))[0]
        mgr = struct.unpack('<Q', p.read(p.base + self.hR + (got - R), 8))[0]
        return self.game_call(p, self.F_ACT_CREATE, (mgr, base + 64, base + 256, 0, 1, 0, 0, 0), timeout=3.0, stack_top=self.en_stack)

    def set_material_visible(self, p, act_host, mat, vis):
        d = self.delta
        q = lambda va: struct.unpack('<Q', p.read(p.base + va - d, 8))[0]
        try:
            model = q(q(q(q(q(q(act_host + d + 624) + 8) + 24) + 40)))
            vt = q(model)
        except OSError:
            return None
        if not 0 < vt - self.main < 100663296:
            return None
        hm = self.hR - (self.R - self.main)
        f_find = struct.unpack('<Q', p.read(p.base + hm + (vt - self.main) + 208, 8))[0] - self.main
        f_vis = struct.unpack('<Q', p.read(p.base + hm + (vt - self.main) + 456, 8))[0] - self.main
        h = p.base + self.hR + 3968
        nb = mat.encode()
        p.write(h, nb + bytes(64 - len(nb)))
        p.write(h + 64, struct.pack('<Q', self.R + 3968))
        idx = self.game_call(p, f_find, (model, self.R + 4032))
        if idx is None or idx & 4294967295 >= 2147483648:
            return None
        return self.game_call(p, f_vis, (model, idx & 4294967295, 1 if vis else 0))

    def delete_actor(self, p, a):
        self.__dict__.setdefault('self_deleted', set()).add(a)
        return self.game_call(p, self.F_ACT_DELETE, (a + self.delta, 0), timeout=2.0)

    def learn_start(self, banc, start, kilde):
        if banc and start and (self.START_POS.get(banc) == start) and (getattr(self, '_door_seen', None) != (banc, start)):
            self._door_seen = (banc, start)
            log(f'PadLink: startpunkt bekreftet {banc} -> {start} ({kilde})')
        if banc and start and (self.START_POS.get(banc) != start):
            self.START_POS[banc] = start
            self.learned_new = True
            log(f'PadLink: lærte startpunkt {banc} -> {start} ({kilde})')
    GOT_WM, F_LD8, F_ST32 = (82307760, 58388592, 10167744)

    def _time_addr(self, p):
        now = time.time()
        if getattr(self, '_wt_addr', None) and now - self._wt_t < 10:
            return self._wt_addr
        self._wt_addr, self._wt_t = (None, now)
        if self.hR is None or self.main is None:
            return None
        ld = lambda va: self.game_call(p, self.F_LD8, (va - 8,))
        wm = struct.unpack('<Q', p.read(p.base + self.hR + (self.main + self.GOT_WM - self.R), 8))[0]
        obj = ld(wm + 1400) if wm else None
        self._wt_obj = obj
        t = ld(obj + 3400) if obj else None
        flags = ld(t + 8) if t else None
        if not t or flags is None or flags >> 8 & 2:
            return None
        self._wt_addr = t + 40
        return self._wt_addr

    def read_time(self, p):
        a = self._time_addr(p)
        v = self.game_call(p, self.F_LD8, (a - 8,)) if a else None
        return None if v is None else (v & 4294967295) % 86400
    WEATHERS = ('Bluesky', 'Rain', 'Snow', 'Thunderstorm', 'Cloudy')
    GOT_WEATHER, F_SET_WEATHER = (80210072, 54874032)

    def read_weather(self, p):
        a = self._time_addr(p)
        obj = getattr(self, '_wt_obj', None)
        if not a or not obj:
            return None
        if getattr(self, '_wt_fget', None) is None:
            vt = self.game_call(p, self.F_LD8, (obj - 8,))
            f = self.game_call(p, self.F_LD8, (vt + 240 - 8,)) if vt else None
            self._wt_fget = f - self.main if f else None
        if not self._wt_fget:
            return None
        v = self.game_call(p, self._wt_fget, (obj,))
        return v if v is not None and 0 <= v < len(self.WEATHERS) else None

    def write_weather(self, p, w, speed=5):
        if self.hR is None or not 0 <= w < len(self.WEATHERS):
            return False
        mgr_p = struct.unpack('<Q', p.read(p.base + self.hR + (self.main + self.GOT_WEATHER - self.R), 8))[0]
        mgr = self.game_call(p, self.F_LD8, (mgr_p - 8,)) if mgr_p else None
        if not mgr:
            return False
        name = self.WEATHERS[w].encode()
        h = p.base + self.hR + 2816
        p.write(h, name + bytes(64 - len(name)))
        p.write(h + 64, struct.pack('<Q', self.R + 2816))
        return self.game_call(p, self.F_SET_WEATHER, (mgr, self.R + 2880, speed), timeout=3.0) is not None

    def write_time(self, p, sec):
        a = self._time_addr(p)
        return a is not None and self.game_call(p, self.F_ST32, (a, int(sec) % 86400)) is not None

    def _find_host_ptr(self, p, va, check_fn):
        if not va:
            return None
        if self.delta:
            cand = va - self.delta
            if 0 <= cand < p.size:
                try:
                    if check_fn(cand):
                        return cand
                except OSError:
                    pass
        low = va & 2097151
        hi = min(p.size, 16106127360)
        for s, e in ((10737418240 + low, hi), (low, min(10737418240, hi))):
            for o in range(s, e, 2097152):
                try:
                    if check_fn(o):
                        return o
                except OSError:
                    pass
        return None

    def read_banc(self, p):
        if self.hR is None:
            return None
        try:
            if self.h_blm is not None:
                s = p.read(p.base + self.h_blm + 76, 75)
                if s.startswith(b'Work/Banc/GameBancParam/'):
                    return s.split(b'\x00')[0].decode('latin1').split('/')[-1].split('.')[0] or None
                self.h_blm = None
            if time.time() - getattr(self, '_blm_scan_t', 0) < 3.0:
                return None
            self._blm_scan_t = time.time()
            va = struct.unpack('<Q', p.read(p.base + self.hR + 1204432, 8))[0]
            h_blm = self._find_host_ptr(p, va, lambda o: p.read(p.base + o + 76, 24) == b'Work/Banc/GameBancParam/')
            if h_blm is None:
                return None
            self.h_blm = h_blm
            s = p.read(p.base + h_blm + 76, 75)
            return s.split(b'\x00')[0].decode('latin1').split('/')[-1].split('.')[0] or None
        except OSError:
            self.h_blm = None
            return None

    def banc_tick(self, p, now):
        if self.p_act != self._last_p_act:
            self._last_p_act = self.p_act
            self._p_act_since = now if self.p_act is not None else 0.0
        if now - self._banc_t < 0.5:
            return
        self._banc_t = now
        d = self.read_door(p)
        if d:
            self.learn_start(d[0], d[1], 'dør')
        b = self.read_banc(p)
        if b and b != self.cur_banc:
            if self.cur_banc is not None:
                log(f'PadLink: sone {self.cur_banc} -> {b}')
                self.scene_changed = True
                self._zone_dead = set(getattr(self, '_zone_dead', set()))
                if self.copy is not None:
                    self._zone_dead.add(self.copy)
                    log('PadLink: sonebytte – glemmer gammel Link 2')
                    self.copy = None
                    self.c_act = self.c_x8 = self.c_x9 = None
                    self._c_sv_cur = self._c_sv_max = None
                    self._hidden = False
                    try:
                        p.write(p.base + self.hR + 648, bytes(8))
                    except Exception:
                        pass
            self.cur_banc = b
            self._banc_since = now
            self._zone_pos = self.my_apos(p)
        tp = self._pending_tp
        if tp:
            if now - tp[3] > 40:
                self._pending_tp = None
            elif self.cur_banc == tp[0] and self.p_act is not None and (self.p_x8 is not None) and (now - self._p_act_since >= 1.5) and (now - self._banc_since >= 1.5) and (self.my_apos(p) is not None):
                self._pending_tp = None
                if tp[1]:
                    ok = self.teleport_player(p, tp[1], tp[2])
                    log(f'PadLink: teleport etter sonebytte -> {ok}')

    def game_call(self, p, func_off, args=(), timeout=2.0, stack_top=0):
        t0 = time.perf_counter()
        try:
            return self._game_call_raw(p, func_off, args, timeout, stack_top)
        finally:
            perf_add('spillkall', time.perf_counter() - t0)

    def _game_call_raw(self, p, func_off, args=(), timeout=2.0, stack_top=0):
        if self.hR is None or self.main is None or getattr(self, 'has_call', None) is False:
            return None
        with self._call_lk:
            h = p.base + self.hR + self.CALL_OFF
            try:
                if struct.unpack('<I', p.read(h, 4))[0] not in (0, 3):
                    return None
                a = (list(args) + [0] * 8)[:8]
                p.write(h + 8, struct.pack('<9Q', self.main + func_off, *a))
                p.write(h + 88, struct.pack('<Q', stack_top))
                p.write(h, struct.pack('<I', 1))
                t0 = time.time()
                while time.time() - t0 < timeout:
                    if struct.unpack('<I', p.read(h, 4))[0] == 3:
                        res = struct.unpack('<Q', p.read(h + 80, 8))[0]
                        p.write(h + 88, bytes(8))
                        p.write(h, bytes(4))
                        self.has_call = True
                        self._call_timeouts = 0
                        return res
                    time.sleep(0.004)
                if struct.unpack('<I', p.read(h, 4))[0] == 1:
                    p.write(h, bytes(4))
                    self._call_timeouts += 1
                    if getattr(self, 'has_call', None) is None and self._call_timeouts >= 3:
                        self.has_call = False
                        log('PadLink: call_v1-patchen svarer ikke – sonebytte (TVANG) er ikke tilgjengelig')
            except OSError:
                pass
            return None

    def change_banc(self, p, banc, start_pos=None, boot_event='Dm_SY_BancChangeArrive'):
        start_pos = start_pos or self.START_POS.get(banc)
        if not banc or not start_pos or self.p_act is None or (self.delta is None):
            return False
        with self._ev_lk:
            try:
                if getattr(self, 'ev_arg', None) is None or self.h_evarg is None:
                    va = self.game_call(p, self.F_ALLOC, (16, 8192))
                    if not va:
                        return False
                    if self.game_call(p, self.F_EVARG_CTOR, (va,)) is None:
                        return False
                    h_ev = self._find_host_ptr(p, va, lambda o: struct.unpack('<Q', p.read(p.base + o + 8, 8))[0] == va + 24)
                    if h_ev is None:
                        log(f'PadLink.change_banc: fant ikke vertsminne for ev_arg 0x{va:X}')
                        return False
                    self.ev_arg, self.h_evarg = (va, h_ev)
                elif self.game_call(p, self.F_EVARG_CTOR, (self.ev_arg,)) is None:
                    return False
                arg, harg = (self.ev_arg, p.base + self.h_evarg)
                cap, arr, cnt = struct.unpack('<I4xQI', p.read(harg, 20))
                if arr != arg + 24 or cnt != 0 or cap < 5:
                    log(f'PadLink.change_banc: uventet arg-layout (kap {cap}, array 0x{arr:X}, antall {cnt})')
                    self.ev_arg = self.h_evarg = None
                    return False
                hs = p.base + self.hR + 1024
                params = (('NextBanc', 1, banc), ('NextStartPos', 1, start_pos), ('NextStartPosAir', 1, ''), ('BootEventName', 1, boot_event), ('IsCheckAir', 4, 0))
                for k, (name, typ, val) in enumerate(params):
                    e = harg + 24 + k * 200
                    nptr = struct.unpack('<Q', p.read(e + 8, 8))[0]
                    if not arg <= nptr < arg + 6944:
                        nptr = arg + 24 + k * 200 + 20
                        p.write(e + 8, struct.pack('<QI', nptr, 128))
                    p.write(harg + (nptr - arg), name.encode() + b'\x00')
                    p.write(e, struct.pack('<I', typ))
                    if typ == 1:
                        b = val.encode()[:63]
                        p.write(hs + k * 64, b + bytes(64 - len(b)))
                        p.write(e + 152, struct.pack('<Q', self.R + 1024 + k * 64))
                    else:
                        p.write(e + 152, bytes([1 if val else 0]))
                p.write(harg + 16, struct.pack('<I', len(params)))
                act_va = self.p_act + self.delta
                p.write(harg + 6488, struct.pack('<QI', act_va, struct.unpack('<I', p.read(p.base + self.p_act + 72, 4))[0]))
                nptr = struct.unpack('<Q', p.read(harg + 6800, 8))[0]
                if not arg <= nptr < arg + 6944:
                    return False
                p.write(harg + (nptr - arg), b'Dm_SY_BancChangeGo\x00')
                got = struct.unpack('<Q', p.read(p.base + self.hR + (self.main + self.GOT_EVMGR - self.R), 8))[0]
                mgr = struct.unpack('<Q', p.read(p.base + self.hR + (got - self.R), 8))[0]
                r = self.game_call(p, self.F_EV_START, (mgr, arg))
                log(f'PadLink.change_banc: {banc} @ {start_pos} -> {r}')
                return bool(r and r & 1)
            except OSError:
                return False

    def teleport_player(self, p, target_pos, target_rot=None):
        if self.p_act is None or self.p_x8 is None or (not target_pos):
            return False
        try:
            tx, ty, tz = target_pos
            rot = target_rot if target_rot and len(target_rot) == 9 else (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
            mat3x4 = struct.pack('<12f', rot[0], rot[1], rot[2], tx, rot[3], rot[4], rot[5], ty, rot[6], rot[7], rot[8], tz)
            hx8 = p.base + self.p_x8
            p.write(hx8 + 464, mat3x4)
            p.write(hx8 + 520, struct.pack('<3f', 1.0, 1.0, 1.0))
            w = struct.unpack('<I', p.read(hx8 + 552, 4))[0]
            p.write(hx8 + 552, struct.pack('<I', w | 7))
            p.write(p.base + self.p_act + 796, struct.pack('<3f', tx, ty, tz))
            log(f'PadLink: Party Teleport -> teleporterte egen Link til ({tx:.2f}, {ty:.2f}, {tz:.2f})')
            return True
        except OSError as e:
            log(f'PadLink: Party Teleport feil: {e}')
            return False

    def _worker(self, p):
        try:
            if self.hR is None:
                self.hR, self.R = self._find(p)
                if self.hR is None:
                    return
                self.main = self.R - self.R_OFF
                log(f'PadLink: R funnet guest+0x{self.hR:X} (gjest 0x{self.R:X}, main 0x{self.main:X})')
                fake = bytearray(512)
                struct.pack_into('<Q', fake, 0, self.R + 512)
                vt = bytearray(64)
                struct.pack_into('<Q', vt, 32, self.main + 6640)
                p.write(p.base + self.hR, bytes(fake))
                p.write(p.base + self.hR + 512, bytes(vt))
            h = p.base + self.hR
            t0 = time.time()
            seen = {}
            if struct.unpack('<Q', p.read(h, 8))[0] != self.R + 512 or struct.unpack('<Q', p.read(h + 544, 8))[0] != self.main + 6640:
                p.write(h, struct.pack('<Q', self.R + 512))
                p.write(h + 544, struct.pack('<Q', self.main + 6640))
                log('PadLink: skrev falsk kontroller/vtable på nytt (var ødelagt)')
            while time.time() - t0 < 0.25:
                d = p.read(h + 656, 32)
                for x in (struct.unpack_from('<Q', d, 0)[0], struct.unpack_from('<Q', d, 24)[0]):
                    if x:
                        seen[x] = seen.get(x, 0) + 1
            if any(p.read(h + 704, 32)):
                self.has_v4 = True
            p_valid = self._is_valid_x19(p, self.player) if self.player else False
            c_valid = self._is_valid_x19(p, self.copy) if self.copy else False
            if not p_valid and self.player is not None:
                log('PadLink: spiller x19 er borte (scenebytte)')
                self.player = None
                self.p_act = self.p_x8 = None
                self.scene_changed = True
            if not c_valid and self.copy is not None:
                log('PadLink: kopi x19 er borte – nullstiller kopi')
                self.copy = None
                self.c_act = self.c_x8 = self.c_x9 = None
                self._c_sv_cur = self._c_sv_max = None
                try:
                    p.write(h + 648, bytes(8))
                except OSError:
                    pass
            now_w = time.time()
            valid_seen = [x for x in seen if self._is_valid_x19(p, x) and (not (x == self._deleted_x19 and now_w < self._deleted_until))]
            self._last_valid_seen = list(valid_seen)
            if self.player is None:
                try:
                    saved = struct.unpack('<Q', p.read(h + 696, 8))[0]
                except OSError:
                    saved = 0
                if saved and saved in valid_seen:
                    self.player = saved
                    prev_copy = struct.unpack('<Q', p.read(h + 648, 8))[0]
                    if prev_copy in valid_seen and prev_copy != saved:
                        self.copy = prev_copy
                        self.active = True
                        self._hidden = True
                        p.write(h + 640, struct.pack('<I', self.MAGIC))
                    log(f'PadLink: spiller fra R+0x2B8: 0x{self.player:X}' + (f', kopi 0x{self.copy:X}' if self.copy else ''))
            if self.player is None:
                if len(valid_seen) == 1:
                    self.player = valid_seen[0]
                    log(f'PadLink: spiller x19 funnet: 0x{self.player:X}')
                elif len(valid_seen) >= 2:
                    prev_copy = struct.unpack('<Q', p.read(h + 648, 8))[0]
                    if prev_copy in valid_seen:
                        self.copy = prev_copy
                        self.player = next((x for x in valid_seen if x != prev_copy))
                        self.active = True
                        p.write(h + 640, struct.pack('<I', self.MAGIC))
                        log(f'PadLink: gjenopptok fra R – spiller 0x{self.player:X}, kopi 0x{self.copy:X}')
                        self._hidden = True
                    else:
                        p_cand = None
                        if self._cam_off is not None:
                            best_dot = -1.0
                            for cand in valid_seen:
                                try:
                                    a = self._u64(p, cand + 24)
                                    apos = p.f32(a - self.delta + 796, 3)
                                    q = cam_quality(p, self._cam_off, apos)
                                    if q and q[1] > best_dot:
                                        best_dot = q[1]
                                        p_cand = cand
                                except Exception:
                                    pass
                        if p_cand:
                            self.player = p_cand
                            cands = [x for x in valid_seen if x != p_cand]
                            self.copy = cands[0] if cands else None
                            log(f'PadLink: identifisert via kamera – spiller 0x{self.player:X}, kopi 0x{self.copy:X}')
                            if self.copy:
                                p.write(h + 648, struct.pack('<Q', self.copy))
                                p.write(h + 640, struct.pack('<I', self.MAGIC))
                                self.active = True
                        else:
                            self.player = valid_seen[0]
                            log(f'PadLink: valgte spiller 0x{self.player:X}')
            if self.player is not None:
                try:
                    if struct.unpack('<Q', p.read(h + 696, 8))[0] != self.player:
                        p.write(h + 696, struct.pack('<Q', self.player))
                except OSError:
                    pass
            if self.player is not None and self.copy is None and (time.time() - self._banc_since > 4.0):
                new = [x for x in valid_seen if x != self.player and x not in getattr(self, '_zone_dead', ())]
                if new:
                    self.copy = new[0]
                    log(f'PadLink: kopi (spiller 2) x19 registrert: 0x{self.copy:X}')
                    p.write(h + 648, struct.pack('<Q', self.copy))
                    p.write(h + 640, struct.pack('<I', self.MAGIC))
                    self.active = True
            if self.copy and self.player and (self.copy == self.player):
                log('PadLink SIKKERHET: copy var lik player! Nullstiller kopi.')
                self.copy = None
                p.write(h + 648, bytes(8))
            self._resolve_actors(p)
            cur_pos = self.my_apos(p)
            if cur_pos:
                self.last_pos = cur_pos
        except OSError as e:
            if self.hR is not None:
                log(f'PadLink: lesefeil ({e}) – søker R på nytt')
            self.hR = None
        except Exception as e:
            log(f'PadLink FEIL: {e!r}')
        finally:
            if self.hR is None and (not getattr(self, '_nf_logged', False)):
                self._nf_logged = True
                log('PadLink: R ikke funnet ennå (er inp_v4 lastet?)')
            self.busy = False
    SPAWN_OFF = 2048

    def _spawn_link2(self, p):
        if self.copy is not None or getattr(self, '_spawning', False):
            return
        pos = self.my_apos(p)
        if not pos or self.hR is None or self.main is None:
            return
        self._spawning = True
        try:
            if getattr(self, 'spawn_stack', None) is None:
                va = self.game_call(p, self.F_ALLOC, (16, 65536))
                if not va:
                    raise RuntimeError('fikk ikke stack fra spillet')
                self.spawn_stack = va + 65536 - 256 & ~15
            sx, sy, sz = (pos[0] + 1.5, pos[1] + 3.0, pos[2])
            R = self.R
            h = p.base + self.hR + self.SPAWN_OFF
            base = R + self.SPAWN_OFF
            arg = bytearray(256)
            struct.pack_into('<3f', arg, 0, sx, sy, sz)
            struct.pack_into('<9f', arg, 12, 1, 0, 0, 0, 1, 0, 0, 0, 1)
            struct.pack_into('<3f', arg, 48, 1, 1, 1)
            arg[152] = 3
            struct.pack_into('<Q', arg, 64, base + 48)
            p.write(h, b'Player_Child\x00'.ljust(64, b'\x00') + struct.pack('<Q', base) + bytes(184) + bytes(arg))
            got = struct.unpack('<Q', p.read(p.base + self.hR + (self.main + self.GOT_ACTMGR - R), 8))[0]
            mgr = struct.unpack('<Q', p.read(p.base + self.hR + (got - R), 8))[0]
            log(f'PadLink: lager Link 2 via CALL v1 ved ({sx:.1f}, {sy:.1f}, {sz:.1f}) …')
            res = self.game_call(p, self.F_ACT_CREATE, (mgr, base + 64, base + 256, 0, 1, 0, 0, 0), timeout=3.0, stack_top=self.spawn_stack)
            log(f'PadLink: requestCreateActor -> {res}')
            if res is not None:
                self._last_spawn_try = time.time() + 12.0
                return
            log('PadLink: CALL v1-spawn svarte ikke – prøver GDB')
        except Exception as e:
            log(f'PadLink: CALL v1-spawn feilet ({e!r}) – prøver GDB')
        finally:
            self._spawning = False
        self._spawn_link2_gdb(p)

    def _spawn_link2_gdb(self, p):
        global gcall, Gdb
        if self.copy is not None:
            return
        if getattr(self, '_spawning', False):
            return
        self._spawning = True
        try:
            if gcall is None or Gdb is None:
                for _p in (os.path.dirname(os.path.abspath(__file__)),):
                    if _p not in sys.path:
                        sys.path.insert(0, _p)
                import gcall
                from gdbc import Gdb
            player_pos = self.my_apos(p)
            if not player_pos:
                return
            sx, sy, sz = (round(player_pos[0] + 1.5, 2), round(player_pos[1] + 3.0, 2), round(player_pos[2], 2))
            log(f'PadLink: Auto-respawn av Link 2 ved ({sx}, {sy}, {sz}) ...')

            def setup_spawn(g, main_va, base):
                mgr = g.u64(g.u64(main_va + 80212960))
                g.write(base + 1024, b'Player_Child\x00')
                g.write(base + 896, struct.pack('<Q', base + 1024))
                arg = bytearray(256)
                struct.pack_into('<3f', arg, 0, sx, sy, sz)
                struct.pack_into('<9f', arg, 12, 1, 0, 0, 0, 1, 0, 0, 0, 1)
                struct.pack_into('<3f', arg, 48, 1, 1, 1)
                arg[152] = 3
                g.write(base + 512, bytes(arg))
                return ([mgr, base + 896, base + 512, 0, 1, 0, 0, 0], None)
            res, g2, _, _, _ = gcall.call(3615344, setup_spawn, stop_off=7618624, stop_timeout=15)
            gcall.finish(g2)
            log(f'PadLink: Auto-respawn fullført (x0={res})')
        except Exception as e:
            log(f'PadLink: Auto-respawn feilet ({e})')
        finally:
            self._spawning = False

    def _delete_link2(self, p):
        if self.c_act is None or self.delta is None or getattr(self, '_deleting', False):
            return
        self._deleting = True
        self._last_del_try = time.time()
        c_act_va = self.c_act + self.delta

        def run():
            try:
                res = self.game_call(p, self.F_ACT_DELETE, (c_act_va, 0), timeout=2.0)
                if res is not None:
                    self._deleted_x19 = self.copy
                    self._deleted_until = time.time() + 2.5
                    self.copy = None
                    self.c_act = self.c_x8 = self.c_x9 = None
                    self._c_sv_cur = self._c_sv_max = None
                    self._hidden = False
                    if self.hR is not None:
                        try:
                            p.write(p.base + self.hR + 648, bytes(8))
                        except OSError:
                            pass
                    log(f'PadLink: Link 2 slettet (despawnet, x0={res})')
            except OSError:
                pass
            finally:
                self._deleting = False
        threading.Thread(target=run, daemon=True).start()

    def tick(self, p, now, alive=False):
        if p is None or self.busy:
            return
        if now - self.last_try >= 2:
            self.last_try = now
            self.busy = True
            threading.Thread(target=self._worker, args=(p,), daemon=True).start()
        self.banc_tick(p, now)
        want_hidden = getattr(self, 'friend_look', 'No tunic') != 'Normal'
        my_hidden = getattr(self, 'my_look', 'No tunic') != 'Normal'
        if self.p_act is not None and getattr(self, '_mycap_done', None) != (self.player, my_hidden) and (now - getattr(self, '_mycap_try', 0) > 2.0):
            self._mycap_try = now

            def _mycap(pl=self.player, a=self.p_act, hide=my_hidden):
                if self.set_material_visible(p, a, 'Mt_Cloth', not hide) is not None:
                    self._mycap_done = (pl, hide)
                    log(f"PadLink: egen Link utseende: {('uten trøye' if hide else 'vanlig')}")
            threading.Thread(target=_mycap, daemon=True).start()
        if self.copy is not None and self.c_act is not None and (getattr(self, '_cap_done', None) != (self.copy, want_hidden)) and (now - getattr(self, '_cap_try', 0) > 2.0):
            self._cap_try = now

            def _cap(c=self.copy, a=self.c_act, hide=want_hidden):
                r = None
                for mat in ('Mt_Cloth',):
                    r = self.set_material_visible(p, a, mat, not hide) if r is not False else r
                if r is not None:
                    self._cap_done = (c, hide)
                    log(f"PadLink: Link 2 utseende: {('uten trøye' if hide else 'vanlig')}")
            threading.Thread(target=_cap, daemon=True).start()
        if alive and self._zone_ready(p, now):
            threading.Thread(target=self._cleanup_orphans, args=(p, now), daemon=True).start() if now - getattr(self, '_orph_t', 0) >= 5.0 else None
        if not alive:
            self._last_spawn_try = 0
        cross = not self.friend_banc or not self.cur_banc or self.friend_banc != self.cur_banc
        if alive and (not cross) and (self._pending_tp is None) and (self.copy is None) and (self.p_act is not None) and (not getattr(self, '_spawning', False)) and self._zone_ready(p, now) and (not (getattr(self, 'warp_banc', None) and self.warp_banc != self.cur_banc and (now - self.warp_t < 25))):
            if now - getattr(self, '_last_spawn_try', 0) > 3.0:
                self._last_spawn_try = now
                threading.Thread(target=self._spawn_link2, args=(p,), daemon=True).start()

    def my_pad(self, p):
        if self.hR is None:
            return None
        try:
            return p.read(p.base + self.hR + 704, 32).hex()
        except OSError:
            return None

    def _zone_ready(self, p, now):
        age = now - self._banc_since
        if age < 4.0:
            return False
        zp = getattr(self, '_zone_pos', None)
        if not zp or age > 15.0:
            return True
        cp = self.my_apos(p)
        return bool(cp) and math.dist(cp, zp) > 3.0

    def _cleanup_orphans(self, p, now):
        if now - getattr(self, '_orph_t', 0) < 5.0 or self.player is None or self.copy is None or (self.delta is None):
            return
        self._orph_t = now
        done = self.__dict__.setdefault('_orph_done', set())
        for x in getattr(self, '_last_valid_seen', []):
            if x in (self.player, self.copy) or x in done:
                continue
            done.add(x)
            try:
                a = self._u64(p, x + 24)
                r = self.game_call(p, self.F_ACT_DELETE, (a, 0), timeout=2.0)
                log(f'PadLink: slettet ekstra Link 2 0x{x:X} -> {r}')
            except Exception as e:
                log(f'PadLink: kunne ikke slette ekstra Link 2 0x{x:X}: {e!r}')

    def my_apos(self, p):
        if self.p_act is None:
            return None
        try:
            pos = p.f32(self.p_act + 796, 3)
            if all((math.isfinite(v) and abs(v) < 100000.0 for v in pos)) and abs(pos[0]) + abs(pos[1]) + abs(pos[2]) > 1.0:
                return list(pos)
        except OSError:
            pass
        return None

    def my_arot(self, p):
        if self.p_act is None:
            return None
        try:
            rot = p.f32(self.p_act + 808, 9)
            if all((math.isfinite(v) and abs(v) <= 1.05 for v in rot)):
                return list(rot)
        except OSError:
            pass
        return None

    def my_cam_angle(self, p, cam_off=None):
        if cam_off is not None:
            try:
                _r, _u, f, _eye = read_cam(p, cam_off)
                if math.hypot(f[0], f[2]) > 0.0001 and all((math.isfinite(v) for v in f)):
                    return float(math.atan2(f[0], f[2]))
            except OSError:
                pass
        h = self.p_x19_h if self.p_x19_h is not None else self.c_x19_h
        if h is None:
            return None
        try:
            s9, s13 = p.f32(h + 29932, 2)
            if math.isfinite(s9) and math.isfinite(s13):
                return float(s13 - s9)
        except OSError:
            pass
        return None

    def apply(self, p, pad_hex, friend_cam_a=None, my_cam_a=None, target_pos=None, target_rot=None, alive=True, cam_off=None, hearts=None):
        with self._lk:
            self._p = p
            self._cam_off = cam_off
            self._alive = bool(alive)
            self._pad_hex = pad_hex if alive else None
            self._f_cam_a = friend_cam_a if alive else None
            self._my_cam_a = my_cam_a
            if alive and target_pos:
                self._t_pos = list(target_pos)
            elif not alive:
                self._t_pos = None
            if alive and target_rot:
                self._t_rot = list(target_rot)
            elif not alive:
                self._t_rot = None
            self._f_hearts = hearts if alive and hearts else None
        if self._thr is None:
            self._thr = threading.Thread(target=self._loop, daemon=True)
            self._thr.start()

    def _set_visible(self, p, visible, snap_pos=None, snap_rot=None):
        if self.c_act is None or self.c_x8 is None:
            return
        try:
            s = 1.0 if visible else 0.0
            s_bytes = struct.pack('<3f', s, s, s)
            p.write(p.base + self.c_act + 844, s_bytes)
            hx8 = p.base + self.c_x8
            p.write(hx8 + 520, s_bytes)
            cx, cy, cz = snap_pos if visible and snap_pos else p.f32(self.c_act + 796, 3)
            if visible:
                rot = snap_rot if snap_rot and len(snap_rot) == 9 else (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
            else:
                rot = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            mat3x4 = struct.pack('<12f', rot[0], rot[1], rot[2], cx, rot[3], rot[4], rot[5], cy, rot[6], rot[7], rot[8], cz)
            p.write(hx8 + 464, mat3x4)
            w = struct.unpack('<I', p.read(hx8 + 552, 4))[0]
            p.write(hx8 + 552, struct.pack('<I', w | 7))
            if self.c_x9 is not None:
                hx9 = p.base + self.c_x9
                b = p.read(hx9 + 498, 1)[0]
                p.write(hx9 + 498, bytes([b | 128]))
            if self._hidden != (not visible):
                self._hidden = not visible
                log(f"PadLink: Link 2 {('skjult (frakoblet)' if not visible else 'synlig igjen (tilkoblet)')}")
        except OSError:
            pass

    def _player_sv(self, p):
        if self.player is None or self.delta is None:
            return (None, None)
        if getattr(self, '_p_sv_for', None) != self.player:
            res = []
            for rel in (7184, 7192):
                sv = self._u64(p, self.player - 8000 + rel)
                res.append(sv - self.delta + 8 if sv and self._u64(p, sv) == self.main + self.SAVEVAR_VT else None)
            self._p_sv, self._p_sv_for = (tuple(res), self.player)
        return self._p_sv

    def _sync_copy_health(self, p, cur_q, max_q):
        now = time.time()
        if now - self._last_hp_sync < 0.2:
            return
        self._last_hp_sync = now
        try:
            lw = getattr(self, '_c_hp_written', None)
            if self._c_sv_cur is not None and lw is not None:
                now_c = struct.unpack('<I', p.read(p.base + self._c_sv_cur, 4))[0]
                if lw < now_c <= lw + 80:
                    pc, pm = self._player_sv(p)
                    if pc is not None:
                        hp = struct.unpack('<I', p.read(p.base + pc, 4))[0]
                        mx = struct.unpack('<I', p.read(p.base + pm, 4))[0] if pm is not None else 80
                        new = min(hp + (now_c - lw), mx)
                        if new != hp:
                            p.write(p.base + pc, struct.pack('<I', new))
                            log(f'PadLink: hjerte plukket av Link 2 – ga +{new - hp} til egen Link ({hp} -> {new})')
            self._c_hp_written = cur_q
            for off, target in ((self._c_sv_cur, cur_q), (self._c_sv_max, max_q)):
                if off is not None and target is not None:
                    cur = struct.unpack('<I', p.read(p.base + off, 4))[0]
                    if cur != target:
                        p.write(p.base + off, struct.pack('<I', target))
        except OSError:
            pass

    def _loop(self):
        while True:
            time.sleep(1 / 120)
            try:
                with self._lk:
                    p = self._p
                    cam_off = self._cam_off
                    alive = self._alive
                    pad_hex = self._pad_hex
                    friend_cam_a = self._f_cam_a
                    my_cam_a = self._my_cam_a
                    target_pos = self._t_pos
                    target_rot = self._t_rot
                    f_hearts = self._f_hearts
                if p is None or self.hR is None or (not self.active):
                    continue
                if not alive:
                    h = p.base + self.hR
                    p.write(h + 8, bytes(8))
                    p.write(h + 276, bytes(4))
                    p.write(h + 288, bytes(16))
                    if struct.unpack('<I', p.read(h + 640, 4))[0] != self.MAGIC:
                        p.write(h + 640, struct.pack('<I', self.MAGIC))
                    self._set_visible(p, False)
                    self._sync_copy_health(p, 12, 16)
                    continue
                if self._hidden:
                    self._set_visible(p, True, target_pos, target_rot)
                live_cam_a = self.my_cam_angle(p, cam_off)
                if live_cam_a is not None:
                    my_cam_a = live_cam_a
                raw = bytes.fromhex(pad_hex) if pad_hex else bytes(32)
                if len(raw) != 32:
                    raw = bytes(32)
                d = bytearray(raw)
                sx, sy = struct.unpack_from('<2f', d, 16)
                smag = math.hypot(sx, sy) if math.isfinite(sx) and math.isfinite(sy) else 0.0
                if smag > 0.001 and friend_cam_a is not None and (my_cam_a is not None):
                    s9_m = math.atan2(-sx, sy) + (friend_cam_a - my_cam_a)
                    sx_n = -smag * math.sin(s9_m)
                    sy_n = smag * math.cos(s9_m)
                    struct.pack_into('<2f', d, 16, sx_n, sy_n)
                struct.pack_into('<2f', d, 24, 0.0, 0.0)
                h = p.base + self.hR
                p.write(h + 8, bytes(d[0:8]))
                p.write(h + 276, bytes(d[8:12]))
                p.write(h + 288, bytes(d[16:32]))
                if struct.unpack('<I', p.read(h + 640, 4))[0] != self.MAGIC:
                    p.write(h + 640, struct.pack('<I', self.MAGIC))
                if target_pos and self.c_act is not None and (self.c_x8 is not None) and (self.friend_banc == self.cur_banc) and self.cur_banc:
                    self._sync_pos(p, target_pos, target_rot, smag > 0.08)
                if self.c_act is not None and f_hearts:
                    h_cur = max(1, min(80, int(f_hearts[0])))
                    h_max = max(4, min(80, int(f_hearts[1])))
                    self._sync_copy_health(p, h_cur, h_max)
            except OSError:
                pass
            except Exception as e:
                log(f'PadLink-løkke feil: {e!r}')
                time.sleep(0.5)

    def _sync_pos(self, p, target_pos, target_rot, moving):
        try:
            tx, ty, tz = target_pos
            if not all((math.isfinite(v) and abs(v) < 100000.0 for v in (tx, ty, tz))):
                return
            cx, cy, cz = p.f32(self.c_act + 796, 3)
            dx, dy, dz = (tx - cx, ty - cy, tz - cz)
            dist_xz = math.hypot(dx, dz)
            thr_xz = 1.25 if moving else 0.04
            if dist_xz < thr_xz and abs(dy) < (0.8 if moving else 0.25):
                return
            if dist_xz > 3.0 or abs(dy) > 2.5:
                nx, ny, nz = (tx, ty, tz)
            elif not moving:
                nx, ny, nz = (cx + dx * 0.35, cy + dy * 0.35, cz + dz * 0.35)
            else:
                nx, ny, nz = (cx + dx * 0.2, cy + dy * 0.2, cz + dz * 0.2)
            rot = target_rot if target_rot and len(target_rot) == 9 else p.f32(self.c_act + 808, 9)
            mat3x4 = struct.pack('<12f', rot[0], rot[1], rot[2], nx, rot[3], rot[4], rot[5], ny, rot[6], rot[7], rot[8], nz)
            hx8 = p.base + self.c_x8
            p.write(hx8 + 464, mat3x4)
            p.write(hx8 + 520, struct.pack('<3f', 1.0, 1.0, 1.0))
            w = struct.unpack('<I', p.read(hx8 + 552, 4))[0]
            p.write(hx8 + 552, struct.pack('<I', w | 3))
            if self.c_x9 is not None:
                hx9 = p.base + self.c_x9
                b = p.read(hx9 + 498, 1)[0]
                p.write(hx9 + 498, bytes([b | 128]))
        except OSError:
            pass

class App:

    def __init__(self, root):
        self.root = root
        self.cfg = load_cfg()
        self.session = random.getrandbits(32)
        self.name = self.cfg.get('name') or os.getenv('USERNAME', 'spiller')
        self.hat_color = self.cfg.get('hat_color') if self.cfg.get('hat_color') in HAT_COLORS else 'Grønn'
        self.p = None
        self.pos_off = None
        self.rupees = None
        self.syncs = {}
        self.cam_off = None
        self.cam_scanning = False
        self.cam_last_scan = 0
        self.overlay = None
        self.lwin = None
        self.lwin_t = 0
        self.ready = False
        self.peer = None
        self.friend = None
        self.friend_t = 0
        self.friend_session = None
        self.figure = FigureSync()
        self.pad = PadLink()
        self.pad.START_POS = dict(PadLink.START_POS, **self.cfg.get('start_pos') or {})
        self.players = {}
        self.ui_q = queue.Queue()
        self.answer = queue.Queue()
        self.chat_seq = 0
        self.chat_out = []
        self.chat_seen = set()
        self._fast_net_lk = threading.Lock()
        root.title(f'OoT Co-oP {VERSION}')
        root.configure(bg=BG)
        root.geometry('680x980')
        root.resizable(False, False)
        root.attributes('-topmost', True)
        st = ttk.Style()
        st.theme_use('clam')
        st.configure('.', background=BG, foreground=FG, fieldbackground=FIELD, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        st.configure('TButton', padding=(10, 4), background=BTN, foreground=GOLD, bordercolor=GOLD_D, focuscolor=GOLD_D, font=('Segoe UI', 9, 'bold'))
        st.map('TButton', background=[('active', BTN_H)], foreground=[('active', '#fff3c4')])
        st.configure('TEntry', fieldbackground=FIELD, foreground=FG, insertcolor=GOLD, bordercolor=BORDER)
        st.configure('TCombobox', fieldbackground=FIELD, background=BTN, foreground=FG, arrowcolor=GOLD, bordercolor=BORDER)
        st.map('TCombobox', fieldbackground=[('readonly', FIELD)], foreground=[('readonly', FG)])
        st.configure('Horizontal.TProgressbar', troughcolor=FIELD, background=GOLD_D)

        def card(parent, title, **pack):
            outer = tk.Frame(parent, bg=GOLD_D, padx=1, pady=1)
            outer.pack(fill='x', padx=14, pady=(4, 4), **pack)
            inner = tk.Frame(outer, bg=PANEL)
            inner.pack(fill='both', expand=True)
            if title:
                tk.Label(inner, text=f'◆ {title}', bg=PANEL, fg=GOLD, font=(ZELDA_FONT, 17, 'bold')).pack(anchor='w', padx=10, pady=(6, 0))
            return (outer, inner)
        hdr = tk.Frame(root, bg=BG)
        hdr.pack(fill='x', padx=14, pady=(10, 2))
        tk.Label(hdr, text='▲  OoT Co-oP  ▲', bg=BG, fg=GOLD, font=(ZELDA_FONT, 32, 'bold')).pack(anchor='center')
        tk.Label(hdr, text=f'Ocarina of Time  ·  two-player co-op  ·  v{VERSION}  ·  Made by NorPat', bg=BG, fg=GOLD_D, font=(ZELDA_FONT, 14, 'italic')).pack(anchor='center', pady=(0, 4))
        tk.Frame(root, bg=GOLD_D, height=1).pack(fill='x', padx=40, pady=(0, 6))
        _o, pc = card(root, 'Player')
        top = tk.Frame(pc, bg=PANEL)
        top.pack(fill='x', padx=10, pady=(4, 8))
        tk.Label(top, text='Name', bg=PANEL, fg=MUTED, font=('Segoe UI', 9)).pack(side='left')
        self.name_var = tk.StringVar(value=self.name)
        e = ttk.Entry(top, textvariable=self.name_var, width=14)
        e.pack(side='left', padx=(6, 16))
        e.bind('<FocusOut>', lambda _e: self._save_name())
        e.bind('<Return>', lambda _e: self._save_name())
        self.color_dot = tk.Label(top, text='●', fg=HAT_COLORS[self.hat_color], bg=PANEL, font=('Segoe UI', 13))
        self.color_var = tk.StringVar(value=self.hat_color)
        tk.Label(top, text='Link', bg=PANEL, fg=MUTED, font=('Segoe UI', 9)).pack(side='left')
        self.look = {'Vanlig': 'Normal', 'Uten trøye': 'No tunic'}.get(self.cfg.get('look'), self.cfg.get('look'))
        self.look = self.look if self.look in LOOKS else 'No tunic'
        self.look_var = tk.StringVar(value=self.look)
        self.pad.my_look = self.look
        cb_look = ttk.Combobox(top, textvariable=self.look_var, values=LOOKS, width=11, state='readonly')
        cb_look.pack(side='left', padx=(6, 0))
        cb_look.bind('<<ComboboxSelected>>', lambda _e: self._save_look())
        self.tvang_var = tk.BooleanVar(value=bool(self.cfg.get('tvang', False)))
        self.tvang = bool(self.cfg.get('tvang', False))
        tk.Checkbutton(top, text='FOLLOW – spawn with friend on zone exit', variable=self.tvang_var, command=self._tvang_toggled, bg=PANEL, fg=FG, selectcolor=FIELD, activebackground=PANEL, activeforeground=GOLD, font=('Segoe UI', 9)).pack(side='right')
        row = tk.Frame(root, bg=BG)
        row.pack(fill='x', padx=14, pady=(4, 4))
        self._status_row = row
        left = tk.Frame(row, bg=GOLD_D, padx=1, pady=1)
        left.pack(side='left', fill='both', expand=True, padx=(0, 4))
        right = tk.Frame(row, bg=GOLD_D, padx=1, pady=1)
        right.pack(side='left', fill='both', expand=True, padx=(4, 0))
        gl = tk.Frame(left, bg=PANEL)
        gl.pack(fill='both', expand=True)
        nr = tk.Frame(right, bg=PANEL)
        nr.pack(fill='both', expand=True)
        tk.Label(gl, text='◆ Game', bg=PANEL, fg=GOLD, font=(ZELDA_FONT, 17, 'bold')).pack(anchor='w', padx=10, pady=(6, 2))
        tk.Label(nr, text='◆ Network', bg=PANEL, fg=GOLD, font=(ZELDA_FONT, 17, 'bold')).pack(anchor='w', padx=10, pady=(6, 2))
        self.checks = {}
        g_in = tk.Frame(gl, bg=PANEL)
        g_in.pack(fill='x', padx=10, pady=(0, 8))
        for idx, (key, text) in enumerate((('lichii', 'Lichii'), ('game', 'Memory'), ('pos', 'Link 3D'), ('rupees', 'Save'), ('cam', 'Camera'), ('clock', 'Clock'), ('net', 'Friend'))):
            cell = tk.Frame(g_in, bg=PANEL)
            cell.grid(row=idx, column=0, sticky='ew', pady=1)
            g_in.columnconfigure(0, weight=1)
            dot = tk.Label(cell, text='●', fg=MUTED, bg=PANEL, font=('Segoe UI', 10))
            dot.pack(side='left')
            tk.Label(cell, text=text, fg=FG, bg=PANEL, font=('Segoe UI', 9, 'bold'), width=8, anchor='w').pack(side='left', padx=4)
            info = tk.Label(cell, text='', fg=MUTED, bg=PANEL, font=('Segoe UI', 8), anchor='e')
            info.pack(side='right')
            self.checks[key] = (dot, info)
        self.net_lbls = {}
        n_in = tk.Frame(nr, bg=PANEL)
        n_in.pack(fill='x', padx=10, pady=(0, 8))
        for idx, (key, text) in enumerate((('state', 'Status'), ('peer', 'Friend'), ('ping', 'Ping'), ('rate', 'Packets'), ('port', 'Port'), ('ip', 'My IP'), ('zone', 'Zone'))):
            cell = tk.Frame(n_in, bg=PANEL)
            cell.grid(row=idx, column=0, sticky='ew', pady=1)
            n_in.columnconfigure(0, weight=1)
            tk.Label(cell, text=text, fg=FG, bg=PANEL, font=('Segoe UI', 9, 'bold'), width=8, anchor='w').pack(side='left')
            v = tk.Label(cell, text='–', fg=MUTED, bg=PANEL, font=('Consolas', 9), anchor='e')
            v.pack(side='right')
            self.net_lbls[key] = v
        self.net_lbls['port'].config(text=f'UDP {GAME_PORT} / {DISC_PORT}', fg=FG)
        self.net_lbls['ip'].config(text=', '.join(local_ips()) or '?', fg=FG)
        self.guide = tk.Frame(root, bg=FIELD)
        self.guide_lbl = tk.Label(self.guide, text='', bg=FIELD, fg=FG, wraplength=560, justify='left', font=('Segoe UI', 10))
        self.guide_lbl.pack(fill='x', padx=10, pady=(8, 4))
        self.guide_entry = ttk.Entry(self.guide, width=10)
        self.guide_btn = ttk.Button(self.guide, text='OK', command=self._guide_ok)
        self.prog = ttk.Progressbar(root, mode='determinate', maximum=1.0)
        _o, cc = card(root, 'Connected')
        pl = tk.Frame(cc, bg=PANEL)
        pl.pack(fill='x', padx=10, pady=(4, 8))
        self.plist = tk.Listbox(pl, height=2, bg=FIELD, fg=FG, selectbackground=SEL, selectforeground='#ffffff', highlightthickness=1, highlightbackground=BORDER, borderwidth=0, font=('Segoe UI', 9))
        self.plist.pack(fill='x', pady=(0, 4))
        self.plist.bind('<Double-Button-1>', lambda _e: self._connect_selected())
        btns = tk.Frame(pl, bg=PANEL)
        btns.pack(fill='x')
        ttk.Button(btns, text='Connect selected', command=self._connect_selected).pack(side='left')
        self.ip_var = tk.StringVar(value=self.cfg.get('last_ip', ''))
        ttk.Button(btns, text='Connect to IP', command=self._connect_ip).pack(side='right')
        ttk.Entry(btns, textvariable=self.ip_var, width=16).pack(side='right', padx=6)
        _o, ic = card(root, 'Life and inventory')
        inv_top = tk.Frame(ic, bg=PANEL)
        inv_top.pack(fill='x', padx=10, pady=(4, 2))
        self.hearts_lbl = tk.Label(inv_top, text='♥ You: waiting …', bg=PANEL, fg=HEART, font=('Segoe UI', 12, 'bold'), anchor='w')
        self.hearts_lbl.pack(side='left')
        self.friend_hearts_lbl = tk.Label(inv_top, text='♥ Friend: offline', bg=PANEL, fg=MUTED, font=('Segoe UI', 12, 'bold'), anchor='e')
        self.friend_hearts_lbl.pack(side='right')
        self.peer_color_lbl = tk.Label(ic, text='', bg=PANEL, fg=GOLD, font=('Segoe UI', 8, 'italic'), anchor='w')
        self.peer_color_lbl.pack(fill='x', padx=10, pady=(0, 2))
        self.status = tk.Label(ic, text='Starting up and connecting to Lichii …', bg=PANEL, fg=FG, justify='left', anchor='w', font=('Segoe UI', 9))
        self.status.pack(fill='x', padx=10, pady=(0, 8))
        chat_outer = tk.Frame(root, bg=GOLD_D, padx=1, pady=1)
        chat_outer.pack(fill='both', expand=True, padx=14, pady=(4, 12))
        chat_frame = tk.Frame(chat_outer, bg=PANEL)
        chat_frame.pack(fill='both', expand=True)
        chat_hdr = tk.Frame(chat_frame, bg=PANEL)
        chat_hdr.pack(fill='x', padx=10, pady=(6, 0))
        tk.Label(chat_hdr, text='◆ Chat', bg=PANEL, fg=GOLD, font=(ZELDA_FONT, 17, 'bold')).pack(side='left')
        tk.Label(chat_hdr, text='Enter = send', bg=PANEL, fg=MUTED, font=('Segoe UI', 8)).pack(side='right')
        chat_in_row = tk.Frame(chat_frame, bg=PANEL)
        chat_in_row.pack(side='bottom', fill='x', padx=10, pady=(4, 8))
        self.chat_var = tk.StringVar()
        self.chat_entry = ttk.Entry(chat_in_row, textvariable=self.chat_var)
        self.chat_entry.pack(side='left', fill='x', expand=True, padx=(0, 6))
        self.chat_entry.bind('<Return>', lambda _e: self._send_chat())
        ttk.Button(chat_in_row, text='Send', command=self._send_chat).pack(side='right')
        self.chat_log = tk.Text(chat_frame, height=12, bg=FIELD, fg=FG, insertbackground=FG, relief='flat', highlightthickness=1, highlightbackground=BORDER, font=('Segoe UI', 9), wrap='word', state='disabled', padx=8, pady=6)
        self.chat_log.pack(side='top', fill='both', expand=True, padx=10, pady=(4, 0))
        self.chat_log.tag_configure('sys', foreground=MUTED, font=('Segoe UI', 8, 'italic'))
        for cname, chex in HAT_COLORS.items():
            self.chat_log.tag_configure(f'col_{cname}', foreground=chex, font=('Segoe UI', 9, 'bold'))
        self._append_chat_sys(f'Welcome to OoT Co-oP {VERSION}! Type a message below to chat with your friend.')
        self.marker_var = tk.BooleanVar(value=bool(self.cfg.get('marker', False)))
        self.ghost_var = tk.BooleanVar(value=bool(self.cfg.get('ghost', False)))
        self.folg_var = tk.BooleanVar(value=bool(self.cfg.get('figur_folg_meg', False)))
        log(f"start, IP {', '.join(local_ips())}, config {CFG}")
        self._net_setup()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._fast_net_loop, daemon=True).start()
        root.after(50, self._tick)

    def ui(self, fn, *a):
        self.ui_q.put((fn, a))

    def set_check(self, key, state, info=''):

        def f():
            dot, lbl = self.checks[key]
            dot.config(fg={'ok': OK, 'work': WARN, 'off': MUTED, 'err': '#e05050'}[state])
            lbl.config(text=info)
        self.ui(f)

    def show_guide(self, text, entry=False, button='OK'):

        def f():
            self.guide.pack(fill='x', padx=12, pady=6, after=self._status_row)
            self.guide_lbl.config(text=text)
            self.guide_entry.pack_forget()
            self.guide_btn.pack_forget()
            if entry:
                self.guide_entry.delete(0, 'end')
                self.guide_entry.pack(side='left', padx=10, pady=(0, 8))
                self.guide_entry.focus_set()
            self.guide_btn.config(text=button)
            self.guide_btn.pack(side='left', padx=10, pady=(0, 8))
        while not self.answer.empty():
            self.answer.get_nowait()
        self.ui(f)
        return self.answer.get()

    def hide_guide(self):
        self.ui(self.guide.pack_forget)

    def _guide_ok(self):
        self.answer.put(self.guide_entry.get().strip())

    def progress(self, frac):

        def f():
            if not self.prog.winfo_ismapped():
                self.prog.pack(fill='x', padx=12, after=self._status_row)
            self.prog['value'] = frac
            if frac >= 1:
                self.prog.pack_forget()
        self.ui(f)

    def _ghost_toggled(self):
        self.cfg['ghost'] = bool(self.ghost_var.get())
        save_cfg(self.cfg)
        log(f"skyggefigur {('på' if self.cfg['ghost'] else 'av')}")

    def _tvang_toggled(self):
        self.tvang = bool(self.tvang_var.get())
        self.cfg['tvang'] = self.tvang
        save_cfg(self.cfg)
        log(f"TVANG {('på' if self.cfg['tvang'] else 'av')}")

    def _folg_toggled(self):
        self.cfg['figur_folg_meg'] = bool(self.folg_var.get())
        save_cfg(self.cfg)
        log(f"figur følger meg (test) {('på' if self.cfg['figur_folg_meg'] else 'av')}")

    def _marker_toggled(self):
        self.cfg['marker'] = bool(self.marker_var.get())
        save_cfg(self.cfg)
        log(f"markør {('på' if self.cfg['marker'] else 'av')}")
        if self.overlay and (not self.cfg['marker']):
            self.overlay.clear()

    def _save_name(self):
        self.name = self.name_var.get().strip() or self.name
        self.cfg['name'] = self.name
        save_cfg(self.cfg)

    def _save_look(self):
        lk = self.look_var.get()
        if lk in LOOKS:
            self.look = lk
            self.cfg['look'] = lk
            save_cfg(self.cfg)
            self.pad.my_look = lk
            log(f'utseende valgt: {lk}')

    def _save_color(self):
        c = self.color_var.get()
        if c in HAT_COLORS:
            self.hat_color = c
            self.color_dot.config(fg=HAT_COLORS[c])
            self.cfg['hat_color'] = c
            save_cfg(self.cfg)
            log(f'hattefarge valgt: {c}')

    def _append_chat_sys(self, text):

        def f():
            self.chat_log.config(state='normal')
            if self.chat_log.index('end-1c') != '1.0':
                self.chat_log.insert('end', '\n')
            self.chat_log.insert('end', f"[{time.strftime('%H:%M')}] {text}", 'sys')
            self.chat_log.see('end')
            self.chat_log.config(state='disabled')
        self.ui(f)

    def _append_chat_msg(self, sender, text, color_name):

        def f():
            tag = f'col_{color_name}' if color_name in HAT_COLORS else 'col_Grønn'
            self.chat_log.config(state='normal')
            if self.chat_log.index('end-1c') != '1.0':
                self.chat_log.insert('end', '\n')
            self.chat_log.insert('end', f"[{time.strftime('%H:%M')}] ", 'sys')
            self.chat_log.insert('end', f'{sender}: ', tag)
            self.chat_log.insert('end', text)
            self.chat_log.see('end')
            self.chat_log.config(state='disabled')
        self.ui(f)

    def _send_chat(self):
        txt = self.chat_var.get().strip()
        if not txt:
            return
        low = txt.lower()
        if low in ('/tp', '/warp', '!tp', '!warp', '/here', '/samle', '/teleport'):
            self.chat_var.set('')
            if not self.peer or not hasattr(self, 'gs'):
                self._append_chat_sys('⚠️ Not connected to a friend!')
                return
            apos = self.pad.my_apos(self.p) if self.p and self.ready else None
            arot = self.pad.my_arot(self.p) if self.p and self.ready else None
            if apos:
                pkt = {'t': 'party_warp', 's': self.session, 'name': self.name, 'pos': apos, 'rot': arot, 'banc': self.pad.cur_banc, 'force': True}
                try:
                    self.gs.sendto(json.dumps(pkt).encode(), self.peer)
                    fname = self.peer_name or 'vennen'
                    self._append_chat_sys(f'🌀 You teleported {fname} to you!')
                    log(f'chat kommando {low}: teleporterte {fname} til ({apos[0]:.2f}, {apos[1]:.2f}, {apos[2]:.2f})')
                except OSError:
                    pass
            return
        self.chat_var.set('')
        self.chat_seq += 1
        item = {'id': self.chat_seq, 'name': self.name, 'text': txt[:240], 'color': self.hat_color}
        self.chat_out.append(item)
        del self.chat_out[:-8]
        self._append_chat_msg(self.name, item['text'], self.hat_color)
        log(f"chat ut ({self.name}): {item['text']}")
        if self.peer and hasattr(self, 'gs'):
            try:
                pkt = {'t': 'chat', 's': self.session, 'chat': list(self.chat_out)}
                self.gs.sendto(json.dumps(pkt).encode(), self.peer)
            except OSError:
                pass

    def _worker(self):
        while True:
            try:
                self._attach_once()
            except MemoryError:
                log('MINNEFEIL: for lite ledig minne – venter 60 s før neste forsøk (lukk andre programmer / større sidefil)')
                self.set_check('game', 'err', 'out of memory – waiting')
                time.sleep(60)
            except Exception as e:
                log(f'FEIL: {e!r}')
                self.set_check('game', 'err', str(e)[:40])
            self.ready = False
            time.sleep(2)

    def _attach_once(self):
        self.set_check('lichii', 'work', 'waiting …')
        pid = None
        while not pid:
            pid = find_pid()
            time.sleep(0 if pid else 2)
        p = Proc(pid)
        self.set_check('lichii', 'ok', f'PID {pid}')
        if p.size < 1 << 30:
            raise OSError('fant ikke spillminnet')
        self.set_check('game', 'ok', f'{p.size >> 20} MB')
        cfg = self.cfg
        try:
            self.pad._worker(p)
        except Exception:
            pass
        shifts = [0, -5368709120, 5368709120, -3221225472, 3221225472] + [s for s in cfg.get('known_shifts', []) if s]
        pos_c = [cfg.get('pos_off')] + [DEFAULT_POS_OFF + s for s in shifts]
        rup_c = [cfg.get('rupee_off')] + [DEFAULT_RUPEE_OFF + s for s in shifts]
        pos = next((o for o in pos_c if o and 0 < o < p.size and valid_pos(p, o)), None)
        rup = next((o for o in rup_c if o and 0 < o < p.size and valid_rupee(p, o)), None)
        if pos and (not rup) and valid_rupee(p, DEFAULT_RUPEE_OFF + pos - DEFAULT_POS_OFF):
            rup = DEFAULT_RUPEE_OFF + pos - DEFAULT_POS_OFF
        if rup and (not pos) and valid_pos(p, DEFAULT_POS_OFF + rup - DEFAULT_RUPEE_OFF):
            pos = DEFAULT_POS_OFF + rup - DEFAULT_RUPEE_OFF
        if pos is None and self.pad.p_act is not None and (self.pad.my_apos(p) is not None):
            pos = self.pad.p_act + 796
            log(f'posisjon funnet direkte fra PadLink (player_actor+0x31C): guest+0x{pos:X}')
        if pos is None:
            self.set_check('pos', 'work', 'waiting for Link …')
            t_wait_pos = time.time()
            while not getattr(self, '_stop', False) and pos is None:
                try:
                    self.pad._worker(p)
                except Exception:
                    pass
                if self.pad.p_act is not None and self.pad.my_apos(p) is not None:
                    pos = self.pad.p_act + 796
                    log(f'posisjon funnet direkte fra PadLink (player_actor+0x31C): guest+0x{pos:X}')
                    break
                pos = next((o for o in pos_c if o and 0 < o < p.size and valid_pos(p, o)), None)
                if pos is not None:
                    break
                if int(time.time() - t_wait_pos) % 5 == 0 and pos is None:
                    cam0 = self.cfg.get('cam_off')
                    if cam0 and 0 < cam0 < p.size and valid_cam(p, cam0, None):
                        pos = relink_near(p, DEFAULT_POS_OFF, cam0)
                time.sleep(0.5)
        if rup is None and pos is not None:
            shift = pos - DEFAULT_POS_OFF
            cand_shifts = [shift, 0, -5368709120, 5368709120, -3221225472, 3221225472]
            for s in dict.fromkeys(cand_shifts):
                cand = DEFAULT_RUPEE_OFF + s
                if 0 < cand < p.size and valid_rupee(p, cand):
                    rup = cand
                    log(f'rupees funnet automatisk: guest+0x{rup:X} (shift {s:+#x})')
                    break
            if rup is None:
                try:
                    self.pad._gdm_scan_t = 0
                    self.pad.read_door(p)
                    if getattr(self.pad, 'h_s32', None) is not None and valid_rupee(p, self.pad.h_s32 + 962 * 40 + 28):
                        rup = self.pad.h_s32 + 962 * 40 + 28
                        log(f'rupees funnet i GameDataMgr (post 962): guest+0x{rup:X}')
                except Exception:
                    pass
            if rup is None:
                rup = find_rupees_by_region(p, pos)
                if rup is not None:
                    log(f'rupees funnet ved områdeforskyvning: guest+0x{rup:X}')
        if pos is not None and rup is not None:
            shift = pos - DEFAULT_POS_OFF
            if shift not in shifts:
                cfg.setdefault('known_shifts', []).append(shift)
                log(f'ny forskyvning lært: {shift:+#x}')
        if (cfg.get('pos_off'), cfg.get('rupee_off')) != (pos, rup):
            cfg.update(pos_off=pos, rupee_off=rup)
            save_cfg(cfg)
        same = self.p is not None and self.p.pid == p.pid and (self.pos_off == pos) and self.rupees and (self.rupees.off == rup)
        if not same:
            log(f'tilkoblet: pos=guest+0x{pos:X} {tuple((round(v, 1) for v in p.f32(pos, 3)))}, rupees=guest+0x{rup:X} ({p.u32(rup)}), markør=0x{p.u32(rup + 12):08X}, forskyvning {pos - DEFAULT_POS_OFF:+#x}')
            self.syncs = {}
            for name, rel, cap_rel, _label in ITEMS:
                if valid_rupee(p, rup + rel):
                    self.syncs[name] = CounterSync(p, rup + rel, 999, name, rup + cap_rel)
                else:
                    log(f'{name}: fant ikke gyldig post på rupees{rel:+#x} – synkes ikke')
            self.rupees = self.syncs['rupees']
            log('synker: ' + ', '.join((f'{n}={c.expected}/{c.cap()}' for n, c in self.syncs.items())))
            try:
                self.bag = BagSync(p, rup, self.cfg, {rel for _n, rel, _c, _l in ITEMS} | {2280, 2320, 2360})
            except OSError as e:
                self.bag = None
                log(f'bag: kunne ikke lese inventar-blokken: {e!r}')
            self.learn_state = {}
        else:
            for c in self.syncs.values():
                c.p = p
                c.expected = p.u32(c.off)
            if getattr(self, 'bag', None):
                self.bag.p = p
                for c in self.bag.counters.values():
                    c.p = p
                    c.expected = p.u32(c.off)
        self.p, self.pos_off = (p, pos)
        self.set_check('pos', 'ok', f'0x{pos:X}')
        self.set_check('rupees', 'ok', f'{self.rupees.expected}')
        self.ready = True
        self._ensure_cam(p, pos)
        fails = cam_fails = far = 0
        last_joint = 0.0
        frozen_since, last_xyz = (None, None)
        while p.alive() and fails < 6:
            try:
                xyz = p.f32(pos, 3)
            except OSError:
                xyz = None
            if xyz is not None:
                if xyz == last_xyz:
                    frozen_since = frozen_since or time.time()
                else:
                    frozen_since = None
                last_xyz = xyz
            if frozen_since and time.time() - frozen_since >= 5 and (time.time() - last_joint >= 30):
                last_joint = time.time()
                anchors = [self.cam_off, getattr(self, 'last_cam', None), self.cfg.get('cam_off'), *self.cfg.get('cam_history', [])]
                full = time.time() - getattr(self, '_last_full_joint', 0) >= 180 and getattr(self, '_joint_misses', 0) >= 2
                if full:
                    self._last_full_joint = time.time()
                try:
                    new, cam = relink_joint(p, pos, anchors, full=full)
                except MemoryError:
                    log('MINNEFEIL under gjenfinning – hopper over i 2 min')
                    new = cam = None
                    last_joint = time.time() + 90
                self._joint_misses = 0 if new is not None else getattr(self, '_joint_misses', 0) + 1
                if new is not None and new != pos:
                    log(f'Link-post guest+0x{pos:X} sto stille mens Link gikk – byttet til guest+0x{new:X} {tuple((round(v, 1) for v in p.f32(new, 3)))} (trolig områdebytte)')
                    pos = new
                    self.pos_off = new
                    self.cfg['pos_off'] = new
                    save_cfg(self.cfg)
                    self.set_check('pos', 'ok', f'0x{new:X}')
                    frozen_since = None
                    last_xyz = None
                if cam is not None and cam != self.cam_off:
                    log(f'kamera byttet til guest+0x{cam:X} (fulgte Link)')
                    self.cam_off = self.last_cam = cam
                    self.cfg['cam_off'] = cam
                    save_cfg(self.cfg)
                    self.set_check('cam', 'ok', f'0x{cam:X}')
            ok = (valid_pos_light(p, pos) or (self.pad.p_act is not None and pos == self.pad.p_act + 796 and (self.pad.my_apos(p) is not None))) and valid_rupee_light(p, rup)
            fails = 0 if ok else fails + 1
            if fails == 1:
                log('validering feilet et øyeblikk (venter før ny oppkobling)')
            if self.cam_off is not None:
                try:
                    cam_ok = valid_cam(p, self.cam_off, p.f32(pos, 3))
                except OSError:
                    cam_ok = False
                cam_fails = 0 if cam_ok else cam_fails + 1
                try:
                    lp = p.f32(pos, 3)
                    ce = read_cam(p, self.cam_off)[3]
                    ref = getattr(self, '_cam_ref', None)
                    if ref is None or ref[2] != self.cam_off:
                        self._cam_ref = (lp, ce, self.cam_off)
                    elif math.dist(lp, ref[0]) > 2.0:
                        if math.dist(ce, ref[1]) < 0.01:
                            log(f'kamera guest+0x{self.cam_off:X} følger ikke Link (dødt kamera) – søker på nytt')
                            cam_fails = 5
                        self._cam_ref = (lp, ce, self.cam_off)
                except (OSError, ValueError, TypeError):
                    pass
                if cam_fails >= 5:
                    log(f'kamera guest+0x{self.cam_off:X} ugyldig i 5 s – søker på nytt')
                    self.cam_off = None
                    cam_fails = 0
                    self.set_check('cam', 'work', 'searching …')
            if self.cam_off is None:
                self._ensure_cam(p, pos)
            time.sleep(1)
        if p.alive():
            log('adresser ugyldige i 6 s – kobler på nytt')
        self.ready = False
        for k in ('pos', 'rupees'):
            self.set_check(k, 'off', '')

    def _ensure_cam(self, p, pos):
        if self.cam_off is not None or self.cam_scanning:
            return
        near = p.f32(pos, 3)
        if abs(near[0]) + abs(near[1]) + abs(near[2]) < 1.0:
            return
        shift = pos - DEFAULT_POS_OFF
        if getattr(self.pad, 'main', None) and getattr(self.pad, 'p_act', None):
            try:
                best = cam_scan_vtable(p, self.pad.main, self.pad.p_act, self.pad.my_apos(p) or near)
            except Exception:
                best = None
            if best is not None:
                self.cam_off = self.last_cam = best
                self.cfg['cam_off'] = best
                save_cfg(self.cfg)
                log(f'kamera: guest+0x{best:X} (vtable)')
                self.set_check('cam', 'ok', f'0x{best:X}')
                return
        for o in (self.cfg.get('cam_off'), DEFAULT_CAM_OFF + shift, DEFAULT_CAM_OFF):
            if o and 0 < o < p.size and valid_cam(p, o, near):
                self.cam_off = self.last_cam = o
                log(f'kamera: guest+0x{o:X} (kjent adresse), øye {tuple((round(v, 1) for v in read_cam(p, o)[3]))}')
                self.set_check('cam', 'ok', f'0x{o:X}')
                return
        raw = [o for o in self.cfg.get('cam_history', []) if o]
        hist = [o for o in raw if not 0 < o < p.size or valid_cam(p, o, near) or (not _cam_readable(p, o))]
        if len(hist) != len(raw):
            log(f'cam_history: fjernet {len(raw) - len(hist)} adresse(r) som ikke er kamera nær Link')
            self.cfg['cam_history'] = hist
            save_cfg(self.cfg)
        anchors = list(dict.fromkeys([a for a in (getattr(self, 'last_cam', None), self.cfg.get('cam_off'), DEFAULT_CAM_OFF + shift, DEFAULT_CAM_OFF, *hist) if a]))
        full_ok = time.time() - self.cam_last_scan >= 120
        quick_ok = time.time() - getattr(self, 'cam_last_quick', 0) >= 5
        if not (full_ok or quick_ok):
            return
        self.cam_scanning = True
        self.set_check('cam', 'work', 'searching …')

        def choose(groups, label, t):
            log(f'kamera-søk ({label}): {sum((g[0] for g in groups))} kandidater i {len(groups)} grupper på {time.time() - t:.1f} s')
            for n, offs, eye, dist, dot in groups[:3]:
                log(f'  gruppe: {n} kopier, øye {eye}, {dist:.1f} m fra Link, retning {dot:.2f}, første guest+0x{offs[0]:X}')
            now_pos = p.f32(pos, 3)
            scored = []
            for _n, offs, _eye, _d, _dot in groups:
                q = cam_quality(p, offs[0], now_pos)
                if q and 1.5 < q[0] < 12 and (q[1] > 0.7):
                    scored.append((q[1], offs[0], q[0]))
            scored.sort(reverse=True)
            if not scored:
                return None
            log(f'  valgt: retning {scored[0][0]:.2f}, {scored[0][2]:.1f} m fra Link')
            return scored[0][1]

        def run():
            try:
                best = None
                if getattr(self.pad, 'main', None) and getattr(self.pad, 'p_act', None):
                    t = time.time()
                    apos = self.pad.my_apos(p) or p.f32(pos, 3)
                    best = cam_scan_vtable(p, self.pad.main, self.pad.p_act, apos)
                    if best is not None:
                        log(f'kamera funnet via vtable på {time.time() - t:.2f} s: guest+0x{best:X}')
                if best is None and quick_ok and anchors:
                    self.cam_last_quick = time.time()
                    span = 32 << 20
                    regions = sorted({(max(0, a - span) & ~(CHUNK - 1), min(p.size, a + span)) for a in anchors})
                    t = time.time()
                    best = choose(cam_scan(p, p.f32(pos, 3), lambda _f: None, regions), 'raskt', t)
                if best is None and full_ok:
                    self.cam_last_scan = time.time()
                    t = time.time()
                    best = choose(cam_scan(p, p.f32(pos, 3), lambda _f: None), 'fullt', t)
                if best is not None:
                    self.cam_off = self.last_cam = best
                    self.cfg['cam_off'] = best
                    self.cfg['cam_history'] = ([best] + [o for o in hist if o != best])[:8]
                    save_cfg(self.cfg)
                    log(f'kamera funnet ved søk: guest+0x{best:X} (forskyvning fra standard {best - DEFAULT_CAM_OFF:+#x})')
                    self.set_check('cam', 'ok', f'0x{best:X}')
                else:
                    self.set_check('cam', 'err', 'not found – retrying')
            except Exception as e:
                log(f'kamera-søk FEIL: {e!r}')
                if isinstance(e, MemoryError):
                    self.cam_last_scan = time.time() + 150
                self.set_check('cam', 'err', 'error')
            finally:
                self.cam_scanning = False
        threading.Thread(target=run, daemon=True).start()

    def _find_link(self, p, phys):
        if not phys:
            raise OSError('ingen fysikk-objekter – er spillet lastet?')
        snap = lambda: {o: p.f32(o, 3) for o in phys if valid_pos(p, o)}
        self.show_guide('Finner Link: stå helt stille og trykk OK.')
        a = snap()
        time.sleep(1.0)
        a2 = snap()
        still_noise = {o for o in a if o in a2 and math.dist(a[o], a2[o]) > 0.01}
        self.show_guide('Gå med Link noen meter (2–3 sekunder), stå stille igjen og trykk OK.')
        b = snap()
        moved = [o for o in a if o in b and o not in still_noise and (math.dist(a[o], b[o]) > 1.0)]
        time.sleep(1.0)
        c = snap()
        moved = [o for o in moved if o in c and math.dist(b[o], c[o]) < 0.01]
        self.hide_guide()
        if not moved:
            raise OSError('fant ikke Link – prøv igjen på et rolig sted')
        moved.sort(key=lambda o: -math.dist(a[o], b[o]))
        return moved[0]

    def _find_rupees(self, p):
        while True:
            v = self.show_guide('Hvor mange rupees har du nå?', entry=True)
            if not v.isdigit():
                continue
            self.hide_guide()
            cands = rupee_scan(p, int(v), self.progress)
            log(f'rupee-søk etter {v}: {len(cands)} kandidater')
            while len(cands) > 1:
                v = self.show_guide(f'{len(cands)} mulige. Plukk opp eller bruk noen rupees, og skriv det nye tallet:', entry=True)
                if v.isdigit():
                    cands = [o for o in cands if valid_rupee(p, o) and p.u32(o) == int(v)]
            if cands:
                self.hide_guide()
                return cands[0]

    def _net_setup(self):
        self.gs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.gs.bind(('0.0.0.0', GAME_PORT))
        self.gs.setblocking(False)
        self.ds = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ds.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ds.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.ds.bind(('0.0.0.0', DISC_PORT))
        self.ds.setblocking(False)
        self.last_hello = 0

    def connect(self, ip):
        self.peer = (ip, GAME_PORT)
        self.cfg['last_ip'] = ip
        save_cfg(self.cfg)
        log(f'kobler til {ip}:{GAME_PORT}')
        self.friend_session = None
        self.set_check('net', 'work', f'connecting to {ip} …')

    def _connect_selected(self):
        sel = self.plist.curselection()
        if sel:
            self.connect(self.plist.get(sel[0]).split('  ')[-1])

    def _connect_ip(self):
        ip = self.ip_var.get().strip()
        if ip:
            self.connect(ip)

    def _fast_net_loop(self):
        while True:
            time.sleep(1 / 60)
            try:
                if not hasattr(self, 'gs'):
                    continue
                now = time.time()
                p = self.p if self.ready else None
                peer = self.peer
                if p is not None and peer is not None:
                    apos = self.pad.my_apos(p)
                    arot = self.pad.my_arot(p)
                    cam_a = self.pad.my_cam_angle(p, self.cam_off)
                    pad_hex = self.pad.my_pad(p)
                    if apos and pad_hex is not None:
                        pkt = {'t': 'fast', 's': self.session, 'name': self.name, 'color': self.hat_color, 'pos': [apos[0], apos[1] + 0.56, apos[2]], 'face': [arot[6], arot[8]] if arot else [0.0, 1.0], 'apos': apos, 'arot': arot, 'cam_a': cam_a, 'pad': pad_hex, 'banc': self.pad.cur_banc}
                        try:
                            self.gs.sendto(json.dumps(pkt).encode(), peer)
                        except OSError:
                            pass
                    if self.pad.scene_changed and now < self.pad.suppress_warp_until:
                        self.pad.scene_changed = False
                    if self.pad.scene_changed and now - getattr(self, '_last_warp_send_t', 0) > 4.0:
                        self._last_warp_send_t = now
                        self.pad.scene_changed = False
                        warp_pkt = {'t': 'party_warp', 's': self.session, 'name': self.name, 'banc': self.pad.read_banc(p) or self.pad.cur_banc, 'start': self.pad.START_POS.get(self.pad.read_banc(p) or self.pad.cur_banc), 'pos': apos, 'rot': arot}
                        try:
                            self.gs.sendto(json.dumps(warp_pkt).encode(), peer)
                            self._append_chat_sys('🚪 You entered a new area / went through a door!')
                            log('Dør-varsel: sendte party_warp (varsel) til vennen')
                        except OSError:
                            pass
                self._drain_udp(now)
            except Exception:
                pass

    def _handle_incoming_chat(self, friend_s, chat_list):
        if not isinstance(chat_list, list):
            return
        for item in chat_list:
            if not isinstance(item, dict):
                continue
            cid = item.get('id')
            txt = item.get('text')
            sender = item.get('name') or (self.friend.get('name') if self.friend else 'Venn')
            col = item.get('color') or (self.friend.get('color') if self.friend else 'Blå')
            if cid is not None and txt and ((friend_s, cid) not in self.chat_seen):
                self.chat_seen.add((friend_s, cid))
                self._append_chat_msg(sender, str(txt), col)
                log(f'chat inn ({sender}): {txt}')

    def _handle_party_warp(self, m):
        now = time.time()
        if now - getattr(self, '_last_warp_recv_t', 0) < 3.0:
            return
        self._last_warp_recv_t = now
        w_name = m.get('name', 'Vennen')
        banc, pos, rot, force = (m.get('banc'), m.get('pos'), m.get('rot'), bool(m.get('force')))
        if banc:
            self.pad.friend_banc = banc
            self.pad.warp_banc, self.pad.warp_t = (banc, now)
            self.pad.learn_start(banc, m.get('start'), 'fra venn')
        if force:
            self._append_chat_sys(f'🌀 {w_name} teleported you to them!')
        else:
            self._append_chat_sys(f'🚪 {w_name} went through a door / into a new area!')
        log(f'party_warp inn fra {w_name}: banc={banc} force={force} (her: {self.pad.cur_banc})')
        if not (force or self.tvang) or not (self.p and self.ready):
            return
        p = self.p
        if banc and self.pad.cur_banc and (banc != self.pad.cur_banc):
            if not force and now - getattr(self, '_last_tvang_t', 0) < 5.0:
                return
            self._last_tvang_t = now

            def _go():
                ok = self.pad.change_banc(p, banc)
                if ok:
                    self.pad.suppress_warp_until = time.time() + 20
                    self.pad._pending_tp = (banc, pos, rot, time.time()) if force and pos else None
                    self.ui(self._append_chat_sys, f'🌀 Følger {w_name} til {banc} …')
                else:
                    self.ui(self._append_chat_sys, f'⚠️ Kan ikke følge til {banc} (ukjent startpunkt eller call_v1-patchen mangler)')
            threading.Thread(target=_go, daemon=True).start()
        elif force and pos:
            self.pad.teleport_player(p, pos, rot)

    def _drain_udp(self, now):
        with self._fast_net_lk:
            while True:
                try:
                    data, addr = self.gs.recvfrom(65535)
                except OSError:
                    break
                try:
                    m = json.loads(data)
                except ValueError:
                    continue
                if m.get('s') == self.session:
                    continue
                t = m.get('t')
                if t == 'chat':
                    self._handle_incoming_chat(m.get('s'), m.get('chat'))
                    continue
                if t == 'party_warp':
                    self._handle_party_warp(m)
                    continue
                if t not in ('state', 'fast'):
                    continue
                if self.peer is None:
                    self.connect(addr[0])
                reset = m.get('s') != self.friend_session
                if self.friend is None or reset:
                    self.friend = dict(m)
                else:
                    self.friend.update(m)
                self.friend_t, self.friend_session = (now, m.get('s'))
                if m.get('pt'):
                    self._rx_pt = (m['pt'], now)
                pe = m.get('pe')
                if isinstance(pe, list) and len(pe) == 2 and pe[0]:
                    rtt = (now - pe[0] - pe[1]) * 1000
                    if 0 <= rtt < 5000:
                        self.ping_ms = rtt if getattr(self, 'ping_ms', None) is None else self.ping_ms * 0.8 + rtt * 0.2
                self._rx_count = getattr(self, '_rx_count', 0) + 1
                if m.get('banc'):
                    self.pad.friend_banc = m.get('banc')
                if m.get('look') in LOOKS:
                    self.pad.friend_look = m.get('look')
                f_col = self.friend.get('color')
                if f_col and f_col == self.hat_color and (self.session > (self.friend_session or 0)):
                    alt = 'Blå' if self.hat_color != 'Blå' else 'Rød'
                    self.hat_color = alt
                    self.ui(lambda a=alt: (self.color_var.set(a), self.color_dot.config(fg=HAT_COLORS[a])))
                    self._append_chat_sys(f'You both had {f_col} – your colour was switched to {alt} so you differ!')
                if 'chat' in m:
                    self._handle_incoming_chat(m.get('s'), m.get('chat'))
                if t == 'state' and self.ready:
                    try:
                        cums = m.get('cums') or {'rupees': m.get('cum', 0)}
                        vals = m.get('vals') or {'rupees': m.get('rupees')}
                        for n, c in self.syncs.items():
                            if n in cums:
                                c.apply(cums[n], reset)
                            c.equalize(vals.get(n), now, force=reset)
                        if getattr(self, 'bag', None):
                            self.bag.apply(m, reset, now)
                    except OSError:
                        pass
            alive = bool(self.friend and now - self.friend_t < 2)
            if self.p is not None:
                my_cam_a = self.pad.my_cam_angle(self.p, self.cam_off)
                f_pos = None
                if alive and self.friend:
                    f_pos = self.friend.get('apos')
                    if not f_pos and self.friend.get('pos'):
                        fp = self.friend['pos']
                        f_pos = [fp[0], fp[1] - 0.56, fp[2]]
                self.pad.apply(self.p, self.friend.get('pad') if alive and self.friend else None, self.friend.get('cam_a') if alive and self.friend else None, my_cam_a, f_pos, self.friend.get('arot') if alive and self.friend else None, alive=alive, cam_off=self.cam_off, hearts=self.friend.get('hearts') if alive and self.friend else None)

    def _time_host(self):
        if not self.peer:
            return True
        f = self.peer[0]
        pre = f.rsplit('.', 1)[0]
        mine = next((ip for ip in local_ips() if ip.rsplit('.', 1)[0] == pre), None)
        if not mine:
            return True
        return tuple((int(x) for x in mine.split('.'))) < tuple((int(x) for x in f.split('.')))
    AMMO_REFUND = {2280: 'nøtter', 2320: 'frø'}

    def _ammo_tick(self, now):
        if not (self.p and self.ready and getattr(self, 'rupees', None)) or now - getattr(self, '_am_t', 0) < 0.05:
            return
        self._am_t = now
        try:
            raw = bytes.fromhex(self.pad.my_pad(self.p) or '')
            btn = struct.unpack_from('<I', raw, 0)[0] if len(raw) >= 4 else 0
            if btn & ~3075:
                self._am_btn_t = now
            last = self.__dict__.setdefault('_am_last', {})
            for rel, nm in self.AMMO_REFUND.items():
                v = self.p.u32(self.rupees.off + rel)
                old = last.get(rel)
                if old is not None and 0 <= old - v <= 3 and (v < old) and (now - getattr(self, '_am_btn_t', 0) > 4.0):
                    self.p.write(self.p.base + self.rupees.off + rel, struct.pack('<I', old))
                    log(f'ammo: Link 2 brukte {old - v} {nm} – gitt tilbake ({v} -> {old})')
                    v = old
                last[rel] = v
        except (OSError, ValueError, struct.error):
            pass

    def _flag_tick(self, now):
        if now - getattr(self, '_fl_tick', 0) < 1.0 or getattr(self, '_fl_busy', False) or (not (self.p and self.ready)):
            return
        self._fl_tick = now
        self._fl_busy = True
        fresh = bool(self.friend and now - self.friend_t < 2)
        fb = (self.friend or {}).get('flagbits') if fresh else None
        theirs = []
        if fb:
            try:
                raw = __import__('base64').b64decode(fb)
                theirs = [k * 8 + j for k, byte in enumerate(raw) if byte for j in range(8) if byte >> j & 1]
            except Exception:
                theirs = []

        def run():
            try:
                _tf = time.perf_counter()
                cur = self.pad.read_bools(self.p)
                perf_add('flagg-les', time.perf_counter() - _tf)
                if cur is None:
                    return
                prev = getattr(self, '_fl_prev', None)
                pend = self.__dict__.setdefault('_fl_pend', {})
                noisy = self.__dict__.setdefault('_fl_noisy', set())
                sent = self.__dict__.setdefault('flags_out', set())
                got = self.__dict__.setdefault('_fl_got', set())
                if prev is not None and len(prev) == len(cur) and (prev != cur):
                    for i in range(len(cur)):
                        if cur[i] != prev[i]:
                            if cur[i] and i not in got:
                                pend.setdefault(i, now)
                            elif not cur[i]:
                                noisy.add(i)
                                pend.pop(i, None)
                t_first = self.__dict__.setdefault('_fl_t0', now)
                if not getattr(self, '_fl_full_done', False) and now - t_first > 60:
                    self._fl_full_done = True
                    full = {i for i in range(len(cur)) if cur[i] and i not in noisy}
                    sent.update(full)
                    log(f'flagg: sender alle {len(full)} satte verdens-flagg én gang')
                for i, t0 in list(pend.items()):
                    if now - t0 >= 8.0:
                        pend.pop(i)
                        if cur[i] and i not in noisy and (i not in sent):
                            sent.add(i)
                            log(f'flagg: sender verdens-flagg {i} til vennen')
                nset = []
                for i in theirs:
                    if isinstance(i, int) and 0 <= i < len(cur) and (not cur[i]) and (i not in got):
                        if self.pad.set_bool(self.p, i):
                            got.add(i)
                            nset.append(i)
                    elif isinstance(i, int):
                        got.add(i)
                if nset:
                    log(f'flagg: satte {len(nset)} verdens-flagg fra vennen: {nset[:12]}' + (' …' if len(nset) > 12 else ''))
                if sent:
                    bits = bytearray((max(sent) >> 3) + 1)
                    for i in sent:
                        bits[i >> 3] |= 1 << (i & 7)
                    self._fl_bits = __import__('base64').b64encode(bytes(bits)).decode()
                self._fl_prev = self.pad.read_bools(self.p) or cur
            except Exception as e:
                log(f'flagg: feil {e!r}')
            finally:
                self._fl_busy = False
        threading.Thread(target=run, daemon=True).start()

    def _enemy_tick(self, now):
        if now - getattr(self, '_en_tick', 0) < 0.1 or getattr(self, '_en_busy', False) or (not (self.p and self.ready)):
            return
        self._en_tick = now
        self._en_busy = True
        fresh = bool(self.friend and now - self.friend_t < 2)
        same = fresh and self.pad.cur_banc and (self.pad.friend_banc == self.pad.cur_banc)
        theirs = (self.friend or {}).get('enem') if same else None

        def run():
            try:
                pad, p = (self.pad, self.p)
                track = getattr(self, '_ac_track', {})
                me = pad.my_apos(p)
                host_t = self._time_host()
                if not (same and me):
                    self._my_enem = None
                    return
                m = self.__dict__.setdefault('_en_map', {})
                driven = {v[0] for hid, v in m.items() if self.__dict__.get('_en_follow', {}).get(hid)}
                lst = []
                for a, (nm, first, last) in list(track.items()):
                    if not nm.startswith('Enemy_') or 'Boss' in nm or 'Stalchild' in nm or ('Scrub_Business' in nm) or ('Skulltula_Gold' in nm) or (a in driven):
                        continue
                    if any((cn == nm and now - ct < 30 and (math.dist(cp, last) < 4.0) for cn, cp, ct in self.__dict__.get('_en_created', []))):
                        continue
                    try:
                        pos = p.f32(a + 796, 3)
                        rot = p.f32(a + 808, 9)
                    except OSError:
                        continue
                    dd = math.dist(pos, me)
                    if dd < 60 and all((math.isfinite(v) for v in pos)):
                        lst.append((dd, [a, nm, [round(v, 2) for v in pos], [round(v, 3) for v in rot], round(dd, 1)]))
                lst.sort(key=lambda t: t[0])
                self._my_enem = [x for _d, x in lst[:24]]
                if not theirs:
                    return
                follow = self.__dict__.setdefault('_en_follow', {})
                seen = self.__dict__.setdefault('_en_seen', {})
                used = {v[0] for v in m.values()}
                ids = set()
                for ent in theirs:
                    if len(ent) < 5:
                        continue
                    hid, nm, pos, rot, their_d = ent[:5]
                    ids.add(hid)
                    seen[hid] = now
                    cur = m.get(hid)
                    if cur and (not pad.actor_alive(p, cur[0])):
                        m.pop(hid)
                        used.discard(cur[0])
                        cur = None
                    free = []
                    if cur is None:
                        free = [(math.dist(last, pos), a) for a, (n2, first, last) in track.items() if n2 == nm and a not in used]
                        free.sort()
                        n_host = sum((1 for x in theirs if x[1] == nm))
                        if free and (free[0][0] < 8.0 or (n_host == 1 and len(free) == 1)):
                            x8 = pad.actor_x8(p, free[0][1])
                            if x8:
                                m[hid] = (free[0][1], x8)
                                used.add(free[0][1])
                                cur = m[hid]
                        elif not free and 'Boss' not in nm and (sum((1 for _a, (n3, _f, _l) in track.items() if n3 == nm)) < sum((1 for x in theirs if x[1] == nm))) and (now - getattr(self, '_en_spawn_t', 0) > 1.0) and (math.dist(pos, me) < 80) and (hid not in self.__dict__.setdefault('_en_pend', {})):
                            self._en_spawn_t = now
                            self._en_pend[hid] = now
                            r = pad.create_actor(p, nm, pos)
                            cr = self.__dict__.setdefault('_en_created', [])
                            cr.append((nm, tuple(pos), now))
                            del cr[:-40]
                            follow[hid] = True
                            log(f'fiende: laget {nm} hos meg (finnes hos vennen) -> {r}')
                    if cur:
                        try:
                            my_d = math.dist(p.f32(cur[0] + 796, 3), me)
                        except OSError:
                            my_d = 1000000000.0
                        sw = self.__dict__.setdefault('_en_sw', {})
                        prev = follow.get(hid)
                        want = their_d + 3.0 < my_d if not prev else not my_d + 3.0 < their_d
                        if prev is None or (want != prev and now - sw.get(hid, 0) > 2.0):
                            follow[hid] = want
                            sw[hid] = now
                        if 'Stalchild' in nm and (not host_t):
                            follow[hid] = True
                        if follow[hid]:
                            try:
                                cpos = p.f32(cur[0] + 796, 3)
                            except OSError:
                                cpos = None
                            if cpos and math.dist(cpos, pos) > 6.0:
                                m.pop(hid, None)
                                follow.pop(hid, None)
                            elif cpos and math.dist(cpos, pos) > 1.5:
                                k = 0.35
                                tgt = [c + (t - c) * k for c, t in zip(cpos, pos)]
                                pad.move_actor(p, cur[0], cur[1], tgt, rot)
                if False and (not host_t):
                    used2 = {v[0] for v in m.values()}
                    for a, (nm, first, last) in list(track.items()):
                        if 'Stalchild' not in nm or a in used2:
                            continue
                        if any((cn == nm and now - ct < 30 and (math.dist(cp, last) < 4.0) for cn, cp, ct in self.__dict__.get('_en_created', []))):
                            continue
                        track.pop(a, None)
                        pad.delete_actor(p, a)
                        log(f'fiende: fjernet eget {nm} (verten eier skjelettene)')
                for hid in list(m):
                    if hid not in ids and now - seen.get(hid, 0) > 3.0:
                        a, _x = m.pop(hid)
                        if follow.pop(hid, False) and pad.actor_alive(p, a) and (math.dist(track.get(a, ('', (0, 0, 0), (0, 0, 0)))[2], me) > 30):
                            pad.delete_actor(p, a)
                            log('fiende: slettet kopi (vennen har den ikke lenger)')
                used_a = {v[0] for v in m.values()}
                for a, (nm, _f, last) in list(track.items()):
                    if a in used_a or 'Boss' in nm or 'Stalchild' in nm:
                        continue
                    n_mine = sum((1 for _a, (n3, _f3, _l3) in track.items() if n3 == nm))
                    n_theirs = sum((1 for x in theirs if x[1] == nm))
                    if n_mine > n_theirs and any((cn == nm and now - ct < 60 and (math.dist(cp, last) < 6.0) for cn, cp, ct in self.__dict__.get('_en_created', []))):
                        track.pop(a, None)
                        pad.delete_actor(p, a)
                        log(f'fiende: slettet ekstra kopi {nm} (har {n_mine}, vennen {n_theirs})')
            except Exception as e:
                log(f'fiende: feil {e!r}')
            finally:
                self._en_busy = False
        threading.Thread(target=run, daemon=True).start()

    def _actor_tick(self, now):
        if now - getattr(self, '_ac_tick', 0) < 4.0 or getattr(self, '_ac_busy', False) or (not (self.p and self.ready)):
            return
        self._ac_tick = now
        self._ac_busy = True
        fresh = bool(self.friend and now - self.friend_t < 2)
        same = fresh and self.pad.cur_banc and (self.pad.friend_banc == self.pad.cur_banc)
        their_gone = list((self.friend or {}).get('gone') or []) if same else []

        def run():
            try:
                pad, p = (self.pad, self.p)
                banc = pad.cur_banc
                cur = pad.scan_actors(p)
                if cur is None:
                    return
                track = self.__dict__.setdefault('_ac_track', {})
                if getattr(self, '_ac_banc', None) != banc:
                    track.clear()
                    self._ac_banc = banc
                else:
                    out = self.__dict__.setdefault('gone_out', [])
                    gone_now = [a for a in list(track) if a not in cur and (not pad.actor_alive(p, a))]
                    mass = len(gone_now) > 4
                    if mass:
                        log(f'aktør: {len(gone_now)} forsvant samtidig (omlasting/død) – sender ikke')
                    copies = {v[0] for v in getattr(self, '_en_map', {}).values()}
                    created = getattr(self, '_en_created', [])
                    for a in gone_now:
                        nm, first, last = track.pop(a)
                        if a in copies or any((cn == nm and math.dist(cp, first) < 6.0 for cn, cp, ct in created)):
                            continue
                        if True:
                            if same and (not mass) and (a not in pad.__dict__.get('self_deleted', ())):
                                self._gone_seq = getattr(self, '_gone_seq', 0) + 1
                                out.append([self._gone_seq, banc, nm, [round(v, 1) for v in last]])
                                del out[:-30]
                                log(f'aktør: {nm} borte her ved {tuple((round(v) for v in last))} – sender til vennen')
                for a, (nm, pos) in cur.items():
                    if a in track:
                        track[a] = (nm, track[a][1], pos)
                    else:
                        track[a] = (nm, pos, pos)
                done = self.__dict__.setdefault('_gone_done', set())
                for seq, gbanc, nm, gpos in their_gone:
                    key = (self.friend_session, seq)
                    if key in done or gbanc != banc:
                        continue
                    done.add(key)
                    best = None
                    for a, (n2, first, last) in track.items():
                        if n2 == nm:
                            dd = min(math.dist(first, gpos), math.dist(last, gpos))
                            if dd < 6.0 and (best is None or dd < best[0]):
                                best = (dd, a)
                    if best:
                        r = pad.delete_actor(p, best[1])
                        track.pop(best[1], None)
                        log(f'aktør: slettet {nm} ({best[0]:.1f} m unna) fordi den er borte hos vennen -> {r}')
            except Exception as e:
                log(f'aktør: feil {e!r}')
            finally:
                self._ac_busy = False
        threading.Thread(target=run, daemon=True).start()

    def _time_tick(self, now):
        if now - getattr(self, '_wt_tick', 0) < 2.0 or getattr(self, '_wt_busy', False) or (not (self.p and self.ready)):
            return
        self._wt_tick = now
        self._wt_busy = True
        host = self._time_host()
        fresh = bool(self.friend and now - self.friend_t < 2)
        fw = (self.friend or {}).get('wtime') if fresh else None
        fww = (self.friend or {}).get('wweather') if fresh else None

        def run():
            try:
                mine = self.pad.read_time(self.p)
                self._my_wtime = mine
                mw = self.pad.read_weather(self.p)
                self._my_wweather = mw
                if not host and fww is not None and (mw is not None) and (fww != mw) and (time.time() - getattr(self, '_ww_set_t', 0) > 15):
                    self._ww_set_t = time.time()
                    first = not getattr(self, '_ww_synced', False)
                    self._ww_synced = True
                    if self.pad.write_weather(self.p, fww, 0 if first else 5):
                        log(f'vær: satt til vertens {PadLink.WEATHERS[fww]} (var {PadLink.WEATHERS[mw]})')
                if mine is not None:
                    rolle = 'host' if host else 'following host' if fw is not None else 'waiting for host'
                    vtxt = {0: 'sunny', 1: 'rain', 2: 'snow', 3: 'storm', 4: 'cloudy'}.get(mw, '?')
                    self.set_check('clock', 'ok', f'{mine // 3600:02d}:{mine % 3600 // 60:02d} {vtxt} ({rolle})')
                else:
                    self.set_check('clock', 'off', 'unknown')
                if not host and fw is not None and (mine is not None):
                    d = abs(fw - mine)
                    d = min(d, 86400 - d)
                    if d > 300:
                        if self.pad.write_time(self.p, fw):
                            log(f'tid: satt til vertens {fw // 3600:02d}:{fw % 3600 // 60:02d} (var {mine // 3600:02d}:{mine % 3600 // 60:02d})')
            except Exception as e:
                log(f'tid: feil {e!r}')
            finally:
                self._wt_busy = False
        threading.Thread(target=run, daemon=True).start()

    def _flagbits_due(self):
        b = getattr(self, '_fl_bits', None)
        now = time.time()
        if b and (b != getattr(self, '_fl_sent_b', None) or now - getattr(self, '_fl_sent_t', 0) > 5.0):
            self._fl_sent_b = b
            self._fl_sent_t = now
            return b
        return None

    def _net_tick(self):
        now = time.time()
        self._time_tick(now)
        self._ammo_tick(now)
        self._flag_tick(now)
        self._actor_tick(now)
        self._enemy_tick(now)
        if self.tvang and self.peer and self.p and self.ready and (now - getattr(self.pad, 'f_time', 0) < 3.0) and self.pad.cur_banc and self.pad.friend_banc and (self.pad.cur_banc != self.pad.friend_banc) and (self.pad.friend_banc in self.pad.START_POS) and (now - getattr(self.pad, '_banc_since', 0) > 8.0) and (now - getattr(self, '_last_tvang_t', 0) > 12.0):
            self._last_tvang_t = now
            _p, _tb = (self.p, self.pad.friend_banc)

            def _auto_tvang():
                if self.pad.change_banc(_p, _tb):
                    self.pad.suppress_warp_until = time.time() + 20
                    self.ui(self._append_chat_sys, f'🌀 TVANG: følger etter til {_tb} …')
            threading.Thread(target=_auto_tvang, daemon=True).start()
        if getattr(self.pad, 'learned_new', False):
            self.pad.learned_new = False
            self.cfg['start_pos'] = {k: v for k, v in self.pad.START_POS.items() if PadLink.START_POS.get(k) != v}
            save_cfg(self.cfg)
            self._append_chat_sys(f'📍 Now knows {len(self.pad.START_POS)} zones for FOLLOW / /tp')
        try:
            import faulthandler
            if getattr(self, '_stall_f', None) is None:
                self._stall_f = open(os.path.join(APP_DIR, 'logs', f'{PC}-heng.txt'), 'a')
            faulthandler.cancel_dump_traceback_later()
            faulthandler.dump_traceback_later(8, repeat=False, file=self._stall_f)
        except Exception:
            pass
        if now - self.last_hello > 1:
            self.last_hello = now
            hello = json.dumps({'t': 'hello', 's': self.session, 'name': self.name, 'v': VERSION, 'color': self.hat_color}).encode()
            for dst in ('255.255.255.255',):
                try:
                    self.ds.sendto(hello, (dst, DISC_PORT))
                except OSError:
                    pass
        while True:
            try:
                data, addr = self.ds.recvfrom(2048)
            except OSError:
                break
            try:
                m = json.loads(data)
            except ValueError:
                continue
            if m.get('t') == 'hello' and m.get('s') != self.session:
                self.players[addr[0]] = (m.get('name', '?'), now, m.get('s'))
        live = {ip: v for ip, v in self.players.items() if now - v[1] < 4}
        if self.peer is None and len(live) == 1:
            self.connect(next(iter(live)))
        items = [f'{v[0]}  {ip}' for ip, v in live.items()]
        if list(self.plist.get(0, 'end')) != items:
            self.plist.delete(0, 'end')
            for it in items:
                self.plist.insert('end', it)
        me = None
        my_hearts = None
        if self.ready:
            try:
                apos = self.pad.my_apos(self.p)
                arot = self.pad.my_arot(self.p)
                if apos and arot:
                    me = (apos[0], apos[1] + 0.56, apos[2], arot[6], arot[8])
                else:
                    fx, _, fz = self.p.f32(self.pos_off - 16, 3)
                    me = (*self.p.f32(self.pos_off, 3), fx, fz)
                vals = {n: c.tick() for n, c in self.syncs.items()}
                bag_vals, bag_cums = self.bag.tick(now) if getattr(self, 'bag', None) else ({}, {})
                try:
                    h_cur = self.p.u32(self.rupees.off + 40)
                    h_max = self.p.u32(self.rupees.off + 80)
                    if 0 <= h_cur <= 80 and 4 <= h_max <= 80:
                        my_hearts = [h_cur, h_max]
                except OSError:
                    pass
                if now - getattr(self, '_learn_t', 0) > 0.5:
                    self._learn_t = now
                    learn_table(self.p, self.rupees.off, self.learn_state, now)
            except OSError:
                self.ready = False
        my_cam_a = self.pad.my_cam_angle(self.p, self.cam_off) if self.p is not None else None
        if self.peer and me:
            _rx = getattr(self, '_rx_pt', None)
            _pe = [_rx[0], round(time.time() - _rx[1], 4)] if _rx else None
            msg = {'t': 'state', 's': self.session, 'pt': round(time.time(), 4), 'pe': _pe, 'name': self.name, 'color': self.hat_color, 'banc': self.pad.cur_banc, 'pos': list(me[:3]), 'face': list(me[3:]), 'apos': self.pad.my_apos(self.p), 'arot': self.pad.my_arot(self.p), 'cam_a': my_cam_a, 'hearts': my_hearts, 'cum': self.rupees.my_cum, 'rupees': vals.get('rupees'), 'cums': {n: c.my_cum for n, c in self.syncs.items()}, 'vals': vals, 'bag': bag_vals, 'bag_cums': bag_cums, 'bag_delta': sorted(self.bag.delta) if getattr(self, 'bag', None) else [], 'bag_ignored': sorted(self.bag.ignored) if getattr(self, 'bag', None) else [], 'pad': self.pad.my_pad(self.p), 'wtime': getattr(self, '_my_wtime', None) if self._time_host() else None, 'wweather': getattr(self, '_my_wweather', None) if self._time_host() else None, 'look': getattr(self, 'look', 'No tunic'), 'gone': getattr(self, 'gone_out', [])[-30:], 'enem': getattr(self, '_my_enem', None), 'chat': list(self.chat_out)}
            _fb = self._flagbits_due()
            if _fb:
                msg['flagbits'] = _fb
            try:
                _pk = json.dumps(msg).encode()
                self.gs.sendto(_pk, self.peer)
                perf_add('pakke-kB', len(_pk) / 1024.0)
            except OSError:
                pass
        self._drain_udp(now)
        alive = bool(self.friend and now - self.friend_t < 2)
        if self.p is not None:
            self.pad.tick(self.p, now, alive=alive)
        if alive != getattr(self, '_was_alive', False):
            self._was_alive = alive
            fname = self.friend.get('name') if self.friend else '?'
            log(f"venn {('tilkoblet' if alive else 'mistet')}: {fname} {self.peer}")
            self._append_chat_sys(f"{fname} {('connected!' if alive else 'disconnected.')}")
        if self.peer:
            self.set_check('net', 'ok' if alive else 'work', f"{self.friend.get('name')} ({self.peer[0]})" if alive else f'waiting for {self.peer[0]} …')
        return (me, alive)

    def _tick(self):
        while not self.ui_q.empty():
            fn, a = self.ui_q.get_nowait()
            fn(*a)
        _t0 = time.perf_counter()
        me, alive = self._net_tick()
        perf_add('nett-tick', time.perf_counter() - _t0)
        try:
            self.figure.tick(self.p if self.ready else None, self.friend, alive, time.time(), me, bool(self.cfg.get('figur_folg_meg')))
        except Exception as e:
            if not getattr(self, '_fig_err', None):
                log(f'figur-feil: {e!r}')
            self._fig_err = True
        _t1 = time.perf_counter()
        self._draw(me, alive)
        try:
            self._overlay_tick(me, alive)
        except Exception as e:
            if not getattr(self, '_ov_err', None):
                log(f'markør-feil: {e!r}')
            self._ov_err = True
        perf_add('tegning+markør', time.perf_counter() - _t1)
        perf_report()
        self.root.after(33, self._tick)

    def _overlay_tick(self, me, alive):
        if self.overlay is None:
            self.overlay = Overlay(self.root)
        ov = self.overlay
        ov.clear()
        reason = self._overlay_draw(ov, me, alive)
        now = time.time()
        if alive and now - getattr(self, '_ov_log_t', 0) > 15:
            self._ov_log_t = now
            log(f'markør: {reason}')

    def _overlay_draw(self, ov, me, alive):
        if not self.marker_var.get():
            return 'slått av i vinduet'
        if not (alive and me and self.ready and self.p):
            return 'venter på venn/spill'
        if self.cam_off is None:
            return 'kamera ikke funnet ennå' + (' (søker)' if self.cam_scanning else '')
        now = time.time()
        if self.lwin is None or now - self.lwin_t > 2:
            self.lwin, self.lwin_t = (lichii_window(self.p.pid), now)
        if not self.lwin:
            return 'fant ikke Lichii-vinduet'
        if u32w.IsIconic(self.lwin):
            return 'Lichii er minimert'
        fg = u32w.GetForegroundWindow()
        if fg != self.lwin and fg != u32w.GetParent(self.root.winfo_id()):
            return 'Lichii er ikke aktivt vindu (tegner ikke over andre programmer)'
        rect = game_rect(self.lwin)
        if not rect:
            return 'ugyldig vindusstørrelse'
        cam = read_cam(self.p, self.cam_off)
        fp = self.friend['pos']
        dist = math.dist(me[:3], fp)
        name = self.friend.get('name', 'venn')
        f_col = HAT_COLORS.get(self.friend.get('color'), ACC)
        if self.ghost_var.get():
            ff = self.friend.get('face') or [0.0, 1.0]
            fn = math.hypot(ff[0], ff[1]) or 1.0
            sf = project(cam, (fp[0], fp[1], fp[2]), rect)
            sh = project(cam, (fp[0], fp[1] + GHOST_HEIGHT, fp[2]), rect)
            if sf[2] and sh[2] and (sf[1] - sh[1] > 6):
                sd = project(cam, (fp[0] + ff[0] / fn * 0.9, fp[1], fp[2] + ff[1] / fn * 0.9), rect)
                ov.draw_ghost(sf, sh, sd if sd[2] else None, name, dist, hat_hex=f_col)
                return f'skyggefigur ({sf[0]:.0f},{sf[1]:.0f}), høyde {sf[1] - sh[1]:.0f} px, {dist:.0f} m, spillbilde {tuple((round(v) for v in rect))}, kamera guest+0x{self.cam_off:X}'
        sx, sy, on = project(cam, (fp[0], fp[1] + MARKER_HEIGHT, fp[2]), rect)
        ov.draw_friend(sx, sy, on, name, dist, hat_hex=f_col)
        return f"tegnet {('på skjermen' if on else 'i kanten')} ({sx:.0f},{sy:.0f}), {dist:.0f} m, spillbilde {tuple((round(v) for v in rect))}, kamera guest+0x{self.cam_off:X}"

    def _draw_net(self, alive):
        if not hasattr(self, 'net_lbls'):
            return
        now = time.time()
        if now - getattr(self, '_net_draw_t', 0) < 0.5:
            return
        dt = now - getattr(self, '_net_draw_t', now - 0.5)
        self._net_draw_t = now
        rate = (getattr(self, '_rx_count', 0) - getattr(self, '_rx_count_last', 0)) / max(dt, 0.1)
        self._rx_count_last = getattr(self, '_rx_count', 0)
        L = self.net_lbls
        if alive:
            L['state'].config(text='● Connected', fg=OK)
        elif self.peer:
            L['state'].config(text='● Waiting for reply', fg=WARN)
        else:
            L['state'].config(text='● Not connected', fg=MUTED)
        if self.peer:
            nm = (self.friend or {}).get('name', '?') if alive else '?'
            L['peer'].config(text=f'{nm} ({self.peer[0]})', fg=FG)
        else:
            L['peer'].config(text='–', fg=MUTED)
        pm = getattr(self, 'ping_ms', None)
        if alive and pm is not None:
            L['ping'].config(text=f'{pm:.0f} ms', fg=OK if pm < 30 else WARN if pm < 100 else '#ff6b6b')
        else:
            L['ping'].config(text='–', fg=MUTED)
        L['rate'].config(text=f'{rate:.0f} in/s', fg=FG if alive else MUTED)
        z = self.pad.cur_banc or '–'
        fz = self.pad.friend_banc
        same = bool(fz and z == fz)
        L['zone'].config(text=z.replace('_Child', '').replace('_Adult', '') + (' (same)' if same else ' (friend: other)' if fz else ''), fg=OK if same else FG)

    def _draw(self, me, alive):
        lines = []
        self._draw_net(alive)
        if alive and self.friend:
            f = self.friend
            fcol = f.get('color', 'Blå')
            self.peer_color_lbl.config(text=f"● {f.get('name', 'Friend')} ({COLOR_EN.get(fcol, fcol)})", fg=HAT_COLORS.get(fcol, ACC))
            if me and f.get('pos'):
                x, y, z = me[:3]
                dx, dy, dz = (f['pos'][0] - x, f['pos'][1] - y, f['pos'][2] - z)
                dist = math.hypot(dx, dz)
                lines.append(f"Distance to {f.get('name')}: {dist:.1f} m ({('above' if dy > 0 else 'below')} you by {abs(dy):.1f} m)")
        else:
            self.peer_color_lbl.config(text='Offline', fg=MUTED)
        if self.ready:
            try:
                h_cur = self.p.u32(self.rupees.off + 40)
                h_max = self.p.u32(self.rupees.off + 80)
                if 0 <= h_cur <= 80 and 4 <= h_max <= 80:
                    full_h = max(1, round(h_max / 4))
                    cur_h = max(0.0, min(float(full_h), h_cur / 4.0))
                    bars = '♥' * int(round(cur_h)) + '♡' * max(0, full_h - int(round(cur_h)))
                    self.hearts_lbl.config(text=f'♥ You: {bars} ({cur_h:g}/{full_h})', fg='#5cdb5c' if cur_h > 1 else '#ff5c5c')
                else:
                    self.hearts_lbl.config(text='♥ You: ♥♥♥♥', fg='#5cdb5c')
            except OSError:
                pass
            if alive and self.friend:
                fh = self.friend.get('hearts')
                fname = self.friend.get('name', 'Venn')
                if fh and len(fh) == 2 and (0 <= fh[0] <= 80) and (4 <= fh[1] <= 80):
                    f_full = max(1, round(fh[1] / 4))
                    f_cur = max(0.0, min(float(f_full), fh[0] / 4.0))
                    f_bars = '♥' * int(round(f_cur)) + '♡' * max(0, f_full - int(round(f_cur)))
                    warn = ' ⚠️' if f_cur <= 1.0 else ''
                    self.friend_hearts_lbl.config(text=f'♥ {fname}: {f_bars} ({f_cur:g}/{f_full}){warn}', fg='#ff3b3b' if f_cur <= 1.0 else '#5cdb5c')
                else:
                    self.friend_hearts_lbl.config(text=f'♥ {fname}: connected', fg='#5cdb5c')
            else:
                self.friend_hearts_lbl.config(text='♥ Friend: offline', fg=MUTED)
            fv = self.friend.get('vals') or {'rupees': self.friend.get('rupees')} if alive and self.friend else {}
            item_parts = []
            for name, _rel, _cap, label in ITEMS:
                if name in self.syncs:
                    c = self.syncs[name]
                    extra = f' (friend: {fv[name]})' if name in fv else ''
                    item_parts.append(f'{label}: {c.expected}/{c.cap()}{extra}')
            lines.extend(item_parts)
            if getattr(self, 'bag', None):
                lines.append(f'Shared inventory: {self.bag.owned()} items/upgrades ({len(self.bag.delta)} consumables)')
        else:
            self.hearts_lbl.config(text='♥ Life: waiting for game …')
            if not lines:
                lines.append('Start Lichii and load your save file to sync.')
        self.status.config(text='\n'.join(lines))

def main():
    try:
        u32w.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    try:
        App(root)
    except OSError as e:
        tk.messagebox.showerror('OoT Co-oP', f'Kunne ikke starte: {e}\nKjører programmet allerede?')
        return
    root.mainloop()
if __name__ == '__main__':
    main()
