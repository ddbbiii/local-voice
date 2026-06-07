from __future__ import annotations

import os
import subprocess
from datetime import datetime

import psutil

from .models import ResourceUsageSnapshot


class ResourceMonitor:
    def __init__(self) -> None:
        self._root = psutil.Process(os.getpid())
        self._logical_cpu_count = max(psutil.cpu_count(logical=True) or 1, 1)
        self._prime_cpu_counters()

    def sample(self) -> ResourceUsageSnapshot:
        processes = self._process_tree()
        cpu_percent = 0.0
        memory_bytes = 0
        for process in processes:
            try:
                cpu_percent += process.cpu_percent(None)
                memory_bytes += process.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        gpu_util_percent, gpu_memory_mb, gpu_scope = self._sample_gpu()
        normalized_cpu = min(max(cpu_percent / self._logical_cpu_count, 0.0), 100.0)

        return ResourceUsageSnapshot(
            cpu_percent=round(normalized_cpu, 1),
            memory_mb=round(memory_bytes / 1024 / 1024, 1),
            gpu_util_percent=gpu_util_percent,
            gpu_memory_mb=gpu_memory_mb,
            gpu_scope=gpu_scope,
            sampled_at=datetime.utcnow(),
        )

    def _process_tree(self) -> list[psutil.Process]:
        try:
            children = self._root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            children = []
        return [self._root, *children]

    def _prime_cpu_counters(self) -> None:
        for process in self._process_tree():
            try:
                process.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _sample_gpu(self) -> tuple[float | None, float | None, str]:
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except (OSError, subprocess.SubprocessError):
            return None, None, "unavailable"

        output = completed.stdout.strip()
        if completed.returncode != 0 or not output:
            return None, None, "unavailable"

        line = output.splitlines()[0]
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            return None, None, "unavailable"

        try:
            return float(parts[0]), float(parts[1]), "device"
        except ValueError:
            return None, None, "unavailable"
