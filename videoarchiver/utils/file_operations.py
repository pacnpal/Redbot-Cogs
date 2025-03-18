"""Safe file operations with retry logic"""

import os
import shutil
import asyncio
import logging
import json
import subprocess
from typing import Tuple
from pathlib import Path

from utils.exceptions import VideoVerificationError
from utils.file_deletion import SecureFileDeleter
from security import safe_command

logger = logging.getLogger("VideoArchiver")


class FileOperations:
    """Handles safe file operations with retries"""

    def __init__(self, max_retries: int = 3, retry_delay: int = 1):
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def safe_delete_file(self, file_path: str) -> bool:
        """Safely delete a file with retries"""
        for attempt in range(self.max_retries):
            try:
                if os.path.exists(file_path):
                    await SecureFileDeleter(file_path)
                return True
            except Exception as e:
                logger.error(f"Delete attempt {attempt + 1} failed: {str(e)}")
                if attempt == self.max_retries - 1:
                    return False
                await asyncio.sleep(self.retry_delay * (attempt + 1))
        return False

    async def safe_move_file(self, src: str, dst: str) -> bool:
        """Safely move a file with retries"""
        for attempt in range(self.max_retries):
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                return True
            except Exception as e:
                logger.error(f"Move attempt {attempt + 1} failed: {str(e)}")
                if attempt == self.max_retries - 1:
                    return False
                await asyncio.sleep(self.retry_delay * (attempt + 1))
        return False

    def verify_video_file(self, file_path: str, ffprobe_path: str) -> bool:
        """Verify video file integrity"""
        try:
            cmd = [
                ffprobe_path,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                file_path,
            ]

            result = safe_command.run(subprocess.run, cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                raise VideoVerificationError(f"FFprobe failed: {result.stderr}")

            probe = json.loads(result.stdout)

            # Verify video stream
            video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
            if not video_streams:
                raise VideoVerificationError("No video streams found")

            # Verify duration
            duration = float(probe["format"].get("duration", 0))
            if duration <= 0:
                raise VideoVerificationError("Invalid video duration")

            # Verify file is readable
            try:
                with open(file_path, "rb") as f:
                    f.seek(0, 2)
                    if f.tell() == 0:
                        raise VideoVerificationError("Empty file")
            except Exception as e:
                raise VideoVerificationError(f"File read error: {str(e)}")

            return True

        except subprocess.TimeoutExpired:
            logger.error(f"FFprobe timed out for {file_path}")
            return False
        except json.JSONDecodeError:
            logger.error(f"Invalid FFprobe output for {file_path}")
            return False
        except Exception as e:
            logger.error(f"Error verifying video file {file_path}: {e}")
            return False

    def get_video_duration(self, file_path: str, ffprobe_path: str) -> float:
        """Get video duration in seconds"""
        try:
            cmd = [
                ffprobe_path,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                file_path,
            ]
            result = safe_command.run(subprocess.run, cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"FFprobe failed: {result.stderr}")

            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except Exception as e:
            logger.error(f"Error getting video duration: {e}")
            return 0

    def check_file_size(self, file_path: str, max_size_mb: int) -> Tuple[bool, int]:
        """Check if file size is within limits"""
        try:
            if os.path.exists(file_path):
                size = os.path.getsize(file_path)
                max_size = max_size_mb * 1024 * 1024
                return size <= max_size, size
            return False, 0
        except Exception as e:
            logger.error(f"Error checking file size: {e}")
            return False, 0
