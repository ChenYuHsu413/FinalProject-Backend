"""Mock simulator (PROMPT §1): produces spec-shaped engine files + Redis events.

Designed so "swap in the real pipeline" means replacing this package's file
generation, not the API layer. Runs in the arq worker, never in the API
process (DECISIONS D3.4).
"""
