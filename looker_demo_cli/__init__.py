import os

# Pre-bake Google API mTLS bypass for Cloudtop and enterprise workstations
os.environ.setdefault("CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE", "false")
os.environ.setdefault("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")

"""Looker Demo Orchestrator CLI (`demo-create`)."""

__version__ = "0.3.0"
