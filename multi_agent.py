"""
Aegis — Multi-Agent Swarm Orchestration (Phase 1)
===================================================
Replaces the single-prompt reasoning loop with a true multi-agent
debate architecture.  Three specialist agents (HazMat, Logistics,
Medical) each independently analyse the field report, then a
Commander agent synthesises their assessments into the final
dispatch plan.

Architecture
------------
  1. Field report is broadcast to all specialist agents.
  2. Each agent runs its own tool-calling loop against the GIS DB,
     restricted to its domain-relevant tools.
  3. Specialist assessments are collected.
  4. A Commander agent receives all assessments and produces the
     final DISPATCH PLAN.

This module provides ``MultiAgentEngine`` as a drop-in replacement
for ``ReasoningEngine`` in command_node.py.
"""
from __future__ import annotations

import json
import logging
import textwrap
import time
from typing import Any

from config import AGENT_MAX_DEBATE_ROUNDS, AGENT_SPECIALISTS

log = logging.getLogger("aegis.swarm")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Specialist Agent Prompts
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SPECIALIST_PROMPTS: dict[str, str] = {
    "hazmat": textwrap.dedent("""\
        You are the HazMat Specialist Agent in the AEGIS crisis system.
        Your SOLE focus is chemical, biological, radiological, and explosive hazards.

        Responsibilities:
        - Identify hazardous materials in the vicinity of the incident.
        - Recommend exclusion zones and required PPE.
        - Flag routes that pass through contaminated areas.

        You have access to: query_hazards, query_sop
        Respond with a concise 3-5 bullet assessment. Start with "[HAZMAT ASSESSMENT]".
    """),

    "logistics": textwrap.dedent("""\
        You are the Logistics Specialist Agent in the AEGIS crisis system.
        Your SOLE focus is evacuation routing and shelter capacity.

        Responsibilities:
        - Find safe zones with remaining capacity near the incident.
        - Identify clear evacuation routes and flag blocked ones.
        - Estimate travel times and recommend primary/alternate corridors.

        You have access to: query_safe_zones, query_routes
        Respond with a concise 3-5 bullet assessment. Start with "[LOGISTICS ASSESSMENT]".
    """),

    "medical": textwrap.dedent("""\
        You are the Medical Specialist Agent in the AEGIS crisis system.
        Your SOLE focus is triage, casualty estimation, and hospital routing.

        Responsibilities:
        - Estimate casualty severity from the field report.
        - Check hospital and clinic capacity in the area.
        - Recommend where to route injured vs. displaced civilians.

        You have access to: query_safe_zones (hospital filter), query_sop
        Respond with a concise 3-5 bullet assessment. Start with "[MEDICAL ASSESSMENT]".
    """),
}

COMMANDER_PROMPT = textwrap.dedent("""\
    You are the AEGIS Commander — the final decision-maker in a multi-agent
    crisis coordination swarm.  You have received independent assessments
    from three specialist agents: HazMat, Logistics, and Medical.

    Your job:
    1. Reconcile conflicting recommendations.
    2. Produce a single, authoritative DISPATCH PLAN.
    3. The plan must include: primary safe zone, primary/alternate routes,
       hazards to avoid, dispatched teams, and estimated travel times.

    Format your response as a structured markdown plan starting with:
    # 🚨 AEGIS MULTI-AGENT DISPATCH PLAN
""")

