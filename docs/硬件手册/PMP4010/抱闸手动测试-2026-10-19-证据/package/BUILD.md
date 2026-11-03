# 抱闸手动测试离线打包

最终包按主会话确认的诊断显示修订源码冻结与 22 项专项验收构建。初版候选及完整日志保存在 candidate-before-diagnostic-refresh/。旧 ca634a 归档及其原始证据保持不变。

`freeze-input.json` 必须包含：

```json
{
  "source_hashes": {
    "device/drivers/pmp4010/tools/pmp4010_brake.py": "新模块最终 SHA256",
    "device/drivers/pmp4010/tools/_test_brake.py": "测试最终 SHA256"
  },
  "local_acceptance": {
    "status": "passed",
    "evidence": "主会话确认的本轮原始验收记录路径"
  }
}
```

取得冻结输入后，从 `/home/zakiu/km3` 执行：

```sh
python3 -I -B docs/硬件手册/PMP4010/抱闸手动测试-2026-10-19-证据/package/build_package.py --freeze-input docs/硬件手册/PMP4010/抱闸手动测试-2026-10-19-证据/package/freeze-input.json
```

输出名为 `pmp4010-brake-offline-payload.tar.gz`，最终 SHA256 见 summary.json 与 archive-manifest.json。归档由可信旧清单逐项复制，新增 brake 模块、run-brake.sh、中文 README；旧 loader 只在既有 I/O 审计下增加一项 brake 导入，单独记录前后哈希。测试源码不入包。预计普通文件 26 个、符号链接 2 个。

基线归档 SHA256：`ca634a034cc538a5a0eee819404404da89a950a803a1b4a6d29c9aabd85e5ad2`；基线清单 SHA256：`0922f0ecacd08f856b2c213e5c737e78fa41c005917f8584c2ad7d5d85f5d3f0`。

manual.py 和 simple.py 保持当前 ca37ecb…110b9 / d1d277…66f5 冻结内容；AArch64 库保持 SHA256 `59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404`。构建器不执行 ARM、不连接板端、不部署或更改 current/服务。

原始命令、返回码、stdout/stderr 及归档成员哈希写入本目录。检查范围为 shell 语法、帮助、无 TTY 拒绝、参数拒绝、带禁止 I/O 审计的 20 模块冷加载、ABI/换算向量和 tar 解压逐文件回读。旧目录不被写入；输出已存在时拒绝覆盖。

包内启动命令与真实抱闸风险见 README-抱闸测试.md。只在单独获准的现场环境下由人类解压并执行 `./run-brake.sh`；本地准备与打包不代表现场授权或机械抱闸验收。
