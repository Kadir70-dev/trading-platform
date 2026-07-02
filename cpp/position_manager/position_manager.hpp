// Position manager: lightweight in-memory registry of orders routed by this
// gateway instance. In a FIX deployment this would reconcile execution reports;
// here it tracks counts and last tickets for the /stats endpoint.
#pragma once
#include <atomic>
#include <mutex>
#include <string>
#include <unordered_map>

class PositionManager {
public:
    void on_routed(const std::string& client_id, const std::string& symbol) {
        std::lock_guard<std::mutex> lk(m_);
        last_client_id_ = client_id;
        per_symbol_[symbol]++;
        routed_++;
    }
    void on_filled(long long ticket) {
        std::lock_guard<std::mutex> lk(m_);
        last_ticket_ = ticket;
        filled_++;
    }
    void on_rejected() { rejected_++; }

    long long routed() const { return routed_; }
    long long filled() const { return filled_; }
    long long rejected() const { return rejected_; }
    long long last_ticket() const {
        std::lock_guard<std::mutex> lk(m_);
        return last_ticket_;
    }

private:
    mutable std::mutex m_;
    std::atomic<long long> routed_{0};
    std::atomic<long long> filled_{0};
    std::atomic<long long> rejected_{0};
    long long last_ticket_ = 0;
    std::string last_client_id_;
    std::unordered_map<std::string, long long> per_symbol_;
};
