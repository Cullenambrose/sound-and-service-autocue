"""SQLite-backed job queue with resumable execution and per-track error handling."""
import signal
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from . import db


class JobQueue:
    """Resumable job queue backed by SQLite.
    
    Features:
    - Ignores SIGHUP (continues after terminal disconnect)
    - Per-track try/except so one failure doesn't stop the batch
    - Status tracking per track
    - Resumable after kill/crash
    """
    
    def __init__(self, db_path: str):
        """Initialize job queue.
        
        Args:
            db_path: Path to the SQLite database.
        """
        self.db_path = db_path
        
        # Ignore SIGHUP so jobs continue after terminal disconnect
        try:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        except (AttributeError, ValueError):
            # SIGHUP may not exist on Windows or signal may not be available
            pass
    
    def create_job(self, track_paths: List[str], config: Optional[Dict[str, Any]] = None) -> str:
        """Create a new job.
        
        Args:
            track_paths: List of track paths to process.
            config: Optional job configuration.
            
        Returns:
            Job ID.
        """
        import json
        
        job_id = uuid.uuid4().hex
        now = time.time()
        
        with db.get_db(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create job
            cursor.execute(
                """INSERT INTO jobs (id, status, created_at, updated_at, track_count, config)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    "pending",
                    now,
                    now,
                    len(track_paths),
                    json.dumps(config) if config else None,
                ),
            )
            
            # Create job tracks
            for track_path in track_paths:
                cursor.execute(
                    """INSERT INTO job_tracks (job_id, track_path, status)
                       VALUES (?, ?, ?)""",
                    (job_id, track_path, "pending"),
                )
            
            conn.commit()
        
        return job_id
    
    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get current status of a job.
        
        Args:
            job_id: The job ID.
            
        Returns:
            Dict with job status and statistics.
        """
        with db.get_db(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            job_row = cursor.fetchone()
            
            if not job_row:
                raise ValueError(f"Job not found: {job_id}")
            
            # Get track statuses
            cursor.execute(
                """SELECT status, COUNT(*) as count 
                   FROM job_tracks 
                   WHERE job_id = ? 
                   GROUP BY status""",
                (job_id,),
            )
            track_counts = {row["status"]: row["count"] for row in cursor.fetchall()}
            
            # Get recent errors
            cursor.execute(
                """SELECT track_path, error 
                   FROM job_tracks 
                   WHERE job_id = ? AND status = 'error' 
                   ORDER BY completed_at DESC 
                   LIMIT 10""",
                (job_id,),
            )
            recent_errors = [
                {"path": row["track_path"], "error": row["error"]} 
                for row in cursor.fetchall()
            ]
            
            return {
                "job_id": job_id,
                "status": job_row["status"],
                "created_at": job_row["created_at"],
                "updated_at": job_row["updated_at"],
                "track_count": job_row["track_count"],
                "completed_count": job_row["completed_count"],
                "error_count": job_row["error_count"],
                "track_status_counts": track_counts,
                "recent_errors": recent_errors,
            }
    
    def process_job(
        self,
        job_id: str,
        processor: Callable[[str], Dict[str, Any]],
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Process a job, executing the processor function for each track.
        
        Args:
            job_id: The job ID to process.
            processor: Function that processes a single track path.
                      Should return a dict with results or raise an exception.
            on_progress: Optional callback called periodically with job status.
            
        Returns:
            Final job status.
        """
        with db.get_db(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Update job status to running
            cursor.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                ("running", time.time(), job_id),
            )
            conn.commit()
            
            try:
                while True:
                    # Get next pending track
                    cursor.execute(
                        """SELECT track_path FROM job_tracks 
                           WHERE job_id = ? AND status = 'pending' 
                           LIMIT 1""",
                        (job_id,),
                    )
                    row = cursor.fetchone()
                    
                    if not row:
                        # No more pending tracks
                        break
                    
                    track_path = row["track_path"]
                    now = time.time()
                    
                    # Mark as processing
                    cursor.execute(
                        """UPDATE job_tracks 
                           SET status = 'processing', started_at = ? 
                           WHERE job_id = ? AND track_path = ?""",
                        (now, job_id, track_path),
                    )
                    conn.commit()
                    
                    # Process the track with error handling
                    status = "completed"
                    error = None
                    try:
                        processor(track_path)
                    except Exception as e:
                        status = "error"
                        error = str(e)
                    
                    # Update track status
                    now = time.time()
                    cursor.execute(
                        """UPDATE job_tracks 
                           SET status = ?, error = ?, completed_at = ? 
                           WHERE job_id = ? AND track_path = ?""",
                        (status, error, now, job_id, track_path),
                    )
                    
                    # Update job counters
                    cursor.execute(
                        """UPDATE jobs 
                           SET completed_count = completed_count + 1,
                               error_count = error_count + ?,
                               updated_at = ?
                           WHERE id = ?""",
                        (1 if status == "error" else 0, now, job_id),
                    )
                    conn.commit()
                    
                    # Call progress callback
                    if on_progress:
                        try:
                            on_progress(self.get_job_status(job_id))
                        except Exception:
                            # Don't let progress callback errors stop the job
                            pass
                
                # Mark job as complete
                cursor.execute("SELECT error_count FROM jobs WHERE id = ?", (job_id,))
                error_count = cursor.fetchone()["error_count"]
                final_status = "completed_with_errors" if error_count > 0 else "completed"
                
                cursor.execute(
                    "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                    (final_status, time.time(), job_id),
                )
                conn.commit()
                
            except KeyboardInterrupt:
                # Mark as paused on interrupt
                cursor.execute(
                    "UPDATE jobs SET status = 'paused', updated_at = ? WHERE id = ?",
                    (time.time(), job_id),
                )
                conn.commit()
                raise
            
            except Exception as e:
                # Mark as failed on unexpected error
                cursor.execute(
                    "UPDATE jobs SET status = 'failed', updated_at = ? WHERE id = ?",
                    (time.time(), job_id),
                )
                conn.commit()
                raise
        
        return self.get_job_status(job_id)
    
    def resume_job(
        self,
        job_id: str,
        processor: Callable[[str], Dict[str, Any]],
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Resume a paused or failed job.
        
        Args:
            job_id: The job ID to resume.
            processor: Function that processes a single track path.
            on_progress: Optional callback called periodically with job status.
            
        Returns:
            Final job status.
        """
        # Reset any 'processing' tracks back to 'pending' (they were interrupted)
        with db.get_db(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE job_tracks 
                   SET status = 'pending' 
                   WHERE job_id = ? AND status = 'processing'""",
                (job_id,),
            )
            conn.commit()
        
        return self.process_job(job_id, processor, on_progress)
