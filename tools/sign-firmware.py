#!/usr/bin/env python3
"""固件离线签名。见 docs/架构设计/OTA升级方案.md §六。

私钥只存在于开发机，不进生产路径；服务器只分发、不持私钥。
轮换 = 一次 OTA 更新设备内公钥表。

依赖 PyNaCl（Ed25519）：
    pip3 install --user pynacl

用法：
    tools/sign-firmware.py --key ~/.km3/fw_signing.key \
        --in build/km3-firmware-1.3.0.tar.zst --out dist \
        --version 1.3.0 --model wcr --min-version 1.0.0 --kind full

产出：firmware.bin、firmware.sig、manifest.json
"""
import argparse
import hashlib
import json
import os
import shutil
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True, help="Ed25519 私钥文件（32 字节原始或 base64）")
    # --in / --version 仅在签名时必需；--genkey 只用来生成密钥对。
    # （早期版本把它们写成 required=True，导致 --genkey 也会因缺少 --in 而报错退出。）
    ap.add_argument("--in", dest="infile", help="待签名固件")
    ap.add_argument("--out", default="dist", help="输出目录")
    ap.add_argument("--version")
    ap.add_argument("--model", default="wcr")
    ap.add_argument("--min-version", default=None)
    ap.add_argument("--kind", default="full", choices=["full", "incremental"])
    ap.add_argument("--genkey", action="store_true", help="生成新密钥对后退出")
    args = ap.parse_args()

    try:
        import nacl.signing  # type: ignore
        import nacl.encoding  # type: ignore
    except ImportError:
        print("需要 PyNaCl：pip3 install --user pynacl", file=sys.stderr)
        return 2

    key_path = os.path.expanduser(args.key)

    if not args.genkey:
        missing = [n for n, v in (("--in", args.infile), ("--version", args.version)) if not v]
        if missing:
            ap.error("签名需要 " + " 和 ".join(missing) + "（或使用 --genkey 只生成密钥）")

    if args.genkey:
        sk = nacl.signing.SigningKey.generate()
        os.makedirs(os.path.dirname(key_path) or ".", exist_ok=True)
        with open(key_path, "wb") as fh:
            fh.write(sk.encode())
        os.chmod(key_path, 0o600)
        pub_path = key_path + ".pub"
        with open(pub_path, "wb") as fh:
            fh.write(sk.verify_key.encode())
        print(f"生成私钥 {key_path}（0600）")
        print(f"生成公钥 {pub_path} —— 需要写入设备公钥表")
        return 0

    with open(key_path, "rb") as fh:
        sk = nacl.signing.SigningKey(fh.read())

    in_path = os.path.expanduser(args.infile)
    with open(in_path, "rb") as fh:
        data = fh.read()

    digest = hashlib.sha256(data).hexdigest()
    signature = sk.sign(data).signature

    os.makedirs(args.out, exist_ok=True)
    bin_path = os.path.join(args.out, "firmware.bin")
    sig_path = os.path.join(args.out, "firmware.sig")
    manifest_path = os.path.join(args.out, "manifest.json")

    shutil.copyfile(in_path, bin_path)
    with open(sig_path, "wb") as fh:
        fh.write(signature)

    manifest = {
        "version": args.version,
        "model": args.model,
        "kind": args.kind,
        "size": len(data),
        "sha256": digest,
        "signature_b64": __import__("base64").b64encode(signature).decode(),
        "min_version": args.min_version,
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    print(f"sha256   {digest}")
    print(f"size     {len(data)}")
    print(f"输出     {bin_path} / {sig_path} / {manifest_path}")
    print("提醒：服务器只分发，不持私钥。manifest 需随固件一起入库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