# Maps each specialist to the tool names it is allowed to call.
AGENT_TOOL_ACCESS: dict[str, set[str]] = {
    "hazmat":    {"query_hazards", "query_sop"},
    "logistics": {"query_safe_zones", "query_routes"},
    "medical":   {"query_safe_zones", "query_sop"},
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Mock Multi-Agent Backend
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MockSpecialistBackend:
    """Deterministic mock that returns realistic per-agent assessments."""

    def generate_assessment(self, agent_name: str, report: dict, tool_results: list[dict]) -> str:
        """Generate a mock specialist assessment."""
        if agent_name == "hazmat":
            return textwrap.dedent("""\
                [HAZMAT ASSESSMENT]
                - Active gas leak detected at 4th & Harbor (150m exclusion zone required).
                - Chemical spill at Cascadia Chemical — 300m clearance, full-face respirators mandatory.
                - Route Golf passes within chemical exclusion zone — masks REQUIRED if using.
                - Route Delta blocked by collapsed structure — ignition risk from gas main.
                - SOP recommends staging HazMat unit at Firehouse #7.
            """)
        elif agent_name == "logistics":
            return textwrap.dedent("""\
                [LOGISTICS ASSESSMENT]
                - PRIMARY: Route Alpha (Harbor Park → Cascadia Bay High School) — CLEAR, 2.1km, ~8min.
                - ALTERNATE: Route Foxtrot (Medical Corridor) — CLEAR, 1.5km, ~6min (ambulance priority).
                - BLOCKED: Route Delta — impassable due to parking garage collapse.
                - Cascadia Bay High School: 488 slots remaining (61% capacity available).
                - Pacific Ridge Elementary: 345 slots remaining (nearest secondary shelter).
            """)
        else:  # medical
            return textwrap.dedent("""\
                [MEDICAL ASSESSMENT]
                - Estimated casualties: 3-8 based on structural collapse + gas leak proximity.
                - St. Mary's Hospital at 86% capacity — accepting critical patients ONLY.
                - Bayfront Medical Clinic at 98% capacity — effectively FULL.
                - Recommend on-site triage before transport; stage ambulances at Firehouse #7.
                - Walking wounded should be directed to Cascadia Bay High School (Red Cross on site).
            """)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Multi-Agent Engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MultiAgentEngine:
    """
    Multi-agent orchestrator that fans out analysis to specialist agents,
    then synthesises their outputs via a Commander agent.

    Drop-in compatible with ReasoningEngine.process_report().
    """

    MAX_TOOL_ROUNDS = 3

    def __init__(self, llm, gis, *, use_mock: bool = False):
        self.llm = llm
        self.gis = gis
        self.use_mock = use_mock
        if use_mock:
            self._mock = MockSpecialistBackend()
        log.info("Multi-Agent Swarm Engine initialised (agents: %s)", AGENT_SPECIALISTS)

    def _run_specialist(self, agent_name: str, report: dict) -> dict:
        """Run a single specialist agent's analysis loop."""
        t0 = time.perf_counter()
        allowed_tools = AGENT_TOOL_ACCESS.get(agent_name, set())

        if self.use_mock:
            # Use domain-specific mock tool calls for realistic output
            tool_results = self._mock_tool_calls(agent_name, report)
            assessment = self._mock.generate_assessment(agent_name, report, tool_results)
        else:
            # Real LLM loop
            assessment, tool_results = self._llm_agent_loop(agent_name, report, allowed_tools)

        elapsed = time.perf_counter() - t0
        log.info("[%s] Assessment complete in %.2fs (%d tool calls)",
                 agent_name.upper(), elapsed, len(tool_results))

        return {
            "agent": agent_name,
            "assessment": assessment,
            "tool_calls": tool_results,
            "processing_time_s": round(elapsed, 2),
        }

    def _mock_tool_calls(self, agent_name: str, report: dict) -> list[dict]:
        """Execute realistic tool calls for mock mode."""
        lat = report.get("location", {}).get("latitude", 46.2088)
        lon = report.get("location", {}).get("longitude", -123.8156)
        results = []

        if agent_name == "hazmat":
            r = self.gis.execute_tool("query_hazards", {"latitude": lat, "longitude": lon, "radius_km": 3.0})
            results.append({"tool": "query_hazards", "result_count": len(r) if isinstance(r, list) else 1})
            r = self.gis.execute_tool("query_sop", {"query": report.get("category", "hazard")})
            results.append({"tool": "query_sop", "result_count": len(r) if isinstance(r, list) else 1})
        elif agent_name == "logistics":
            r = self.gis.execute_tool("query_safe_zones", {"latitude": lat, "longitude": lon, "radius_km": 5.0})
            results.append({"tool": "query_safe_zones", "result_count": len(r) if isinstance(r, list) else 1})
            r = self.gis.execute_tool("query_routes", {"from_lat": lat, "from_lon": lon})
            results.append({"tool": "query_routes", "result_count": len(r) if isinstance(r, list) else 1})
        else:  # medical
            r = self.gis.execute_tool("query_safe_zones", {"latitude": lat, "longitude": lon, "radius_km": 5.0, "zone_type": "hospital"})
            results.append({"tool": "query_safe_zones", "result_count": len(r) if isinstance(r, list) else 1})

        return results

    def _llm_agent_loop(self, agent_name: str, report: dict, allowed_tools: set[str]) -> tuple[str, list[dict]]:
        """Run the real LLM tool-calling loop for a specialist."""
        import re
        TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

        from command_node import TOOL_DEFINITIONS
        agent_tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in allowed_tools]
        sys_prompt = SPECIALIST_PROMPTS[agent_name] + f"\n\nAvailable tools:\n{json.dumps(agent_tools, indent=2)}"

        user_msg = (
            f"FIELD REPORT — {report.get('threat_level', 'UNKNOWN').upper()}\n"
            f"Location: {report.get('location', {}).get('latitude')}, {report.get('location', {}).get('longitude')}\n"
            f"Category: {report.get('category')}\n"
            f"Audio: {report.get('audio_transcript', 'N/A')}\n"
            f"Visual: {report.get('image_analysis', 'N/A')}\n"
        )

        messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}]
        tool_log: list[dict] = []

        for _ in range(self.MAX_TOOL_ROUNDS):
            response = self.llm.generate(messages)
            match = TOOL_CALL_RE.search(response)
            if not match:
                return response, tool_log

            try:
                call = json.loads(match.group(1))
            except json.JSONDecodeError:
                return response, tool_log

            tool_name = call.get("name", "")
            if tool_name not in allowed_tools:
                return response, tool_log

            result = self.gis.execute_tool(tool_name, call.get("arguments", {}))
            tool_log.append({"tool": tool_name, "result_count": len(result) if isinstance(result, list) else 1})
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"<tool_response>\n{json.dumps(result, indent=2, default=str)}\n</tool_response>"})

        return response, tool_log

    def _synthesise_dispatch(self, report: dict, assessments: list[dict]) -> str:
        """Commander agent synthesises specialist outputs into a dispatch plan."""
        if self.use_mock:
            return self._mock_dispatch(assessments)

        assessment_text = "\n\n".join(a["assessment"] for a in assessments)
        messages = [
            {"role": "system", "content": COMMANDER_PROMPT},
            {"role": "user", "content": f"SPECIALIST ASSESSMENTS:\n\n{assessment_text}\n\nProduce the final DISPATCH PLAN."},
        ]
        return self.llm.generate(messages)

    def _mock_dispatch(self, assessments: list[dict]) -> str:
        """Generate the final mock dispatch plan from agent assessments."""
        return textwrap.dedent("""\
            # 🚨 AEGIS MULTI-AGENT DISPATCH PLAN — PRIORITY: CRITICAL

            ## Swarm Consensus
            - **HazMat Agent**: Active gas leak + chemical spill detected. 150m/300m exclusion zones.
            - **Logistics Agent**: Route Alpha is primary corridor (CLEAR). Route Delta is BLOCKED.
            - **Medical Agent**: St. Mary's near capacity. On-site triage required.

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

            ### 3. Evacuation Route — ALTERNATE
            - **Route**: Route Foxtrot — Bayfront Clinic → St. Mary's Hospital
            - **Status**: ✅ CLEAR (ambulance priority)
            - **Distance**: 1.5 km | **ETA**: ~6 minutes (critical patients ONLY)

            ### 4. Hazards to AVOID
            - 🚫 Route Delta — BLOCKED (parking garage collapse debris)
            - ⚠️ 150m clearance from 4th & Harbor (gas main rupture)
            - ⚠️ 300m clearance from Cascadia Chemical (industrial solvent)
            - ⚠️ Masks REQUIRED on Route Golf (chemical spill proximity)
        """)

    def process_report(self, report: dict) -> dict:
        """
        Process a field report through the multi-agent swarm.

        Returns the same dict shape as ReasoningEngine.process_report().
        """
        t0 = time.perf_counter()
        log.info("=== Multi-Agent Swarm Processing ===")

        # Fan out to specialists
        assessments = []
        all_tool_calls = []
        for agent_name in AGENT_SPECIALISTS:
            log.info("Dispatching to [%s] agent …", agent_name.upper())
            result = self._run_specialist(agent_name, report)
            assessments.append(result)
            all_tool_calls.extend(
                {"agent": agent_name, **tc} for tc in result["tool_calls"]
            )

        # Commander synthesis
        log.info("Commander agent synthesising dispatch plan …")
        dispatch_plan = self._synthesise_dispatch(report, assessments)

        elapsed = time.perf_counter() - t0
        return {
            "status": "success",
            "dispatch_plan": dispatch_plan,
            "tool_calls": all_tool_calls,
            "agent_assessments": [
                {"agent": a["agent"], "assessment": a["assessment"]}
                for a in assessments
            ],
            "reasoning_rounds": sum(len(a["tool_calls"]) for a in assessments) + 1,
            "processing_time_s": round(elapsed, 2),
        }
