// Execution gateway: a line-delimited JSON TCP server invoked by the Go layer.
//
//   -> {"cmd":"ping"}
//   <- {"ok":true,"pong":true,"stats":{...}}
//   -> {"cmd":"execute","order":{...}}
//   <- {ok,retcode,retcode_desc,ticket,price,volume,spread_points,latency_ms}
//
// Responsibilities: trade validation, spread guard, latency measurement,
// order routing to the broker, execution logging. The forwarding seam in
// OrderRouter::route is where a FIX session would later replace HTTP.
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

#include "json.hpp"
#include "spread_monitor.hpp"
#include "latency_monitor.hpp"
#include "order_router.hpp"
#include "position_manager.hpp"

namespace {

struct HostPort { std::string host; int port; };

HostPort parse_addr(const std::string& addr, const std::string& def_host, int def_port) {
    auto pos = addr.find(':');
    if (pos == std::string::npos) return {def_host, def_port};
    return {addr.substr(0, pos), std::atoi(addr.substr(pos + 1).c_str())};
}

std::string env_or(const char* k, const std::string& def) {
    const char* v = std::getenv(k);
    return (v && *v) ? std::string(v) : def;
}

void log_line(const std::string& level, const std::string& msg) {
    std::cout << "{\"level\":\"" << level << "\",\"src\":\"cpp-gateway\",\"msg\":\""
              << msg << "\"}" << std::endl;
}

// Shared components
SpreadMonitor* g_spread = nullptr;
LatencyMonitor* g_latency = nullptr;
OrderRouter* g_router = nullptr;
PositionManager* g_positions = nullptr;

json::Value make_reject(const std::string& desc) {
    json::Value r = json::Value::object();
    r.set("ok", false);
    r.set("retcode", -1);
    r.set("retcode_desc", desc);
    return r;
}

json::Value handle_execute(const json::Value& req) {
    if (!req.has("order")) return make_reject("missing order");
    Order o = Order::from_json(req["order"]);

    // 1. Trade validation
    auto v = g_router->validate(o);
    if (!v.ok) {
        g_positions->on_rejected();
        std::string why;
        for (auto& e : v.errors) why += e + "; ";
        log_line("warn", "validation failed for " + o.symbol + ": " + why);
        return make_reject("validation: " + why);
    }

    // 2. Spread guard (live spread from broker)
    double spread = g_router->current_spread(o.symbol);
    if (spread >= 0) {
        auto sc = g_spread->evaluate(spread);
        if (!sc.ok) {
            g_positions->on_rejected();
            log_line("warn", "spread too high on " + o.symbol);
            auto r = make_reject("spread too high");
            r.set("spread_points", spread);
            return r;
        }
    }

    // 3. Route + measure latency
    Stopwatch sw;
    g_positions->on_routed(o.client_id, o.symbol);
    json::Value result = g_router->route(o);
    double latency = sw.ms();
    g_latency->record(latency);

    result.set("latency_ms", latency);
    if (spread >= 0 && !result.has("spread_points"))
        result.set("spread_points", spread);

    if (result["ok"].as_bool()) {
        long long ticket = static_cast<long long>(result["ticket"].as_num());
        g_positions->on_filled(ticket);
        log_line("info", "routed " + o.side + " " + o.symbol +
                 " ticket=" + std::to_string(ticket) +
                 " latency_ms=" + std::to_string(latency));
    } else {
        g_positions->on_rejected();
        log_line("warn", "broker rejected " + o.symbol + ": " + result["retcode_desc"].as_str());
    }
    return result;
}

json::Value handle_ping() {
    json::Value r = json::Value::object();
    r.set("ok", true);
    r.set("pong", true);
    auto st = g_latency->stats();
    json::Value stats = json::Value::object();
    stats.set("routed", static_cast<double>(g_positions->routed()));
    stats.set("filled", static_cast<double>(g_positions->filled()));
    stats.set("rejected", static_cast<double>(g_positions->rejected()));
    stats.set("latency_p50_ms", st.p50);
    stats.set("latency_p99_ms", st.p99);
    stats.set("avg_spread", g_spread->average());
    r.set("stats", stats);
    return r;
}

void handle_connection(int fd) {
    std::string buffer;
    char tmp[2048];
    ssize_t n;
    while ((n = ::recv(fd, tmp, sizeof(tmp), 0)) > 0) {
        buffer.append(tmp, n);
        size_t nl;
        while ((nl = buffer.find('\n')) != std::string::npos) {
            std::string line = buffer.substr(0, nl);
            buffer.erase(0, nl + 1);
            if (line.empty()) continue;
            json::Value reply;
            try {
                json::Value req = json::parse(line);
                std::string cmd = req["cmd"].as_str();
                if (cmd == "ping") reply = handle_ping();
                else if (cmd == "execute") reply = handle_execute(req);
                else reply = make_reject("unknown cmd: " + cmd);
            } catch (const std::exception& e) {
                reply = make_reject(std::string("parse error: ") + e.what());
            }
            std::string out = reply.dump() + "\n";
            ::send(fd, out.data(), out.size(), 0);
        }
    }
    ::close(fd);
}

} // namespace

int main() {
    auto bind_addr = parse_addr(env_or("CPP_GATEWAY_ADDR", "127.0.0.1:8900"), "127.0.0.1", 8900);
    auto py_addr = parse_addr(env_or("PYTHON_SERVICE_ADDR", "127.0.0.1:8800"), "127.0.0.1", 8800);
    double max_spread = std::atof(env_or("MAX_SPREAD_POINTS", "50").c_str());

    SpreadMonitor spread(max_spread);
    LatencyMonitor latency;
    OrderRouter router(py_addr.host, py_addr.port);
    PositionManager positions;
    g_spread = &spread; g_latency = &latency; g_router = &router; g_positions = &positions;

    int srv = ::socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) { log_line("error", "socket() failed"); return 1; }
    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(bind_addr.port);
    ::inet_pton(AF_INET, bind_addr.host.c_str(), &addr.sin_addr);
    if (::bind(srv, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        log_line("error", "bind() failed on " + bind_addr.host + ":" + std::to_string(bind_addr.port));
        return 1;
    }
    if (::listen(srv, 64) < 0) { log_line("error", "listen() failed"); return 1; }

    log_line("info", "execution gateway listening on " + bind_addr.host + ":" +
             std::to_string(bind_addr.port) + " -> broker " + py_addr.host + ":" +
             std::to_string(py_addr.port) + " (max_spread=" + std::to_string((int)max_spread) + ")");

    while (true) {
        int client = ::accept(srv, nullptr, nullptr);
        if (client < 0) continue;
        std::thread(handle_connection, client).detach();
    }
    return 0;
}
