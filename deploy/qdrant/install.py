"""Install the pinned official Linux x86_64 static Qdrant binary as this user."""
from __future__ import annotations

import hashlib
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile
import urllib.request

VERSION = "1.20.2"
SHA256 = "7c3e956b97830a9faa6391540330380c2fa4aa2f92df99bd8d1880a703b922af"
URL = f"https://github.com/qdrant/qdrant/releases/download/v{VERSION}/qdrant-x86_64-unknown-linux-musl.tar.gz"


def main():
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("this installer supports Linux x86_64 only")
    user_root = Path.home()
    binary_root = user_root / f".local/opt/retailops-qdrant-v{VERSION}-musl"
    config = user_root / ".config/retailops/qdrant.yaml"
    data = user_root / ".local/share/retailops-qdrant"
    unit_dir = user_root / ".config/systemd/user"
    for directory in (binary_root, config.parent, data, unit_dir):
        directory.mkdir(parents=True, exist_ok=True)
    if config.exists():
        raise SystemExit("config already exists; retain it and manage the existing service explicitly")
    with urllib.request.urlopen(URL, timeout=120) as response:
        raw = response.read()
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise RuntimeError("release artifact does not match the pinned checksum")
    with tempfile.TemporaryFile() as archive:
        archive.write(raw)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tar:
            matches = [m for m in tar.getmembers() if Path(m.name).name == "qdrant" and m.isfile()]
            if len(matches) != 1:
                raise RuntimeError("unexpected release archive")
            (binary_root / "qdrant").write_bytes(tar.extractfile(matches[0]).read())
    (binary_root / "qdrant").chmod(0o755)
    subprocess.run([str(binary_root / "qdrant"), "--version"], check=True)
    config.write_text(f"storage:\n  storage_path: {data}/storage\n  snapshots_path: {data}/snapshots\n"
                      "service:\n  host: 127.0.0.1\n  http_port: 6333\n  grpc_port: 6334\n"
                      "telemetry_disabled: true\n", encoding="utf-8")
    (unit_dir / "retailops-qdrant.service").write_text(
        Path(__file__).with_name("retailops-qdrant.service").read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "retailops-qdrant.service"], check=True)


if __name__ == "__main__":
    main()
