# utils.py
from enum import Enum
import pandas as pd

class ReviewStatus(str, Enum):
    UNASSIGNED        = "Level {level} – Unassigned"
    PENDING_REVIEW    = "Pending Level {level} Review"
    SME_REFERRED      = "Referred to SME"
    SME_RETURNED      = "Returned from SME – Awaiting Review"
    QC_REWORK         = "Level {level} QC – Rework Required"
    QC_IN_PROGRESS    = "Level {level} QC – In Progress"
    QC_UNASSIGNED     = "Level {level} QC – Awaiting Assignment"
    # UPDATED: align with MI (“Completed at Level N”)
    COMPLETED         = "Completed at Level {level}"

def is_missing(val):
    return val in [None, ""] or pd.isna(val)

# --- helpers to normalise sqlite REAL(0/1), TEXT("0"/"1"), etc. to booleans ---
def _as_bool(v):
    if v is None:
        return False
    if isinstance(v, (int, float)):
        try:
            return int(v) == 1
        except Exception:
            return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"1", "true", "y", "yes", "t"}:
            return True
        if s in {"0", "false", "n", "no", "f"}:
            return False
    return bool(v)

def derive_status(review: dict, level: int) -> str:
    referred              = review.get(f"l{level}_referred_to_sme")
    sme_returned          = review.get(f"l{level}_sme_returned_date")
    outcome               = review.get(f"l{level}_outcome")
    assigned_to           = review.get(f"l{level}_assigned_to")

    qc_assigned           = review.get(f"l{level}_qc_assigned_to")
    qc_check              = review.get(f"l{level}_qc_check_date")
    qc_outcome            = review.get(f"l{level}_qc_outcome")
    qc_rework_required    = _as_bool(review.get(f"l{level}_qc_rework_required"))
    qc_rework_completed   = _as_bool(review.get(f"l{level}_qc_rework_completed"))

    # SME flow (keep generic strings for compatibility with existing views)
    if referred and not sme_returned:
        return ReviewStatus.SME_REFERRED.value
    if referred and sme_returned and not outcome:
        return ReviewStatus.SME_RETURNED.value

    # Assignment vs pending
    if not assigned_to:
        return ReviewStatus.UNASSIGNED.value.format(level=level)
    if assigned_to and not outcome:
        return ReviewStatus.PENDING_REVIEW.value.format(level=level)

    # Post‐decision: QC / Completed
    if outcome:
        # If QC outcome exists, we treat as completed at this level (new label)
        if qc_outcome:
            return ReviewStatus.COMPLETED.value.format(level=level)

        # Rework states
        if qc_rework_required and not qc_rework_completed:
            if not qc_assigned:
                return ReviewStatus.QC_UNASSIGNED.value.format(level=level)
            if qc_assigned and not qc_check:
                return ReviewStatus.QC_IN_PROGRESS.value.format(level=level)
            return ReviewStatus.QC_REWORK.value.format(level=level)

        # If rework is required AND completed (even without qc_outcome),
        # fall through to "completed" handling at this level.
        if qc_rework_required and qc_rework_completed:
            return ReviewStatus.COMPLETED.value.format(level=level)

        # QC assignment/progress
        if qc_assigned and not qc_check:
            return ReviewStatus.QC_IN_PROGRESS.value.format(level=level)
        if not qc_assigned:
            return ReviewStatus.QC_UNASSIGNED.value.format(level=level)

        # Default to in progress if none of the above matched
        return ReviewStatus.QC_IN_PROGRESS.value.format(level=level)

    # Fallback
    return ReviewStatus.PENDING_REVIEW.value.format(level=level)

