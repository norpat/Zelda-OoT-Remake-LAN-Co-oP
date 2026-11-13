import re, socket, struct, time
from gdbc import Gdb
HOOK = 7618624
GADGET = 23647428

def regs_safe(g):
    for _ in range(4):
        r = g.cmd('g')
        if len(r) >= 528 and all((c in '0123456789abcdefABCDEF' for c in r[:64])):
            return r
    raise OSError('fikk ikke lest registre: ' + r[:40])

def halt(g):
    g.s.sendall(b'\x03')
    r = g._read_packet()
    g.running = False
    return r

def call(func_off, setup, log=print, after_return=False, effect_watch=False, stop_off=HOOK, stop_timeout=10, host='127.0.0.1', port=6543, g=None):
    own_g = g is None
    if own_g:
        g = Gdb(host=host, port=port, timeout=20)
        g.s = socket.create_connection((g.host, g.port), timeout=20)
        halt(g)
    elif g.running:
        halt(g)
    orig = tid = None
    res = None
    try:
        maps = g.monitor('get mappings')
        main = int([l for l in maps.splitlines() if 'r-x Code' in l][0].split('-')[0].strip(), 16) + 131072
        log('main_va 0x%X' % main)
        log('breakpoint main+0x%X:' % stop_off, g.set_watch(main + stop_off, 4, 0))
        g.cont()
        try:
            stop = g.wait_stop(timeout=stop_timeout)
        except socket.timeout:
            halt(g)
            g.clear_watch(main + stop_off, 4, 0)
            raise SystemExit('main+0x%X ble ikke kalt på %d s' % (stop_off, stop_timeout))
        log('fjerner breakpoint:', g.clear_watch(main + stop_off, 4, 0))
        tid = re.search('thread:([0-9a-fA-F]+)', stop).group(1)
        g.set_thread(tid)
        if effect_watch:
            eff = struct.unpack_from('<Q', bytes.fromhex(regs_safe(g)), 0)[0]
            log('effekt-behandler 0x%X; Z2 på +0x654' % eff)
            g.set_watch(eff + 1620, 4, 2)
            g.cont()
            stop = g.wait_stop(timeout=20)
            g.clear_watch(eff + 1620, 4, 2)
            tid = re.search('thread:([0-9a-fA-F]+)', stop).group(1)
            g.set_thread(tid)
        if after_return:
            lr = struct.unpack_from('<Q', bytes.fromhex(regs_safe(g)), 30 * 8)[0]
            log('retur-adresse main+0x%X' % (lr - main))
            g.set_watch(lr, 4, 0)
            for _ in range(20):
                g.cont()
                stop = g.wait_stop(timeout=10)
                t2 = re.search('thread:([0-9a-fA-F]+)', stop).group(1)
                if t2 == tid:
                    break
            g.clear_watch(lr, 4, 0)
            g.set_thread(tid)
        orig = regs_safe(g)
        raw = bytearray(bytes.fromhex(orig))
        x = list(struct.unpack_from('<31Q', raw, 0))
        sp, pc = struct.unpack_from('<2Q', raw, 248)
        log('tråd %s stoppet på main+0x%X, sp=0x%X' % (tid, pc - main, sp))
        base = sp - 16384 & ~15
        g.write(base, bytes(2048))
        args, info = setup(g, main, base)
        for i, v in enumerate(args):
            x[i] = v
        x[30] = main + GADGET
        struct.pack_into('<31Q', raw, 0, *x)
        struct.pack_into('<2Q', raw, 248, base, main + func_off)
        g.write_regs(raw.hex())
        for i in range(40):
            g.cont()
            time.sleep(0.1)
            halt(g)
            g.set_thread(tid)
            r = bytes.fromhex(regs_safe(g))
            if struct.unpack_from('<Q', r, 256)[0] == main + GADGET:
                res = struct.unpack_from('<Q', r, 0)[0]
                log('returnerte: x0 = 0x%X' % res)
                break
        else:
            log('ADVARSEL: nådde ikke gadgeten')
        return (res, g, main, base, info)
    except BaseException:
        if orig is not None:
            g._orig = orig
            g._tid = tid
        finish(g)
        raise
    finally:
        if orig is not None:
            if g.running:
                halt(g)
            g.set_thread(tid)
            g._orig = orig
            g._tid = tid

def finish(g, close=True):
    try:
        if g.running:
            halt(g)
        if getattr(g, '_orig', None):
            g.set_thread(g._tid)
            g.write_regs(g._orig)
    finally:
        if close:
            g.cont()
            time.sleep(0.3)
            g.detach()
        else:
            g.cont()
