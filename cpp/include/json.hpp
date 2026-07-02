// Minimal, dependency-free JSON for the execution gateway.
// Supports null/bool/number/string/array/object, parse + serialize.
// Not a general-purpose library — scoped to the platform's message shapes,
// but handles nested objects/arrays so broker replies parse cleanly.
#pragma once
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace json {

struct Value;
using Object = std::map<std::string, Value>;
using Array = std::vector<Value>;

enum class Type { Null, Bool, Number, String, Array, Object };

struct Value {
    Type type = Type::Null;
    bool b = false;
    double num = 0.0;
    std::string str;
    std::shared_ptr<Array> arr;
    std::shared_ptr<Object> obj;

    Value() = default;
    Value(bool v) : type(Type::Bool), b(v) {}
    Value(double v) : type(Type::Number), num(v) {}
    Value(int v) : type(Type::Number), num(v) {}
    Value(long long v) : type(Type::Number), num(static_cast<double>(v)) {}
    Value(const char* v) : type(Type::String), str(v) {}
    Value(const std::string& v) : type(Type::String), str(v) {}

    static Value object() { Value v; v.type = Type::Object; v.obj = std::make_shared<Object>(); return v; }
    static Value array()  { Value v; v.type = Type::Array;  v.arr = std::make_shared<Array>();  return v; }

    bool is_null() const { return type == Type::Null; }
    bool has(const std::string& k) const { return type == Type::Object && obj && obj->count(k); }

    const Value& operator[](const std::string& k) const {
        static Value null_value;
        if (type == Type::Object && obj) { auto it = obj->find(k); if (it != obj->end()) return it->second; }
        return null_value;
    }
    Value& set(const std::string& k, const Value& v) {
        if (type != Type::Object) { type = Type::Object; obj = std::make_shared<Object>(); }
        (*obj)[k] = v; return *this;
    }
    void push(const Value& v) {
        if (type != Type::Array) { type = Type::Array; arr = std::make_shared<Array>(); }
        arr->push_back(v);
    }

    double as_num(double def = 0.0) const { return type == Type::Number ? num : def; }
    bool as_bool(bool def = false) const { return type == Type::Bool ? b : def; }
    std::string as_str(const std::string& def = "") const { return type == Type::String ? str : def; }

    std::string dump() const {
        std::ostringstream os; write(os); return os.str();
    }

    void write(std::ostringstream& os) const {
        switch (type) {
            case Type::Null: os << "null"; break;
            case Type::Bool: os << (b ? "true" : "false"); break;
            case Type::Number: {
                if (num == static_cast<long long>(num)) os << static_cast<long long>(num);
                else os << num;
                break;
            }
            case Type::String: write_string(os, str); break;
            case Type::Array: {
                os << '['; bool first = true;
                for (auto& e : *arr) { if (!first) os << ','; first = false; e.write(os); }
                os << ']'; break;
            }
            case Type::Object: {
                os << '{'; bool first = true;
                for (auto& kv : *obj) {
                    if (!first) os << ','; first = false;
                    write_string(os, kv.first); os << ':'; kv.second.write(os);
                }
                os << '}'; break;
            }
        }
    }

    static void write_string(std::ostringstream& os, const std::string& s) {
        os << '"';
        for (char c : s) {
            switch (c) {
                case '"': os << "\\\""; break;
                case '\\': os << "\\\\"; break;
                case '\n': os << "\\n"; break;
                case '\r': os << "\\r"; break;
                case '\t': os << "\\t"; break;
                default: os << c;
            }
        }
        os << '"';
    }
};

class Parser {
public:
    explicit Parser(const std::string& s) : s_(s) {}
    Value parse() { skip(); Value v = value(); return v; }

private:
    const std::string& s_;
    size_t i_ = 0;

    void skip() { while (i_ < s_.size() && (s_[i_]==' '||s_[i_]=='\t'||s_[i_]=='\n'||s_[i_]=='\r')) i_++; }
    char peek() { return i_ < s_.size() ? s_[i_] : '\0'; }
    char get() { return i_ < s_.size() ? s_[i_++] : '\0'; }

    Value value() {
        skip();
        char c = peek();
        if (c == '{') return object();
        if (c == '[') return array();
        if (c == '"') return Value(string());
        if (c == 't' || c == 'f') return boolean();
        if (c == 'n') { expect("null"); return Value(); }
        return number();
    }

    Value object() {
        Value v = Value::object(); get(); skip();
        if (peek() == '}') { get(); return v; }
        while (true) {
            skip(); std::string key = string(); skip();
            if (get() != ':') throw std::runtime_error("expected ':'");
            v.set(key, value()); skip();
            char c = get();
            if (c == ',') continue;
            if (c == '}') break;
            throw std::runtime_error("expected ',' or '}'");
        }
        return v;
    }

    Value array() {
        Value v = Value::array(); get(); skip();
        if (peek() == ']') { get(); return v; }
        while (true) {
            v.push(value()); skip();
            char c = get();
            if (c == ',') continue;
            if (c == ']') break;
            throw std::runtime_error("expected ',' or ']'");
        }
        return v;
    }

    std::string string() {
        if (get() != '"') throw std::runtime_error("expected string");
        std::string out;
        while (true) {
            char c = get();
            if (c == '"') break;
            if (c == '\0') throw std::runtime_error("unterminated string");
            if (c == '\\') {
                char e = get();
                switch (e) {
                    case 'n': out += '\n'; break;
                    case 't': out += '\t'; break;
                    case 'r': out += '\r'; break;
                    case '"': out += '"'; break;
                    case '\\': out += '\\'; break;
                    case '/': out += '/'; break;
                    case 'u': { for (int k=0;k<4;k++) get(); out += '?'; break; }
                    default: out += e;
                }
            } else out += c;
        }
        return out;
    }

    Value number() {
        size_t start = i_;
        while (i_ < s_.size() && (isdigit(s_[i_]) || s_[i_]=='-' || s_[i_]=='+' ||
               s_[i_]=='.' || s_[i_]=='e' || s_[i_]=='E')) i_++;
        return Value(std::stod(s_.substr(start, i_ - start)));
    }

    Value boolean() {
        if (peek() == 't') { expect("true"); return Value(true); }
        expect("false"); return Value(false);
    }

    void expect(const std::string& lit) {
        for (char c : lit) { if (get() != c) throw std::runtime_error("invalid literal"); }
    }
};

inline Value parse(const std::string& s) { return Parser(s).parse(); }

} // namespace json
