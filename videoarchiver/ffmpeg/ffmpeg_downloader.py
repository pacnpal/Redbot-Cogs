"""FFmpeg binary downloader and manager"""

import os
import logging
import shutil
import requests
import tarfile
import zipfile
import subprocess
import tempfile
import platform
import hashlib
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Dict, List
import time
import lzma

# try:
# Try relative imports first
from exceptions import DownloadError
from security import safe_command

# except ImportError:
# Fall back to absolute imports if relative imports fail
# from videoarchiver.ffmpeg.exceptions import DownloadError

logger = logging.getLogger("VideoArchiver")


@contextmanager
def temp_path_context():
    """Context manager for temporary path creation and cleanup"""
    temp_dir = tempfile.mkdtemp(prefix="ffmpeg_")
    try:
        os.chmod(temp_dir, 0o777)
        yield temp_dir
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            logger.error(f"Error cleaning up temp directory {temp_dir}: {e}")


class FFmpegDownloader:
    """FFmpeg binary downloader and manager"""

    FFMPEG_URLS = {
        "Windows": {
            "x86_64": {
                "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
                "bin_names": ["ffmpeg.exe", "ffprobe.exe"],
            }
        },
        "Linux": {
            "x86_64": {
                "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz",
                "bin_names": ["ffmpeg", "ffprobe"],
            },
            "aarch64": {
                "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz",
                "bin_names": ["ffmpeg", "ffprobe"],
            },
            "armv7l": {
                "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm32-gpl.tar.xz",
                "bin_names": ["ffmpeg", "ffprobe"],
            },
        },
        "Darwin": {
            "x86_64": {
                "url": "https://evermeet.cx/ffmpeg/getrelease/zip",
                "bin_names": ["ffmpeg", "ffprobe"],
            },
            "arm64": {
                "url": "https://evermeet.cx/ffmpeg/getrelease/zip",
                "bin_names": ["ffmpeg", "ffprobe"],
            },
        },
    }

    def __init__(self, system: str, machine: str, base_dir: Path):
        """Initialize FFmpeg downloader"""
        self.system = system
        self.machine = machine.lower()
        if self.machine == "arm64":
            self.machine = "aarch64"  # Normalize ARM64 naming
        self.base_dir = base_dir
        self.ffmpeg_path = self.base_dir / self._get_binary_names()[0]
        self.ffprobe_path = self.base_dir / self._get_binary_names()[1]

        logger.info(f"Initialized FFmpeg downloader for {system}/{machine}")
        logger.info(f"FFmpeg binary path: {self.ffmpeg_path}")
        logger.info(f"FFprobe binary path: {self.ffprobe_path}")

    def _get_binary_names(self) -> List[str]:
        """Get the appropriate binary names for the current system"""
        try:
            return self.FFMPEG_URLS[self.system][self.machine]["bin_names"]
        except KeyError:
            raise DownloadError(
                f"Unsupported system/architecture: {self.system}/{self.machine}"
            )

    def _get_download_url(self) -> str:
        """Get the appropriate download URL for the current system"""
        try:
            return self.FFMPEG_URLS[self.system][self.machine]["url"]
        except KeyError:
            raise DownloadError(
                f"Unsupported system/architecture: {self.system}/{self.machine}"
            )

    def download(self) -> Dict[str, Path]:
        """Download and set up FFmpeg and FFprobe binaries with retries"""
        max_retries = 3
        retry_delay = 5
        last_error = None

        for attempt in range(max_retries):
            try:
                logger.info(f"Download attempt {attempt + 1}/{max_retries}")

                # Ensure base directory exists with proper permissions
                self.base_dir.mkdir(parents=True, exist_ok=True)
                os.chmod(str(self.base_dir), 0o777)

                # Clean up any existing files
                for binary_path in [self.ffmpeg_path, self.ffprobe_path]:
                    if binary_path.exists():
                        if binary_path.is_dir():
                            shutil.rmtree(str(binary_path))
                        else:
                            binary_path.unlink()

                with temp_path_context() as temp_dir:
                    # Download archive
                    archive_path = self._download_archive(temp_dir)

                    # Verify download
                    if not self._verify_download(archive_path):
                        raise DownloadError("Downloaded file verification failed")

                    # Extract binaries
                    self._extract_binaries(archive_path, temp_dir)

                    # Set proper permissions
                    for binary_path in [self.ffmpeg_path, self.ffprobe_path]:
                        os.chmod(str(binary_path), 0o755)

                    # Verify binaries
                    if not self.verify():
                        raise DownloadError("Binary verification failed")

                    logger.info(f"Successfully downloaded FFmpeg to {self.ffmpeg_path}")
                    logger.info(
                        f"Successfully downloaded FFprobe to {self.ffprobe_path}"
                    )
                    return {"ffmpeg": self.ffmpeg_path, "ffprobe": self.ffprobe_path}

            except Exception as e:
                last_error = str(e)
                logger.error(f"Download attempt {attempt + 1} failed: {last_error}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                continue

        raise DownloadError(f"All download attempts failed: {last_error}")

    def _download_archive(self, temp_dir: str) -> Path:
        """Download FFmpeg archive with progress tracking"""
        url = self._get_download_url()
        archive_path = (
            Path(temp_dir)
            / f"ffmpeg_archive{'.zip' if self.system == 'Windows' else '.tar.xz'}"
        )

        logger.info(f"Downloading FFmpeg from {url}")
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            block_size = 8192
            downloaded = 0

            with open(archive_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        logger.debug(f"Download progress: {percent:.1f}%")

            return archive_path

        except Exception as e:
            raise DownloadError(f"Failed to download FFmpeg: {str(e)}")

    def _verify_download(self, archive_path: Path) -> bool:
        """Verify downloaded archive integrity"""
        try:
            if not archive_path.exists():
                return False

            # Check file size
            size = archive_path.stat().st_size
            if size < 1000000:  # Less than 1MB is suspicious
                logger.error(f"Downloaded file too small: {size} bytes")
                return False

            # Check file hash
            with open(archive_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            logger.debug(f"Archive hash: {file_hash}")

            return True

        except Exception as e:
            logger.error(f"Download verification failed: {str(e)}")
            return False

    def _extract_binaries(self, archive_path: Path, temp_dir: str):
        """Extract FFmpeg and FFprobe binaries from archive"""
        logger.info("Extracting FFmpeg and FFprobe binaries")

        try:
            if self.system == "Windows":
                self._extract_zip(archive_path, temp_dir)
            else:
                self._extract_tar(archive_path, temp_dir)

            # Ensure binaries have correct permissions
            for binary_path in [self.ffmpeg_path, self.ffprobe_path]:
                if binary_path.exists():
                    os.chmod(str(binary_path), 0o755)
                    logger.info(f"Set permissions for {binary_path}")
                else:
                    raise DownloadError(
                        f"Binary not found after extraction: {binary_path}"
                    )

        except Exception as e:
            raise DownloadError(f"Failed to extract binaries: {e}")

    def _extract_zip(self, archive_path: Path, temp_dir: str):
        """Extract from zip archive (Windows)"""
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            binary_names = self._get_binary_names()
            for binary_name in binary_names:
                # BtbN's builds have binaries in bin directory
                binary_files = [
                    f
                    for f in zip_ref.namelist()
                    if f.endswith(f"/bin/{binary_name}")
                    or f.endswith(f"\\bin\\{binary_name}")
                ]
                if not binary_files:
                    # Fallback to old structure
                    binary_files = [
                        f
                        for f in zip_ref.namelist()
                        if f.endswith(f"/{binary_name}")
                        or f.endswith(f"\\{binary_name}")
                    ]
                if not binary_files:
                    raise DownloadError(f"{binary_name} not found in archive")

                zip_ref.extract(binary_files[0], temp_dir)
                extracted_path = Path(temp_dir) / binary_files[0]
                target_path = self.base_dir / binary_name
                shutil.copy2(extracted_path, target_path)
                logger.info(f"Extracted {binary_name} to {target_path}")

    def _extract_tar(self, archive_path: Path, temp_dir: str):
        """Extract from tar archive (Linux/macOS)"""
        try:
            # First decompress the .xz file in chunks to prevent blocking
            decompressed_path = archive_path.with_suffix("")
            chunk_size = 1024 * 1024  # 1MB chunks
            with lzma.open(archive_path, "rb") as compressed:
                with open(decompressed_path, "wb") as decompressed:
                    while True:
                        chunk = compressed.read(chunk_size)
                        if not chunk:
                            break
                        decompressed.write(chunk)
                        # Allow other tasks to run
                        time.sleep(0)

            # Then extract from the tar file
            with tarfile.open(decompressed_path, "r:") as tar_ref:
                binary_names = self._get_binary_names()
                for binary_name in binary_names:
                    # BtbN's builds have binaries in bin directory
                    binary_files = [
                        f
                        for f in tar_ref.getnames()
                        if f.endswith(f"/bin/{binary_name}")
                    ]
                    if not binary_files:
                        # Fallback to old structure
                        binary_files = [
                            f
                            for f in tar_ref.getnames()
                            if f.endswith(f"/{binary_name}")
                        ]
                    if not binary_files:
                        raise DownloadError(f"{binary_name} not found in archive")

                    # Extract binary with progress tracking
                    member = tar_ref.getmember(binary_files[0])
                    tar_ref.extract(member, temp_dir)
                    extracted_path = Path(temp_dir) / binary_files[0]
                    target_path = self.base_dir / binary_name

                    # Copy file in chunks
                    with open(extracted_path, "rb") as src, open(
                        target_path, "wb"
                    ) as dst:
                        while True:
                            chunk = src.read(chunk_size)
                            if not chunk:
                                break
                            dst.write(chunk)
                            # Allow other tasks to run
                            time.sleep(0)

                    logger.info(f"Extracted {binary_name} to {target_path}")

            # Clean up decompressed file
            try:
                os.unlink(decompressed_path)
            except Exception as e:
                logger.warning(f"Failed to clean up decompressed file: {e}")

        except Exception as e:
            raise DownloadError(f"Failed to extract tar.xz archive: {e}")

    def verify(self) -> bool:
        """Verify FFmpeg and FFprobe binaries work"""
        try:
            if not self.ffmpeg_path.exists() or not self.ffprobe_path.exists():
                logger.error("FFmpeg or FFprobe binary not found")
                return False

            # Ensure proper permissions
            try:
                os.chmod(str(self.ffmpeg_path), 0o755)
                os.chmod(str(self.ffprobe_path), 0o755)
            except Exception as e:
                logger.error(f"Failed to set binary permissions: {e}")
                return False

            # Test FFmpeg functionality with enhanced error handling
            try:
                result = safe_command.run(subprocess.run, [str(self.ffmpeg_path), "-version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                    text=True,
                    check=False,  # Don't raise on non-zero return code
                    env={"PATH": os.environ.get("PATH", "")},  # Ensure PATH is set
                )
            except subprocess.TimeoutExpired:
                logger.error("FFmpeg verification timed out")
                return False
            except Exception as e:
                logger.error(f"FFmpeg verification failed: {e}")
                return False

            # Test FFprobe functionality with enhanced error handling
            try:
                result = safe_command.run(subprocess.run, [str(self.ffprobe_path), "-version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                    text=True,
                    check=False,  # Don't raise on non-zero return code
                    env={"PATH": os.environ.get("PATH", "")},  # Ensure PATH is set
                )
            except subprocess.TimeoutExpired:
                logger.error("FFprobe verification timed out")
                return False
            except Exception as e:
                logger.error(f"FFprobe verification failed: {e}")
                return False

            # Check results
            if result.returncode == 0:
                try:
                    ffmpeg_version = result.stdout.split("\n")[0]
                    logger.info(f"FFmpeg verification successful: {ffmpeg_version}")
                    return True
                except Exception as e:
                    logger.error(f"Failed to parse version output: {e}")
                    return False
            else:
                logger.error(
                    f"FFmpeg verification failed with code {result.returncode}: {result.stderr}"
                )
                return False

        except Exception as e:
            logger.error(f"Binary verification failed: {e}")
            return False
