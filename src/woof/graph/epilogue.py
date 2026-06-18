"""Canonical denial epilogue appended to every dispatch prompt by the graph."""

DISPATCH_DENIAL_EPILOGUE = (
    "\n\nDo not run Woof graph or dispatch commands, `woof check`, gates, commits,"
    " or reviewer steps yourself; the graph runs those and selects the next node."
    " Running the project quality command your task declares is fine.\n"
)
