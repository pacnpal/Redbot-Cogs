"""Queue cleanup operations"""

import asyncio
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Any, Tuple
from datetime import datetime, timedelta

from models import QueueItem, QueueMetrics
from cleaners.history_cleaner import (
    HistoryCleaner,
    CleanupStrategy as HistoryStrategy
)
from cleaners.guild_cleaner import (
    GuildCleaner,
    GuildCleanupStrategy
)
from cleaners.tracking_cleaner import (
    TrackingCleaner,
    TrackingCleanupStrategy
)

logger = logging.getLogger("QueueCleanup")

class CleanupMode(Enum):
    """Cleanup operation modes"""
    NORMAL = "normal"      # Regular cleanup
    AGGRESSIVE = "aggressive"  # More aggressive cleanup
    MAINTENANCE = "maintenance"  # Maintenance mode cleanup
    EMERGENCY = "emergency"    # Emergency cleanup

class CleanupPhase(Enum):
    """Cleanup operation phases"""
    HISTORY = "history"
    TRACKING = "tracking"
    GUILD = "guild"
    VERIFICATION = "verification"

@dataclass
class CleanupConfig:
    """Configuration for cleanup operations"""
    cleanup_interval: int = 1800  # 30 minutes
    max_history_age: int = 43200  # 12 hours
    batch_size: int = 100
    max_concurrent_cleanups: int = 3
    verification_interval: int = 300  # 5 minutes
    emergency_threshold: int = 10000  # Items threshold for emergency

@dataclass
class CleanupResult:
    """Result of a cleanup operation"""
    timestamp: datetime
    mode: CleanupMode
    duration: float
    items_cleaned: Dict[CleanupPhase, int]
    error: Optional[str] = None

class CleanupScheduler:
    """Schedules cleanup operations"""

    def __init__(self, config: CleanupConfig):
        self.config = config
        self.next_cleanup: Optional[datetime] = None
        self.next_verification: Optional[datetime] = None
        self._last_emergency: Optional[datetime] = None

    def should_cleanup(self, queue_size: int) -> Tuple[bool, CleanupMode]:
        """Determine if cleanup should run"""
        now = datetime.utcnow()
        
        # Check for emergency cleanup
        if (
            queue_size > self.config.emergency_threshold and
            (
                not self._last_emergency or
                now - self._last_emergency > timedelta(minutes=5)
            )
        ):
            self._last_emergency = now
            return True, CleanupMode.EMERGENCY

        # Check scheduled cleanup
        if not self.next_cleanup or now >= self.next_cleanup:
            self.next_cleanup = now + timedelta(
                seconds=self.config.cleanup_interval
            )
            return True, CleanupMode.NORMAL

        # Check verification
        if not self.next_verification or now >= self.next_verification:
            self.next_verification = now + timedelta(
                seconds=self.config.verification_interval
            )
            return True, CleanupMode.MAINTENANCE

        return False, CleanupMode.NORMAL

class CleanupCoordinator:
    """Coordinates cleanup operations"""

    def __init__(self):
        self.active_cleanups: Set[CleanupPhase] = set()
        self._cleanup_lock = asyncio.Lock()
        self._phase_locks: Dict[CleanupPhase, asyncio.Lock] = {
            phase: asyncio.Lock() for phase in CleanupPhase
        }

    async def start_cleanup(self, phase: CleanupPhase) -> bool:
        """Start a cleanup phase"""
        async with self._cleanup_lock:
            if phase in self.active_cleanups:
                return False
            self.active_cleanups.add(phase)
            return True

    async def end_cleanup(self, phase: CleanupPhase) -> None:
        """End a cleanup phase"""
        async with self._cleanup_lock:
            self.active_cleanups.discard(phase)

    async def acquire_phase(self, phase: CleanupPhase) -> bool:
        """Acquire lock for a cleanup phase"""
        return await self._phase_locks[phase].acquire()

    def release_phase(self, phase: CleanupPhase) -> None:
        """Release lock for a cleanup phase"""
        self._phase_locks[phase].release()

