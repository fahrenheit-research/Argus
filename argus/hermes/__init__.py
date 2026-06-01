"""Ports of NousResearch/hermes-agent production patterns.

Each module here corresponds 1:1 to a file under hermes-agent/agent/.
Where Hermes' version is 50–200 KB of accreted production logic, ARGUS
ports the high-value 80% that matters for a self-hosted single-user CLI,
trimming features that only matter at hosted-scale (credential pools,
account billing, OpenRouter aggregator quirks, etc.).

Upstream:  https://github.com/NousResearch/hermes-agent
License:   MIT (preserved per-file).
"""
