"""Batch the v1 frozen-profile digest without modifying the attested sources."""

import hashlib
import json
from pathlib import Path

from .. import provider_versions, team

# The single-profile verifier (team.frozen_profile) and the provider module are
# themselves inputs to the historical digest. Keep that verifier independent
# and compare all installed case signatures with it in regression tests.
_SHARED_MODULES = {
    "team",
    "engine",
    "control",
    "records",
    "coverage",
    "translation",
    "media",
    "provider_versions",
    "reference_batches",
    "repair_plan",
    "roles",
    "role_tools",
    "role_evidence",
    "role_analysis",
    "document_common",
    "narrative_checks",
    "geo_raster",
}


def case_signatures():
    profiles = team.catalog()
    providers = json.dumps(provider_versions.profile(), sort_keys=True).encode()
    states = {}
    for code, definition in profiles.items():
        state = hashlib.sha256(json.dumps(definition, ensure_ascii=False, sort_keys=True).encode())
        state.update(providers)
        states[code] = state

    for path in sorted(Path(team.__file__).resolve().parent.glob("*.py")):
        if path.stem in _SHARED_MODULES:
            codes = profiles
        elif path.stem.startswith("geo_"):
            codes = [code for code in profiles if code.startswith("G")]
        elif path.stem.startswith("document_"):
            codes = [code for code in profiles if not code.startswith("G")]
        elif path.stem == "vision":
            codes = ["B03"] if "B03" in profiles else []
        else:
            continue
        filename = path.name.encode()
        source = path.read_text(encoding="utf-8").replace("\r\n", "\n").encode()
        for code in codes:
            states[code].update(filename)
            states[code].update(source)
    return {code: state.hexdigest() for code, state in states.items()}
