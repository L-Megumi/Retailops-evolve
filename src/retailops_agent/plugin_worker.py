from __future__ import annotations

import json
import sys
import ctypes
import platform


SAFE_BUILTINS = {
    "dict": dict,
    "enumerate": enumerate,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "sorted": sorted,
    "str": str,
    "sum": sum,
}


def _apply_limits() -> None:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
    memory = 128 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))


def _lock_syscalls() -> None:
    """Deny filesystem, network, process creation and exec syscalls after setup.

    Linux classic-BPF seccomp filter; unsupported architectures fail closed.
    The architecture check prevents using an alternate syscall ABI.
    """
    architecture = platform.machine().lower()
    if architecture in {"x86_64", "amd64"}:
        audit_arch = 0xC000003E
        allowed = (0, 1, 3, 5, 8, 9, 10, 11, 12, 13, 14, 15, 24, 25, 28,
                   39, 60, 96, 131, 158, 186, 202, 218, 228, 231, 273, 318, 334)
    elif architecture in {"aarch64", "arm64"}:
        audit_arch = 0xC00000B7
        allowed = (57, 62, 63, 64, 80, 93, 94, 96, 98, 99, 101, 113, 124,
                   132, 134, 135, 139, 169, 172, 178, 214, 215, 216, 222, 226, 233, 278, 293)
    else:
        raise RuntimeError("unsupported seccomp architecture")

    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                    ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]

    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    # seccomp_data.arch at offset 4; nr at offset 0. Wrong ABI kills the worker.
    instructions = [(0x20, 0, 0, 4), (0x15, 1, 0, audit_arch),
                    (0x06, 0, 0, 0x80000000), (0x20, 0, 0, 0)]
    for number in allowed:
        instructions.extend([(0x15, 0, 1, number), (0x06, 0, 0, 0x7FFF0000)])
    instructions.append((0x06, 0, 0, 0x00050001))  # default EPERM
    filters = (Filter * len(instructions))(*(Filter(*row) for row in instructions))
    program = Program(len(instructions), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "cannot enable no_new_privs")
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:  # PR_SET_SECCOMP
        raise OSError(ctypes.get_errno(), "cannot install seccomp filter")


def main() -> int:
    _apply_limits()
    payload = json.loads(sys.stdin.read(4 * 1024 * 1024 + 1))
    namespace: dict[str, object] = {"__builtins__": SAFE_BUILTINS}
    code = compile(payload["source"], "<candidate-plugin>", "exec")
    _lock_syscalls()
    exec(code, namespace, namespace)
    entrypoint = str(payload["entrypoint"])
    function = namespace[entrypoint]
    result = function(*(payload["arguments"][name] for name in payload["argument_names"]))
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("plugin output exceeds 4 MiB")
    sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
