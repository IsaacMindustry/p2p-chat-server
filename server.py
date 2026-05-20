import json
import bcrypt
import jwt
import datetime
import random
import string
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

users = {}        # {username: hashed_password}
peers = {}        # {username: websocket}
public_rooms = {} # {room_name: set of usernames}
private_rooms = {}# {invite_code: set of usernames}

SECRET_KEY = "changethislater123"

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
    if req.username in users:
        raise HTTPException(status_code=400, detail="Username already taken")
    hashed = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt())
    users[req.username] = hashed
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

            # Direct message
            if msg_type == "message":
                target = data.get("to")
                if target and target in peers:
                    await peers[target].send_text(raw)

            # Join public room
            elif msg_type == "join_public":
                room = data.get("room")
                if room in public_rooms:
                    public_rooms[room].add(username)
                    await websocket.send_text(json.dumps({
                        "type": "system",
                        "text": f"Joined room '{room}'"
                    }))

            # Join private room
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

            # Room message
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