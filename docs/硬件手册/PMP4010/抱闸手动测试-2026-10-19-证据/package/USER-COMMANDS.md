# 用户自行复制、解压和启动

最终离线包已包含诊断显示修订。以下复制、解压及启动命令由用户自行执行；本轮 agent 未执行任何复制到板端、SSH、CAN 或启动操作。

在开发机复制最终归档到测试板：

```sh
scp /home/zakiu/km3/docs/硬件手册/PMP4010/抱闸手动测试-2026-10-19-证据/package/pmp4010-brake-offline-payload.tar.gz root@192.168.0.188:/tmp/
```

用户进入板端交互终端后：

```sh
brake_test_dir=$(mktemp -d /tmp/pmp4010-brake-XXXXXXXX)
tar -xzf /tmp/pmp4010-brake-offline-payload.tar.gz -C "$brake_test_dir"
cd "$brake_test_dir"
./run-brake.sh
```

按 `1`/`e` 后 Enter 请求使能保持并松闸，按 `2`/`d` 立即请求失能抱闸；Space/`x` 停止，`q` 退出。保持独立急停可用；使能接通保持力矩，不能保证轴绝不调整，机械抱闸效果需听声确认。

启动器固定节点 1；使用原设备配置 can-2 逻辑总线。仓库配置 device/config/device.json 对应 can1、1 Mbps；本轮未访问板上实时配置，不覆盖设备配置。
