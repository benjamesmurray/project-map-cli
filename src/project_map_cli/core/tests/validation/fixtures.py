from __future__ import annotations
import textwrap
from pathlib import Path

class BaseRepoFixture:
    def __init__(self, root: Path):
        self.root = root

    def write(self, rel_path: str, content: str):
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content).strip(), encoding="utf-8")

class MultiLanguageRepo(BaseRepoFixture):
    def generate(self):
        # Python FastAPI
        self.write("__init__.py", "")
        self.write("app/__init__.py", "")
        self.write("app/main.py", """
            from fastapi import FastAPI
            from pydantic import BaseModel
            
            app = FastAPI()
            
            class User(BaseModel):
                id: int
                username: str
            
            @app.get("/api/v1/users", tags=["users"])
            async def get_users():
                return [{"id": 1, "username": "alice"}]
        """)
        
        # Vue Frontend
        self.write("web/src/components/UserList.vue", """
            <template><div>Users</div></template>
            <script setup>
            import axios from 'axios';
            const fetchUsers = async () => {
                const res = await axios.get('/api/v1/users');
                return res.data;
            };
            </script>
        """)
        
        # Kotlin Kafka Streams
        self.write("streams/src/main/kotlin/com/example/UserProcessor.kt", """
            package com.example
            import org.apache.kafka.streams.StreamsBuilder
            import org.apache.kafka.streams.kstream.KStream
            
            class UserProcessor {
                fun process(builder: StreamsBuilder) {
                    val stream: KStream<String, String> = builder.stream("users-topic")
                    stream.to("processed-users")
                }
            }
        """)
        
        # SQL Schema
        self.write("db/migrations/001_create_users.sql", """
            CREATE TABLE users (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

class EdgeCaseRepo(BaseRepoFixture):
    def generate(self):
        # Empty files
        self.write("empty.py", "")
        self.write("empty.kt", "")
        
        # Syntax errors
        self.write("error.py", "def broken_func(")
        self.write("error.kt", "class Broken { fun oops() {")
        
        # Large file simulation
        large_py = "\n".join([f"def func_{i}():\n    return {i}" for i in range(1000)])
        self.write("large.py", large_py)
        
        # Deeply nested
        self.write("a/b/c/d/e/f/g/deep.py", "def deep(): pass")
