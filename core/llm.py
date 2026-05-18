"""
Aegis — LLM Orchestration & Reasoning
======================================
Backends for Gemma 4 31B and the multi-turn reasoning engine.
"""
from __future__ import annotations

import json
import logging
import re
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

from config import (
    CONTEXT_SIZE, MAX_TOKENS, OLLAMA_HOST, OLLAMA_MODEL, TEMPERATURE, TOP_K, TOP_P,
)
from core.gis_tools import TOOL_DEFINITIONS

log = logging.getLogger("aegis.core.llm")

SYSTEM_PROMPT = textwrap.dedent("""\
You are AEGIS, an AI crisis coordinator for disaster response. You operate
in a zero-internet environment and must make life-safety decisions using
ONLY the local GIS database accessible through the tools provided.

CRITICAL RULES:
1. ALWAYS query the database before making routing decisions.
2. NEVER recommend routes through known hazards.
3. Prioritise hospitals for injured, shelters for displaced civilians.
4. Factor in remaining capacity — do not send people to full shelters.
5. Use <think> tags to reason step-by-step before calling tools or giving
   your final answer.

You have access to the following tools:
{tools}

When you need to call a tool, respond with EXACTLY this JSON format
on its own line:
<tool_call>{{"name": "<function_name>", "arguments": {{...}}}}</tool_call>

After receiving tool results, synthesise them into a DISPATCH PLAN with:
- Recommended safe zone (with remaining capacity)
- Primary and alternate evacuation routes
- Hazards to avoid along each route
- Estimated travel time
- Priority level and recommended response teams
""")

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


class LLMBackend:
    """llama-cpp-python backend for Gemma 4 31B Dense."""

    def __init__(self, model_path: Path):
        from llama_cpp import Llama
        log.info("Loading Gemma 4 31B from %s (this may take a minute) …", model_path)
        self.llm = Llama(
            model_path=str(model_path), n_ctx=CONTEXT_SIZE, n_gpu_layers=-1,
            verbose=False, chat_format="gemma",
        )
        log.info("Model loaded.")

    def generate(self, messages: list[dict]) -> str:
        resp = self.llm.create_chat_completion(
            messages=messages, temperature=TEMPERATURE, top_p=TOP_P, top_k=TOP_K,
            max_tokens=MAX_TOKENS,
        )
        return resp["choices"][0]["message"]["content"]


class MockLLMBackend:
    """Deterministic mock that simulates the full tool-calling loop."""

    def __init__(self):
        log.info("[MOCK] Using mock LLM backend — no model weights required.")
        self._call_count = 0

    def generate(self, messages: list[dict]) -> str:
        self._call_count += 1

        # First call → simulate thinking + tool calls
        if self._call_count == 1:
            return (
                "<think>\nAnalysing field report. Operator reports structural collapse with gas leak "
                "at ~46.2088, -123.8156. I need to:\n1. Check nearby hazards to understand the threat landscape\n"
                "2. Find safe zones with remaining capacity\n3. Find clear evacuation routes\nLet me query the database.\n</think>\n\n"
                'I need to assess the situation. Let me check the area.\n\n'
                '<tool_call>{"name": "query_hazards", "arguments": {"latitude": 46.2088, "longitude": -123.8156, "radius_km": 3.0, "min_severity": "moderate"}}</tool_call>'
            )
        if self._call_count == 2:
            return '<tool_call>{"name": "query_safe_zones", "arguments": {"latitude": 46.2088, "longitude": -123.8156, "radius_km": 5.0, "zone_type": "any"}}</tool_call>'
        if self._call_count == 3:
            return '<tool_call>{"name": "query_sop", "arguments": {"query": "gas leak"}}</tool_call>'
        if self._call_count == 4:
            return '<tool_call>{"name": "query_routes", "arguments": {"from_lat": 46.2088, "from_lon": -123.8156}}</tool_call>'

        # Final call → dispatch plan
        return textwrap.dedent("""\
            <think>
            [AGENT: HazMat] Analyzed incident. Multiple hazards in the area including a collapsed parking garage and active gas leak. Chemical spill near industrial area requires masks on Route Golf.
            [AGENT: Logistics] Safe zones queried. Cascadia Bay High School has remaining capacity (488 slots) and is operational. Route Alpha from Harbor Park is clear with National Guard escort. Route Delta is BLOCKED due to parking garage collapse.
            [AGENT: Medical] Casualties reported. St. Mary's is at capacity. Triage required on-site before transport.
            [AGENT: Commander] Synthesizing inputs... I should route civilians to the High School via Route Alpha, avoiding the gas leak on 4th & Harbor. Dispatching specialized units.
            </think>

            # 🚨 AEGIS MULTI-AGENT DISPATCH PLAN — PRIORITY: CRITICAL

            ## Swarm Assessment
            - **HazMat Agent**: Detected active gas leak creating ignition risk.
            - **Logistics Agent**: Identified Route Delta as blocked. Route Alpha is the primary evacuation corridor.
            - **Medical Agent**: Prioritizing on-site triage due to limited hospital capacities.

            ## Recommended Actions

            ### 1. Immediate Dispatch
            | Team | Destination | Priority |
            |------|------------|----------|
            | SAR Team (Urban) | Collapse site — 4th & Harbor | IMMEDIATE |
            | HazMat Unit | Gas leak — 4th & Harbor | IMMEDIATE |
            | Ambulance × 2 | Stage at Firehouse #7 | HIGH |

            ### 2. Evacuation Route — PRIMARY
            - **Route**: Route Alpha — Harbor Park → Cascadia Bay High School
            - **Status**: ✅ CLEAR (National Guard escorted)
            - **Distance**: 2.1 km | **ETA**: ~8 minutes
            - **Destination Capacity**: 488 remaining slots

            ### 3. Hazards to AVOID
            - 🚫 Route Delta — BLOCKED (parking garage collapse debris)
            - ⚠️ Keep 150m clearance from 4th & Harbor (gas main rupture)
            - ⚠️ Keep 30m from Oak Street (live 12kV power line)
        """)


