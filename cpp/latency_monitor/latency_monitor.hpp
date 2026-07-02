// Latency monitoring: a scoped timer plus rolling p50/p99 estimates for
// execution round-trips. Useful groundwork for future HFT SLAs.
#pragma once
#include <algorithm>
#include <chrono>
#include <deque>
#include <mutex>
#include <vector>

class LatencyMonitor {
public:
    void record(double ms) {
        std::lock_guard<std::mutex> lk(m_);
        samples_.push_back(ms);
        if (samples_.size() > kWindow) samples_.pop_front();
    }

    struct Stats { double p50; double p99; double max; std::size_t n; };

    Stats stats() const {
        std::lock_guard<std::mutex> lk(m_);
        if (samples_.empty()) return {0, 0, 0, 0};
        std::vector<double> v(samples_.begin(), samples_.end());
        std::sort(v.begin(), v.end());
        auto pct = [&](double p) {
            std::size_t idx = std::min(v.size() - 1, static_cast<std::size_t>(p * v.size()));
            return v[idx];
        };
        return {pct(0.50), pct(0.99), v.back(), v.size()};
    }

private:
    static constexpr std::size_t kWindow = 1024;
    mutable std::mutex m_;
    std::deque<double> samples_;
};

// RAII stopwatch in milliseconds.
class Stopwatch {
public:
    Stopwatch() : start_(std::chrono::steady_clock::now()) {}
    double ms() const {
        auto now = std::chrono::steady_clock::now();
        return std::chrono::duration<double, std::milli>(now - start_).count();
    }
private:
    std::chrono::steady_clock::time_point start_;
};
