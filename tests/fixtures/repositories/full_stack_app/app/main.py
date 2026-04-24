from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class User(BaseModel):
    id: int
    username: str

@app.get("/api/v1/users", tags=["users"])
async def get_users():
    return [{"id": 1, "username": "alice"}]
