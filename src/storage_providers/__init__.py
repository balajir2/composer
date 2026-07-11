"""File Storage Provider Framework.

Backs two node types: file-trigger (read side — list/read/move) and
file-write (write side — write). One shared interface serves both, so a
future S3/Google Drive/OneDrive implementation only needs to be written
once. See docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md.
"""