class OllamaBackend:
    """
    Ollama backend — calls any Gemma 4 model served by a local Ollama instance.
    Uses the OpenAI-compatible /v1/chat/completions endpoint so the message
    format is identical to LLMBackend; no extra dependencies needed (httpx
    is already in requirements.txt).

    Setup:
        ollama pull gemma4:e2b    # 7.2 GB, runs on CPU
        ollama pull gemma4:27b    # 18 GB, GPU recommended
    Run:
        python command_node.py --ollama
        python command_node.py --ollama --ollama-model gemma4:27b
    """

    # One global semaphore shared across all OllamaBackend instances.
    # Kept at 1 so only one LLM call reaches Ollama at a time — prevents
    # multiple context allocations from blowing RAM on CPU-only machines.
    _semaphore: threading.Semaphore = threading.Semaphore(1)

    def __init__(self, model: str = OLLAMA_MODEL, host: str = OLLAMA_HOST):
        import httpx
        self.model = model
        self._url = f"{host.rstrip('/')}/v1/chat/completions"
        self._client = httpx.Client(timeout=300.0)  # 5 min — enough for any single inference
        # Verify Ollama is reachable and the model is available
        try:
            tags_url = f"{host.rstrip('/')}/api/tags"
            resp = self._client.get(tags_url)
            available = [m["name"] for m in resp.json().get("models", [])]
            if not any(model in name for name in available):
                log.warning(
                    "Model '%s' not found in Ollama. Run: ollama pull %s\n"
                    "  Available: %s",
                    model, model, ", ".join(available) or "none",
                )
            else:
                log.info("Ollama backend ready — model: %s  endpoint: %s", model, self._url)
        except Exception as e:
            log.warning("Could not reach Ollama at %s: %s", host, e)

    def generate(self, messages: list[dict]) -> str:
        import httpx
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "max_tokens": MAX_TOKENS,
            "stream": False,
        }
        with OllamaBackend._semaphore:
            try:
                resp = self._client.post(self._url, json=payload)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                log.error("Ollama HTTP error: %s — %s", e.response.status_code, e.response.text)
                raise
            except Exception as e:
                log.error("Ollama generate error: %s", e)
                raise


class ReasoningEngine:
    """
    Multi-turn orchestrator that manages the think → tool_call → tool_response
    loop between the LLM and the GIS database.
    """

    MAX_TOOL_ROUNDS = 5

    def __init__(self, llm: LLMBackend | MockLLMBackend, gis: Any):
        self.llm = llm
        self.gis = gis

    def process_report(self, report: dict) -> dict:
        """Process a field report through the full reasoning pipeline."""
        t0 = time.perf_counter()
        tools_json = json.dumps(TOOL_DEFINITIONS, indent=2)
        sys_prompt = SYSTEM_PROMPT.format(tools=tools_json)

        user_msg = (
            f"INCOMING FIELD REPORT — PRIORITY: {report.get('threat_level','UNKNOWN').upper()}\n\n"
            f"Operator: {report.get('operator_id')}\n"
            f"Time: {report.get('timestamp')}\n"
            f"Location: {report.get('location',{}).get('latitude')}, {report.get('location',{}).get('longitude')}\n"
            f"Category: {report.get('category')}\n\n"
            f"AUDIO TRANSCRIPT:\n{report.get('audio_transcript','N/A')}\n\n"
            f"VISUAL ASSESSMENT:\n{report.get('image_analysis','N/A')}\n\n"
            "Analyse the situation, query the GIS database for safe zones, hazards, "
            "and evacuation routes, then produce a DISPATCH PLAN."
        )

        messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}]
        tool_calls_log = []

        for round_num in range(1, self.MAX_TOOL_ROUNDS + 1):
            log.info("LLM reasoning round %d/%d …", round_num, self.MAX_TOOL_ROUNDS)
            response = self.llm.generate(messages)
            log.info("LLM response (%d chars)", len(response))

            tool_match = TOOL_CALL_RE.search(response)
            if not tool_match:
                # No tool call — this is the final response
                elapsed = time.perf_counter() - t0
                return {
                    "status": "success",
                    "dispatch_plan": response,
                    "tool_calls": tool_calls_log,
                    "reasoning_rounds": round_num,
                    "processing_time_s": round(elapsed, 2),
                }

            # Parse and execute tool call
            try:
                call = json.loads(tool_match.group(1))
            except json.JSONDecodeError:
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": "Error: malformed tool call JSON. Please try again."})
                continue

            tool_name = call.get("name", "")
            tool_args = call.get("arguments", {})
            log.info("Tool call: %s(%s)", tool_name, json.dumps(tool_args))

            result = self.gis.execute_tool(tool_name, tool_args)
            tool_calls_log.append({
                "round": round_num, 
                "tool": tool_name, 
                "arguments": tool_args, 
                "result_count": len(result) if isinstance(result, list) else 1
            })
            log.info("Tool returned %s results", len(result) if isinstance(result, list) else 1)

            # Feed results back to model
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"<tool_response>\n{json.dumps(result, indent=2, default=str)}\n</tool_response>\n\nContinue your analysis. Call another tool if needed, or provide the final DISPATCH PLAN."})

        elapsed = time.perf_counter() - t0
        return {
            "status": "max_rounds_reached", 
            "dispatch_plan": response, 
            "tool_calls": tool_calls_log, 
            "reasoning_rounds": self.MAX_TOOL_ROUNDS, 
            "processing_time_s": round(elapsed, 2)
        }
