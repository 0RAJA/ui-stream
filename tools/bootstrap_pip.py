"""从固定官方 wheel 安全引导当前环境的 pip，不用旧 pip 联网自升级。

URL 和 SHA-256 与 Python 官方安全公告及 PyPI 元数据交叉核验。
下载保留正常 TLS 校验；只有哈希通过的有限大小内容才能交给离线安装器。
仅修改当前解释器环境，不调整系统信任或其他虚拟环境。
"""

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

PIP_VERSION = "26.2.2"
PIP_FILENAME = f"pip-{PIP_VERSION}-py3-none-any.whl"
PIP_URL = (
    "https://files.pythonhosted.org/packages/3f/06/"
    "3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/" + PIP_FILENAME
)
PIP_SHA256 = "fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
MAX_WHEEL_BYTES = 8 * 1024 * 1024
_WHEEL_RUNNER = (
    "import runpy,sys;wheel=sys.argv.pop(1);sys.path.insert(0,wheel);" "runpy.run_module('pip',run_name='__main__')"
)


def verify_wheel(payload: bytes, expected_sha256: str) -> None:
    """拒绝与已核验摘要不一致的内容，避免被替换的 CA 信任链影响代码完整性。"""

    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("Official pip wheel SHA-256 mismatch")


def bootstrap() -> None:
    """校验官方 wheel 后离线安装；失败直接退出，不回退到不安全下载。"""

    if sys.version_info < (3, 10):
        raise RuntimeError("This pip wheel needs Python >=3.10; use a supported vendor fix for older Python")
    with urlopen(PIP_URL, timeout=30) as response:
        payload = response.read(MAX_WHEEL_BYTES + 1)
    if len(payload) > MAX_WHEEL_BYTES:
        raise ValueError("Official pip wheel exceeds the download limit")
    verify_wheel(payload, PIP_SHA256)
    with tempfile.TemporaryDirectory(prefix="ui-stream-pip-bootstrap-") as folder:
        wheel = Path(folder) / PIP_FILENAME
        wheel.write_bytes(payload)
        # 直接运行已校验 wheel，兼容没有 pip 的新环境；安装过程禁止索引和依赖下载。
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                _WHEEL_RUNNER,
                str(wheel),
                "--isolated",
                "install",
                "--no-index",
                "--no-deps",
                "--disable-pip-version-check",
                str(wheel),
            ],
            check=True,
        )


if __name__ == "__main__":
    bootstrap()
