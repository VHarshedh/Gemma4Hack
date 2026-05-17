"""
Aegis — MQTT Listener Task
Windows note: aiomqtt/paho-mqtt use add_reader/add_writer which are only
supported by SelectorEventLoop. Uvicorn on Windows uses ProactorEventLoop.
The listener therefore runs its own SelectorEventLoop in a dedicated thread
and bridges incoming messages back to the main loop via asyncio.run_coroutine_threadsafe.
"""
import asyncio
import json
import logging
import threading

from config import MQTT_HOST, MQTT_PORT, MQTT_TOPIC_SENSORS

log = logging.getLogger("aegis.server.mqtt")


def _run_mqtt_thread(gis, ws_manager, main_loop: asyncio.AbstractEventLoop,
                     stop_event: threading.Event) -> None:
    """
    Runs in a background thread with its own SelectorEventLoop.
    Bridges received messages to the main (ProactorEventLoop) via
    run_coroutine_threadsafe so WebSocket broadcasts work correctly.
    """
    try:
        import aiomqtt
    except ImportError:
        log.info("aiomqtt not installed — MQTT listener disabled.")
        return

    async def _loop():
        _warned_once = False
        while not stop_event.is_set():
            try:
                async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as client:
                    _warned_once = False
                    await client.subscribe(MQTT_TOPIC_SENSORS)
                    log.info("MQTT subscribed to %s", MQTT_TOPIC_SENSORS)
                    async for message in client.messages:
                        if stop_event.is_set():
                            return
                        try:
                            data = json.loads(message.payload.decode())
                            data["msg_type"] = "sensor"

                            if hasattr(gis, "store_sensor_reading"):
                                try:
                                    gis.store_sensor_reading(
                                        data["sensor_id"], data["latitude"], data["longitude"],
                                        data["type"], data["value"], data["unit"],
                                    )
                                except Exception as e:
                                    log.error("Failed to store sensor reading: %s", e)

                            # Bridge broadcast to the main event loop
                            asyncio.run_coroutine_threadsafe(
                                ws_manager.broadcast(data), main_loop
                            )
                        except Exception as e:
                            log.error("MQTT message parse error: %s", e)
            except Exception as e:
                if not _warned_once:
                    log.warning("MQTT broker unavailable (%s). Retrying silently …", e)
                    _warned_once = True
                else:
                    log.debug("MQTT retry failed: %s", e)
                # Sleep in short intervals so stop_event is checked frequently.
                # A single long asyncio.sleep cannot be interrupted from outside
                # without force-stopping the loop (which causes RuntimeError).
                for _ in range(10):
                    if stop_event.is_set():
                        return
                    await asyncio.sleep(0.5)

    # Use SelectorEventLoop explicitly — required for paho-mqtt on Windows.
    selector_loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(selector_loop)
    try:
        selector_loop.run_until_complete(_loop())
    finally:
        selector_loop.close()


async def mqtt_listener(gis, ws_manager) -> None:
    """
    Spawns the MQTT loop in a background thread and waits until cancelled.
    Called from the FastAPI lifespan context.
    """
    main_loop = asyncio.get_running_loop()
    stop_event = threading.Event()

    thread = threading.Thread(
        target=_run_mqtt_thread,
        args=(gis, ws_manager, main_loop, stop_event),
        daemon=True,
        name="mqtt-listener",
    )
    thread.start()
    log.info("MQTT listener thread started.")

    try:
        # Poll with short sleeps so this coroutine remains cancellable.
        while thread.is_alive():
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        log.info("MQTT listener cancelled — signalling thread to stop.")
        stop_event.set()
        # Thread will exit within ≤0.5 s (next sleep interval check).
        thread.join(timeout=5.0)
