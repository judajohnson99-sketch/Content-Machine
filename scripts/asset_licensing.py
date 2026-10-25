#!/usr/bin/env python3
"""Licence records and the approval policy for visual assets.

Two jobs, kept together because one feeds the other:

1. **What a source's terms say**, as a rights record in the shape
   ``scripts/media.py`` already requires (``rights_record``), so externally
   sourced media travels the same path as the owner's own: catalog ->
   selection -> staged bytes -> render -> Kdenlive -> package.
2. **Who may decide to use it.** ``decide_use`` is the whole approval policy.
   It is table-driven so a future, more autonomous level can be described
   here without anyone having to hunt through the callers - and the table
   says, in code, that no such level is switched on.

The rules that matter:

* A provider's published licence is evidence, not clearance. A candidate's
  rights record carries ``provider_terms`` (what the provider says) beside a
  top-level ``commercial_use`` that stays ``False`` until a person approves
  first use. Anything that leaks into the catalog unapproved is therefore
  refused by ``media.rights_record`` - the existing gate - without this
  module having to be consulted.
* Unknown, ambiguous or incompatible rights are never inferred away. A
  record missing its creator or source page is ``review_required``; one the
  terms rule out is ``ineligible`` and cannot be approved at all.
* Nothing here spends money or enables a paid route. ``decide_use`` blocks
  ``cloud`` candidates unless the caller reports an explicit authorisation.
"""
import os

ELIGIBLE = "eligible"
REVIEW_REQUIRED = "review_required"
INELIGIBLE = "ineligible"

PENDING = "PENDING_OWNER_APPROVAL"
NEEDS_REVIEW = "NEEDS_RIGHTS_REVIEW"
APPROVED = "OWNER_APPROVED"

# What each provider's published terms say. These are the vendors' own
# statements, recorded so a reader can check them; they are re-read by a
# person at approval time, and `terms_reference` is where to look.
PROVIDER_TERMS = {
    "pexels": {
        "label": "Pexels",
        "license": "Pexels License",
        "license_url": "https://www.pexels.com/license/",
        "commercial_use": True,
        "modification_allowed": True,
        "attribution_required": True,
        "attribution_basis": "Pexels API terms ask that photographers and Pexels be credited",
        "redistribution": "only as part of a derivative work; not as standalone or unaltered copies",
        "restrictions": [
            "Do not sell or redistribute the file unaltered or standalone.",
            "Do not imply endorsement by, or association with, people or brands shown.",
            "Identifiable people, brands and private property may need separate permission for some uses.",
        ],
    },
    "pixabay": {
        "label": "Pixabay",
        "license": "Pixabay Content License",
        "license_url": "https://pixabay.com/service/license-summary/",
        "commercial_use": True,
        "modification_allowed": True,
        "attribution_required": False,
        "attribution_basis": "attribution is not required by the licence; recorded as a courtesy credit",
        "redistribution": "only as part of a derivative work; not as standalone or unaltered copies",
        "restrictions": [
            "Do not sell or distribute the file unaltered or standalone.",
            "Do not show identifiable people in a way that is offensive or implies endorsement.",
            "The licence does not prevent third-party Content ID claims on YouTube; keep this record to dispute one.",
        ],
    },
    "unsplash": {
        "label": "Unsplash",
        "license": "Unsplash License",
        "license_url": "https://unsplash.com/license",
        "commercial_use": True,
        "modification_allowed": True,
        "attribution_required": True,
        "attribution_basis": "Unsplash API guidelines require crediting the photographer and Unsplash",
        "redistribution": "only as part of a derivative work; not as standalone or unaltered copies",
        "restrictions": [
            "Do not sell unaltered copies or compile photos to build a competing service.",
            "No model or property release is granted for identifiable people, brands or private property.",
        ],
    },
}


