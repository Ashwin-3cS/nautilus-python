# Nautilus-Python: Python TEE app for AWS Nitro Enclaves.
#
# Architecture:
#   - Full Python 3.12 runtime from Alpine (musl-based, compact)
#   - socat for VSOCK bridging inside enclave
#   - nit init handles loopback, NSM, process management
#   - eif_build packages kernel + initramfs into EIF

# --- StageX base images (reproducible builds) ---
FROM stagex/core-ca-certificates@sha256:d135f1189e9b232eb7316626bf7858534c5540b2fc53dced80a4c9a95f26493e AS core-ca-certificates
FROM stagex/core-gcc@sha256:964ffd3793c5a38ca581e9faefd19918c259f1611c4cbf5dc8be612e3a8b72f5 AS core-gcc
FROM stagex/core-openssl@sha256:d6487f0cb15f4ee02b420c717cb9abd85d73043c0bb3a2c6ce07688b23c1df07 AS core-openssl
FROM stagex/core-zlib@sha256:06f5168e20d85d1eb1d19836cdf96addc069769b40f8f0f4a7a70b2f49fc18f8 AS core-zlib
FROM stagex/core-musl@sha256:d9af23284cca2e1002cd53159ada469dfe6d6791814e72d6163c7de18d4ae701 AS core-musl
FROM stagex/core-libunwind@sha256:eb66122d8fc543f5e2f335bb1616f8c3a471604383e2c0a9df4a8e278505d3bc AS core-libunwind
FROM stagex/core-busybox@sha256:637b1e0d9866807fac94c22d6dc4b2e1f45c8a5ca1113c88172e0324a30c7283 AS core-busybox
FROM stagex/user-eif_build@sha256:935032172a23772ea1a35c6334aa98aa7b0c46f9e34a040347c7b2a73496ef8a AS user-eif_build
FROM stagex/user-linux-nitro@sha256:aa1006d91a7265b33b86160031daad2fdf54ec2663ed5ccbd312567cc9beff2c AS user-linux-nitro
FROM stagex/user-cpio@sha256:9c8bf39001eca8a71d5617b46f8c9b4f7426db41a052f198d73400de6f8a16df AS user-cpio
FROM stagex/user-nit@sha256:60b6eef4534ea6ea78d9f29e4c7feb27407b615424f20ad8943d807191688be7 AS user-nit
FROM stagex/user-socat:local@sha256:acef3dacc5b805d0eaaae0c2d13f567bf168620aea98c8d3e60ea5fd4e8c3108 AS user-socat

# --- Install Python + deps on Alpine (musl-native, no glibc) ---
FROM python:3.12-alpine AS python-build
WORKDIR /app

# Runtime libs needed by pynacl and Python
RUN apk add --no-cache libffi libsodium openssl

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ src/
COPY app.py .

