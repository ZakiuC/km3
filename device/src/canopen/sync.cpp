#include "km3/canopen_sync.hpp"

#include <algorithm>
#include <cerrno>
#include <limits>
#include <poll.h>
#include <sched.h>
#include <sys/eventfd.h>
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>

#include "km3/config.hpp"

namespace km3::canopen {

SyncProducer::SyncProducer(ICanDevice& device, std::uint64_t period_us, ClockFn clock,
                           std::uint32_t cob_id, SyncCycle* cycle) noexcept
    : device_(device), period_us_(period_us), frame_(MakeFrame(cob_id)), clock_(clock),
      cycle_(cycle) {}

CanFrame SyncProducer::MakeFrame(std::uint32_t cob_id) noexcept {
    CanFrame frame{};
    frame.id = cob_id;  // CiA 301 default SYNC COB-ID, no counter, no response.
    return frame;
}

Status SyncProducer::Start() noexcept {
    if (active_)
        return Status(ErrorCode::EDeviceBusy);
    if (frame_.id == 0 || frame_.id > 0x7FF || period_us_ == 0 || period_us_ > kMaxSyncPeriodUs ||
        clock_ == nullptr)
        return Status(ErrorCode::EInvalidConfig);
    if (cycle_) {
        const auto valid = cycle_->ValidateForSync(frame_.id);
        if (!valid)
            return valid;
    }
    if (!device_.IsOpen())
        return Status(ErrorCode::EDeviceOffline);
    const auto now = clock_();
    if (now > std::numeric_limits<std::uint64_t>::max() - period_us_)
        return Status(ErrorCode::EInvalidRequest);
    stats_ = {};
    next_us_ = now + period_us_;  // First frame after one full configured period.
    active_ = true;
    return Status::Ok();
}

void SyncProducer::Tick() noexcept {
    if (!active_)
        return;
    const auto now = clock_();
    if (now < next_us_)
        return;
    stats_.max_late_us = std::max(stats_.max_late_us, now - next_us_);
    ++stats_.attempts;
    // One sender owns the entire cycle. Every RPDO must be confirmed complete
    // before SYNC is even queued: send() success alone permits CAN arbitration
    // to let the lower-ID SYNC overtake queued RPDOs.
    if (cycle_ && next_us_ > std::numeric_limits<std::uint64_t>::max() - period_us_) {
        active_ = false;
        ++stats_.failures;
        stats_.last_error = ErrorCode::EInternal;
        cycle_->LatchFailure();
        return;
    }
    auto status = cycle_ ? cycle_->BeforeSync(next_us_ + period_us_) : Status::Ok();
    if (status.ok() && cycle_ && clock_() >= next_us_ + period_us_)
        status = Status(ErrorCode::ECommandTimeout);
    if (status.ok())
        status =
            cycle_ ? device_.SendConfirmed(frame_, next_us_ + period_us_) : device_.Send(frame_);
    if (status.ok()) {
        ++stats_.sent;
        stats_.consecutive_failures = 0;
    } else {
        ++stats_.failures;
        ++stats_.consecutive_failures;
        stats_.last_error = status.code();
        // A partial RPDO batch may already be stored by the peer. Never send a
        // later SYNC that would apply it; explicit external recovery is needed.
        if (cycle_) {
            cycle_->LatchFailure();
            active_ = false;
        }
    }
    // Advance against send COMPLETION, so a delayed send cannot cause a second
    // immediate transmission. Drop all expired slots while preserving phase.
    const auto end = std::max(now, clock_());
    const auto periods = (end - next_us_) / period_us_ + 1;
    stats_.skipped += periods - 1;
    if (periods > (std::numeric_limits<std::uint64_t>::max() - next_us_) / period_us_) {
        active_ = false;
        stats_.last_error = ErrorCode::EInternal;
        return;
    }
    next_us_ += periods * period_us_;
}

SyncWorker::SyncWorker(ICanDevice& device, std::uint64_t period_us, std::uint32_t cob_id,
                       SyncCycle* cycle) noexcept
    : producer_(device, period_us, &MonotonicUs, cob_id, cycle), device_(device), cycle_(cycle) {}
SyncWorker::~SyncWorker() {
    Stop();
}

Status SyncWorker::Start() noexcept {
    if (joinable_)
        return Status(ErrorCode::EDeviceBusy);
    auto status = producer_.Start();
    if (!status.ok())
        return status;
    producer_.Stop();
    // Producer is not ticked until the worker verifies its actual policy.
    timer_fd_ = ::timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
    stop_fd_ = ::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    ready_fd_ = ::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    if (timer_fd_ < 0 || stop_fd_ < 0 || ready_fd_ < 0) {
        system_error_.store(errno);
        Stop();
        return Status(ErrorCode::EDeviceError);
    }
    startup_.store(0);
    stopping_.store(false);
    worker_error_.store(ErrorCode::Ok);
    system_error_.store(0);
    Publish();
    pthread_attr_t attr;
    int error = ::pthread_attr_init(&attr);
    if (error != 0) {
        system_error_.store(error);
        Stop();
        return Status(ErrorCode::EInternal);
    }
    // pthread defaults to INHERIT_SCHED, even for std::thread: explicitly prevent
    // inheriting FIFO/90 from a caller that already configured realtime policy.
    sched_param param{};
    error = ::pthread_attr_setinheritsched(&attr, PTHREAD_EXPLICIT_SCHED);
    if (error == 0)
        error = ::pthread_attr_setschedpolicy(&attr, SCHED_OTHER);
    if (error == 0)
        error = ::pthread_attr_setschedparam(&attr, &param);
    if (error == 0)
        error = ::pthread_create(&thread_, &attr, &Entry, this);
    ::pthread_attr_destroy(&attr);
    if (error != 0) {
        system_error_.store(error);
        Stop();
        return Status(error == EPERM ? ErrorCode::EPermission : ErrorCode::EInternal);
    }
    joinable_ = true;
    // Block the caller, including FIFO callers restricted to one CPU. A yield
    // loop can starve the lower-priority worker forever. Startup wait is bounded.
    const auto deadline = MonotonicUs() + 5000000;
    bool acknowledged = false;
    for (;;) {
        const auto now = MonotonicUs();
        if (now >= deadline)
            break;
        pollfd pfd{ready_fd_, POLLIN, 0};
        const int timeout = static_cast<int>((deadline - now + 999) / 1000);
        const int ready = ::poll(&pfd, 1, std::max(0, timeout));
        if (ready < 0 && errno == EINTR)
            continue;
        if (ready > 0 && (pfd.revents & POLLIN) != 0)
            acknowledged = true;
        break;
    }
    if (!acknowledged) {
        worker_error_.store(ErrorCode::ECommandTimeout);
        Stop();  // Wake and join even if the worker only starts after timeout.
        return Status(ErrorCode::ECommandTimeout);
    }
    if (startup_.load() < 0) {
        const auto result = worker_error_.load();
        Stop();
        return Status(result);
    }
    return Status::Ok();
}

void SyncWorker::Stop() noexcept {
    stopping_.store(true, std::memory_order_release);
    if (joinable_) {
        const std::uint64_t one = 1;
        // EINTR is the only retry; EAGAIN means an earlier wake is already queued.
        ssize_t count;
        do {
            count = ::write(stop_fd_, &one, sizeof(one));
        } while (count < 0 && errno == EINTR);
        if (count < 0 && errno != EAGAIN)
            system_error_.store(errno);
        ::pthread_join(thread_, nullptr);
        joinable_ = false;
    }
    producer_.Stop();
    if (timer_fd_ >= 0) {
        ::close(timer_fd_);
        timer_fd_ = -1;
    }
    if (stop_fd_ >= 0) {
        ::close(stop_fd_);
        stop_fd_ = -1;
    }
    if (ready_fd_ >= 0) {
        ::close(ready_fd_);
        ready_fd_ = -1;
    }
}

void* SyncWorker::Entry(void* self) noexcept {
    static_cast<SyncWorker*>(self)->Run();
    return nullptr;
}

void SyncWorker::Run() noexcept {
    int policy = -1;
    sched_param param{};
    const int result = ::pthread_getschedparam(::pthread_self(), &policy, &param);
    actual_policy_.store(policy);
    actual_priority_.store(param.sched_priority);
    const auto acknowledge = [&]() {
        const std::uint64_t one = 1;
        ssize_t count;
        do {
            count = ::write(ready_fd_, &one, sizeof(one));
        } while (count < 0 && errno == EINTR);
    };
    if (result != 0 || policy != SCHED_OTHER || param.sched_priority != 0) {
        system_error_.store(result);
        worker_error_.store(ErrorCode::EPermission);
        startup_.store(-1, std::memory_order_release);
        acknowledge();
        return;
    }
    // Anchor the first deadline to verified worker startup, not pthread creation.
    const auto started = producer_.Start();
    if (!started.ok()) {
        worker_error_.store(started.code());
        startup_.store(-1, std::memory_order_release);
        acknowledge();
        return;
    }
    startup_.store(1, std::memory_order_release);
    acknowledge();
    while (!stopping_.load(std::memory_order_acquire) && producer_.Active()) {
        const auto deadline = producer_.NextDeadlineUs();
        itimerspec timer{};
        timer.it_value.tv_sec = static_cast<time_t>(deadline / 1000000);
        timer.it_value.tv_nsec = static_cast<long>(deadline % 1000000) * 1000L;
        if (::timerfd_settime(timer_fd_, TFD_TIMER_ABSTIME, &timer, nullptr) < 0) {
            system_error_.store(errno);
            worker_error_.store(ErrorCode::EDeviceError);
            break;
        }
        pollfd fds[2]{{stop_fd_, POLLIN, 0}, {timer_fd_, POLLIN, 0}};
        int ready;
        do {
            ready = ::poll(fds, 2, -1);
        } while (ready < 0 && errno == EINTR && !stopping_.load());
        // Stop wins when both descriptors become ready together. Never emit a
        // final "shutdown" frame. Join is the externally visible stop boundary.
        if (stopping_.load(std::memory_order_acquire) || (fds[0].revents & POLLIN) != 0)
            break;
        if (ready < 0 || (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) != 0 ||
            (fds[1].revents & (POLLERR | POLLHUP | POLLNVAL)) != 0) {
            system_error_.store(ready < 0 ? errno : EIO);
            worker_error_.store(ErrorCode::EDeviceError);
            break;
        }
        if ((fds[1].revents & POLLIN) == 0)
            continue;
        std::uint64_t expirations = 0;
        const auto count = ::read(timer_fd_, &expirations, sizeof(expirations));
        if (count < 0 && (errno == EINTR || errno == EAGAIN))
            continue;
        if (count != static_cast<ssize_t>(sizeof(expirations))) {
            system_error_.store(count < 0 ? errno : EIO);
            worker_error_.store(ErrorCode::EDeviceError);
            break;
        }
        if (stopping_.load(std::memory_order_acquire))
            break;
        producer_.Tick();
        Publish();
        if (!producer_.Active()) {
            worker_error_.store(producer_.Stats().last_error);
            break;
        }
        // Bounded, nonblocking drain keeps CAN error notifications observable
        // even in a transmit-only application. No netlink queries/log formatting
        // in this cycle. Recovery is never automatic.
        if (cycle_) {
            cycle_->ReceiveAvailable();
            continue;
        }
        for (unsigned i = 0; i < 8 && !stopping_.load(); ++i) {
            const auto rx = device_.Receive(0);
            if (rx.code() == ErrorCode::ECommandTimeout)
                break;
        }
    }
    producer_.Stop();
}

void SyncWorker::Publish() noexcept {
    const auto stats = producer_.Stats();
    attempts_.store(stats.attempts, std::memory_order_relaxed);
    sent_.store(stats.sent, std::memory_order_relaxed);
    failures_.store(stats.failures, std::memory_order_relaxed);
    consecutive_failures_.store(stats.consecutive_failures, std::memory_order_relaxed);
    skipped_.store(stats.skipped, std::memory_order_relaxed);
    max_late_us_.store(stats.max_late_us, std::memory_order_relaxed);
    last_error_.store(stats.last_error, std::memory_order_relaxed);
}

SyncStats SyncWorker::Stats() const noexcept {
    SyncStats stats{};
    stats.attempts = attempts_.load(std::memory_order_relaxed);
    stats.sent = sent_.load(std::memory_order_relaxed);
    stats.failures = failures_.load(std::memory_order_relaxed);
    stats.consecutive_failures = consecutive_failures_.load(std::memory_order_relaxed);
    stats.skipped = skipped_.load(std::memory_order_relaxed);
    stats.max_late_us = max_late_us_.load(std::memory_order_relaxed);
    stats.last_error = last_error_.load(std::memory_order_relaxed);
    return stats;
}

}  // namespace km3::canopen
