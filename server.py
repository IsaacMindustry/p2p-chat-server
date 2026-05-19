import asyncio
import json
from fastapi import FastAPI, WebSocket
from fastapi.websockets import WebSocketDisconnect

app = FastAPI()

# Store connected peers: {peer_id: websocket}
peers = {}

@app.websocket("/ws/{peer_id}")
async def websocket_endpoint(websocket: WebSocket, peer_id: str):
    await websocket.accept()
    peers[peer_id] = websocket
    print(f"[+] {peer_id} connected. Online: {list(peers.keys())}")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            target = data.get("to")

            if target and target in peers:
                # Forward message to the target peer
                await peers[target].send_text(raw)
                print(f"[→] {peer_id} → {target}: {data.get('type')}")
            else:
                print(f"[!] Target '{target}' not found")

    except WebSocketDisconnect:
        del peers[peer_id]
        print(f"[-] {peer_id} disconnected")