import asyncio
import json
import bcrypt
import jwt
import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage (we'll add a real database later)
users = {}   # {username: hashed_password}
peers = {}   # {username: websocket}

SECRET_KEY = "changethislater123"

# ─── Models ────────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    username: str
    password: str

# ─── Auth Routes ───────────────────────────────────────────────────────────

@app.post("/register")
async def register(req: AuthRequest):
    if req.username in users:
        raise HTTPException(status_code=400, detail="Username already taken")
    
    hashed = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt())
    users[req.username] = hashed
    print(f"[+] Registered: {req.username}")
    return {"message": "Account created"}

@app.post("/login")
async def login(req: AuthRequest):
    if req.username not in users:
        raise HTTPException(status_code=400, detail="User not found")
    
    if not bcrypt.checkpw(req.password.encode(), users[req.username]):
        raise HTTPException(status_code=400, detail="Wrong password")
    
    token = jwt.encode({
        "username": req.username,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }, SECRET_KEY, algorithm="HS256")

    print(f"[+] Logged in: {req.username}")
    return {"token": token, "username": req.username}

# ─── WebSocket ─────────────────────────────────────────────────────────────

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    # Verify token
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        username = data["username"]
    except:
        await websocket.close()
        return

    await websocket.accept()
    peers[username] = websocket
    print(f"[+] {username} connected. Online: {list(peers.keys())}")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            target = data.get("to")

            if target and target in peers:
                await peers[target].send_text(raw)
                print(f"[→] {username} → {target}: {data.get('type')}")
            else:
                print(f"[!] Target '{target}' not found")

    except WebSocketDisconnect:
        del peers[username]
        print(f"[-] {username} disconnected")