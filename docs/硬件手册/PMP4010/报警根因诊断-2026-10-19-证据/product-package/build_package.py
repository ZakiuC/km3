#!/usr/bin/env python3
"""Offline package builder; requires a separately reviewed frozen-source input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[4]
BASE = REPO / 'docs/硬件手册/PMP4010/报警内容显示-2026-10-19-证据/package'
OLD_ARCHIVE_SHA = '7355389782042fdc3baa7d1ddc114cc2e11df14fa304293f8b1683037bde1d72'
OLD_MANIFEST_SHA = '5072e207253957356b28f03d989efa3b798efa85b240e196060d6eb40495e40b'
ARM_SHA = '59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404'
REPLACEMENTS = {
    'drivers/pmp4010/pmp4010_manual.py': 'device/drivers/pmp4010/tools/pmp4010_manual.py',
    'drivers/pmp4010/pmp4010_simple.py': 'device/drivers/pmp4010/tools/pmp4010_simple.py',
}
README = '''# PMP4010 手动复位离线候选包（2026-10-19）

本包包含本地手动复位与退出关闭修复。只完成离线加载、Mock/PTY 验收；板端 ARM 加载、复位和运动验收待完成。当前物理堵转原因仍未解决，不能据此恢复运行。

在获得单独现场授权后才可启动 ./run-manual.sh。节点 1、can-2 配置解析到原 can1；沿用旧配置解析器。每次依界面确认 RT 与 OD，启动或扫描不自动使能。

t 打开手动复位确认，再按 Enter 执行；仅针对本轮已验证的 Warning 2312 路径，不是通用故障复位。不会自动复位，不将复位与运动绑定。其他报警、未知状态或通信异常应保留诊断并停止处理；复位成功也不能证明机械问题消失。

默认电机一圈 4 mm；c 或 --manual-mm-per-rev 可修改该比例。若输出轴一圈前进 L 毫米、外部减速比 i 为电机转数/输出转数，输入 L/i。
z 把当前位置记为本会话软件零点，不回零、不写 607C；r 输入相对位移 mm，p 输入相对软件零点的绝对位置 mm。
a 设置点动 mm、s 设置电机 rpm、f/b 后 Enter 确认正反点动；e 后 Enter 保持当前位置使能。
Space/x 停止，d 失能，q 退出；不会自动寻找机械端点。驱动自身 607D 限位及 Int32、607C、故障、通信、停止和 OD 守卫仍有效。

日志保存在包内 logs/manual-XXXXXXXX/；本包不修改 current、服务或网络配置。原有运动和复位证据未更改。manifest 只记录本轮冻结输入，不沿用旧包验收或审批结论。

仅离线宿主机冷加载（原生库只通过现有 --library 参数替换）：
python3 -I -B ./load_smoke.py --library /home/zakiu/km3/build-host/libpmp4010_velocity.so --check-missing-library
此检查禁止 socket 与子进程操作，19 模块导入、ABI 与向量检查不等于 ARM 或硬件验收。不要在 x86 主机执行包内 AArch64 库。
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok, text):
    if not ok:
        raise RuntimeError(text)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def run(name, command, expected=0):
    result = subprocess.run(command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (ROOT / (name + '.stdout.log')).write_bytes(result.stdout)
    (ROOT / (name + '.stderr.log')).write_bytes(result.stderr)
    COMMANDS.append({'name': name, 'argv': command, 'cwd': str(REPO), 'returncode': result.returncode})
    write_json(ROOT / 'command-index.json', COMMANDS)
    require(result.returncode == expected, name + ' failed; see raw logs')
    return result.stdout.decode()


def check_freeze(freeze):
    hashes = freeze['source_hashes']
    require(all(source in hashes for source in REPLACEMENTS.values()), 'two changed source hashes required')
    allowed = set(REPLACEMENTS.values()) | {
        'device/drivers/pmp4010/tools/_test_manual.py',
        'device/drivers/pmp4010/tools/_test_simple.py',
        'device/drivers/pmp4010/tools/_test_bench.py',
    }
    require(set(hashes) <= allowed, 'freeze source paths exceed explicit scope')
    for source, expected in hashes.items():
        require(sha(REPO / source) == expected, 'frozen source changed: ' + source)
    acceptance = freeze['local_acceptance']
    require(acceptance.get('status') == 'passed' and acceptance.get('evidence'), 'parent local acceptance evidence required')


def inventory(directory, files):
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file() or p.is_symlink()}
    require(actual == {x['path'] for x in files} | {'payload-manifest.json'}, 'unexpected/missing payload file')
    for item in files:
        path = directory / item['path']
        if 'symlink' in item:
            require(path.is_symlink() and os.readlink(path) == item['symlink'], 'symlink mismatch')
        else:
            require(not path.is_symlink() and sha(path) == item['sha256'], 'file SHA mismatch: ' + str(path))
    require(sum('symlink' not in x for x in files) + 1 == 23 and sum('symlink' in x for x in files) == 2, 'inventory must be 23 files + 2 symlinks')


COMMANDS = []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-input', required=True, type=Path, help='JSON with source_hashes and parent local_acceptance status/evidence')
    args = parser.parse_args()
    require(REPO == Path('/home/zakiu/km3'), 'unexpected checkout root')
    freeze_path = args.freeze_input.resolve(strict=True)
    freeze = json.loads(freeze_path.read_text())
    check_freeze(freeze)
    require(sha(BASE / 'pmp4010-manual-board-payload.tar.gz') == OLD_ARCHIVE_SHA, 'old archive changed')
    require(sha(BASE / 'payload/payload-manifest.json') == OLD_MANIFEST_SHA, 'old manifest changed')
    old = json.loads((BASE / 'payload/payload-manifest.json').read_text())
    inventory(BASE / 'payload', old['files'])
    for item in old['files']:
        relative = Path(item['path'])
        require(not relative.is_absolute() and '..' not in relative.parts, 'unsafe manifest path')
    payload, roundtrip = ROOT / 'payload', ROOT / 'roundtrip'
    require(not payload.exists() and not roundtrip.exists(), 'output exists; preserve it and choose a fresh version manually')
    payload.mkdir()
    new_files = []
    for item in old['files']:
        relative = item['path']
        destination = payload / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        record = {'path': relative}
        if 'symlink' in item:
            destination.symlink_to(item['symlink'])
            record['symlink'] = item['symlink']
        else:
            if relative in REPLACEMENTS:
                shutil.copy2(REPO / REPLACEMENTS[relative], destination)
                record['origin'] = REPLACEMENTS[relative]
                record['provenance'] = 'parent-frozen local manual reset/close fix'
            elif relative == 'README-手动入口.md':
                destination.write_text(README)
                record['provenance'] = 'current offline candidate instructions'
            else:
                shutil.copy2(BASE / 'payload' / relative, destination)
                record['provenance'] = 'unchanged trusted old package'
            record.update(size=destination.stat().st_size, sha256=sha(destination))
        new_files.append(record)
    head = run('source-head', ['git', 'rev-parse', 'HEAD']).strip()
    manifest = {'stage': 'warning2312_manual_reset_close_local_2026_10_19', 'source_head': head,
                'freeze_input_sha256': sha(freeze_path), 'source_freeze': freeze['source_hashes'],
                'local_acceptance': freeze['local_acceptance'], 'baseline_archive_sha256': OLD_ARCHIVE_SHA,
                'baseline_manifest_sha256': OLD_MANIFEST_SHA, 'files': new_files,
                'physical_stall_resolved': False, 'hardware_acceptance': False, 'board_arm_load': 'pending',
                'board_reset_acceptance': 'pending', 'motion_acceptance': 'pending'}
    write_json(payload / 'payload-manifest.json', manifest)
    inventory(payload, new_files)
    require(sha(payload / 'drivers/pmp4010/libpmp4010_velocity.so.1.0.0') == ARM_SHA, 'ARM library drift')
    run('launcher-shell-syntax', ['sh', '-n', str(payload / 'run-manual.sh')])
    run('launcher-help', ['sh', str(payload / 'run-manual.sh'), '--help'])
    # Help creates only a log directory. Keep archive strictly manifest-listed.
    smoke_command = [sys.executable, '-I', '-B', str(payload / 'load_smoke.py'), '--library', str(REPO / 'build-host/libpmp4010_velocity.so'), '--check-missing-library']
    output = run('host-load-smoke', smoke_command)
    stats = json.loads(output.strip().splitlines()[-1])
    require(stats.get('native_vectors') == 33 and stats.get('python_vectors') == 33 and stats.get('native_int32_vectors') == 14, 'vector totals mismatch')
    require(output.count('import=') == 19 and '"forbidden_io_attempts": 0' in output and '"result": "PASS"' in output, 'cold loader did not establish required checks')
    run('arm-file', ['file', str(payload / 'drivers/pmp4010/libpmp4010_velocity.so.1.0.0')])
    run('arm-elf-header', ['readelf', '-h', str(payload / 'drivers/pmp4010/libpmp4010_velocity.so.1.0.0')])
    check_freeze(freeze)
    archive = ROOT / 'pmp4010-manual-reset-offline-payload.tar.gz'
    members = [x['path'] for x in new_files] + ['payload-manifest.json']
    with tarfile.open(archive, 'w:gz', dereference=False) as tar:
        for name in members:
            tar.add(payload / name, arcname=name, recursive=False)
    roundtrip.mkdir()
    with tarfile.open(archive) as tar:
        require(len(tar.getmembers()) == 25, 'archive member count mismatch')
        tar.extractall(roundtrip, filter='data')
    inventory(roundtrip, new_files)
    require(sha(roundtrip / 'payload-manifest.json') == sha(payload / 'payload-manifest.json'), 'roundtrip manifest drift')
    output = run('roundtrip-smoke', [sys.executable, '-I', '-B', str(roundtrip / 'load_smoke.py'), '--library', str(REPO / 'build-host/libpmp4010_velocity.so'), '--check-missing-library'])
    require(output.count('import=') == 19 and json.loads(output.strip().splitlines()[-1]).get('result') == 'PASS', 'roundtrip coldload mismatch')
    run('archive-list', ['tar', '-tzf', str(archive)])
    check_freeze(freeze)
    shutil.copy2(freeze_path, ROOT / 'source-freeze-input.json')
    write_json(ROOT / 'source-manifest.json', manifest)
    summary = {'archive': str(archive), 'archive_sha256': sha(archive), 'archive_bytes': archive.stat().st_size,
               'manifest_sha256': sha(payload / 'payload-manifest.json'), 'regular_files': 23, 'symlinks': 2,
               'imports': 19, 'native_vectors': 33, 'python_vectors': 33, 'native_int32_vectors': 14,
               'forbidden_io_attempts': 0, 'arm_library_sha256': ARM_SHA,
               'arm_execution': False, 'hardware_acceptance': False, 'board_arm_load': 'pending',
               'board_reset_acceptance': 'pending', 'motion_acceptance': 'pending', 'physical_stall_resolved': False}
    write_json(ROOT / 'archive-manifest.json', {'archive': archive.name, 'sha256': sha(archive),
               'bytes': archive.stat().st_size, 'members': new_files + [{'path': 'payload-manifest.json',
               'size': (payload / 'payload-manifest.json').stat().st_size, 'sha256': sha(payload / 'payload-manifest.json')}]})
    write_json(ROOT / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
