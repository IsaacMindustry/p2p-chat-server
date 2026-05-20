import json
import bcrypt
import jwt
import datetime
import random
import string
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import acreate_client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
db = None

peers = {}
public_rooms = {}
private_rooms = {}

SECRET_KEY = "changethislater123"

@app.on_event("startup")
async def startup():
    global db
    db = await acreate_client(SUPABASE_URL, SUPABASE_KEY)

class AuthRequest(BaseModel):
    username: str
    password: str

class RoomRequest(BaseModel):
    token: str
    room_name: str

class PrivateRoomRequest(BaseModel):
    token: str

def verify_token(token: str):
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return data["username"]
    except:
        return None

def make_invite_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# ─── Auth ──────────────────────────────────────────────────────────────────

@app.post("/register")
async def register(req: AuthRequest):
    existing = await db.table("users").select("username").eq("username", req.username).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Username already taken")

    hashed = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    await db.table("users").insert({"username": req.username, "password": hashed}).execute()
    print(f"[+] Registered: {req.username}")
    return {"message": "Account created"}

@app.post("/login")
async def login(req: AuthRequest):
    result = await db.table("users").select("*").eq("username", req.username).execute()
    if not result.data:
        raise HTTPException(status_code=400, detail="User not found")

    user = result.data[0]
    if not bcrypt.checkpw(req.password.encode(), user["password"].encode()):
        raise HTTPException(status_code=400, detail="Wrong password")

    token = jwt.encode({
        "username": req.username,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }, SECRET_KEY, algorithm="HS256")

    print(f"[+] Logged in: {req.username}")
    return {"token": token, "username": req.username}

# ─── Rooms ─────────────────────────────────────────────────────────────────

@app.post("/rooms/create-public")
async def create_public_room(req: RoomRequest):
    username = verify_token(req.token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")
    if req.room_name in public_rooms:
        raise HTTPException(status_code=400, detail="Room already exists")
    public_rooms[req.room_name] = set()
    return {"message": f"Room '{req.room_name}' created"}

@app.post("/rooms/create-private")
async def create_private_room(req: PrivateRoomRequest):
    username = verify_token(req.token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")
    code = make_invite_code()
    private_rooms[code] = set()
    return {"invite_code": code}

@app.get("/rooms/public")
async def list_public_rooms():
    return {"rooms": list(public_rooms.keys())}

# ─── WebSocket ─────────────────────────────────────────────────────────────

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        username = data["username"]
    except:
        await websocket.close()
        return

    await websocket.accept()
    peers[username] = websocket
    print(f"[+] {username} connected")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "message":
                target = data.get("to")
                if target and target in peers:
                    await peers[target].send_text(raw)

            elif msg_type == "join_public":
                room = data.get("room")
                if room in public_rooms:
                    public_rooms[room].add(username)
                    await websocket.send_text(json.dumps({
                        "type": "system",
                        "text": f"Joined room '{room}'"
                    }))

            elif msg_type == "join_private":
                code = data.get("code")
                if code in private_rooms:
                    private_rooms[code].add(username)
                    await websocket.send_text(json.dumps({
                        "type": "system",
                        "text": f"Joined private room '{code}'"
                    }))
                else:
                    await websocket.send_text(json.dumps({
                        "type": "system",
                        "text": "Invalid invite code"
                    }))

            elif msg_type == "room_message":
                room = data.get("room")
                is_private = data.get("is_private", False)
                room_dict = private_rooms if is_private else public_rooms
                if room in room_dict:
                    for member in room_dict[room]:
                        if member != username and member in peers:
                            await peers[member].send_text(json.dumps({
                                "type": "room_message",
                                "from": username,
                                "room": room,
                                "text": data.get("text"),
                                "is_private": is_private
                            }))

    except WebSocketDisconnect:
        del peers[username]
        for room in public_rooms.values():
            room.discard(username)
        for room in private_rooms.values():
            room.discard(username)
        print(f"[-] {username} disconnected")