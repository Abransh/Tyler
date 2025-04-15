"""
Scheduler module for the BookMyShow Bot.

This module handles scheduling of monitoring and purchase tasks using
APScheduler or Celery, with support for different schedules and priorities.
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError
from apscheduler.job import Job

from ..config import config
from ..utils.logger import get_logger
from ..monitoring.event_tracker import event_tracker, Event
from ..tasks.purchase_flow import purchase_flow


logger = get_logger(__name__)


class SchedulerManager:
    """
    Manages scheduling of monitoring and purchase tasks.
    
    Uses APScheduler for task scheduling with support for different
    schedules and priorities.
    """
    
    def __init__(self):
        """Initialize the scheduler manager."""
        self.scheduler_type = config.get("scheduler.type", "apscheduler")
        self.timezone = config.get("scheduler.timezone", "Asia/Kolkata")
        self.job_store = config.get("scheduler.job_store", "sqlite")
        
        # Initialize scheduler
        self.scheduler = None
        self.initialized = False
        self.running = False
    
    def initialize(self) -> None:
        """Initialize the scheduler."""
        if self.initialized:
            return
        
        logger.info(f"Initializing scheduler ({self.scheduler_type})")
        
        # Configure APScheduler
        if self.scheduler_type == "apscheduler":
            self._init_apscheduler()
        elif self.scheduler_type == "celery":
            self._init_celery()
        else:
            logger.error(f"Unsupported scheduler type: {self.scheduler_type}")
            raise ValueError(f"Unsupported scheduler type: {self.scheduler_type}")
        
        self.initialized = True
        logger.info("Scheduler initialized")
    
    def _init_apscheduler(self) -> None:
        """Initialize APScheduler."""
        # Configure jobstores
        jobstores = {}
        
        if self.job_store == "sqlite":
            from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
            jobstores["default"] = SQLAlchemyJobStore(url="sqlite:///data/db/scheduler.sqlite")
        
        # Configure executors
        executors = {
            "default": {"type": "threadpool", "max_workers": 20},
            "processpool": {"type": "processpool", "max_workers": 5}
        }
        
        # Create scheduler
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            timezone=self.timezone
        )
    
    def _init_celery(self) -> None:
        """Initialize Celery."""
        # This is a placeholder for Celery initialization
        logger.warning("Celery scheduler not implemented, falling back to APScheduler")
        self._init_apscheduler()
    
    def start(self) -> None:
        """Start the scheduler."""
        if not self.initialized:
            self.initialize()
        
        if not self.running:
            logger.info("Starting scheduler")
            self.scheduler.start()
            self.running = True
    
    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        if self.running and self.scheduler:
            logger.info("Shutting down scheduler")
            self.scheduler.shutdown()
            self.running = False
    
    def schedule_regular_monitoring(self, 
                                   interval: int = 60, 
                                   event_ids: Optional[List[str]] = None,
                                   job_id: str = "regular_monitoring") -> str:
        """
        Schedule regular event monitoring.
        
        Args:
            interval: Monitoring interval in seconds
            event_ids: List of event IDs to monitor, or None for all
            job_id: Unique ID for the job
            
        Returns:
            Job ID
        """
        if not self.initialized:
            self.initialize()
        
        # Remove existing job with same ID if it exists
        self.remove_job(job_id)
        
        # Create async wrapper function for the monitoring task
        async def monitoring_task():
            try:
                return await event_tracker.monitor_events(
                    event_ids=event_ids,
                    single_run=True,
                    notification_callback=self._on_ticket_available
                )
            except Exception as e:
                logger.error(f"Error in monitoring task: {str(e)}")
        
        # Schedule the job
        job = self.scheduler.add_job(
            monitoring_task,
            IntervalTrigger(seconds=interval),
            id=job_id,
            replace_existing=True,
            name=f"Monitor events ({len(event_ids) if event_ids else 'all'})"
        )
        
        event_desc = ", ".join(event_ids) if event_ids else "all events"
        logger.info(f"Scheduled regular monitoring (every {interval}s) for {event_desc}, job ID: {job_id}")
        
        # Start the scheduler if not running
        if not self.running:
            self.start()
            
        return job_id
    
    def schedule_one_time_monitoring(self, 
                                    run_date: datetime,
                                    event_ids: Optional[List[str]] = None,
                                    job_id: Optional[str] = None) -> str:
        """
        Schedule one-time event monitoring.
        
        Args:
            run_date: Date/time to run the monitoring
            event_ids: List of event IDs to monitor, or None for all
            job_id: Unique ID for the job, or None to generate one
            
        Returns:
            Job ID
        """
        if not self.initialized:
            self.initialize()
        
        # Generate job ID if not provided
        if job_id is None:
            job_id = f"one_time_monitoring_{run_date.strftime('%Y%m%d_%H%M%S')}"
        
        # Create async wrapper function for the monitoring task
        async def monitoring_task():
            try:
                newly_available = await event_tracker.monitor_events(
                    event_ids=event_ids,
                    single_run=True,
                    notification_callback=self._on_ticket_available
                )
                logger.info(f"One-time monitoring completed, found {len(newly_available) if newly_available else 0} new events")
                return newly_available
            except Exception as e:
                logger.error(f"Error in one-time monitoring task: {str(e)}")
        
        # Schedule the job
        job = self.scheduler.add_job(
            monitoring_task,
            DateTrigger(run_date=run_date, timezone=self.timezone),
            id=job_id,
            replace_existing=True,
            name=f"One-time monitoring at {run_date.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        event_desc = ", ".join(event_ids) if event_ids else "all events"
        logger.info(f"Scheduled one-time monitoring at {run_date} for {event_desc}, job ID: {job_id}")
        
        # Start the scheduler if not running
        if not self.running:
            self.start()
            
        return job_id
    
    def schedule_intensified_monitoring(self, 
                                       event_id: str,
                                       start_time: datetime,
                                       end_time: datetime,
                                       base_interval: int = 60,
                                       peak_interval: int = 5,
                                       job_id: Optional[str] = None) -> str:
        """
        Schedule intensified monitoring for a specific event.
        
        Args:
            event_id: Event ID to monitor
            start_time: Start time for intensified monitoring
            end_time: End time for intensified monitoring
            base_interval: Base monitoring interval in seconds
            peak_interval: Peak (intensified) monitoring interval in seconds
            job_id: Unique ID for the job, or None to generate one
            
        Returns:
            Job ID
        """
        if not self.initialized:
            self.initialize()
        
        # Generate job ID if not provided
        if job_id is None:
            job_id = f"intensified_monitoring_{event_id}_{start_time.strftime('%Y%m%d_%H%M%S')}"
        
        # Get event details for logging
        event = event_tracker.get_event(event_id)
        event_name = event.name if event else event_id
        
        # Schedule regular monitoring
        self.schedule_regular_monitoring(
            interval=base_interval,
            event_ids=[event_id],
            job_id=f"{job_id}_base"
        )
        
        # Schedule start of intensified monitoring
        self.schedule_one_time_job(
            lambda: self._start_intensified_monitoring(event_id, peak_interval, f"{job_id}_peak"),
            run_date=start_time,
            job_id=f"{job_id}_start",
            name=f"Start intensified monitoring for {event_name}"
        )
        
        # Schedule end of intensified monitoring
        self.schedule_one_time_job(
            lambda: self._stop_intensified_monitoring(f"{job_id}_peak"),
            run_date=end_time,
            job_id=f"{job_id}_end",
            name=f"End intensified monitoring for {event_name}"
        )
        
        logger.info(f"Scheduled intensified monitoring for '{event_name}'")
        logger.info(f"  Base monitoring: every {base_interval}s")
        logger.info(f"  Intensified monitoring: every {peak_interval}s from {start_time} to {end_time}")
        
        # Start the scheduler if not running
        if not self.running:
            self.start()
            
        return job_id
    
    def _start_intensified_monitoring(self, 
                                     event_id: str, 
                                     interval: int, 
                                     job_id: str) -> None:
        """
        Start intensified monitoring for an event.
        
        Args:
            event_id: Event ID to monitor
            interval: Monitoring interval in seconds
            job_id: Job ID
        """
        logger.info(f"Starting intensified monitoring for event {event_id} (every {interval}s)")
        self.schedule_regular_monitoring(interval=interval, event_ids=[event_id], job_id=job_id)
    
    def _stop_intensified_monitoring(self, job_id: str) -> None:
        """
        Stop intensified monitoring.
        
        Args:
            job_id: Job ID to stop
        """
        logger.info(f"Stopping intensified monitoring (job ID: {job_id})")
        self.remove_job(job_id)
    
    def schedule_one_time_job(self, 
                             func: Callable, 
                             run_date: datetime,
                             job_id: str,
                             name: str = "One-time job") -> str:
        """
        Schedule a one-time job.
        
        Args:
            func: Function to execute
            run_date: Date/time to run the job
            job_id: Unique ID for the job
            name: Display name for the job
            
        Returns:
            Job ID
        """
        if not self.initialized:
            self.initialize()
        
        # Schedule the job
        job = self.scheduler.add_job(
            func,
            DateTrigger(run_date=run_date, timezone=self.timezone),
            id=job_id,
            replace_existing=True,
            name=name
        )
        
        logger.info(f"Scheduled one-time job '{name}' at {run_date}, job ID: {job_id}")
        
        # Start the scheduler if not running
        if not self.running:
            self.start()
            
        return job_id
    
    def get_jobs(self) -> List[Dict[str, Any]]:
        """
        Get all scheduled jobs.
        
        Returns:
            List of job information dictionaries
        """
        if not self.initialized or not self.scheduler:
            return []
        
        jobs = []
        
        for job in self.scheduler.get_jobs():
            next_run = job.next_run_time
            
            job_info = {
                "id": job.id,
                "name": job.name,
                "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "Not scheduled",
                "trigger": str(job.trigger)
            }
            
            jobs.append(job_info)
        
        return jobs
    
    def remove_job(self, job_id: str) -> bool:
        """
        Remove a scheduled job.
        
        Args:
            job_id: ID of the job to remove
            
        Returns:
            True if job was removed, False if not found
        """
        if not self.initialized or not self.scheduler:
            return False
        
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed job: {job_id}")
            return True
        except JobLookupError:
            logger.debug(f"Job not found: {job_id}")
            return False
    
    async def _on_ticket_available(self, event: Event) -> None:
        """
        Callback when tickets become available.
        
        Args:
            event: Event with available tickets
        """
        logger.info(f"Ticket availability callback triggered for '{event.name}'")
        
        # Auto-purchase if configured
        auto_purchase = config.get("purchase.auto_purchase", True)
        
        if auto_purchase:
            logger.info(f"Auto-purchase enabled, starting purchase for '{event.name}'")
            
            # Schedule the purchase task to run immediately
            asyncio.create_task(purchase_flow.execute_purchase(event))
        else:
            logger.info(f"Auto-purchase disabled, not purchasing tickets for '{event.name}'")
    
    def schedule_sale_date_monitoring(self, event_id: str, sale_date: datetime) -> List[str]:
        """
        Schedule monitoring for an event with known on-sale date.
        
        Args:
            event_id: Event ID to monitor
            sale_date: Expected on-sale date/time
            
        Returns:
            List of scheduled job IDs
        """
        if not self.initialized:
            self.initialize()
        
        job_ids = []
        
        # Get event details
        event = event_tracker.get_event(event_id)
        if not event:
            logger.error(f"Event {event_id} not found")
            return []
        
        # Schedule regular monitoring (every hour)
        base_job_id = f"sale_date_base_{event_id}"
        self.schedule_regular_monitoring(
            interval=3600,  # 1 hour
            event_ids=[event_id],
            job_id=base_job_id
        )
        job_ids.append(base_job_id)
        
        # Schedule monitoring with increasing frequency as sale date approaches
        monitoring_schedule = [
            # days before, interval in seconds, duration in hours
            (7, 1800, 24),    # 1 week before: every 30 minutes for 24 hours
            (1, 600, 24),     # 1 day before: every 10 minutes for 24 hours
            (0.5, 300, 12),   # 12 hours before: every 5 minutes for 12 hours
            (0.25, 60, 6),    # 6 hours before: every 1 minute for 6 hours
            (0.125, 30, 3),   # 3 hours before: every 30 seconds for 3 hours
            (0.0625, 10, 1)   # 1.5 hours before: every 10 seconds for 1 hour
        ]
        
        for days_before, interval, duration in monitoring_schedule:
            start_time = sale_date - timedelta(days=days_before)
            end_time = start_time + timedelta(hours=duration)
            
            # Skip schedules that are in the past
            if end_time < datetime.now():
                continue
            
            job_id = f"sale_date_{event_id}_{days_before}"
            self.schedule_intensified_monitoring(
                event_id=event_id,
                start_time=start_time,
                end_time=end_time,
                base_interval=3600,  # Keep the base monitoring
                peak_interval=interval,
                job_id=job_id
            )
            job_ids.append(job_id)
        
        logger.info(f"Scheduled monitoring for '{event.name}' with on-sale date {sale_date}")
        logger.info(f"  Created {len(job_ids)} monitoring schedules with increasing frequency")
        
        # Start the scheduler if not running
        if not self.running:
            self.start()
            
        return job_ids


# Singleton instance
scheduler_manager = SchedulerManager()