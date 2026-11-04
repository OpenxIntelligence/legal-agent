"""Offline test suite (unittest, both VERIFICATION modes).

This package marker is load-bearing: without it, `tests` is a
namespace package and any installed dependency shipping
top-level tests (e.g. fast-diff-match-patch, via eyecite)
shadows this directory for `from tests import ...`.
"""
