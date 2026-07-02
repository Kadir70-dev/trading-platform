# Go orchestrator / scheduler (the trader binary).
FROM golang:1.22-bookworm AS build
WORKDIR /src
COPY go.mod ./
COPY cmd/ ./cmd/
COPY internal/ ./internal/
COPY services/ ./services/
# Build with the postgres tag so the container can use the DB.
RUN go mod tidy 2>/dev/null || true && \
    CGO_ENABLED=0 go build -tags postgres -o /out/trader ./cmd/trader || \
    CGO_ENABLED=0 go build -o /out/trader ./cmd/trader

FROM debian:bookworm-slim
WORKDIR /app
COPY --from=build /out/trader /usr/local/bin/trader
COPY config/ /app/config/
EXPOSE 8080
ENTRYPOINT ["trader"]
CMD ["run"]
