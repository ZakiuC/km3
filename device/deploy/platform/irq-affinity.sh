#!/usr/bin/env bash
# 把所有中断的亲和性压到 CPU0-3，让 CPU4-7 只跑实时控制。
# 见 device/deploy/platform/README.md。幂等，可重复执行。
#
# 需要在 root 下运行。仅影响 irq 亲和性，不改内核命令行。
set -euo pipefail

MASK="${1:-0f}"          # CPU0-3 的位掩码 0b1111 = 0x0f
RT_CPUS="4-7"

if [ "$(id -u)" -ne 0 ]; then
  echo "需要 root：sudo $0 [mask]" >&2
  exit 1
fi

echo "==> 校验隔离状态"
if [ -r /sys/devices/system/cpu/isolated ]; then
  iso="$(cat /sys/devices/system/cpu/isolated)"
  echo "    isolcpus 生效范围: ${iso:-<空，说明内核命令行未设置 isolcpus>}"
  echo "    目标实时核      : ${RT_CPUS}"
  [ -z "$iso" ] && echo "    !! 内核命令行尚未设置 isolcpus，本脚本只处理中断亲和性"
fi

echo "==> 设置中断亲和性为掩码 ${MASK}"
total=0; moved=0; skipped=0
for irq_dir in /proc/irq/[0-9]*; do
  total=$((total + 1))
  if [ -w "${irq_dir}/smp_affinity" ]; then
    # 已经是目标掩码则跳过，避免无谓写入
    cur="$(cat "${irq_dir}/smp_affinity" 2>/dev/null || echo '')"
    if [ "$cur" = "$MASK" ]; then
      skipped=$((skipped + 1))
      continue
    fi
    if echo "$MASK" > "${irq_dir}/smp_affinity" 2>/dev/null; then
      moved=$((moved + 1))
    fi
  fi
done

echo "    共 ${total} 个中断，调整 ${moved}，已是目标值 ${skipped}"

echo "==> 校验：列出仍落在 ${RT_CPUS} 上的中断（应当为空或极少）"
stray=0
for irq_dir in /proc/irq/[0-9]*; do
  aff="$(cat "${irq_dir}/smp_affinity_list" 2>/dev/null || true)"
  case ",$aff," in
    *,4,*|*,5,*|*,6,*|*,7,*) echo "    IRQ $(basename "$irq_dir") -> $aff"; stray=$((stray + 1)) ;;
  esac
done
[ "$stray" -eq 0 ] && echo "    无中断落在实时核上"

echo
echo "==> 提醒"
echo "    1) 这是运行时设置，重启会丢失。需要持久化时应由 systemd 单元在启动后调用本脚本。"
echo "    2) 网卡/USB 等新设备上线会带来新中断，需重新执行或由单元周期化。"
echo "    3) 必须用 cyclictest 在真实负载下验证最坏延迟，见 README.md。"
