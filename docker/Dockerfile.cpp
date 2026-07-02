# C++ execution gateway.
FROM gcc:13 AS build
WORKDIR /src
RUN apt-get update && apt-get install -y --no-install-recommends cmake && rm -rf /var/lib/apt/lists/*
COPY cpp/ ./cpp/
RUN cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build cpp/build -j

FROM debian:bookworm-slim
COPY --from=build /src/cpp/build/execution_gateway /usr/local/bin/execution_gateway
EXPOSE 8900
ENTRYPOINT ["execution_gateway"]
