import asyncio
import json
import os
import socket
import time
from datetime import datetime
from urllib.parse import urlparse

import aiohttp
import dns.asyncresolver
import websockets


CERTSTREAM_URL = (
    os.getenv("CERTSTREAM_URL", "").strip()
    or "wss://certstream.calidog.io/"
)
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
TARGET_IPS = {
    value.strip()
    for value in os.getenv("TARGET_IPS", "").split(",")
    if value.strip()
}
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "260"))
DNS_TIMEOUT = float(os.getenv("DNS_TIMEOUT", "2.5"))
DNS_CONCURRENCY = int(os.getenv("DNS_CONCURRENCY", "200"))
SEND_TEST_MESSAGE = os.getenv("SEND_TEST_MESSAGE", "false").lower() == "true"

QUEUE: asyncio.Queue[str] = asyncio.Queue(maxsize=100_000)
SEEN_DOMAINS: set[str] = set()
SEMAPHORE = asyncio.Semaphore(DNS_CONCURRENCY)
STATS = {
    "certificates": 0,
    "domains": 0,
    "resolved": 0,
    "matches": 0,
    "queue_drops": 0,
}


def normalize_domain(value: str) -> str | None:
    domain = value.strip().lower().rstrip(".")
    if domain.startswith("*."):
        domain = domain[2:]
    if not domain or len(domain) > 253:
        return None
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if "/" in domain or " " in domain or "." not in domain:
        return None
    return domain


async def send_slack(text: str) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            SLACK_WEBHOOK_URL,
            json={"text": text},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            body = await response.text()
            if response.status >= 300:
                raise RuntimeError(
                    f"Slack returned HTTP {response.status}: {body[:300]}"
                )


async def resolve_ipv4(domain: str) -> set[str]:
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = DNS_TIMEOUT
    resolver.timeout = DNS_TIMEOUT
    try:
        answers = await resolver.resolve(domain, "A", search=False)
    except Exception:
        return set()
    return {answer.address for answer in answers}


async def check_domain(domain: str) -> None:
    async with SEMAPHORE:
        addresses = await resolve_ipv4(domain)
        if addresses:
            STATS["resolved"] += 1
        matched = addresses.intersection(TARGET_IPS)
        if not matched:
            return

        STATS["matches"] += 1
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        ip_text = ", ".join(sorted(addresses))
        message = (
            ":rotating_light: *New domain detected*\n"
            f"*Domain:* `{domain}`\n"
            f"*IP:* `{ip_text}`\n"
            f"*Detected:* `{timestamp}`\n"
            f"*URL:* https://{domain}"
        )
        print(f"MATCH {domain} -> {ip_text}", flush=True)
        await send_slack(message)


async def worker() -> None:
    while True:
        domain = await QUEUE.get()
        try:
            await check_domain(domain)
        except Exception as error:
            print(f"Worker error for {domain}: {error}", flush=True)
        finally:
            QUEUE.task_done()


def extract_domains(message: dict) -> list[str]:
    message_type = message.get("message_type")
    if message_type == "dns_entries":
        data = message.get("data", [])
        return data if isinstance(data, list) else []
    if message_type == "certificate_update":
        STATS["certificates"] += 1
        return (
            message.get("data", {})
            .get("leaf_cert", {})
            .get("all_domains", [])
        )
    return []


async def listen_until(deadline: float) -> None:
    print(f"Connecting to {CERTSTREAM_URL}", flush=True)
    async with websockets.connect(
        CERTSTREAM_URL,
        ping_interval=25,
        ping_timeout=20,
        close_timeout=5,
        max_size=5_000_000,
        open_timeout=20,
    ) as websocket:
        print("CertStream connected", flush=True)
        while time.monotonic() < deadline:
            timeout = max(0.1, min(30, deadline - time.monotonic()))
            try:
                raw_message = await asyncio.wait_for(websocket.recv(), timeout)
            except asyncio.TimeoutError:
                continue
            message = json.loads(raw_message)
            for raw_domain in extract_domains(message):
                domain = normalize_domain(str(raw_domain))
                if not domain or domain in SEEN_DOMAINS:
                    continue
                SEEN_DOMAINS.add(domain)
                STATS["domains"] += 1
                try:
                    QUEUE.put_nowait(domain)
                except asyncio.QueueFull:
                    STATS["queue_drops"] += 1


async def monitor() -> None:
    deadline = time.monotonic() + RUN_SECONDS
    delay = 3
    while time.monotonic() < deadline:
        try:
            await listen_until(deadline)
        except Exception as error:
            remaining = deadline - time.monotonic()
            print(f"Stream error: {error}", flush=True)
            if remaining <= 0:
                break
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 2, 20)
        else:
            delay = 3


async def main() -> None:
    if not TARGET_IPS:
        raise SystemExit("TARGET_IPS is empty")
    for address in TARGET_IPS:
        try:
            socket.inet_pton(socket.AF_INET, address)
        except OSError as error:
            raise SystemExit(f"Invalid IPv4 address in TARGET_IPS: {address}") from error
    parsed = urlparse(CERTSTREAM_URL)
    if parsed.scheme not in {"ws", "wss"}:
        raise SystemExit("CERTSTREAM_URL must start with ws:// or wss://")
    if not SLACK_WEBHOOK_URL.startswith("https://hooks.slack.com/"):
        raise SystemExit("SLACK_WEBHOOK_URL is missing or invalid")

    print("Target IPs: " + ", ".join(sorted(TARGET_IPS)), flush=True)
    if SEND_TEST_MESSAGE:
        await send_slack(
            ":white_check_mark: *Fast Domain Monitor test succeeded*\n"
            "GitHub Actions can reach Slack. Live CT monitoring is starting."
        )

    workers = [
        asyncio.create_task(worker())
        for _ in range(DNS_CONCURRENCY)
    ]
    await monitor()

    try:
        await asyncio.wait_for(QUEUE.join(), timeout=20)
    except asyncio.TimeoutError:
        print("Queue drain timed out; the next scheduled run will continue monitoring.")
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
    print("Run summary: " + json.dumps(STATS, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
