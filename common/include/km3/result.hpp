#pragma once
#include <string>
#include <utility>
#include "km3/error_code.hpp"

namespace km3 {

// 轻量结果类型。实时路径只传 ErrorCode，不构造字符串。
template <typename T>
class Result {
public:
    static Result Ok(T value) { return Result(ErrorCode::Ok, std::move(value)); }
    static Result Err(ErrorCode code) { return Result(code, T{}); }

    bool ok() const { return code_ == ErrorCode::Ok; }
    explicit operator bool() const { return ok(); }
    ErrorCode code() const { return code_; }
    const T& value() const { return value_; }
    T& value() { return value_; }

private:
    Result(ErrorCode code, T value) : code_(code), value_(std::move(value)) {}
    ErrorCode code_;
    T value_;
};

// 无返回值的版本
class Status {
public:
    Status() = default;
    explicit Status(ErrorCode code) : code_(code) {}
    static Status Ok() { return Status(); }
    bool ok() const { return code_ == ErrorCode::Ok; }
    explicit operator bool() const { return ok(); }
    ErrorCode code() const { return code_; }

private:
    ErrorCode code_ = ErrorCode::Ok;
};

}  // namespace km3
