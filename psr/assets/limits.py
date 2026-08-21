"""Confirmed Source SDK 2013 SP studio-model capacity limits."""

# Valve's public studio.h declares a 32-entry MAXSTUDIOSKINS table and describes
# it as the total texture count. The shipped SDK 2013 SP StudioMDL rejects the
# 32nd unique name with "Too many materials used, max 32", so a recompilable QC
# may contain at most 31 unique model material names.
MAX_STUDIO_MATERIALS = 31

# The installed SDK 2013 SP StudioMDL accepts 1024 skin-family rows. A 1025-row
# isolated compile terminates with EXCEPTION_ACCESS_VIOLATION, so PSR must stop
# before invoking the tool at that boundary.
MAX_STUDIO_SKIN_FAMILIES = 1024


__all__ = ["MAX_STUDIO_MATERIALS", "MAX_STUDIO_SKIN_FAMILIES"]
