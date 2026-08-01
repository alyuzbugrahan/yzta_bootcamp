"""Load harness for the scanning WebSocket.

Answers one question: **how many farmers can one replica carry before frame rate collapses?**

Run it against a live server::

    AGROVISION_MODEL__ALLOW_DEMO=1 uvicorn app.main:app --port 8000
    .venv/bin/python tools/loadtest_scan.py --url http://127.0.0.1:8000 --clients 8

Each simulated client self-clocks the way the real browser should (§4.2 of the plan): send one
frame, wait for its ``frame`` reply, send the next. That measures the round trip a farmer
actually experiences rather than how fast frames can be pushed into a socket.

**The number that matters is per-frame inference cost, and that depends on the model.** With
``--allow-demo`` there is no YOLO in the loop, so the result is transport-plus-pipeline overhead
— a floor, not a capacity figure. Re-run against the real weights to size a deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import cv2
import numpy as np

try:
    import httpx
    import websockets
except ImportError as exc:  # pragma: no cover - harness only
    raise SystemExit(
        "Load harness needs httpx and websockets: pip install httpx websockets"
    ) from exc


# A frame that never gets a reply means the server dropped it; fail rather than block.
RECV_TIMEOUT = 15.0


def synthetic_frame(width: int, height: int, quality: int) -> bytes:
    """One fig-shaped blob, encoded the way a browser downscaler would."""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.ellipse(
        image,
        (width // 2, height // 2),
        (width // 8, height // 8),
        0,
        0,
        360,
        (220, 220, 220),
        -1,
    )
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("could not encode frame")
    return buffer.tobytes()


class Client:
    def __init__(self, index: int, base_url: str, frame: bytes) -> None:
        self.index = index
        self.base_url = base_url.rstrip("/")
        self.frame = frame
        self.latencies: list[float] = []
        self.frames = 0
        self.dropped = 0
        self.inspections = 0
        self.error: str | None = None

    async def run(self, duration: float) -> None:
        try:
            await self._run(duration)
        except Exception as exc:  # noqa: BLE001 - harness reports rather than raises
            self.error = f"{type(exc).__name__}: {exc}"

    async def _run(self, duration: float) -> None:
        email = f"load{self.index}-{int(time.time() * 1000)}@example.com"

        async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as http:
            registration = await http.post(
                "/api/v1/auth/register", json={"email": email, "password": "harvest-2026"}
            )
            registration.raise_for_status()
            headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}

            created = await http.post("/api/v1/sessions", json={}, headers=headers)
            created.raise_for_status()
            session_uuid = created.json()["uuid"]

            ticket = await http.post(
                f"/api/v1/sessions/{session_uuid}/ticket", headers=headers
            )
            ticket.raise_for_status()
            ticket_value = ticket.json()["ticket"]

        ws_url = (
            self.base_url.replace("http://", "ws://").replace("https://", "wss://")
            + f"/api/v1/ws/scan/{session_uuid}?ticket={ticket_value}"
        )

        deadline = time.monotonic() + duration

        async with websockets.connect(ws_url, max_size=4 * 1024 * 1024) as socket:
            while time.monotonic() < deadline:
                started = time.monotonic()
                await socket.send(self.frame)

                # Self-clocking: wait for this frame's reply before sending the next.
                # Bounded, because a dropped frame produces no reply at all — without a
                # timeout the harness would hang rather than report the stall.
                while True:
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=RECV_TIMEOUT)
                    except TimeoutError:
                        self.error = f"no reply within {RECV_TIMEOUT}s"
                        return
                    message = json.loads(raw)
                    if message["type"] == "inspection":
                        self.inspections += 1
                        continue
                    if message["type"] == "error":
                        self.error = message.get("code", "ERROR")
                        return
                    if message["type"] == "dropped":
                        # The server declined this frame. Acknowledged rather than silent, so
                        # a self-clocking client can move on instead of stalling.
                        self.dropped += 1
                        break
                    if message["type"] == "frame":
                        break

                if message["type"] == "frame":
                    self.latencies.append((time.monotonic() - started) * 1000.0)
                    self.frames += 1


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--quality", type=int, default=70)
    args = parser.parse_args()

    frame = synthetic_frame(args.width, args.height, args.quality)
    clients = [Client(i, args.url, frame) for i in range(args.clients)]

    print(
        f"{args.clients} clients x {args.duration:.0f}s, "
        f"{args.width}x{args.height} q{args.quality} ({len(frame) / 1024:.0f} KB/frame)"
    )

    started = time.monotonic()
    await asyncio.gather(*(client.run(args.duration) for client in clients))
    elapsed = time.monotonic() - started

    failed = [c for c in clients if c.error]
    latencies = [value for c in clients for value in c.latencies]
    total_frames = sum(c.frames for c in clients)

    if failed:
        print(f"\n{len(failed)} client(s) failed:")
        for client in failed[:5]:
            print(f"  client {client.index}: {client.error}")

    if not latencies:
        print("\nno frames completed")
        return

    latencies.sort()
    per_client = [c.frames / elapsed for c in clients if c.frames]

    print(f"\nframes           {total_frames}")
    print(f"aggregate fps    {total_frames / elapsed:.1f}")
    print(f"per-client fps   {statistics.mean(per_client):.2f} (mean)")
    print(f"latency p50      {latencies[len(latencies) // 2]:.0f} ms")
    print(f"latency p95      {latencies[int(len(latencies) * 0.95)]:.0f} ms")
    print(f"latency max      {latencies[-1]:.0f} ms")
    print(f"upstream         {total_frames * len(frame) / elapsed / 1024:.0f} KB/s")


if __name__ == "__main__":
    asyncio.run(main())
