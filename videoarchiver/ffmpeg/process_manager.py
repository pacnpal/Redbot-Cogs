"""Module for managing FFmpeg processes"""

import logging
import psutil # type: ignore
import subprocess
import time
from typing import Set, Optional
from security import safe_command

logger = logging.getLogger("FFmpegProcessManager")

class ProcessManager:
    """Manages FFmpeg process execution and lifecycle"""

    def __init__(self):
        self._active_processes: Set[subprocess.Popen] = set()

    def add_process(self, process: subprocess.Popen) -> None:
        """Add a process to track"""
        self._active_processes.add(process)

    def remove_process(self, process: subprocess.Popen) -> None:
        """Remove a process from tracking"""
        self._active_processes.discard(process)

    def kill_all_processes(self) -> None:
        """Kill all active FFmpeg processes"""
        try:
            # First try graceful termination
            self._terminate_processes()

            # Give processes a moment to terminate
            time.sleep(0.5)

            # Force kill any remaining processes
            self._kill_remaining_processes()

            # Find and kill any orphaned FFmpeg processes
            self._kill_orphaned_processes()

            self._active_processes.clear()
            logger.info("All FFmpeg processes terminated")

        except Exception as e:
            logger.error(f"Error killing FFmpeg processes: {e}")

    def _terminate_processes(self) -> None:
        """Attempt graceful termination of processes"""
        for process in self._active_processes:
            try:
                if process.poll() is None:  # Process is still running
                    process.terminate()
            except Exception as e:
                logger.error(f"Error terminating FFmpeg process: {e}")

    def _kill_remaining_processes(self) -> None:
        """Force kill any remaining processes"""
        for process in self._active_processes:
            try:
                if process.poll() is None:  # Process is still running
                    process.kill()
            except Exception as e:
                logger.error(f"Error killing FFmpeg process: {e}")

    def _kill_orphaned_processes(self) -> None:
        """Find and kill any orphaned FFmpeg processes"""
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'ffmpeg' in proc.info['name'].lower():
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
            except Exception as e:
                logger.error(f"Error killing orphaned FFmpeg process: {e}")

    def execute_command(
        self,
        command: list,
        timeout: Optional[int] = None,
        check: bool = False
    ) -> subprocess.CompletedProcess:
        """Execute an FFmpeg command with proper process management
        
        Args:
            command: Command list to execute
            timeout: Optional timeout in seconds
            check: Whether to check return code
            
        Returns:
            subprocess.CompletedProcess: Result of command execution
        """
        process = None
        try:
            process = safe_command.run(subprocess.Popen, command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            self.add_process(process)

            stdout, stderr = process.communicate(timeout=timeout)
            result = subprocess.CompletedProcess(
                args=command,
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr
            )

            if check and process.returncode != 0:
                raise subprocess.CalledProcessError(
                    returncode=process.returncode,
                    cmd=command,
                    output=stdout,
                    stderr=stderr
                )

            return result

        except subprocess.TimeoutExpired:
            if process:
                process.kill()
                _, stderr = process.communicate()
            raise

        finally:
            if process:
                self.remove_process(process)
