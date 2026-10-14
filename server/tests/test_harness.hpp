#pragma once
// 零依赖测试骨架：设备端与服务器端在未安装 GoogleTest 时也能跑。
//
// 顺序很重要：注册表 API 必须先声明，宏才能引用它。
// （早期版本把宏写在命名空间定义之前，导致 KM3_TEST 展开时 Register/Count 尚未声明。）

#include <cstdio>
#include <string>

namespace km3test {

using TestFn = void (*)();

struct Case {
    const char* name;
    TestFn fn;
};

Case* Registry();
int& Count();
int& FailureCount();
int& CheckCount();

void Register(const char* name, TestFn fn);
void ReportFailure(const char* file, int line, const std::string& what);
int RunAll();

inline Case* Registry() {
    static Case cases[512];
    return cases;
}
inline int& Count() { static int count = 0; return count; }
inline int& FailureCount() { static int failures = 0; return failures; }
inline int& CheckCount() { static int checks = 0; return checks; }

inline void Register(const char* name, TestFn fn) {
    const int index = Count();
    if (index < 512) {
        Registry()[index].name = name;
        Registry()[index].fn = fn;
        Count() = index + 1;
    }
}

inline void ReportFailure(const char* file, int line, const std::string& what) {
    ++FailureCount();
    std::fprintf(stderr, "  FAIL %s:%d  %s\n", file, line, what.c_str());
}

inline int RunAll() {
    std::printf("running %d test(s)\n", Count());
    for (int i = 0; i < Count(); ++i) {
        const Case& c = Registry()[i];
        const int before = FailureCount();
        c.fn();
        std::printf("  %-46s %s\n", c.name,
                    (FailureCount() - before) == 0 ? "ok" : "FAILED");
    }
    std::printf("\n%d check(s), %d failure(s)\n", CheckCount(), FailureCount());
    return FailureCount() == 0 ? 0 : 1;
}

}  // namespace km3test

#define KM3_CHECK(cond)                                                        \
    do {                                                                       \
        ++km3test::CheckCount();                                               \
        if (!(cond)) {                                                         \
            km3test::ReportFailure(__FILE__, __LINE__, #cond);                  \
        }                                                                      \
    } while (false)

// 失败即中止当前用例。
// 起因：一个断言失败后测试继续执行，在失败的 Result 上取下标（空 vector），
// 把「断言失败」放大成 SEGV，掩盖了真正的根因。
#define KM3_REQUIRE(cond)                                                      \
    do {                                                                       \
        ++km3test::CheckCount();                                               \
        if (!(cond)) {                                                         \
            km3test::ReportFailure(__FILE__, __LINE__, #cond);                  \
            return;                                                            \
        }                                                                      \
    } while (false)

#define KM3_CHECK_EQ(a, b)                                                     \
    do {                                                                       \
        ++km3test::CheckCount();                                               \
        if (!((a) == (b))) {                                                   \
            km3test::ReportFailure(__FILE__, __LINE__, #a " == " #b);           \
        }                                                                      \
    } while (false)

// 用法：KM3_TEST(name) { ... }  —— 自动注册
#define KM3_TEST(test_name)                                                    \
    static void test_name##_body();                                            \
    namespace {                                                                \
    struct test_name##_registrar {                                              \
        test_name##_registrar() { km3test::Register(#test_name, &test_name##_body); } \
    } test_name##_registrar_instance;                                           \
    }                                                                          \
    static void test_name##_body()

#define KM3_TEST_MAIN() int main() { return km3test::RunAll(); }
