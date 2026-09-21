# TDLib is a C++ dependency with no distribution package, so building it is the
# slow part of installing TDelegram natively. This image does that once, which
# is why Docker is the recommended way in.
#
# The TDLib commit is pinned to the one the method registry was generated from.
# Building master instead would let the image drift from methods.json, and the
# registry is what the write gate consults, so drift there is a safety issue
# rather than a cosmetic one. Keep this in step with TDLIB_COMMIT in
# .github/workflows/ci.yml.
ARG TDLIB_COMMIT=d1085f9cebc5a62379991ae1652673954f229c1f
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS tdlib
ARG TDLIB_COMMIT
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake g++ git zlib1g-dev libssl-dev gperf php-cli ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/tdlib/td.git /tmp/td \
 && git -C /tmp/td checkout --quiet "${TDLIB_COMMIT}" \
 && cmake -S /tmp/td -B /tmp/td/build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build /tmp/td/build --target install -j"$(nproc)" \
 # Debug symbols are most of the shared library's size and nothing loads them
 # at runtime.
 && strip --strip-unneeded /usr/local/lib/libtdjson.so*

FROM python:${PYTHON_VERSION}-slim
LABEL org.opencontainers.image.title="tdelegram" \
      org.opencontainers.image.description="Telegram client library and CLI over TDLib, with a read/write/destructive gate on every call" \
      org.opencontainers.image.source="https://github.com/bulanovdm/TDelegram" \
      org.opencontainers.image.licenses="Apache-2.0"

# Only the shared library is needed at runtime. The previous image also copied
# /usr/local/include, which is TDLib's C++ headers -- useful for regenerating
# the registry, dead weight in a runtime image.
#
# Copy the versioned file alone. `libtdjson.so*` also matches the unversioned
# development symlink, and COPY dereferences symlinks, so the glob shipped the
# same 34MB library twice. ldconfig recreates the soname link, which is what
# find_library actually resolves.
COPY --from=tdlib /usr/local/lib/libtdjson.so.* /usr/local/lib/
RUN ldconfig

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /root/.cache

# The session lives here and is mounted from the host, so a login survives the
# container. It is full account access; see SECURITY.md.
ENV TDELEGRAM_HOME=/session
VOLUME ["/session"]

ENTRYPOINT ["tdelegram"]
CMD ["--help"]
