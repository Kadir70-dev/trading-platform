// Order router: final pre-trade validation + forwarding to the broker
// (Python MT5 service). This is the seam where a FIX session would later be
// dropped in instead of the HTTP forward — see route().
#pragma once
#include <string>
#include <vector>

#include "json.hpp"
#include "http_client.hpp"

struct Order {
    std::string client_id;
    std::string symbol;
    std::string side;     // BUY | SELL
    double volume = 0;
    double sl = 0;
    double tp = 0;
    long long magic = 0;
    std::string comment;

    static Order from_json(const json::Value& o) {
        Order r;
        r.client_id = o["client_id"].as_str();
        r.symbol = o["symbol"].as_str();
        r.side = o["side"].as_str();
        r.volume = o["volume"].as_num();
        r.sl = o["sl"].as_num();
        r.tp = o["tp"].as_num();
        r.magic = static_cast<long long>(o["magic"].as_num());
        r.comment = o["comment"].as_str();
        return r;
    }
};

struct Validation {
    bool ok = true;
    std::vector<std::string> errors;
    void fail(const std::string& e) { ok = false; errors.push_back(e); }
};

class OrderRouter {
public:
    OrderRouter(std::string py_host, int py_port)
        : py_host_(std::move(py_host)), py_port_(py_port) {}

    // Trade validation independent of market context.
    Validation validate(const Order& o) const {
        Validation v;
        if (o.symbol.empty()) v.fail("empty symbol");
        if (o.side != "BUY" && o.side != "SELL") v.fail("invalid side");
        if (o.volume <= 0) v.fail("volume must be > 0");
        if (o.sl <= 0) v.fail("missing stop loss");
        if (o.tp <= 0) v.fail("missing take profit");
        // Directional sanity of stops vs. take profit
        if (o.side == "BUY" && o.sl > 0 && o.tp > 0 && !(o.sl < o.tp))
            v.fail("BUY requires sl < tp");
        if (o.side == "SELL" && o.sl > 0 && o.tp > 0 && !(o.sl > o.tp))
            v.fail("SELL requires sl > tp");
        return v;
    }

    // Forward to broker. Returns the broker OrderResult JSON (passed through).
    json::Value route(const Order& o) const {
        json::Value body = json::Value::object();
        body.set("symbol", o.symbol);
        body.set("side", o.side);
        body.set("volume", o.volume);
        body.set("sl", o.sl);
        body.set("tp", o.tp);
        body.set("comment", o.comment.empty() ? std::string("cpp-gateway") : o.comment);

        auto resp = http::post_json(py_host_, py_port_, "/order/market", body.dump());
        if (resp.status < 200 || resp.status >= 300) {
            json::Value err = json::Value::object();
            err.set("ok", false);
            err.set("retcode", -1);
            err.set("retcode_desc", std::string("broker HTTP ") + std::to_string(resp.status));
            return err;
        }
        return json::parse(resp.body);
    }

    // Current spread (points) for a symbol, read from the broker symbol info.
    double current_spread(const std::string& symbol) const {
        auto resp = http::get(py_host_, py_port_, "/symbol/" + symbol);
        if (resp.status < 200 || resp.status >= 300) return -1.0;
        auto v = json::parse(resp.body);
        return v["spread_points"].as_num(-1.0);
    }

private:
    std::string py_host_;
    int py_port_;
};
