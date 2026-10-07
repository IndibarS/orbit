ARG BASE_IMAGE=debian:trixie
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 update \
 && apt-get install -y --no-install-recommends python3 python3-apt python3-gi \
    python3-venv python3-hatchling gir1.2-gtk-4.0 gir1.2-adw-1 \
    gir1.2-appstream-1.0 pkexec xvfb xauth dbus-x11 dpkg-dev ca-certificates \
 && python3 -m venv --system-site-packages /opt/orbit-env \
 && /opt/orbit-env/bin/pip install --no-cache-dir uv==0.9.13
ENV PATH="/opt/orbit-env/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin" \
    UV_PROJECT_ENVIRONMENT=/opt/orbit-env \
    GTK_IM_MODULE=simple GDK_BACKEND=x11 GSK_RENDERER=cairo \
    GSETTINGS_BACKEND=memory PYTHONWARNINGS=ignore::DeprecationWarning
RUN apt-get install -y --no-install-recommends python3-editables
ARG EXPECTED_DISTRO
ENV ORBIT_EXPECTED_DISTRO=${EXPECTED_DISTRO}
# The Mint build image can retain Ubuntu base-files; use Mint's real package.
RUN if [ "$EXPECTED_DISTRO" = linuxmint ]; then \
      apt-get install -y --allow-downgrades --no-install-recommends base-files/zena; \
    fi
WORKDIR /app
COPY . /app
RUN uv sync --active --offline --no-build-isolation
CMD ["python", "tests/docker/run_checks.py"]
