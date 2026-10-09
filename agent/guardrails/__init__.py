"""Guardrails: what nobody can skip, the agent included.

The rules (rules/) run the checks on each invoice (controls/) and say whether it may be paid. The
gate (gate.py) is the first limit the agent meets: it refuses to stage a payment the rules do not
allow. The shop contract on Arc is the last one: the payer simulates every payment before sending.
"""
