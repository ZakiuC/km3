"""Actual kernel test only inside an isolated user/network namespace.

Never run directly in the host namespace; requires explicit namespace marker.
Creates only vcan_probe in that isolated namespace and sends synthetic frames.
"""
import json
import socket
import struct
import subprocess
import sys


def main():
    assert sys.argv[1:] == ['--isolated-vcan-namespace']
    # The parent unshare command creates a new network namespace. A host interface
    # other than loopback is a hard refusal, before creating anything.
    links = json.loads(subprocess.check_output(['ip','-json','link','show']))
    assert all(link['ifname'] == 'lo' for link in links)
    subprocess.run(['ip','link','add','vcan_probe','type','vcan'],check=True)
    subprocess.run(['ip','link','set','vcan_probe','up'],check=True)
    raw=struct.pack('<IB3x8s',0x581,8,bytes.fromhex('4b416000c0020000'))
    results=[]
    for mask in (0xFFFFFFFF,0xC00007FF):
        with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as receiver, \
             socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sender:
            receiver.setsockopt(101,1,struct.pack('=II',0x581,mask))
            receiver.setsockopt(101,2,struct.pack('=I',0x1FFFFFFF))
            receiver.bind(('vcan_probe',))
            sender.bind(('vcan_probe',))
            receiver.settimeout(.1)
            assert sender.send(raw)==16
            try: received=receiver.recv(17)
            except socket.timeout: received=None
            assert (received==raw)==(mask==0xC00007FF)
            results.append(dict(mask=f'{mask:08X}',received_hex=None if received is None else received.hex()))
    print(json.dumps(dict(result='PASS',kernel_vcan_test=True,results=results,physical_CAN_opens=0)))


if __name__=='__main__':
    main()
