#!/usr/bin/env bash
# 设备端置备：创建业务用户、目录、数据盘挂载、udev 规则、systemd 单元。
# 幂等，可重复执行。**默认 dry-run**，必须显式 --apply 才会改动系统。
#
# 依据：docs/架构设计/系统总体架构.md §9.3、设备端程序架构.md §5。
# 背景（2026-10-13 实测）：/opt/km3、/etc/km3、/data 均不存在；
# km3 用户不存在；data 盘当前挂在 /run/media/sda1（桌面自动挂载，不可作持久契约）。
set -euo pipefail

HOST=""
APPLY=0
DATA_UUID=""

usage() {
  cat <<EOF
usage: $0 --host <ip> [--data-uuid <uuid>] [--apply]

默认只打印将要执行的动作（dry-run）。加 --apply 才真正改动设备。

  --data-uuid   外接数据盘的 UUID。省略时脚本会打印当前块设备 UUID 供选择。
                必须显式提供，避免把错误的盘当成数据盘格式化/挂载。

前置条件：
  - /etc/km3、/opt/km3 由本脚本创建
  - 部署二进制请用 tools/deploy_device.sh，本脚本不传二进制
EOF
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host)      HOST="$2"; shift 2 ;;
    --data-uuid) DATA_UUID="$2"; shift 2 ;;
    --apply)     APPLY=1; shift ;;
    -h|--help)   usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done
[ -n "$HOST" ] || usage

SSH_OPTS=(-o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)
REMOTE="root@${HOST}"

# 把待执行脚本整体推到远端再跑，避免逐条引号转义出错。
# 远端脚本经**引号 here-doc**（<<'REMOTE_EOF'）传递，本地不做任何展开。
#
# 为什么必须引号：未加引号的 here-doc 会在本地展开 $、$(...) 与反引号，
# 且**不认注释语法**——注释里的 \`VAR=$(cmd)\` 也会被真的执行。
# 首次实测的表现是 "cmd: 未找到命令" 加一串 "UUID: 未绑定的变量"，
# 而脚本看起来完全正常。引号 here-doc 从结构上消除这类缺陷，
# 代价是需要注入的值改由环境变量传。
#
# 传入的 UUID 通过远端环境变量 KM3_DATA_UUID 交给远端脚本。
remote_script() {
  if [ "$APPLY" -eq 1 ]; then
    ssh "${SSH_OPTS[@]}" "$REMOTE" \
      "KM3_DATA_UUID='${DATA_UUID}' KM3_HOST='${HOST}' bash -s"
  else
    echo "---- [dry-run] 以下内容将写入远程并执行 ----"
    echo "---- [dry-run] 远端环境：KM3_DATA_UUID='${DATA_UUID}' ----"
    cat
    echo "---- [dry-run] 结束 ----"
  fi
}

echo "==> 目标 ${REMOTE}  ($([ "$APPLY" -eq 1 ] && echo APPLY || echo DRY-RUN))"
echo

# 先看现状，帮助确认数据盘 UUID
ssh "${SSH_OPTS[@]}" "$REMOTE" \
  "echo '--- 当前块设备与 UUID ---'; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,UUID | grep -Ev 'mmcblk0boot'; echo; echo '--- 当前 /data 状态 ---'; findmnt -n /data || echo '(未挂载)'"
echo

if [ -z "$DATA_UUID" ]; then
  echo "!! 未提供 --data-uuid。请从上面的 UUID 列选择数据盘（当前 SSD 挂在 /run/media/sda1）。"
  echo "   确认后重新执行：$0 --host $HOST --data-uuid <UUID> $([ "$APPLY" -eq 1 ] && echo --apply)"
  exit 0
fi

