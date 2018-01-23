#!/usr/bin/env python3
import fcntl
import os
import pty
import signal
import socket
import struct
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import termios

CMD_DATA = 1
CMD_WINSZ = 2


class PartialRead(Exception):
    pass


def recv_n(sock, n):
    d = []
    while n > 0:
        s = sock.recv(n)
        if not s:
            break
        d.append(s)
        n -= len(s)
    if n > 0:
        raise PartialRead('EOF while reading')
    return b''.join(d)


def read_message(sock):
    length = struct.unpack('I', recv_n(sock, 4))[0]
    return recv_n(sock, length)


def child(cmdline, cwd, winsize, env):
    os.chdir(cwd)
    fcntl.ioctl(0, termios.TIOCSWINSZ, winsize)
    envdict = dict(line.split(b'=', 1) for line in env.split(b'\0'))
    envdict[b'ELEVATED_SHELL'] = b'1'
    if not cmdline:
        print("No command given")
    else:
        argv = cmdline.split(b'\0')
        os.execvpe(argv[0], argv, envdict)


def try_read(fd, size):
    try:
        return os.read(fd, size)
    except Exception:
        return b''


def pty_read_loop(child_pty, sock):
    try:
        for chunk in iter(lambda: try_read(child_pty, 8192), b''):
            sock.sendall(chunk)
        sock.shutdown(socket.SHUT_WR)
    except Exception as e:
        traceback.print_exc()


def sock_read_loop(sock, child_pty, pid):
    try:
        while True:
            message = read_message(sock)
            id, data = struct.unpack('I', message[:4])[0], message[4:]
            if id == CMD_DATA:
                os.write(child_pty, data)
            elif id == CMD_WINSZ:
                fcntl.ioctl(child_pty, termios.TIOCSWINSZ, data)
                os.kill(pid, signal.SIGWINCH)
    except PartialRead:
        print('FIN received')
    except Exception:
        traceback.print_exc()


def main():
    port = int(sys.argv[1])
    with open(sys.argv[2], 'rb') as f:
        password = f.read()
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.connect(('127.0.0.1', port))
        received_password = read_message(sock)
        if received_password != password:
            print("error: invalid password")
            sys.exit(1)

        child_args = [read_message(sock) for _ in range(4)]
        print("> " + child_args[0].decode())

        child_pid, child_pty = pty.fork()
        if child_pid == 0:
            sock.close()
            try:
                child(*child_args)
            except BaseException:
                traceback.print_exc()
            finally:
                sys._exit(1)
        else:
            with ThreadPoolExecutor(max_workers=2) as executor:
                executor.submit(pty_read_loop, child_pty, sock)
                executor.submit(sock_read_loop, sock, child_pty, child_pid)


if __name__ == '__main__':
    main()
