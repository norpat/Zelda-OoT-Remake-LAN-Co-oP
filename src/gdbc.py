import socket, time

class Gdb:

    def __init__(self, host='127.0.0.1', port=6543, timeout=10):
        self.host, self.port, self.timeout = (host, port, timeout)
        self.s = None
        self.buf = b''
        self.running = False

    def connect(self, retries=30):
        for _ in range(retries):
            try:
                self.s = socket.create_connection((self.host, self.port), timeout=self.timeout)
                break
            except OSError:
                time.sleep(1)
        else:
            raise OSError(f'fikk ikke kontakt med GDB-stub på {self.host}:{self.port}')
        self.s.settimeout(1.0)
        try:
            self.s.sendall(b'\x03')
            self._read_packet()
        except Exception:
            pass
        self.s.settimeout(self.timeout)
        sup = self.cmd('qSupported:multiprocess+;xmlRegisters=aarch64;qRelocInsn+')
        stop = self.cmd('?')
        self.running = False
        return (sup, stop)

    def _recv_more(self):
        d = self.s.recv(65536)
        if not d:
            raise OSError('forbindelsen ble lukket')
        self.buf += d

    def _read_packet(self):
        while True:
            while self.buf[:1] in (b'+', b'-'):
                self.buf = self.buf[1:]
            i = self.buf.find(b'$')
            j = self.buf.find(b'#', i + 1) if i != -1 else -1
            if i != -1 and j != -1 and (len(self.buf) >= j + 3):
                body = self.buf[i + 1:j]
                self.buf = self.buf[j + 3:]
                self.s.sendall(b'+')
                return self._unescape(body)
            self._recv_more()

    @staticmethod
    def _unescape(b):
        out = bytearray()
        k = 0
        while k < len(b):
            c = b[k]
            if c == 125:
                out.append(b[k + 1] ^ 32)
                k += 2
            elif c == 42 and out:
                n = b[k + 1] - 29
                out += bytes([out[-1]]) * n
                k += 2
            else:
                out.append(c)
                k += 1
        return bytes(out).decode('latin-1')

    def send(self, payload):
        data = payload.encode('latin-1')
        pkt = b'$' + data + b'#' + f'{sum(data) & 255:02x}'.encode()
        self.s.sendall(pkt)

    def cmd(self, payload):
        self.send(payload)
        return self._read_packet()

    def monitor(self, text):
        self.send('qRcmd,' + text.encode().hex())
        out = []
        while True:
            r = self._read_packet()
            if r.startswith('O') and r != 'OK':
                out.append(bytes.fromhex(r[1:]).decode('latin-1'))
            elif r == 'OK' or r.startswith('E') or r == '':
                break
            else:
                try:
                    out.append(bytes.fromhex(r).decode('latin-1'))
                except ValueError:
                    out.append(r)
                break
        return ''.join(out)

    def read(self, addr, n):
        out = bytearray()
        while n > 0:
            k = min(n, 2048)
            r = self.cmd(f'm{addr:x},{k:x}')
            while r and r.startswith('T'):
                r = self._read_packet()
            if not r or (len(r) == 3 and r[0] == 'E'):
                raise OSError(f'kan ikke lese 0x{addr:X}: {r}')
            out += bytes.fromhex(r)
            addr += k
            n -= k
        return bytes(out)

    def u64(self, addr):
        return int.from_bytes(self.read(addr, 8), 'little')

    def cont(self):
        self.send('c')
        self.running = True

    def interrupt(self):
        if not self.running:
            return None
        self.s.sendall(b'\x03')
        r = self._read_packet()
        self.running = False
        return r

    def wait_stop(self, timeout=None):
        old = self.s.gettimeout()
        self.s.settimeout(timeout)
        try:
            r = self._read_packet()
        finally:
            self.s.settimeout(old)
        self.running = False
        return r

    def set_watch(self, addr, length=4, kind=2):
        return self.cmd(f'Z{kind},{addr:x},{length:x}')

    def clear_watch(self, addr, length=4, kind=2):
        return self.cmd(f'z{kind},{addr:x},{length:x}')

    def regs(self):
        return self.cmd('g')

    def detach(self):
        try:
            self.cmd('D')
        except OSError:
            pass
        self.s.close()

def _gdb_write(self, addr, data):
    r = self.cmd(f'M{addr:x},{len(data):x}:{data.hex()}')
    while r and r.startswith('T'):
        r = self._read_packet()
    if r != 'OK':
        raise OSError(f'kan ikke skrive 0x{addr:X}: {r}')

def _gdb_set_thread(self, tid):
    return self.cmd(f'Hg{tid}')

def _gdb_write_regs(self, hexstr):
    return self.cmd('G' + hexstr)
Gdb.write = _gdb_write
Gdb.set_thread = _gdb_set_thread
Gdb.write_regs = _gdb_write_regs
