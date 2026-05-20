import json
import bcrypt
import jwt
import datetime
import random
import string
import os
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
@app.get("/")
async def health():
    return {"status": "ok"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

peers = {}
public_rooms = {}
private_rooms = {}

SECRET_KEY = "changethislater123"

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

async def sb_get(table, filters=""):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{filters}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=sb_headers())
        return r.json()

async def sb_insert(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = sb_headers()
    headers["Prefer"] = "return=minimal"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=headers, json=data)
        return {}

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
    existing = await sb_get("users", f"username=eq.{req.username}&select=username")
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")

    hashed = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    await sb_insert("users", {"username": req.username, "password": hashed})
    print(f"[+] Registered: {req.username}")
    return {"message": "Account created"}

@app.post("/login")
async def login(req: AuthRequest):
    result = await sb_get("users", f"username=eq.{req.username}&select=*")
    if not result:
        raise HTTPException(status_code=400, detail="User not found")

    user = result[0]
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

class FriendRequest(BaseModel):
    token: str
    to_user: str

class FriendResponse(BaseModel):
    token: str
    request_id: int
    accept: bool

@app.post("/friends/request")
async def send_friend_request(req: FriendRequest):
    username = verify_token(req.token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")
    if username == req.to_user:
        raise HTTPException(status_code=400, detail="Can't add yourself")

    # Check user exists
    user = await sb_get("users", f"username=eq.{req.to_user}&select=username")
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check already friends
    already = await sb_get("friends", f"or=(and(user1.eq.{username},user2.eq.{req.to_user}),and(user1.eq.{req.to_user},user2.eq.{username}))&select=id")
    if already:
        raise HTTPException(status_code=400, detail="Already friends")

    # Check already requested
    existing = await sb_get("friend_requests", f"from_user=eq.{username}&to_user=eq.{req.to_user}&status=eq.pending&select=id")
    if existing:
        raise HTTPException(status_code=400, detail="Request already sent")

    headers = sb_headers()
    headers["Prefer"] = "return=minimal"
    async with httpx.AsyncClient() as client:
        await client.post(f"{SUPABASE_URL}/rest/v1/friend_requests",
            headers=headers,
            json={"from_user": username, "to_user": req.to_user, "status": "pending"})

    # Notify recipient if online
    if req.to_user in peers:
        await peers[req.to_user].send_text(json.dumps({
            "type": "friend_request",
            "from": username
        }))

    return {"message": "Friend request sent"}

@app.post("/friends/respond")
async def respond_to_request(req: FriendResponse):
    username = verify_token(req.token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await sb_get("friend_requests", f"id=eq.{req.request_id}&to_user=eq.{username}&select=*")
    if not result:
        raise HTTPException(status_code=404, detail="Request not found")

    from_user = result[0]["from_user"]

    # Update request status
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/friend_requests?id=eq.{req.request_id}",
            headers={**sb_headers(), "Prefer": "return=minimal"},
            json={"status": "accepted" if req.accept else "declined"}
        )

    if req.accept:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/friends",
                headers={**sb_headers(), "Prefer": "return=minimal"},
                json={"user1": from_user, "user2": username}
            )
        # Notify sender if online
        if from_user in peers:
            await peers[from_user].send_text(json.dumps({
                "type": "friend_accepted",
                "from": username
            }))

    return {"message": "accepted" if req.accept else "declined"}

@app.get("/friends/list")
async def get_friends(token: str):
    username = verify_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await sb_get("friends", f"or=(user1.eq.{username},user2.eq.{username})&select=*")
    friends = []
    for row in result:
        friend = row["user2"] if row["user1"] == username else row["user1"]
        friends.append({"username": friend, "online": friend in peers})
    return {"friends": friends}

@app.get("/friends/requests")
async def get_requests(token: str):
    username = verify_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await sb_get("friend_requests", f"to_user=eq.{username}&status=eq.pending&select=*")
    return {"requests": result}


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