def derive_case_status(review: dict, _current_level=None) -> str:
    """
    Case-level status line used in the sidebar and stored in `reviews.status`.

    IMPORTANT tweak:
    - If `lN_qc_rework_required == 1` **and** `lN_qc_rework_completed == 1`,
      we consider the rework closed and move the case on as if QC is resolved,
      without requiring/overwriting `lN_qc_outcome`.
    """

    # --- Level 1 ---
    if is_missing(review.get("l1_assigned_to")) and is_missing(review.get("l1_outcome")) and is_missing(review.get("l1_referred_to_sme")):
        return "Level 1 – Unassigned"
    if not is_missing(review.get("l1_referred_to_sme")) and is_missing(review.get("l1_sme_returned_date")):
        return "Referred to SME (Level 1)"
    if not is_missing(review.get("l1_sme_returned_date")) and is_missing(review.get("l1_outcome")):
        return "Returned from SME (Level 1)"
    if not is_missing(review.get("l1_assigned_to")) and is_missing(review.get("l1_outcome")):
        return "Pending Level 1 Review"
    if not is_missing(review.get("l1_outcome")):
        l1_outcome = review.get("l1_outcome")
        l1_qc_outcome = review.get("l1_qc_outcome")
        l1_rework_req = _as_bool(review.get("l1_qc_rework_required"))
        l1_rework_done = _as_bool(review.get("l1_qc_rework_completed"))

        # QC outcome present -> terminal at this level (then next level if PTM)
        if not is_missing(l1_qc_outcome):
            if l1_outcome == "Discount":
                return "Completed at Level 1"
            if l1_outcome == "Potential True Match":
                return "Pending Level 2 Review"

        # Rework states
        if l1_rework_req and not l1_rework_done:
            if is_missing(review.get("l1_qc_assigned_to")):
                return "Level 1 QC – Awaiting Assignment"
            if not is_missing(review.get("l1_qc_assigned_to")) and is_missing(review.get("l1_qc_check_date")):
                return "Level 1 QC – In Progress"
            return "Level 1 QC – Rework Required"

        # Rework completed (treat as resolved, keep original QC outcome unchanged)
        if l1_rework_req and l1_rework_done:
            if l1_outcome == "Discount":
                return "Completed at Level 1"
            if l1_outcome == "Potential True Match":
                return "Pending Level 2 Review"

        # QC assignment/progress when no rework scenario
        if is_missing(review.get("l1_qc_assigned_to")):
            return "Level 1 QC – Awaiting Assignment"
        if not is_missing(review.get("l1_qc_assigned_to")) and is_missing(review.get("l1_qc_check_date")):
            return "Level 1 QC – In Progress"

    # --- Level 2 ---
    if review.get("l1_outcome") == "Potential True Match":
        if is_missing(review.get("l2_assigned_to")):
            return "Level 2 – Unassigned"
        if not is_missing(review.get("l2_assigned_to")) and is_missing(review.get("l2_outcome")):
            return "Pending Level 2 Review"
        if not is_missing(review.get("l2_outcome")):
            l2_outcome = review.get("l2_outcome")
            l2_qc_outcome = review.get("l2_qc_outcome")
            l2_rework_req = _as_bool(review.get("l2_qc_rework_required"))
            l2_rework_done = _as_bool(review.get("l2_qc_rework_completed"))

            if not is_missing(l2_qc_outcome):
                if l2_outcome == "Discount":
                    return "Completed at Level 2"
                if l2_outcome == "Potential True Match":
                    return "Pending Level 3 Review"

            if l2_rework_req and not l2_rework_done:
                if is_missing(review.get("l2_qc_assigned_to")):
                    return "Level 2 QC – Awaiting Assignment"
                if not is_missing(review.get("l2_qc_assigned_to")) and is_missing(review.get("l2_qc_check_date")):
                    return "Level 2 QC – In Progress"
                return "Level 2 QC – Rework Required"

            if l2_rework_req and l2_rework_done:
                if l2_outcome == "Discount":
                    return "Completed at Level 2"
                if l2_outcome == "Potential True Match":
                    return "Pending Level 3 Review"

            if is_missing(review.get("l2_qc_assigned_to")):
                return "Level 2 QC – Awaiting Assignment"
            if not is_missing(review.get("l2_qc_assigned_to")) and is_missing(review.get("l2_qc_check_date")):
                return "Level 2 QC – In Progress"

    # --- Level 3 ---
    if review.get("l2_outcome") == "Potential True Match":
        if is_missing(review.get("l3_assigned_to")):
            return "Level 3 – Unassigned"
        if not is_missing(review.get("l3_assigned_to")) and is_missing(review.get("l3_outcome")):
            return "Pending Level 3 Review"
        if not is_missing(review.get("l3_outcome")):
            l3_outcome = review.get("l3_outcome")
            l3_qc_outcome = review.get("l3_qc_outcome")
            l3_rework_req = _as_bool(review.get("l3_qc_rework_required"))
            l3_rework_done = _as_bool(review.get("l3_qc_rework_completed"))

            if not is_missing(l3_qc_outcome):
                return "Completed at Level 3"

            if l3_rework_req and not l3_rework_done:
                if is_missing(review.get("l3_qc_assigned_to")):
                    return "Level 3 QC – Awaiting Assignment"
                if not is_missing(review.get("l3_qc_assigned_to")) and is_missing(review.get("l3_qc_check_date")):
                    return "Level 3 QC – In Progress"
                return "Level 3 QC – Rework Required"

            if l3_rework_req and l3_rework_done:
                return "Completed at Level 3"

            if is_missing(review.get("l3_qc_assigned_to")):
                return "Level 3 QC – Awaiting Assignment"
            if not is_missing(review.get("l3_qc_assigned_to")) and is_missing(review.get("l3_qc_check_date")):
                return "Level 3 QC – In Progress"

    return "(Unclassified)"