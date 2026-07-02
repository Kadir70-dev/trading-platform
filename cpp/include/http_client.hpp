// Minimal HTTP/1.1 client over POSIX TCP sockets. Enough to POST/GET JSON to the
// Python MT5 service. No TLS (service is loopback). Dependency-free.
#pragma once
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstring>
#include <stdexcept>
#include <string>

namespace http {

struct Response {
    int status = 0;
    std::string body;
};

inline int dial(const std::string& host, int port, int timeout_ms = 5000) {
    int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    timeval tv{timeout_ms / 1000, (timeout_ms % 1000) * 1000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    if (::inet_pton(AF_INET, host.c_str(), &addr.sin_addr) <= 0) {
        // resolve hostname
        hostent* he = ::gethostbyname(host.c_str());
        if (!he) { ::close(fd); return -1; }
        std::memcpy(&addr.sin_addr, he->h_addr, he->h_length);
    }
    if (::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        ::close(fd); return -1;
    }
    return fd;
}

inline Response request(const std::string& host, int port, const std::string& method,
                        const std::string& path, const std::string& body) {
    Response r;
    int fd = dial(host, port);
    if (fd < 0) throw std::runtime_error("connect failed to " + host);

    std::string req = method + " " + path + " HTTP/1.1\r\n";
    req += "Host: " + host + "\r\n";
    req += "Content-Type: application/json\r\n";
    req += "Connection: close\r\n";
    req += "Content-Length: " + std::to_string(body.size()) + "\r\n\r\n";
    req += body;

    ::send(fd, req.data(), req.size(), 0);

    std::string raw;
    char buf[4096];
    ssize_t n;
    while ((n = ::recv(fd, buf, sizeof(buf), 0)) > 0) raw.append(buf, n);
    ::close(fd);

    auto sp = raw.find(' ');
    if (sp != std::string::npos) r.status = std::atoi(raw.substr(sp + 1, 3).c_str());
    auto hdr_end = raw.find("\r\n\r\n");
    if (hdr_end != std::string::npos) r.body = raw.substr(hdr_end + 4);
    return r;
}

inline Response post_json(const std::string& host, int port, const std::string& path,
                          const std::string& body) {
    return request(host, port, "POST", path, body);
}
inline Response get(const std::string& host, int port, const std::string& path) {
    return request(host, port, "GET", path, "");
}

} // namespace http
