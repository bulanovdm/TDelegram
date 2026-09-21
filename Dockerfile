FROM python:3.12-slim AS tdlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake g++ git zlib1g-dev libssl-dev gperf php-cli ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/tdlib/td.git /tmp/td \
 && cmake -S /tmp/td -B /tmp/td/build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build /tmp/td/build --target install -j"$(nproc)" \
 && ldconfig

FROM python:3.12-slim
COPY --from=tdlib /usr/local/lib/libtdjson.so* /usr/local/lib/
COPY --from=tdlib /usr/local/include/ /usr/local/include/
RUN ldconfig
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir .
ENTRYPOINT ["tdelegram"]
CMD ["--help"]
