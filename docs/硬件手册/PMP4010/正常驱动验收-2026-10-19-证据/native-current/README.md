当前C++基线实测（2026-10-19）

顺序执行Host、ASan、TSan，每套先cmake --build <dir> --clean-first -j4，再CTest；TSan实际setarch -R。六条命令均rc0，当前配置的完整CTest集合各17/17，无失败、无重试。不是引用历史结果。

完整argv/cwd/时刻/耗时/stdout/stderr/rc见command-index.json及每步.command.json；原始日志逐步保留。summary.json记录三套cache选择：none/address/thread，均Debug、KM3_BUILD_TESTS=ON。

source-before-sha256.json与source-after-sha256.json中的77个文件前后一致：明确枚举device/src、include、tests、PMP4010 driver/tests/native、common、proto、cmake及顶层/驱动CMakeLists。此边界覆盖这些目录当时存在的文件，不声称覆盖系统头文件、外部依赖/toolchain或未列出的其它仓库内容。实际干净重建证明使用当前工作树重新编译；只证明当前配置的构建与17项离线测试，不替代生产注册或硬件验收。

既有状态样本修复的Python/工具冻结清单逐文件当前哈希全部匹配，见existing-python-freeze-check.json；未重跑375/217/8或七门。未改业务源码、未重新配置、未关守卫、未SSH/CAN/硬件操作、未修改旧证据。
