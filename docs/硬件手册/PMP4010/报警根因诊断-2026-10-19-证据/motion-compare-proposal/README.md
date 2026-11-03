本目录仅完成本地准备；没有访问板端、SSH、CAN、抱闸、PWM，也没有现场执行授权。根审后如决定执行，应单独派执行任务，不得直接重跑旧0218脚本。

- `motion_compare_once.py`：默认只打印计划。唯一动作是原用户要求的相对-10mm、100rpm一次；先将失能RAM内6083/6084从各5000000逐项降为131072并读回，不Save、不自动回滚、不回程。
- `pinned_payload.py`：冷启动校验7355 manifest及所有成员、ARM库、模块真实来源；现场固定TL3588型号、boot/config/env/current、被动快照的三个服务PID/启动时刻/状态、RT PID600的原fd及空CAN接收表。并不要求已批准的RT服务退出，也不操作服务。
- `verify_mock.py`：固定7355 Python+原标准MockBus，26项检查；只为x86使用已有host C ABI速度库，未替换速度换算或运动协议。audit禁止创建真实socket/子进程。原始日志和每个案例TX在`mock-results.json`指向的目录。
- `run_once_stdin.py`：拟在同7355 stage的`motion-compare-proposal/`读取源文件，先核验两个运行文件SHA，再运行；必须经stdin启动，避免父shell命令行中的脚本名被占用检查识别。

本次与先前报警的+10mm相比，方向和加减速度都变了，不是单因素实验。成功只能说明这一次方向和配置下的结果；不能据此确认高加速度是2312根因。相反方向端点/阻力、抱闸或其他原因仍须证据。禁止自动追加+10mm或恢复5m试验。4mm/电机rev、R=131072、G=1对应冻结delta=-327680U，6081=218453U/s；900°行程在131072U/s²对称加减速度下理论三角峰值约94.87rpm、总时长约3.16s，未到100rpm本身不判失败。

原`SimpleController.review_action -> execute_action`及内部`ManualController`提交、Halt使能、Go新ACK流程不变。原target会写两次607A，必须一致；607C非0拒绝、设备607D/Int32/比例、OD会话确认、静止检查、隔离不重试保留。每次a/d写前还要求本项仍为5000000，防止覆盖外部变更。参数失败可留下部分低a/d RAM，不盲目恢复高值；这不是已经确定的修复。

诊断使用原Journal的observer：原enable成功回调中，最多0.5s做一批16读；原Go握手不插入SDO，首次002F写前开始10s观察（包括Go握手）。Go成功后最多20批、起点间隔至少0.5s；第一批16读，后续每批12读，额外诊断总上限260读。每批都以6041/603F/1001健康读前后夹住60FC(I32)、606B(I32)、6069(I32)、60F4(I32)、6077(I16)、6078(I16)。200B:12/13/14/15(U16)只在Halt和Go首批读。所有请求仍经同一个原ManualClient；每请求最多250ms，每批最多0.5s，记录每个raw值、UTC、monotonic及请求起止时间；样本串行且不同时。60FC为编码器单位，6064/60F4为用户单位，606B/6069为用户单位/s，不能默认相等；本机实读G=1才具备比较尺度的条件。6077/6078保留raw，未据此标定实际轴转矩或电流。

任一已观察报警/异常立即退出，不为了日志补读；任何在途不确定由原SDO隔离，禁止更换client/重连/清队列/重试，连停止写也不追加。逻辑观察超时且通信仍确定时，先解除仅诊断的deadline，再走原cleanup一次：0002→0000，每段原状态期限2s，最多另4s；正常退出原close=0006，最多另2s，只核实operation enabled清零。10s是首次002F写前起的运动观察上限，不包含之前原准备阶段或独立清理时间；Halt批次最多0.5s。若传输未在途而日志处理耗尽0.5s，Mock证实仍原2→0；若响应迟到超过250ms，Mock证实隔离后零追加SDO。机械停止和抱闸动作始终未确认。

正常完成只认原新鲜保护快照的exact目标位置、零速度、bit10同时成立；finally更新退出确认后主入口才返回。已用真实main+Mock证明：即使到达，若0006确认丢失，`operation_enabled_cleared=false`且返回码1。动作runner独占创建固定日志目录作为防重入哨兵，禁止删除后重跑；总线只允许一次can1打开，can0在打开前拒绝。

本地验证命令（cwd `/home/zakiu/km3`）：

```sh
python3 -I -B docs/硬件手册/PMP4010/报警根因诊断-2026-10-19-证据/motion-compare-proposal/verify_mock.py
python3 -I -B docs/硬件手册/PMP4010/报警根因诊断-2026-10-19-证据/motion-compare-proposal/motion_compare_once.py
python3 -I -B docs/硬件手册/PMP4010/报警根因诊断-2026-10-19-证据/motion-compare-proposal/motion_compare_once.py --cold-load --payload-root /home/zakiu/km3/docs/硬件手册/PMP4010/报警内容显示-2026-10-19-证据/package/payload
```

最终Mock为`mock-attempt-8.stdout/stderr`，rc0、26项PASS、真实CAN打开/发送均0；`local-checks.json`保存plan/cold-load的精确argv、stdin audit引导、rc0及无字节码语法检查。attempt1/2是fixture初态与固定前置不符；attempt3把迟到不确定误期待为可清理；attempt4测试名“迟到”实际丢reply，后续改为真正迟到；attempt5的旧fixture未对0006清ACK，后续修正；attempt6为25项PASS；attempt7入口测试中host库替代的别名边界不完整，后续通过仅替换本地候选库路径修正。所有失败/重试输出保留，未将这些Mock修正说成现场缺陷。

拟现场入口的进程argv仅`python3 -I -B -`，stdin为`run_once_stdin.py`。引导内部固定argv为：

```text
motion_compare_once.py --execute-once --od-session-ack 接受本会话OD缺口和独立停机责任 --motion-confirm 确认仅一次相对-10mm_100rpm_低加速度对照
```

此处仅给可审阅入口；文件上传、执行和现场观察未发生。未测试真实ARM加载、真实OS前置、真实CAN时延、机械/抱闸/运动/2312根因；未做production源码、架构、harness、commit/push修改。requested route laborer/GPT-6.1 Sol/high，runtime model/effort/input_tokens未知；初始context保守估计<20K不是实际用量。