# Collect all shared libs the Python binary needs into /collected-libs
RUN mkdir -p /collected-libs && \
    for lib in $(ldd /usr/local/bin/python3.12 2>/dev/null | awk '{print $3}' | grep -v '^$'); do \
        cp -L "$lib" /collected-libs/ 2>/dev/null || true; \
    done && \
    # Also grab libsodium and libffi for pynacl
    cp -L /usr/lib/libsodium.so* /collected-libs/ 2>/dev/null || true && \
    cp -L /usr/lib/libffi.so* /collected-libs/ 2>/dev/null || true && \
    # libz can be in /lib/, /usr/lib/, or /usr/lib/ — find it wherever Alpine puts it
    find / -name 'libz.so*' -exec cp -L {} /collected-libs/ \; 2>/dev/null || true && \
    cp -L /lib/ld-musl-x86_64.so.1 /collected-libs/ 2>/dev/null || true && \
    chmod 755 /collected-libs/* && \
    echo "=== Collected libs ===" && ls -la /collected-libs/ && \
    echo "=== libz check ===" && ls -la /collected-libs/libz* 2>/dev/null || echo "WARNING: libz not found!"

# --- Assemble initramfs ---
FROM scratch AS base
COPY --from=core-busybox . /
COPY --from=core-musl . /
COPY --from=core-libunwind . /
COPY --from=core-gcc . /
COPY --from=core-openssl . /
COPY --from=core-zlib . /
COPY --from=core-ca-certificates . /
COPY --from=user-eif_build . /
COPY --from=user-cpio . /
COPY --from=user-linux-nitro /bzImage .
COPY --from=user-linux-nitro /linux.config .

FROM base AS build
WORKDIR /build_cpio
ENV KBUILD_BUILD_TIMESTAMP=1

RUN mkdir -p initramfs/etc/ssl/certs initramfs/tmp initramfs/app

# Core system
COPY --from=core-busybox . initramfs
COPY --from=core-ca-certificates /etc/ssl/certs initramfs/etc/ssl/certs
COPY --from=user-nit /bin/init initramfs

# socat for VSOCK bridging (must be in /bin/ for PATH)
COPY --from=user-socat /bin/socat initramfs/bin/socat

# Python runtime — use Alpine's musl + Python (self-consistent set)
COPY --from=python-build /collected-libs/ initramfs/lib/
COPY --from=python-build /usr/local/bin/python3.12 initramfs/usr/local/bin/python3
# Stdlib .py files + C extension modules (lib-dynload/*.so) + site-packages (pynacl, cbor2)
COPY --from=python-build /usr/local/lib/python3.12 initramfs/usr/local/lib/python3.12
RUN chmod 755 initramfs/usr/local/bin/python3 && \
    chmod 755 initramfs/lib/* && \
    find initramfs/usr/local/lib/python3.12/lib-dynload -name '*.so' -exec chmod 755 {} \; && \
    # musl dynamic linker config — tells musl where to find shared libs
    echo "/lib:/usr/lib:/usr/local/lib" > initramfs/etc/ld-musl-x86_64.path && \
    # Symlink libs into /usr/lib as well for any hardcoded rpaths
    mkdir -p initramfs/usr/lib && \
    for f in initramfs/lib/*.so*; do \
        bn=$(basename "$f"); \
        [ ! -e "initramfs/usr/lib/$bn" ] && ln -s "/lib/$bn" "initramfs/usr/lib/$bn" || true; \
    done && \
    echo "=== initramfs /lib/ ===" && ls -la initramfs/lib/ && \
    echo "=== initramfs /usr/lib/ ===" && ls -la initramfs/usr/lib/

# Python application
COPY --from=python-build /app/app.py initramfs/app/
COPY --from=python-build /app/src initramfs/app/src/

# Run script: start socat bridge then exec Python app
COPY <<-'RUNEOF' initramfs/run.sh
#!/bin/sh
# Setup loopback interface (enclave has no networking by default)
busybox ip addr add 127.0.0.1/32 dev lo
busybox ip link set dev lo up
echo "127.0.0.1   localhost" > /etc/hosts

export LD_LIBRARY_PATH=/lib:/usr/lib
export PYTHONPATH=/app:/usr/local/lib/python3.12/site-packages
export PYTHONHOME=/usr/local
export ENCLAVE_MODE=true

# VSOCK bridge: forward VSOCK:5000 -> TCP:localhost:5000
socat VSOCK-LISTEN:5000,fork,reuseaddr TCP:localhost:5000 &
exec /usr/local/bin/python3 /app/app.py
RUNEOF
RUN chmod +x initramfs/run.sh

# Environment
COPY <<-EOF initramfs/etc/environment
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
SSL_CERT_DIR=/etc/ssl/certs
LD_LIBRARY_PATH=/lib:/usr/lib
PATH=/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin
EOF

# Build cpio
RUN <<-EOF
    set -eux
    cd initramfs
    find . -exec touch -hcd "@0" "{}" + -print0 \
    | sort -z \
    | cpio \
        --null \
        --create \
        --verbose \
        --reproducible \
        --format=newc \
    | gzip --best \
    > /build_cpio/rootfs.cpio
EOF

# Build EIF — nit runs our shell script which starts socat + Python
WORKDIR /build_eif
RUN eif_build \
	--kernel /bzImage \
	--kernel_config /linux.config \
	--ramdisk /build_cpio/rootfs.cpio \
	--pcrs_output /nitro.pcrs \
	--output /nitro.eif \
	--cmdline 'reboot=k initrd=0x2000000,3228672 root=/dev/ram0 panic=1 pci=off nomodules console=ttyS0 i8042.noaux i8042.nomux i8042.nopnp i8042.dumbkbd nit.target=/run.sh'

# --- Output ---
FROM base AS install
WORKDIR /rootfs
COPY --from=build /nitro.eif .
COPY --from=build /nitro.pcrs .
COPY --from=build /build_cpio/rootfs.cpio .

FROM scratch AS package
COPY --from=install /rootfs .
