#!/usr/bin/env python3
"""Reproduce, on demand, the two states the rpc-server watchdog predicate must
tell apart (F51). Nothing here is a benchmark -- it is the test rig for
`cluster/llama-watchdog.sh`'s `predicate_rpc`, which cannot be exercised any
other way without a real multi-hour shard upload.

    busy <host> <port> [bytes_per_s] [seconds]
        Speak enough of the llama.cpp RPC wire protocol (see
        src/ggml/src/ggml-rpc/ggml-rpc.cpp, rpc_serve_client) to get past HELLO,
        then open an oversized RPC_CMD_SET_TENSOR and deliver its payload
        slowly. The server blocks in recv_data() until the whole payload
        arrives -- exactly the state a shard upload puts it in. One client owns
        the server, so new connections are refused, while the kernel's TCP byte
        counters advance. THE WATCHDOG MUST NOT RESTART THIS.

    hold <host> <port> [seconds]
        Occupy a slot in the listen backlog (depth 1) without being served, and
        send nothing. Run alongside `busy` to make the port actually refuse;
        run against a SIGSTOPped server, then kill it, to leave the accept
        queue full with no ESTABLISHED socket -- which is the genuine wedge
        signature (refusing, nobody attached, CPU flat). THE WATCHDOG MUST
        STILL RESTART THAT.

Usage is deliberately manual. See the F51 verification notes for the exact
sequence and the expected verdicts.
"""
import socket, struct, sys, time

RPC_CMD_SET_TENSOR = 6
RPC_CMD_HELLO      = 14
CONN_CAPS_SIZE     = 24          # transport.h: RPC_CONN_CAPS_SIZE


def _recvn(s, n):
    b = b''
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise SystemExit('server closed the connection during HELLO')
        b += c
    return b


def hello(s):
    s.sendall(bytes([RPC_CMD_HELLO]) + struct.pack('<Q', CONN_CAPS_SIZE)
              + b'\x00' * CONN_CAPS_SIZE)          # all-zero caps = plain TCP
    n = struct.unpack('<Q', _recvn(s, 8))[0]
    r = _recvn(s, n)
    print(f'HELLO ok: server proto {r[0]}.{r[1]}.{r[2]}', flush=True)


def busy(host, port, rate, secs):
    s = socket.create_connection((host, port), timeout=15)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    hello(s)
    # Announce far more than we intend to deliver, capped so the server's
    # input.resize() does not allocate anything silly.
    payload = min(int(rate * secs * 4), 512 * 1024 * 1024)
    s.sendall(bytes([RPC_CMD_SET_TENSOR]) + struct.pack('<Q', payload))
    print(f'SET_TENSOR announced {payload} B; dribbling {rate:.0f} B/s for {secs:.0f}s',
          flush=True)
    chunk, sent, t0 = b'\x5a' * 65536, 0, time.time()
    try:
        while time.time() - t0 < secs:
            s.sendall(chunk)
            sent += len(chunk)
            due = t0 + sent / rate
            if due > time.time():
                time.sleep(due - time.time())
    except (BrokenPipeError, ConnectionResetError) as e:
        print(f'connection lost after {sent} B: {e}', flush=True)
    print(f'done: {sent} B in {time.time()-t0:.0f}s', flush=True)


def hold(host, port, secs):
    socket.create_connection((host, port), timeout=15)
    print('backlog slot held', flush=True)
    time.sleep(secs)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == 'busy':
        busy(sys.argv[2], int(sys.argv[3]),
             float(sys.argv[4]) if len(sys.argv) > 4 else 1_000_000.0,
             float(sys.argv[5]) if len(sys.argv) > 5 else 300.0)
    elif mode == 'hold':
        hold(sys.argv[2], int(sys.argv[3]),
             float(sys.argv[4]) if len(sys.argv) > 4 else 300.0)
    else:
        raise SystemExit(__doc__)
