"""Regression entry point for the redesigned creator workspace.

The old single-page refresh assertions have migrated to the workflow suite:
independent panes, deferred details, draft recovery and real guidance.
This entry point keeps the existing all-tests runner compatible.
"""
from test_frontend_workflow import *  # The suite intentionally executes when run directly.
