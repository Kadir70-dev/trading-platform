// Spread monitoring: rejects trades when the spread exceeds the configured cap
// and keeps simple rolling statistics for observability.
#pragma once
#include <algorithm>
#include <cstddef>
#include <deque>
#include <mutex>

class SpreadMonitor {
public:
    explicit SpreadMonitor(double max_points) : max_points_(max_points) {}

    struct Check {
        bool ok;
        double spread;
        double max;
    };

    Check evaluate(double spread_points) {
        {
            std::lock_guard<std::mutex> lk(m_);
            history_.push_back(spread_points);
            if (history_.size() > kWindow) history_.pop_front();
        }
        return Check{spread_points <= max_points_, spread_points, max_points_};
    }

    double average() const {
        std::lock_guard<std::mutex> lk(m_);
        if (history_.empty()) return 0.0;
        double sum = 0;
        for (double v : history_) sum += v;
        return sum / static_cast<double>(history_.size());
    }

    void set_max(double m) { max_points_ = m; }
    double max_points() const { return max_points_; }

private:
    static constexpr std::size_t kWindow = 256;
    double max_points_;
    mutable std::mutex m_;
    std::deque<double> history_;
};