def utc_now():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def stock_rights(provider, *, creator, creator_url, source_url, kind,
                 external_id, retrieved_utc=None):
    """The unapproved rights record for one stock candidate."""
    terms = PROVIDER_TERMS.get(provider)
    retrieved = retrieved_utc or utc_now()
    if terms is None:
        return {
            "source": provider, "license": "unknown", "license_url": None,
            "commercial_use": False, "evidence": f"no recorded terms for {provider}",
            "attribution_required": None, "attribution_text": None,
            "status": NEEDS_REVIEW, "eligibility": REVIEW_REQUIRED,
            "eligibility_reasons": [f"{provider} has no recorded licence terms"],
            "external": True, "retrieved_utc": retrieved,
            "verification": {"status": "unverified", "evidence": []},
        }
    reasons = []
    if not (creator or "").strip():
        reasons.append("the provider did not name a creator")
    if not (source_url or "").strip():
        reasons.append("the provider did not give a source page to cite")
    eligibility = REVIEW_REQUIRED if reasons else ELIGIBLE
    credit = (f"{'Video' if kind == 'video' else 'Photo'} by {creator or 'unknown creator'} "
              f"on {terms['label']}")
    if source_url:
        credit += f" ({source_url})"
    return {
        "source": terms["label"],
        "license": terms["license"],
        "license_url": terms["license_url"],
        "terms_reference": terms["license_url"],
        "terms_version": f"as published at {terms['license_url']} on {retrieved[:10]}",
        "creator": creator,
        "creator_url": creator_url,
        "source_url": source_url,
        "external_id": str(external_id),
        "retrieved_utc": retrieved,
        "provider_terms": {
            "commercial_use": terms["commercial_use"],
            "modification_allowed": terms["modification_allowed"],
            "redistribution": terms["redistribution"],
            "restrictions": list(terms["restrictions"]),
            "attribution_basis": terms["attribution_basis"],
        },
        # Stays False until a person approves first use: a provider saying
        # "free for commercial use" is the lead, not the clearance.
        "commercial_use": False,
        "attribution_required": terms["attribution_required"],
        "attribution_text": credit,
        "evidence": (f"{terms['label']} API record {source_url or external_id}, retrieved "
                     f"{retrieved}; {terms['license']} ({terms['license_url']}); "
                     "awaiting owner approval"),
        "status": NEEDS_REVIEW if reasons else PENDING,
        "eligibility": eligibility,
        "eligibility_reasons": reasons,
        "external": True,
        "verification": {
            "status": "provider_asserted",
            "evidence": [source_url, terms["license_url"]],
        },
    }


class ApprovalError(ValueError):
    pass


def approve(rights, *, actor, note="", now=None):
    """The rights record as it stands once ``actor`` approves first use.

    Refuses the ineligible outright, and refuses a ``review_required`` record
    unless the person says what they checked: approval of an ambiguous
    record is a human judgement and must carry its reason.
    """
    if not (actor or "").strip():
        raise ApprovalError("an approval records who made it; actor is required")
    if rights.get("eligibility") == INELIGIBLE:
        raise ApprovalError("these terms rule out use in a production; nothing to approve")
    terms = rights.get("provider_terms") or {}
    if terms.get("commercial_use") is not True:
        raise ApprovalError("the provider's terms do not clearly allow commercial use")
    if rights.get("eligibility") == REVIEW_REQUIRED and not (note or "").strip():
        raise ApprovalError(
            "the rights record is incomplete (" + "; ".join(rights.get("eligibility_reasons") or ["unclear"])
            + "); say what you checked in the approval note")
    stamp = now or utc_now()
    approved = dict(rights)
    approved["commercial_use"] = True
    approved["status"] = APPROVED
    approved["approval"] = {
        "actor": actor.strip(), "utc": stamp,
        "scope": "first_use", "note": (note or "").strip(),
        "terms_reference": rights.get("terms_reference"),
        # What the approver was looking at: the licence as it stood, and the
        # uses its terms allowed. A later reuse is only covered while the
        # record still says the same (see ``approval_covers``).
        "covers": {
            "license": rights.get("license"),
            "terms_reference": rights.get("terms_reference"),
            "commercial_use": terms.get("commercial_use") is True,
            "modification_allowed": terms.get("modification_allowed") is True,
            "redistribution": ("derivative_only" if "derivative" in
                               (terms.get("redistribution") or "") else terms.get("redistribution")),
        },
    }
    approved["evidence"] = (f"{rights.get('evidence', '').replace('awaiting owner approval', '').rstrip('; ')}"
                            f"; approved for production use by {actor.strip()} at {stamp}")
    return approved