class CleanupTracker:
    """Tracks cleanup operations"""

    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self.history: List[CleanupResult] = []
        self.total_items_cleaned = 0
        self.last_cleanup: Optional[datetime] = None
        self.cleanup_counts: Dict[CleanupMode, int] = {
            mode: 0 for mode in CleanupMode
        }

    def record_cleanup(self, result: CleanupResult) -> None:
        """Record a cleanup operation"""
        self.history.append(result)
        if len(self.history) > self.max_history:
            self.history.pop(0)

        self.total_items_cleaned += sum(result.items_cleaned.values())
        self.last_cleanup = result.timestamp
        self.cleanup_counts[result.mode] += 1

    def get_stats(self) -> Dict[str, Any]:
        """Get cleanup statistics"""
        return {
            "total_cleanups": len(self.history),
            "total_items_cleaned": self.total_items_cleaned,
            "last_cleanup": (
                self.last_cleanup.isoformat()
                if self.last_cleanup
                else None
            ),
            "cleanup_counts": {
                mode.value: count
                for mode, count in self.cleanup_counts.items()
            },
            "recent_cleanups": [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "mode": r.mode.value,
                    "duration": r.duration,
                    "items_cleaned": {
                        phase.value: count
                        for phase, count in r.items_cleaned.items()
                    }
                }
                for r in self.history[-5:]  # Last 5 cleanups
            ]
        }

