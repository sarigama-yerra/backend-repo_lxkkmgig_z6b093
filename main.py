import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Database helpers
from database import db, create_document, get_documents

app = FastAPI(title="Smart Timetable & Productivity API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------
# Utility helpers
# ------------------------------

def _to_doc(doc: Dict[str, Any]):
    """Convert Mongo _id to string and attach id field."""
    if not doc:
        return doc
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    return d

# ------------------------------
# Pydantic models (request bodies)
# ------------------------------

class TaskIn(BaseModel):
    title: str
    description: Optional[str] = None
    project: Optional[str] = None
    estimate_minutes: int = Field(30, ge=5, le=480)
    energy: Optional[str] = Field(None, description="low|medium|high")
    priority: str = Field("medium", description="low|medium|high|urgent")
    deadline: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)

class Task(TaskIn):
    id: str
    status: str

class TimeBlockIn(BaseModel):
    task_id: Optional[str] = None
    title: str
    start: datetime
    end: datetime
    status: str = Field("planned")
    context: Optional[str] = None

class TimeBlock(TimeBlockIn):
    id: str

class RecommendResponse(BaseModel):
    now: datetime
    suggestions: List[Dict[str, Any]]

# ------------------------------
# Basic routes
# ------------------------------

@app.get("/")
def read_root():
    return {"message": "Smart Timetable & Productivity API running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["connection_status"] = "Connected"
            try:
                cols = db.list_collection_names()
                response["collections"] = cols
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response

# ------------------------------
# Tasks
# ------------------------------

@app.get("/tasks", response_model=List[Task])
def list_tasks():
    docs = get_documents("task", {}) if db else []
    results = []
    for d in docs:
        d = _to_doc(d)
        results.append(Task(
            id=d.get("id"),
            title=d.get("title"),
            description=d.get("description"),
            project=d.get("project"),
            estimate_minutes=d.get("estimate_minutes", 30),
            energy=d.get("energy"),
            priority=d.get("priority", "medium"),
            deadline=d.get("deadline"),
            tags=d.get("tags", []),
            status=d.get("status", "todo"),
        ))
    return results

@app.post("/tasks", response_model=Task)
def create_task(task: TaskIn):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    data = task.model_dump()
    data["status"] = "todo"
    inserted_id = create_document("task", data)
    return Task(id=inserted_id, status="todo", **task.model_dump())

# ------------------------------
# Timeblocks & Scheduling
# ------------------------------

@app.get("/timeblocks", response_model=List[TimeBlock])
def list_timeblocks():
    docs = get_documents("timeblock", {}) if db else []
    results: List[TimeBlock] = []
    for d in docs:
        d = _to_doc(d)
        results.append(TimeBlock(
            id=d.get("id"),
            task_id=d.get("task_id"),
            title=d.get("title"),
            start=d.get("start"),
            end=d.get("end"),
            status=d.get("status", "planned"),
            context=d.get("context"),
        ))
    return results

class AutoScheduleRequest(BaseModel):
    start: Optional[datetime] = None
    end: Optional[datetime] = None

@app.post("/schedule/auto", response_model=List[TimeBlock])
def auto_schedule(req: AutoScheduleRequest):
    """Very simple heuristic: fill from now across free time in 30-120m blocks."""
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    now = datetime.now(timezone.utc)
    start = req.start or now
    end = req.end or (start + timedelta(hours=8))

    # Fetch tasks not done
    tasks = [ _to_doc(t) for t in get_documents("task", {"status": {"$ne": "done"}}) ]

    # Priority order: urgent > high > medium > low, then earliest deadline, then estimate asc
    prio_rank = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    tasks.sort(key=lambda t: (
        prio_rank.get(t.get("priority", "medium"), 2),
        t.get("deadline") or datetime.max.replace(tzinfo=timezone.utc),
        t.get("estimate_minutes", 30)
    ))

    cursor = start
    created_blocks: List[TimeBlock] = []
    for t in tasks:
        est = int(t.get("estimate_minutes", 30))
        block_start = cursor
        block_end = cursor + timedelta(minutes=est)
        if block_end > end:
            break
        tb = {
            "task_id": t.get("id") or str(t.get("_id")) if t.get("_id") else None,
            "title": t.get("title"),
            "start": block_start,
            "end": block_end,
            "status": "planned",
            "context": t.get("project") or ",".join(t.get("tags", [])) or None,
        }
        inserted_id = create_document("timeblock", tb)
        created_blocks.append(TimeBlock(id=inserted_id, **tb))
        cursor = block_end + timedelta(minutes=5)  # small buffer

    return created_blocks

# ------------------------------
# Recommendations
# ------------------------------

@app.get("/recommend", response_model=RecommendResponse)
def recommend_next():
    now = datetime.now(timezone.utc)
    suggestions: List[Dict[str, Any]] = []
    if db:
        tasks = [ _to_doc(t) for t in get_documents("task", {"status": {"$ne": "done"}}) ]
        prio_weight = {"urgent": 4, "high": 3, "medium": 2, "low": 1}
        for t in tasks:
            # Simple score: priority + deadline proximity - duration penalty
            base = prio_weight.get(t.get("priority", "medium"), 2)
            deadline = t.get("deadline")
            time_to_deadline = (deadline - now).total_seconds()/3600 if isinstance(deadline, datetime) else 1e6
            urgency_bonus = 0
            if isinstance(deadline, datetime):
                if time_to_deadline < 4:
                    urgency_bonus = 3
                elif time_to_deadline < 24:
                    urgency_bonus = 2
                elif time_to_deadline < 72:
                    urgency_bonus = 1
            duration_penalty = max(0, (t.get("estimate_minutes", 30) - 30) / 30) * 0.2
            score = base + urgency_bonus - duration_penalty
            suggestions.append({
                "task": {
                    "id": t.get("id"),
                    "title": t.get("title"),
                    "estimate_minutes": t.get("estimate_minutes", 30),
                    "priority": t.get("priority", "medium"),
                    "deadline": t.get("deadline"),
                },
                "score": round(float(score), 2)
            })
        suggestions.sort(key=lambda s: s["score"], reverse=True)
        suggestions = suggestions[:3]
    return RecommendResponse(now=now, suggestions=suggestions)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
