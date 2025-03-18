"""GPU detection functionality for FFmpeg"""

import os
import subprocess
import logging
import platform
import re
from typing import Dict, List, Tuple
from pathlib import Path
from security import safe_command

logger = logging.getLogger("VideoArchiver")

class GPUDetector:
    def __init__(self, ffmpeg_path: Path):
        """Initialize GPU detector
        
        Args:
            ffmpeg_path: Path to FFmpeg binary
        """
        self.ffmpeg_path = Path(ffmpeg_path)
        if not self.ffmpeg_path.exists():
            raise FileNotFoundError(f"FFmpeg not found at {self.ffmpeg_path}")

    def detect_gpu(self) -> Dict[str, bool]:
        """Detect available GPU acceleration support
        
        Returns:
            Dict containing boolean flags for each GPU type
        """
        gpu_info = {
            "nvidia": False,
            "amd": False,
            "intel": False
        }

        try:
            # First detect physical GPUs
            physical_gpus = self._detect_physical_gpus()
            
            # Then check FFmpeg support
            ffmpeg_support = self._verify_ffmpeg_gpu_support()
            
            # Only enable GPU if both physical GPU exists and FFmpeg supports it
            gpu_info["nvidia"] = physical_gpus["nvidia"] and ffmpeg_support["nvidia"]
            gpu_info["amd"] = physical_gpus["amd"] and ffmpeg_support["amd"]
            gpu_info["intel"] = physical_gpus["intel"] and ffmpeg_support["intel"]

            # Log detection results
            detected_gpus = [name for name, detected in gpu_info.items() if detected]
            if detected_gpus:
                logger.info(f"Detected GPUs with FFmpeg support: {', '.join(detected_gpus)}")
            else:
                logger.info("No GPU acceleration available")

            return gpu_info

        except Exception as e:
            logger.error(f"Error during GPU detection: {str(e)}")
            return {"nvidia": False, "amd": False, "intel": False}

    def _detect_physical_gpus(self) -> Dict[str, bool]:
        """Detect physical GPUs in the system"""
        system = platform.system().lower()
        if system == "windows":
            return self._detect_windows_gpu()
        elif system == "linux":
            return self._detect_linux_gpu()
        elif system == "darwin":
            return self._detect_macos_gpu()
        return {"nvidia": False, "amd": False, "intel": False}

    def _detect_windows_gpu(self) -> Dict[str, bool]:
        """Detect GPUs on Windows using PowerShell"""
        gpu_info = {"nvidia": False, "amd": False, "intel": False}
        
        try:
            # Use PowerShell to get GPU info
            cmd = ["powershell", "-Command", "Get-WmiObject Win32_VideoController | Select-Object Name"]
            result = safe_command.run(subprocess.run, cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                output = result.stdout.lower()
                gpu_info["nvidia"] = "nvidia" in output
                gpu_info["amd"] = any(x in output for x in ["amd", "radeon"])
                gpu_info["intel"] = "intel" in output
                
        except Exception as e:
            logger.error(f"Windows GPU detection failed: {str(e)}")
            
        return gpu_info

    def _detect_linux_gpu(self) -> Dict[str, bool]:
        """Detect GPUs on Linux using multiple methods"""
        gpu_info = {"nvidia": False, "amd": False, "intel": False}
        
        try:
            # Check for NVIDIA GPU
            try:
                result = subprocess.run(
                    ["nvidia-smi"],
                    capture_output=True,
                    timeout=10
                )
                gpu_info["nvidia"] = result.returncode == 0
            except FileNotFoundError:
                pass

            # Check for AMD GPU using DRI
            if os.path.exists("/dev/dri"):
                try:
                    result = subprocess.run(
                        ["ls", "/dev/dri/render*"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0:
                        # Check device info using lspci
                        lspci_result = subprocess.run(
                            ["lspci", "-v"],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        if lspci_result.returncode == 0:
                            output = lspci_result.stdout.lower()
                            gpu_info["amd"] = any(x in output for x in ["amd", "radeon", "advanced micro devices"])
                            gpu_info["intel"] = "intel" in output and "graphics" in output
                except (FileNotFoundError, subprocess.SubprocessError):
                    pass

            # Additional check for Intel GPU
            if not gpu_info["intel"] and os.path.exists("/sys/class/drm"):
                try:
                    result = subprocess.run(
                        ["find", "/sys/class/drm", "-name", "*i915*"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    gpu_info["intel"] = bool(result.stdout.strip())
                except subprocess.SubprocessError:
                    pass

        except Exception as e:
            logger.error(f"Linux GPU detection failed: {str(e)}")
            
        return gpu_info

    def _detect_macos_gpu(self) -> Dict[str, bool]:
        """Detect GPUs on macOS using system_profiler"""
        gpu_info = {"nvidia": False, "amd": False, "intel": False}
        
        try:
            cmd = ["system_profiler", "SPDisplaysDataType"]
            result = safe_command.run(subprocess.run, cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                output = result.stdout.lower()
                gpu_info["nvidia"] = "nvidia" in output
                gpu_info["amd"] = any(x in output for x in ["amd", "radeon"])
                gpu_info["intel"] = "intel" in output
                
        except Exception as e:
            logger.error(f"macOS GPU detection failed: {str(e)}")
            
        return gpu_info

    def _verify_ffmpeg_gpu_support(self) -> Dict[str, bool]:
        """Verify GPU support in FFmpeg installation"""
        gpu_support = {"nvidia": False, "amd": False, "intel": False}
        
        try:
            # Check FFmpeg encoders
            cmd = [str(self.ffmpeg_path), "-hide_banner", "-encoders"]
            result = safe_command.run(subprocess.run, cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                output = result.stdout.lower()
                
                # Check for specific GPU encoders
                gpu_support["nvidia"] = "h264_nvenc" in output
                gpu_support["amd"] = "h264_amf" in output
                gpu_support["intel"] = "h264_qsv" in output

                # Log available encoders
                encoders = []
                if gpu_support["nvidia"]:
                    encoders.append("NVENC")
                if gpu_support["amd"]:
                    encoders.append("AMF")
                if gpu_support["intel"]:
                    encoders.append("QSV")
                
                if encoders:
                    logger.info(f"FFmpeg compiled with GPU encoders: {', '.join(encoders)}")
                else:
                    logger.info("No GPU encoders available in FFmpeg")

        except Exception as e:
            logger.error(f"FFmpeg GPU support verification failed: {str(e)}")
            
        return gpu_support

    def get_gpu_info(self) -> Dict[str, List[str]]:
        """Get detailed GPU information"""
        gpu_info = {
            "nvidia": [],
            "amd": [],
            "intel": []
        }

        try:
            system = platform.system().lower()
            
            if system == "linux":
                try:
                    # Try lspci first
                    result = subprocess.run(
                        ["lspci", "-v"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0:
                        for line in result.stdout.splitlines():
                            if "vga" in line.lower() or "3d" in line.lower():
                                if "nvidia" in line.lower():
                                    gpu_info["nvidia"].append(line.strip())
                                elif any(x in line.lower() for x in ["amd", "radeon"]):
                                    gpu_info["amd"].append(line.strip())
                                elif "intel" in line.lower():
                                    gpu_info["intel"].append(line.strip())
                except FileNotFoundError:
                    pass

                # Try nvidia-smi for NVIDIA GPUs
                if not gpu_info["nvidia"]:
                    try:
                        result = subprocess.run(
                            ["nvidia-smi", "-L"],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        if result.returncode == 0:
                            gpu_info["nvidia"].extend(line.strip() for line in result.stdout.splitlines() if line.strip())
                    except FileNotFoundError:
                        pass

            elif system == "windows":
                cmd = ["powershell", "-Command", "Get-WmiObject Win32_VideoController | Select-Object Name"]
                result = safe_command.run(subprocess.run, cmd, capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        line = line.strip()
                        if line and not line.startswith("Name"):
                            if "nvidia" in line.lower():
                                gpu_info["nvidia"].append(line)
                            elif any(x in line.lower() for x in ["amd", "radeon"]):
                                gpu_info["amd"].append(line)
                            elif "intel" in line.lower():
                                gpu_info["intel"].append(line)

            elif system == "darwin":
                cmd = ["system_profiler", "SPDisplaysDataType"]
                result = safe_command.run(subprocess.run, cmd, capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0:
                    current_gpu = None
                    for line in result.stdout.splitlines():
                        line = line.strip()
                        if "Chipset Model:" in line:
                            model = line.split(":", 1)[1].strip()
                            if "nvidia" in model.lower():
                                gpu_info["nvidia"].append(model)
                            elif any(x in model.lower() for x in ["amd", "radeon"]):
                                gpu_info["amd"].append(model)
                            elif "intel" in model.lower():
                                gpu_info["intel"].append(model)

        except Exception as e:
            logger.error(f"Error getting detailed GPU info: {str(e)}")

        return gpu_info