def approved(rights):
    """Whether a person has approved this record, by the record itself."""
    approval = (rights or {}).get("approval") or {}
    return ((rights or {}).get("status") == APPROVED
            and bool((approval.get("actor") or "").strip())
            and rights.get("commercial_use") is True)


DEFAULT_USE = {"commercial": True, "modification": True,
               "redistribution": "derivative_only"}


def approval_covers(rights, use=None):
    """Whether a past approval still covers a (new) use. ``(ok, reasons)``.

    The rule graduated autonomy is defined by, and a check supervised mode
    applies too: an approval is not a blanket pass. It covers a use only when

    * a named person approved it and the approval recorded its scope;
    * the licence and terms reference are the ones that were approved;
    * the provider's terms (verified at retrieval, restated on the record)
      allow every part of the requested use - commercial, modified, and
      redistributed only as part of a derivative work; and
    * any attribution the terms require can actually be given.

    Anything else fails closed and asks a person again.
    """
    use = {**DEFAULT_USE, **(use or {})}
    rights = rights or {}
    if not approved(rights):
        return False, ["no one has approved this for production use"]
    approval = rights.get("approval") or {}
    covers = approval.get("covers")
    reasons = []
    if not isinstance(covers, dict) or approval.get("scope") not in ("first_use", "reuse"):
        return False, ["the approval did not record what it covers; approve it again"]
    if (covers.get("license") != rights.get("license")
            or covers.get("terms_reference") != rights.get("terms_reference")):
        reasons.append("the licence or its terms changed since it was approved")
    terms = rights.get("provider_terms") or {}
    if use["commercial"] and not (covers.get("commercial_use") and terms.get("commercial_use") is True):
        reasons.append("commercial use is not covered")
    if use["modification"] and not (covers.get("modification_allowed")
                                    and terms.get("modification_allowed") is True):
        reasons.append("modifying the media is not covered")
    if use["redistribution"] != covers.get("redistribution"):
        reasons.append(f"redistribution as {use['redistribution']!r} is not covered")
    if rights.get("attribution_required") is not False and not (rights.get("attribution_text") or "").strip():
        reasons.append("required attribution cannot be given: no credit line is recorded")
    return not reasons, reasons


def is_external(rights):
    return bool((rights or {}).get("external"))


# --------------------------------------------------------------------------
# the approval policy
# --------------------------------------------------------------------------

# Each level is a row of answers to the same questions. Only "supervised" is
# enabled. "graduated" is the shape a later milestone may fill in - for
# example waiving the approval for a provider and creator a person has
# already approved N times - and it is written down now so that adding it is
# a table change with tests, not a change of habit. `enabled: False` means
# `autonomy_level` will not return it, whatever the environment asks for.
AUTONOMY_LEVELS = {
    "supervised": {
        "enabled": True,
        "summary": "Search, rank and reuse automatically; a person approves every first use of external media.",
        "auto_search": True,
        "auto_reuse_approved": True,
        "reuse_requires": "approval_covers",
        "external_first_use": "owner_approval",
        "generation": "owner_initiated",
        "cloud_generation": "never",
    },
    "graduated": {
        "enabled": False,
        "summary": ("Defined, not active. Previously approved assets are reused with no "
                    "new prompt only where the verified licence and the approval's scope "
                    "cover the new use; anything not covered asks a person again."),
        "auto_search": True,
        "auto_reuse_approved": True,
        # Reuse is permitted exactly when `approval_covers` says so - for the
        # new use, not merely because the asset was approved once. First use
        # of anything new still needs a person; this level never waives that.
        "reuse_requires": "approval_covers",
        "external_first_use": "owner_approval",
        "generation": "owner_initiated",
        "cloud_generation": "never",
    },
}

