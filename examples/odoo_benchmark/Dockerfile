ARG RUST_IMAGE
ARG PYTHON_IMAGE
FROM ${RUST_IMAGE} AS rust-toolchain

FROM ${PYTHON_IMAGE}

ARG ODOO_GIT_SHA
ARG ODOO_REQUIREMENTS_SHA256

COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup

ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_HOME=/usr/local/cargo \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/usr/local/cargo/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUSTUP_HOME=/usr/local/rustup

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        git \
        libfreetype6-dev \
        libfribidi-dev \
        libharfbuzz-dev \
        libjpeg62-turbo-dev \
        libldap2-dev \
        liblcms2-dev \
        libopenjp2-7-dev \
        libpq-dev \
        libsasl2-dev \
        libssl-dev \
        libtiff-dev \
        libwebp-dev \
        libxcb1-dev \
        libxml2-dev \
        libxslt1-dev \
        pkg-config \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN rustc --version | tee /opt/pyroxide-rustc-version \
    && cargo --version | tee /opt/pyroxide-cargo-version \
    && test "$(rustc --version | cut -d ' ' -f 2)" = "1.86.0"

RUN git init /opt/odoo \
    && git -C /opt/odoo remote add origin https://github.com/odoo/odoo.git \
    && git -C /opt/odoo fetch --depth 1 origin "${ODOO_GIT_SHA}" \
    && git -C /opt/odoo checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/odoo rev-parse HEAD)" = "${ODOO_GIT_SHA}" \
    && test "$(sha256sum /opt/odoo/requirements.txt | cut -d ' ' -f 1)" = "${ODOO_REQUIREMENTS_SHA256}"

RUN python -m pip install --requirement /opt/odoo/requirements.txt

COPY Cargo.lock Cargo.toml LICENSE-APACHE LICENSE-MIT README.md pyproject.toml /opt/pyroxide-source/
COPY python /opt/pyroxide-source/python
COPY src /opt/pyroxide-source/src
RUN python -m pip install /opt/pyroxide-source

COPY examples/odoo_benchmark/container_entrypoint.py /opt/pyroxide-source/examples/odoo_benchmark/
COPY examples/odoo_benchmark/ledger_kernel.py /opt/pyroxide-source/examples/odoo_benchmark/

WORKDIR /opt/odoo
ENTRYPOINT ["python", "/opt/pyroxide-source/examples/odoo_benchmark/container_entrypoint.py"]
