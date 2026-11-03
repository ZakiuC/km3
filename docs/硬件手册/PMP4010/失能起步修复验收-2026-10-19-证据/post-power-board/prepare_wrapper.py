from pathlib import Path
import difflib
b=Path(__file__).resolve().parent;prior=b.parent/'stage2-board-final'
s=(prior/'board_app.py').read_text()
s=s.replace('/tmp/km3-pp-disabled-start-stage2-20261019-e0fhd4gp','/tmp/km3-pp-disabled-start-stage3-recovery-20261019-0213')
s=s.replace('5b1c1e1aae87afa8f239bd8c5b8231b8c1b604063cbd4a76f3b27569d2fd18cc','280884d0e1cf519675a590aa053dc16419d0266555683f61d93fcef6bcce0c5c')
s=s.replace('1aa97f73db9299d950bfa7e7b4b701f06998b3cdfd99c0c18d8dc7b251bf270f','b70396b228cb594793b44ad6d6f4dfc65a0167fd0efd90325d8feec276f69c5e')
s=s.replace("OUTPUT=PAYLOAD.parent/'probe-session-1'", "OUTPUT=PAYLOAD.parent/'move-session-1'")
start=s.index('AUTHORIZATION_CONTEXT=');end=s.index('\n',start)
s=s[:start]+"AUTHORIZATION_CONTEXT='断电后新会话：原RT/F2软限起点±36U及budget36/OD均键入；Ready先原D两段确认到strictdisabled，再R+36两段确认。每次motion必须全新106/107/127/137/127预装当前目标及独立freshACK/6062、12F Halt使能五次严格零漂移、2F清Halt仍原点零速ACK0、final607A/readback、3F新finalACK及2F新ACK0/非Haltbit10/目标实际位置/6062/零速，最后2/0；全部15秒同client，失败隔离不重试，q另记Shutdown6。D/R冻结页分别待主线程批准；断电前seal与target proof全部无效。'"+s[end:]
# Tie the live observer to the new measured boot and unchanged passive RT identity.
s=s.replace("    assert root==PAYLOAD and root.resolve(strict=True)==PAYLOAD\n", "    assert root==PAYLOAD and root.resolve(strict=True)==PAYLOAD\n    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()=='bacd8411-f822-4f19-b7cb-8bcbf755b54a','boot changed; new passive acceptance required'\n    rt_cmdline=Path('/proc/558/cmdline').read_bytes().replace(b'\\0',b' ').decode()\n    assert rt_cmdline=='/opt/km3/current/km3-rt-controller --config /etc/km3/device.json --require-config ','RT process changed'\n    assert set(p.name for p in Path('/proc/558/fd').iterdir())=={'0','1','2'},'RT descriptors changed'\n    for p in Path('/proc/net/can').glob('rcvlist*'):\n        assert all(not line.strip() or line.startswith(\"receive list\") or 'no entry' in line for line in p.read_text().splitlines()),'CAN receiver present'\n")
(b/'board_app.py').write_text(s)
s=(prior/'board_pty_runner.py').read_text().replace('/tmp/km3-pp-disabled-start-stage2-20261019-e0fhd4gp','/tmp/km3-pp-disabled-start-stage3-recovery-20261019-0213')
(b/'board_pty_runner.py').write_text(s)
for name in ('board_app.py','board_pty_runner.py'):
 (b/(name+'.diff')).write_text(''.join(difflib.unified_diff((prior/name).read_text().splitlines(True),(b/name).read_text().splitlines(True),fromfile='stage2/'+name,tofile='post-power/'+name)))
print('Prepared only; zero SSH/CAN calls')