DEFAULT_LEVEL = "supervised"


def autonomy_level(environ=None):
    """The level in force. Always an enabled one, with a note when the
    environment asked for something that is not available yet."""
    environ = os.environ if environ is None else environ
    requested = (environ.get("VISUAL_ASSET_AUTONOMY") or DEFAULT_LEVEL).strip().lower()
    row = AUTONOMY_LEVELS.get(requested)
    note = None
    if row is None or not row["enabled"]:
        note = (f"VISUAL_ASSET_AUTONOMY={requested!r} is not available; "
                f"using {DEFAULT_LEVEL!r}")
        requested = DEFAULT_LEVEL
    return {"level": requested, "policy": dict(AUTONOMY_LEVELS[requested]), "note": note}


def decide_use(candidate, *, level=DEFAULT_LEVEL, cloud_authorised=False):
    """May this candidate go into a production, and who must say so?

    ``action`` is one of:

    * ``auto``              - usable with no further decision
    * ``approval_required`` - a person must approve first use
    * ``blocked``           - cannot be used; ``reasons`` says what would change that
    """
    policy = AUTONOMY_LEVELS.get(level) or AUTONOMY_LEVELS[DEFAULT_LEVEL]
    source = candidate.get("source")
    rights = candidate.get("rights") or {}
    reasons = []

    if source == "cloud":
        if not cloud_authorised:
            return {"action": "blocked",
                    "reasons": ["Cloud generation is off until you authorise spending on it."]}
        return {"action": "approval_required",
                "reasons": ["Cloud generation costs money; confirm before it runs."]}

    if source == "generated":
        # Choosing a generation is the owner starting it, and it costs
        # nothing but their own GPU time. When the GPU is off the choice is
        # recorded and the work waits - deferral is an outcome, not a refusal.
        if candidate.get("state") in ("deferred", "queued"):
            return {"action": "auto",
                    "reasons": [candidate.get("state_detail") or
                                "Will be generated when the GPU is available."]}
        return {"action": "auto", "reasons": ["Generated from the owner's own prompt on the owner's own machine."]}

    if rights.get("eligibility") == INELIGIBLE:
        return {"action": "blocked",
                "reasons": rights.get("eligibility_reasons") or ["The licence terms rule this out."]}

    if source == "library":
        library = candidate.get("library") or {}
        if candidate.get("state") == "unavailable":
            return {"action": "blocked",
                    "reasons": ["The file is on another machine; bring it online or retrieve it first."]}
        if library.get("rights_ok") is not True:
            if library.get("approvable"):
                return {"action": "approval_required",
                        "reasons": ["Saved from " + (rights.get("source") or "an external source")
                                    + " but not yet approved for production use."]}
            return {"action": "blocked",
                    "reasons": [library.get("rights_problem")
                                or "Needs a rights record in the library before use."]}
        if not is_external(rights):
            return {"action": "auto" if policy["auto_reuse_approved"] else "approval_required",
                    "reasons": ["Already in your library with an approved rights record."]}
        covered, why = approval_covers(rights, candidate.get("use"))
        if covered and policy["auto_reuse_approved"]:
            return {"action": "auto",
                    "reasons": ["Approved before, and that approval and its licence cover this use."]}
        return {"action": "approval_required",
                "reasons": ["External media in your library that is not covered by an approval: "
                            + "; ".join(why) + "."]}

    # stock
    if approved(rights):
        covered, why = approval_covers(rights, candidate.get("use"))
        if covered:
            return {"action": "auto", "reasons": ["Already approved for production use."]}
        reasons.append("An earlier approval does not cover this use: " + "; ".join(why) + ".")
    reasons.append("First use of media from " + (rights.get("source") or "an external source")
                   + " needs your approval.")
    if rights.get("eligibility") == REVIEW_REQUIRED:
        reasons.append("Its rights record is incomplete: " +
                       "; ".join(rights.get("eligibility_reasons") or ["unclear"]) + ".")
    return {"action": "approval_required", "reasons": reasons}