# ---- 先上传单元与 udev 规则到 /tmp，再由远端脚本安装到位 ----
#
# 为什么必须有这一步：本脚本头部注释声称负责「udev 规则、systemd 单元」，
# 但早期版本的第 4 步只 echo 一句「规则文件由部署脚本一并上传」，
# 而 deploy_device.sh 里并没有上传单元的代码 —— 两个脚本互相指望对方，
# 实际没人安装。结果是部署脚本重启 km3-core 时单元根本不存在，必然失败。
UNITS=(km3-can.service km3-core.service km3-rt-controller.service km3-video.service)
for u in "${UNITS[@]}"; do
  [ -f "device/deploy/systemd/${u}" ] || { echo "缺少 device/deploy/systemd/${u}" >&2; exit 1; }
done
[ -f "device/deploy/70-km3-serial.rules" ] || { echo "缺少 device/deploy/70-km3-serial.rules" >&2; exit 1; }

if [ "$APPLY" -eq 1 ]; then
  echo "==> 上传 systemd 单元与 udev 规则"
  ssh "${SSH_OPTS[@]}" "$REMOTE" 'mkdir -p /tmp/km3-deploy/systemd'
  scp "${SSH_OPTS[@]}" device/deploy/systemd/*.service \
      "${REMOTE}:/tmp/km3-deploy/systemd/"
  scp "${SSH_OPTS[@]}" device/deploy/70-km3-serial.rules \
      "${REMOTE}:/tmp/km3-deploy/"
else
  echo "[dry-run] 将上传：device/deploy/systemd/*.service -> ${REMOTE}:/tmp/km3-deploy/systemd/"
  echo "[dry-run] 将上传：device/deploy/70-km3-serial.rules -> ${REMOTE}:/tmp/km3-deploy/"
fi
echo

remote_script <<'REMOTE_EOF'
set -e
UUID="${KM3_DATA_UUID:?需要由远端环境提供 KM3_DATA_UUID}"

echo "==> 1. 业务用户（不以 root 运行）"
if id km3 >/dev/null 2>&1; then
  echo "    已存在 km3，跳过"
else
  useradd --system --create-home --shell /usr/sbin/nologin km3
  echo "    已创建 km3"
fi
# 串口/CAN 访问通过组权限，见 udev 规则
for g in dialout km3; do getent group "$g" >/dev/null || groupadd "$g"; done
usermod -aG dialout km3 2>/dev/null || true

echo "==> 2. 目录"
install -d -o km3 -g km3 -m 0755 /opt/km3 /opt/km3/releases
install -d -o km3 -g km3 -m 0755 /etc/km3
# /data 此处**只建挂载点本身**。三个子目录必须在挂载之后创建：
# 提前建出来会被随后挂上的文件系统整个遮住，表现为"明明建过却不存在"。
# 首次置备正是这样留下了假象。
install -d -m 0755 /data

echo "==> 3. 数据盘按 UUID 稳定挂载"
# FSTYPE 探测必须用 -t 形式。实测（TL3588 板载 util-linux 2.37.2）：
#   blkid -s TYPE -o value "UUID=xxx"   ->  rc=2，**无任何输出**
#   裸的 UUID=xxx 会被 blkid 当成**设备路径**去 stat，找不到即报错；
#   按 token 检索要显式给 -t：blkid -o value -s TYPE -t "UUID=xxx"
#
# 更要紧的是：本脚本远端部分跑在 set -e 下，而 `VAR=$(cmd)` 的命令替换失败
# **会直接终止整个脚本**。所以上面那个 rc=2 不只是"取不到值"，
# 而是让置备停在第 3 步——fstab 一行没写、备份也没有、后续 4/5/6 步全部未执行。
# 因此这里一律用 `|| true` 兜住，再由显式判空给出人能看懂的报错。
FSTYPE="$(blkid -o value -s TYPE -t "UUID=$UUID" 2>/dev/null || true)"
[ -n "$FSTYPE" ] || { echo "    找不到 UUID=$UUID 的文件系统类型（设备未接或未分区）" >&2; exit 1; }
echo "    文件系统类型：$FSTYPE"

# 数据盘可能已被桌面环境（udisks2）自动挂到 /run/media/<name>。
# 同一设备不能同时挂两处，必须先卸下来，否则下面的 mount /data 必然失败。
CUR_TARGET="$(findmnt -n -o TARGET --source "/dev/disk/by-uuid/$UUID" 2>/dev/null || true)"
if [ -n "$CUR_TARGET" ] && [ "$CUR_TARGET" != "/data" ]; then
  echo "    当前挂在 $CUR_TARGET（桌面自动挂载），先卸载"
  umount "$CUR_TARGET" || {
    echo "    卸载失败：有进程正在使用该盘，请先关闭占用后重跑" >&2; exit 1; }
fi

if grep -q "$UUID" /etc/fstab; then
  echo "    fstab 已有该 UUID，跳过"
else
  cp /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d%H%M%S)"
  echo "UUID=$UUID  /data  $FSTYPE  defaults,nofail,noatime,x-systemd.device-timeout=10  0  2" >> /etc/fstab
  echo "    已写入 fstab（已备份原文件）"
fi
systemctl daemon-reload

mountpoint -q /data || mount /data
# 挂载后**再确认一次**：若这里仍是普通目录，后续所有写入都会落进根分区，
# 而根分区只剩 4GiB。宁可在此失败，也不要静默写满根分区。
mountpoint -q /data || { echo "    /data 仍未挂载，拒绝继续" >&2; exit 1; }
echo "    /data 挂载状态：$(findmnt -n -o SOURCE,FSTYPE /data)"

install -d -o km3 -g km3 -m 0755 /data/logs /data/recordings /data/ota
chown km3:km3 /data
echo "    子目录已建在数据盘上：logs / recordings / ota"

echo "==> 4. udev 规则（串口权限按设备身份授予）"
# 按设备身份授予权限，而不是 chmod 666 /dev/ttyS*：后者会让任何进程都能占用串口。
if [ -f /tmp/km3-deploy/70-km3-serial.rules ]; then
  install -o root -g root -m 0644 /tmp/km3-deploy/70-km3-serial.rules \
    /etc/udev/rules.d/70-km3-serial.rules
  udevadm control --reload-rules
  udevadm trigger --subsystem-match=tty || true
  echo "    已安装 70-km3-serial.rules 并重载 udev"
else
  echo "    !! /tmp/km3-deploy/70-km3-serial.rules 不存在，跳过（用 --apply 才会上传）"
fi

echo "==> 5. CAN 环境文件（速率是部署参数）"
[ -f /etc/km3/can.env ] || echo "CAN_BITRATE=500000" > /etc/km3/can.env
# km3-can.service 通过 EnvironmentFile=/etc/km3/can.env 读取，不写死在单元里。
chown km3:km3 /etc/km3/can.env
chmod 0640 /etc/km3/can.env
echo "    /etc/km3/can.env 就位（CAN_BITRATE 需按现场总线确认）"

echo "==> 6. systemd 单元"
if [ -d /tmp/km3-deploy/systemd ]; then
  install -o root -g root -m 0644 /tmp/km3-deploy/systemd/*.service /etc/systemd/system/
  systemctl daemon-reload
  echo "    已安装 4 个单元：km3-can / km3-core / km3-rt-controller / km3-video"
  # 只 enable，不 start：二进制尚未部署，现在启动必然失败。
  # 真正的启动由 deploy_device.sh 的 systemctl restart 完成。
  for u in km3-can.service km3-core.service km3-rt-controller.service km3-video.service; do
    systemctl enable "$u" >/dev/null 2>&1 && echo "    已 enable $u"
  done
  # can.service 是 Before= 其它 km3 单元的前置，需要先起
  systemctl start km3-can.service 2>/dev/null || \
    echo "    (km3-can.service 未启动：CAN 设备树与速率需现场确认，见设备端文档 §5.2)"
else
  echo "    !! /tmp/km3-deploy/systemd 不存在，跳过（用 --apply 才会上传）"
fi

echo
echo "==> 置备完成。下一步："
echo "    tools/deploy_device.sh --host ${KM3_HOST} --version <semver>"
echo "    内核命令行 isolcpus 与 IRQ 亲和性另见 device/deploy/platform/README.md"
REMOTE_EOF
