#!/usr/bin/env python3
"""Single fixed-board SSH PTY controller; explicit --launch required.

All operations remain terminal key bytes. No automatic authorization, recovery,
reconnection, client replacement, or action loop. EOF keeps the PTY open.
"""
import argparse
import datetime
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shlex
import hashlib
import struct
import subprocess
import sys
import termios
import time

sys.dont_write_bytecode=True
ROOT=Path(__file__).parent/'pty-source'
sys.path.insert(0,str(ROOT/'tools/hwtui'))
from verify_pmp4010_pty import Terminal

KEYS={'enter':'\r','esc':'\x1b','f2':'\x1bOQ','f5':'\x1b[15~','f6':'\x1b[17~',
      'home':'\x1bOH','delete':'\x1b[3~','up':'\x1bOA','down':'\x1bOB'}


REMOTE_WRAPPER='/tmp/km3-pp-disabled-start-stage3-recovery-20261019-0213/board_app.py'

def launch_command():
    wrapper=Path(__file__).with_name('board_app.py')
    digest=hashlib.sha256(wrapper.read_bytes()).hexdigest()
    # Verify exact reviewed observer bytes before importing/executing it. The
    # fixed payload manifest and every payload file are verified in the wrapper.
    code="import hashlib,pathlib,runpy,sys; p=pathlib.Path(sys.argv[1]); assert hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[2], 'wrapper hash mismatch'; sys.argv=[str(p),'--run-tui']; runpy.run_path(str(p),run_name='__main__')"
    remote=shlex.join(['env','LANG=C.UTF-8','LC_ALL=C.UTF-8','TERM=xterm','ESCDELAY=25','python3','-I','-B','-c',code,REMOTE_WRAPPER,digest])
    return ['ssh','-tt','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5','-o','ConnectionAttempts=1','root@192.168.0.188',remote]


def stamp():
    return dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),monotonic=time.monotonic())


class Session:
    def __init__(self,out):
        self.out=out
        out.mkdir(parents=True,exist_ok=False)
        self.terminal=Terminal(36,160)
        self.master,slave=pty.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',36,160,0,0))
        self.command=launch_command()
        self.raw=(out/'terminal.raw').open('xb')
        self.keys=(out/'keys.jsonl').open('x')
        env=dict(os.environ,LANG='C.UTF-8',LC_ALL='C.UTF-8',TERM='xterm',ESCDELAY='25')
        env.pop('LINES',None)
        env.pop('COLUMNS',None)
        (out/'command.json').write_text(json.dumps(dict(argv=self.command,cwd=str(out.resolve()),environment={k:env[k] for k in ('LANG','LC_ALL','TERM','ESCDELAY')},mode='fixed_board_ssh_pty',**stamp()),ensure_ascii=False,indent=2)+'\n')
        self.process=subprocess.Popen(self.command,stdin=slave,stdout=slave,stderr=slave,cwd=out.resolve(),env=env,start_new_session=True)
        os.close(slave)

    def read(self,duration=.1):
        deadline=time.monotonic()+min(max(duration,0),10)
        while time.monotonic()<deadline:
            remaining=deadline-time.monotonic()
            if not select.select([self.master],[],[],max(0,remaining))[0]:
                break
            try:
                data=os.read(self.master,65536)
            except OSError as exc:
                if exc.errno==errno.EIO:
                    break
                raise
            if not data:
                break
            self.raw.write(data)
            self.raw.flush()
            self.terminal.feed(data)
        return '\n'.join(self.terminal.lines())

    def send(self,text,label=None):
        if self.process.poll() is not None:
            raise RuntimeError('app already exited; no key sent')
        encoded=text.encode('utf-8')
        self.keys.write(json.dumps(dict(**stamp(),label=label or text,text=text,bytes_hex=encoded.hex()),ensure_ascii=False)+'\n')
        self.keys.flush()
        os.write(self.master,encoded)
        return self.read(.15)

    def capture(self,label):
        if not label or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in label):
            raise ValueError('capture label must be alphanumeric/_/-')
        self.read(.1)
        path=self.out/(label+'.screen.txt')
        path.write_text('\n'.join(self.terminal.lines())+'\n')
        meta=dict(**stamp(),label=label,unknown_escapes=self.terminal.unknown,child_rc=self.process.poll())
        (self.out/(label+'.screen.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
        return dict(path=str(path),**meta)

    def command_input(self,item):
        action=item['action']
        if action=='key':
            self.send(KEYS[item['name']],item['name'])
        elif action=='text':
            self.send(item['text'],'typed text')
        elif action=='replace':
            self.send(KEYS['home']+KEYS['delete']*128+str(item['text']),'replace active field')
        elif action=='wait':
            self.read(float(item.get('seconds',.2)))
        elif action=='capture':
            return self.capture(item['label'])
        elif action=='screen':
            self.read(.1)
            return dict(screen='\n'.join(self.terminal.lines()),child_rc=self.process.poll())
        elif action=='finish':
            # Finish is a deliberate ordinary UI quit. No forced signal/kill.
            self.send('q','q ordinary UI exit')
            deadline=time.monotonic()+10
            while self.process.poll() is None and time.monotonic()<deadline:
                self.read(.1)
            if self.process.poll() is None:
                return dict(finished=False,error='app still running; use UI and inspect cleanup; process was not killed')
            self.read(.1)
            return dict(finished=True,child_rc=self.process.returncode)
        else:
            raise ValueError('unsupported operator command')
        return dict(action=action,child_rc=self.process.poll())

    def close_files(self):
        self.read(.1)
        self.raw.close()
        self.keys.close()
        os.close(self.master)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--launch',action='store_true',help='start the reviewed fixed SSH command; external authorization required')
    args=parser.parse_args()
    if not args.launch:
        print(json.dumps(dict(mode='offline_command_plan',argv=launch_command(),actual_ssh_calls=0),ensure_ascii=False,indent=2))
        return 0
    session=Session(args.output_dir)
    print(json.dumps(dict(started=True,pid=session.process.pid,mode='fixed_board_ssh_pty',output_dir=str(args.output_dir)),ensure_ascii=False),flush=True)
    try:
        for line in sys.stdin:
            try:
                result=session.command_input(json.loads(line))
            except (ValueError,KeyError,RuntimeError) as exc:
                # Bad operator command must never tear down the active PTY.
                result=dict(operator_error=type(exc).__name__,message=str(exc),child_rc=session.process.poll())
            print(json.dumps(result,ensure_ascii=False),flush=True)
            if result.get('finished'):
                return int(result['child_rc']!=0)
        # Preserve the live SSH/app rather than implicitly SIGHUP via PTY close.
        # An active lost input channel requires operator attention, never retry.
        (session.out/'operator-input-ended.json').write_text(json.dumps(dict(**stamp(),child_rc=session.process.poll(),automatic_quit=False,automatic_retry=False))+'\n')
        while session.process.poll() is None:
            session.read(.1)
        return int(session.process.returncode!=0)
    finally:
        if session.process.poll() is not None:
            session.close_files()
            (session.out/'exit.json').write_text(json.dumps(dict(**stamp(),child_rc=session.process.returncode,forced_kill=False))+'\n')
        else:
            # Exceptional local controller failure must not close the PTY while
            # the original app may still hold an enabled-session cleanup duty.
            while session.process.poll() is None:
                session.read(.1)
            session.close_files()


if __name__=='__main__':
    sys.exit(main())
