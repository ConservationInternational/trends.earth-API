"""STATUS LOG MODEL"""

import datetime

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID


class StatusLog(db.Model):
    """
    StatusLog Model for tracking execution status changes and system monitoring.
    
    This model tracks both execution status transitions and overall system execution counts.
    Each entry records a snapshot of the system state after a specific status change,
    including details about which execution changed and what the transition was.
    
    Status transition entries (when status_from/status_to are provided):
    - Record individual execution status changes
    - Include execution ID and transition details
    - Capture system state AFTER the change
    
    System monitoring entries (when status_from/status_to are None):
    - Record periodic system health snapshots
    - Include only execution counts by status
    - Used for monitoring and analytics
    """

    __tablename__ = "status_log"
    id = db.Column(db.Integer(), primary_key=True)
    timestamp = db.Column(
        db.DateTime(), default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    # Execution counts
    executions_pending = db.Column(db.Integer(), default=0)
    executions_ready = db.Column(db.Integer(), default=0)
    executions_running = db.Column(db.Integer(), default=0)
    executions_finished = db.Column(db.Integer(), default=0)
    executions_failed = db.Column(db.Integer(), default=0)
    executions_cancelled = db.Column(db.Integer(), default=0)

    # Status transition fields
    status_from = db.Column(db.String(20), nullable=True)
    status_to = db.Column(db.String(20), nullable=True)
    execution_id = db.Column(db.String(36), nullable=True)

    def __init__(
        self,
        executions_pending=0,
        executions_ready=0,
        executions_running=0,
        executions_finished=0,
        executions_failed=0,
        executions_cancelled=0,
        status_from=None,
        status_to=None,
        execution_id=None,
    ):
        """
        Initialize a StatusLog entry.
        
        Args:
            executions_pending (int): Count of executions in PENDING status
            executions_ready (int): Count of executions in READY status
            executions_running (int): Count of executions in RUNNING status
            executions_finished (int): Count of executions in FINISHED status
            executions_failed (int): Count of executions in FAILED status
            executions_cancelled (int): Count of executions in CANCELLED status
            status_from (str, optional): Previous status for transition tracking
            status_to (str, optional): New status for transition tracking
            execution_id (str, optional): Execution ID for transition tracking
        """
        self.executions_pending = executions_pending
        self.executions_ready = executions_ready
        self.executions_running = executions_running
        self.executions_finished = executions_finished
        self.executions_failed = executions_failed
        self.executions_cancelled = executions_cancelled
        self.status_from = status_from
        self.status_to = status_to
        self.execution_id = execution_id

    def __repr__(self):
        return f"<StatusLog {self.id!r}>"

    def serialize(self):
        """
        Return object data in easily serializable format.
        
        Returns:
            dict: Dictionary containing all status log fields including 
                  execution counts, timestamp, and transition tracking fields
        """
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "executions_pending": self.executions_pending,
            "executions_ready": self.executions_ready,
            "executions_running": self.executions_running,
            "executions_finished": self.executions_finished,
            "executions_failed": self.executions_failed,
            "executions_cancelled": self.executions_cancelled,
            "status_from": self.status_from,
            "status_to": self.status_to,
            "execution_id": self.execution_id,
        }
