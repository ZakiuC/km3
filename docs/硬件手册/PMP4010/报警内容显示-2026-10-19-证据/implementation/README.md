# 报警内容显示实现证据

本地离线实现与MockBus验证，无SSH、CAN、硬件、部署或提交操作。

- 映射来源：本地E06手册§9.1表9-1，50表行、49唯一603F键；0x3285同义重复。逐字保留中文，去除尾部*和_脚注标记。没有使用默认故障反应码或自复位列推断实际配置。
- 显示：0x2312（电机堵转）；None为未知；0为0x0000（无错误码）；未知值保留完整hex并显示未知报警，超16位不掩码。
- JSONL：原fault_code数值保留，fault_code_hex保留/补齐，追加fault_description。失败样本中未读到603F时三字段为null/null/未知，未使用旧遥测填充。
- UI：详情独立报警行；80x24总览优先完整码义，其他遥测摘要按节点/报警列及分隔符所占宽度后的剩余空间扩展，详情保留完整字段。simple实时/历史/首次失败样本显示中文内容。
- 保护与采样：仅新增纯映射和文本/元数据，不新增OD读取，不修改guard条件、SDO次序、snapshot_serial或partial证据归属。

最终验证：在device/drivers/pmp4010/tools执行`python3 -m unittest -v _test_faults _test_bench _test_manual _test_simple _test_tui`，167 tests，rc=0，OK。原始输出见affected-tests.log；argv/cwd/耗时/rc/文件SHA见verification.json。首轮测试构造误用只读rows属性导致1 error，修正为_put；首轮原始输出和记录保留在affected-tests-attempt1.log与verification-attempt1.json。

检查覆盖手册完整映射、known/unknown/None/0/超16位、全部码义在80x24详情/总览可见、160列完整位置/速度/模式可见、四席0x2312、simple实时与历史显示、JSONL与异常/保护提示、603F读取失败不借旧值，以及既有动作和读取顺序回归。源码基线见baseline/，本轮差异见changes.patch，最终源码哈希见SHA256SUMS。未执行完整Python全量、native或板端验收，交由主线独立集成与打包。

追加审查已收口：宽终端动态扩展摘要；同一受影响suite重跑167 tests、rc=0，源码已冻结。0x2312-manual-mock-80x24.txt为既有MockBus构造生成的80x24手动界面文本，非硬件现场截图。

引用门收口：仅pmp4010_faults.py的49条映射行追加E06 §9.1表9-1同行注释，AST（忽略位置）相等，其他8个源码SHA不变。引用门rc=0、_test_faults 2 tests rc=0；原独立全量及引用门失败记录均保留。新freeze以verification.json与SHA256SUMS为准。
