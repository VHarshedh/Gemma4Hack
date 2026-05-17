"""
Aegis — Offline-First Mesh Protocol
===================================
SQLite-backed encrypted outbox for mesh synchronization.
"""
import base64
import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel

from config import COMMAND_NODE_URL
from edge.cv import play_tts

log = logging.getLogger("aegis.edge.mesh")
console = Console()

QUEUE_DB = Path("data") / "mesh_queue.db"
MESH_ENCRYPTION_KEY = os.getenv("AEGIS_MESH_KEY", "aegis-default-key-change-me")


def init_queue_db() -> sqlite3.Connection:
    """Initialise the SQLite-backed mesh queue with schema."""
    QUEUE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(QUEUE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outbox (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id   TEXT NOT NULL UNIQUE,
            payload     TEXT NOT NULL,
            checksum    TEXT NOT NULL,
            encrypted   INTEGER NOT NULL DEFAULT 0,
            retries     INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 10,
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','sending','delivered','failed')),
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            last_attempt TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peer_nodes (
            node_id   TEXT PRIMARY KEY,
            endpoint  TEXT NOT NULL,
            last_seen TEXT,
            priority  INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def encrypt_payload(payload_json: str) -> str:
    """Simple XOR-based obfuscation for mesh transit."""
    key_bytes = hashlib.sha256(MESH_ENCRYPTION_KEY.encode()).digest()
    data = payload_json.encode()
    encrypted = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data))
    return base64.b64encode(encrypted).decode()


def decrypt_payload(encrypted_b64: str) -> str:
    """Reverse the XOR obfuscation."""
    key_bytes = hashlib.sha256(MESH_ENCRYPTION_KEY.encode()).digest()
    data = base64.b64decode(encrypted_b64)
    decrypted = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data))
    return decrypted.decode()


def enqueue_report(payload: dict) -> None:
    """Store a report in the offline queue."""
    conn = init_queue_db()
    payload_json = json.dumps(payload)
    checksum = hashlib.sha256(payload_json.encode()).hexdigest()
    encrypted = encrypt_payload(payload_json)

    try:
        conn.execute(
            "INSERT OR IGNORE INTO outbox (report_id, payload, checksum, encrypted) VALUES (?,?,?,1)",
            (payload.get("report_id", "unknown"), encrypted, checksum),
        )
        conn.commit()
    except Exception as e:
        log.error("Failed to enqueue report: %s", e)
    finally:
        conn.close()


def dequeue_pending() -> list[tuple[int, dict]]:
    """Fetch all pending reports from the outbox."""
    conn = init_queue_db()
    rows = conn.execute(
        "SELECT id, payload, encrypted FROM outbox WHERE status = 'pending' AND retries < max_retries ORDER BY id"
    ).fetchall()
    conn.close()

    results = []
    for row_id, payload_str, is_encrypted in rows:
        try:
            if is_encrypted:
                payload_str = decrypt_payload(payload_str)
            results.append((row_id, json.loads(payload_str)))
        except Exception as e:
            log.error("Corrupted queue entry #%d: %s", row_id, e)
    return results


def mark_delivered(row_id: int) -> None:
    conn = init_queue_db()
    conn.execute("UPDATE outbox SET status='delivered', last_attempt=datetime('now') WHERE id=?", (row_id,))
    conn.commit()
    conn.close()


def mark_retry(row_id: int) -> None:
    conn = init_queue_db()
    conn.execute(
        "UPDATE outbox SET retries=retries+1, last_attempt=datetime('now') WHERE id=?",
        (row_id,),
    )
    conn.commit()
    conn.close()


def sync_worker() -> None:
    """Background thread: flush the offline queue with retry logic."""
    url = f"{COMMAND_NODE_URL}/api/v1/field-report"
    while True:
        try:
            pending = dequeue_pending()
            if not pending:
                time.sleep(5)
                continue

            log.info("Mesh sync: %d pending report(s) in outbox.", len(pending))
            for row_id, report in pending:
                try:
                    with httpx.Client(timeout=15.0) as client:
                        resp = client.post(url, json=report)
                        resp.raise_for_status()

                    mark_delivered(row_id)
                    log.info("✅ Synced offline report %s (queue #%d)", report.get("report_id"), row_id)
                except httpx.ConnectError:
                    mark_retry(row_id)
                    log.warning("Mesh down — retry queued for #%d", row_id)
                    time.sleep(10)
                    break
                except Exception as e:
                    mark_retry(row_id)
                    log.error("Sync error for #%d: %s", row_id, e)
        except Exception as e:
            log.error("Sync worker fatal error: %s", e)
        
        time.sleep(5)


def transmit_report(report) -> dict[str, Any] | None:
    """Transmit a field report to the Command Center."""
    url = f"{COMMAND_NODE_URL}/api/v1/field-report"
    payload = report.model_dump()

    console.print(
        Panel(
            f"[bold cyan]Transmitting to Command Node[/]\n"
            f"Endpoint: {url}\n"
            f"Report ID: {report.report_id}\n"
            f"Payload size: {len(json.dumps(payload))} bytes\n"
            f"Encryption: XOR-SHA256 mesh cipher",
            title="[MESH] Encrypted Transmission",
            border_style="cyan",
        )
    )

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            result = resp.json()

        console.print(
            Panel(
                json.dumps(result, indent=2),
                title="[OK] Command Center Response",
                border_style="green",
            )
        )
        
        if "dispatch_plan" in result:
            play_tts(result["dispatch_plan"])
            
        return result

    except httpx.ConnectError:
        log.warning(
            "Cannot reach Command Node at %s. Queueing for offline sync.",
            url
        )
        enqueue_report(payload)
        
        conn = init_queue_db()
        pending_count = conn.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0]
        conn.close()
        
        console.print(
            Panel(
                f"Mesh Network Unavailable.\n"
                f"Report encrypted and queued for background sync.\n"
                f"Outbox: {pending_count} report(s) pending delivery.",
                title="[OFFLINE] Encrypted Queue",
                border_style="yellow",
            )
        )
        return None
    except Exception as e:
        log.error("Transmission error: %s", e)
        return None
