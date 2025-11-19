"""
Database Schemas for Smart Timetable & Productivity App

Each Pydantic model corresponds to a MongoDB collection. The collection
name is the lowercase of the class name (e.g., Task -> "task").
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime

class Task(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    title: str = Field(..., description="Short task title")
    description: Optional[str] = Field(None, description="Details/context")
    project: Optional[str] = Field(None, description="Project or category")
    estimate_minutes: int = Field(30, ge=5, le=480, description="Estimated duration in minutes")
    energy: Optional[str] = Field(None, description="low | medium | high")
    priority: str = Field("medium", description="low | medium | high | urgent")
    deadline: Optional[datetime] = Field(None, description="Hard deadline (UTC)")
    status: str = Field("todo", description="todo | in_progress | done")
    tags: List[str] = Field(default_factory=list)

class TimeBlock(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    task_id: Optional[str] = Field(None, description="Linked task id as string")
    title: str
    start: datetime
    end: datetime
    status: str = Field("planned", description="planned | in_progress | completed | slipped")
    context: Optional[str] = None

class Routine(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    cadence: str = Field(..., description="cron-like or simple 'daily/mwf'")
    steps: List[str] = Field(default_factory=list)