class QueueCleaner:
    """Handles cleanup of queue items and tracking data"""

    def __init__(self, config: Optional[CleanupConfig] = None):
        self.config = config or CleanupConfig()
        self.scheduler = CleanupScheduler(self.config)
        self.coordinator = CleanupCoordinator()
        self.tracker = CleanupTracker()

        # Initialize cleaners
        self.history_cleaner = HistoryCleaner()
        self.guild_cleaner = GuildCleaner()
        self.tracking_cleaner = TrackingCleaner()

        self._shutdown = False
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(
        self,
        state_manager,
        metrics_manager
    ) -> None:
        """Start periodic cleanup process"""
        if self._cleanup_task is not None:
            logger.warning("Cleanup task already running")
            return

        logger.info("Starting queue cleanup task...")
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(state_manager, metrics_manager)
        )

    async def _cleanup_loop(
        self,
        state_manager,
        metrics_manager
    ) -> None:
        """Main cleanup loop"""
        while not self._shutdown:
            try:
                # Check if cleanup should run
                queue_size = len(await state_manager.get_queue())
                should_run, mode = self.scheduler.should_cleanup(queue_size)

                if should_run:
                    await self._perform_cleanup(
                        state_manager,
                        metrics_manager,
                        mode
                    )

                await asyncio.sleep(1)  # Short sleep to prevent CPU hogging

            except asyncio.CancelledError:
                logger.info("Queue cleanup cancelled")
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {str(e)}")
                await asyncio.sleep(30)  # Longer sleep on error

    async def stop(self) -> None:
        """Stop the cleanup process"""
        logger.info("Stopping queue cleanup...")
        self._shutdown = True
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        self._cleanup_task = None

    async def _perform_cleanup(
        self,
        state_manager,
        metrics_manager,
        mode: CleanupMode
    ) -> None:
        """Perform cleanup operations"""
        start_time = datetime.utcnow()
        items_cleaned: Dict[CleanupPhase, int] = {
            phase: 0 for phase in CleanupPhase
        }

        try:
            # Get current state
            queue = await state_manager.get_queue()
            processing = await state_manager.get_processing()
            completed = await state_manager.get_completed()
            failed = await state_manager.get_failed()
            guild_queues = await state_manager.get_guild_queues()
            channel_queues = await state_manager.get_channel_queues()

            # Clean historical items
            if await self.coordinator.start_cleanup(CleanupPhase.HISTORY):
                try:
                    await self.coordinator.acquire_phase(CleanupPhase.HISTORY)
                    cleanup_cutoff = self.history_cleaner.get_cleanup_cutoff()
                    
                    # Adjust strategy based on mode
                    if mode == CleanupMode.AGGRESSIVE:
                        self.history_cleaner.strategy = HistoryStrategy.AGGRESSIVE
                    elif mode == CleanupMode.MAINTENANCE:
                        self.history_cleaner.strategy = HistoryStrategy.CONSERVATIVE
                    
                    completed_cleaned = await self.history_cleaner.cleanup_completed(
                        completed,
                        cleanup_cutoff
                    )
                    failed_cleaned = await self.history_cleaner.cleanup_failed(
                        failed,
                        cleanup_cutoff
                    )
                    items_cleaned[CleanupPhase.HISTORY] = (
                        completed_cleaned + failed_cleaned
                    )
                finally:
                    self.coordinator.release_phase(CleanupPhase.HISTORY)
                    await self.coordinator.end_cleanup(CleanupPhase.HISTORY)

            # Clean tracking data
            if await self.coordinator.start_cleanup(CleanupPhase.TRACKING):
                try:
                    await self.coordinator.acquire_phase(CleanupPhase.TRACKING)
                    
                    # Adjust strategy based on mode
                    if mode == CleanupMode.AGGRESSIVE:
                        self.tracking_cleaner.strategy = TrackingCleanupStrategy.AGGRESSIVE
                    elif mode == CleanupMode.MAINTENANCE:
                        self.tracking_cleaner.strategy = TrackingCleanupStrategy.CONSERVATIVE
                    
                    tracking_cleaned, _ = await self.tracking_cleaner.cleanup_tracking(
                        guild_queues,
                        channel_queues,
                        queue,
                        processing
                    )
                    items_cleaned[CleanupPhase.TRACKING] = tracking_cleaned
                finally:
                    self.coordinator.release_phase(CleanupPhase.TRACKING)
                    await self.coordinator.end_cleanup(CleanupPhase.TRACKING)

            # Update state
            await state_manager.update_state(
                completed=completed,
                failed=failed,
                guild_queues=guild_queues,
                channel_queues=channel_queues
            )

            # Record cleanup result
            duration = (datetime.utcnow() - start_time).total_seconds()
            result = CleanupResult(
                timestamp=datetime.utcnow(),
                mode=mode,
                duration=duration,
                items_cleaned=items_cleaned
            )
            self.tracker.record_cleanup(result)

            # Update metrics
            metrics_manager.update_cleanup_time()

            logger.info(
                "%s%s%s", f"Cleanup completed ({mode.value}):\n", "\n".join(
                    f"- {phase.value}: {count} items"
                    for phase, count in items_cleaned.items()
                    if count > 0
                ), f"\nTotal duration: {duration:.2f}s")

        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
            duration = (datetime.utcnow() - start_time).total_seconds()
            self.tracker.record_cleanup(CleanupResult(
                timestamp=datetime.utcnow(),
                mode=mode,
                duration=duration,
                items_cleaned=items_cleaned,
                error=str(e)
            ))
            raise CleanupError(f"Cleanup failed: {str(e)}")

    async def clear_guild_queue(
        self,
        guild_id: int,
        state_manager
    ) -> int:
        """Clear all queue items for a specific guild"""
        try:
            if not await self.coordinator.start_cleanup(CleanupPhase.GUILD):
                raise CleanupError("Guild cleanup already in progress")

            try:
                await self.coordinator.acquire_phase(CleanupPhase.GUILD)
                
                # Get current state
                queue = await state_manager.get_queue()
                processing = await state_manager.get_processing()
                completed = await state_manager.get_completed()
                failed = await state_manager.get_failed()
                guild_queues = await state_manager.get_guild_queues()
                channel_queues = await state_manager.get_channel_queues()

                # Clear guild items
                cleared_count, counts = await self.guild_cleaner.clear_guild_items(
                    guild_id,
                    queue,
                    processing,
                    completed,
                    failed,
                    guild_queues,
                    channel_queues
                )

                # Update state
                await state_manager.update_state(
                    queue=queue,
                    processing=processing,
                    completed=completed,
                    failed=failed,
                    guild_queues=guild_queues,
                    channel_queues=channel_queues
                )

                return cleared_count

            finally:
                self.coordinator.release_phase(CleanupPhase.GUILD)
                await self.coordinator.end_cleanup(CleanupPhase.GUILD)

        except Exception as e:
            logger.error(f"Error clearing guild queue: {str(e)}")
            raise CleanupError(f"Failed to clear guild queue: {str(e)}")

    def get_cleaner_stats(self) -> Dict[str, Any]:
        """Get comprehensive cleaner statistics"""
        return {
            "config": {
                "cleanup_interval": self.config.cleanup_interval,
                "max_history_age": self.config.max_history_age,
                "batch_size": self.config.batch_size,
                "max_concurrent_cleanups": self.config.max_concurrent_cleanups,
                "verification_interval": self.config.verification_interval,
                "emergency_threshold": self.config.emergency_threshold
            },
            "scheduler": {
                "next_cleanup": (
                    self.scheduler.next_cleanup.isoformat()
                    if self.scheduler.next_cleanup
                    else None
                ),
                "next_verification": (
                    self.scheduler.next_verification.isoformat()
                    if self.scheduler.next_verification
                    else None
                ),
                "last_emergency": (
                    self.scheduler._last_emergency.isoformat()
                    if self.scheduler._last_emergency
                    else None
                )
            },
            "coordinator": {
                "active_cleanups": [
                    phase.value for phase in self.coordinator.active_cleanups
                ]
            },
            "tracker": self.tracker.get_stats(),
            "cleaners": {
                "history": self.history_cleaner.get_cleaner_stats(),
                "guild": self.guild_cleaner.get_cleaner_stats(),
                "tracking": self.tracking_cleaner.get_cleaner_stats()
            }
        }

class CleanupError(Exception):
    """Base exception for cleanup-related errors"""
    pass
